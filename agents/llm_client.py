"""LLM_Client: the only component that calls an LLM provider. Never raises to callers.

Providers (Rule_Config ``llm.provider``):

- ``ollama`` (default): HTTP API, ``POST {base_url}/api/chat`` with ``"format": "json"``; tokens
  from ``prompt_eval_count`` and ``eval_count``; cost 0 EUR. Local server; the model comes from
  ``OLLAMA_MODEL`` (environment or ``.env``) or Rule_Config ``llm.model``; ``OLLAMA_BASE_URL`` overrides the URL.
- ``anthropic``: ``anthropic.Anthropic().messages.create``; key read from ``ANTHROPIC_API_KEY`` at each
  call and never written anywhere.

The interface used by the rest of the code is unchanged: ``is_available()``, ``complete(...)``,
``totals(run_id)``. ``availability()`` also returns the reason when the provider cannot be used.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx
from sqlalchemy import Column, DateTime, Engine, Integer, MetaData, String, Table, func, insert, select

from rules.config import LLMConfig
from rules.timeutil import naive_utc, utcnow

LLMErrorType = Literal["missing_key", "timeout", "provider_error"]
Purpose = Literal["schema_discovery", "resolver", "question_rephrase", "query_agent", "question_advisor",
                  "legacy_b_query"]
KEY_ENV = "ANTHROPIC_API_KEY"
# Transport used for Ollama when no client is injected; the test suite replaces it so that no test
# can reach a real Ollama server.
OLLAMA_TRANSPORT: httpx.BaseTransport | None = None
AVAILABILITY_TIMEOUT_S = 3.0

LLM_MD = MetaData()
llm_call = Table(
    "llm_call", LLM_MD,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("purpose", String(24), nullable=False),
    Column("status", String(8), nullable=False),          # ok | error
    Column("error_type", String(16)),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("at", DateTime, nullable=False),
)


@dataclass(frozen=True)
class LLMSuccess:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: int
    ok: Literal[True] = True


@dataclass(frozen=True)
class LLMFailure:
    error_type: LLMErrorType
    message: str                                   # never contains the key
    input_tokens: int = 0
    output_tokens: int = 0
    ok: Literal[False] = False


LLMResult = LLMSuccess | LLMFailure


@dataclass(frozen=True)
class RunTokenTotals:
    input_tokens: int
    output_tokens: int
    calls: int


class ProviderError(Exception):
    """A provider call failed; ``error_type`` is timeout or provider_error, ``message`` has no secret."""

    def __init__(self, error_type: LLMErrorType, message: str, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message)
        self.error_type, self.message = error_type, message
        self.input_tokens, self.output_tokens = input_tokens, output_tokens


def _safe_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status is not None else "")


# ---------------------------------------------------------------- providers
def _anthropic_factory(api_key: str, timeout_s: float) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, client_factory: Callable[[str, float], Any] = _anthropic_factory) -> None:
        self.cfg = cfg
        self._factory = client_factory

    @staticmethod
    def _key() -> str:
        return os.environ.get(KEY_ENV, "").strip()

    def availability(self) -> tuple[bool, str | None]:
        return (True, None) if self._key() else (False, f"anthropic: {KEY_ENV} is not set")

    def call(self, system: str, prompt: str, max_tokens: int) -> tuple[str, int, int, str]:
        try:
            client = self._factory(self._key(), self.cfg.timeout_s)
            resp = client.messages.create(model=self.cfg.model, max_tokens=max_tokens, system=system,
                                          messages=[{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001
            tin, tout = self._usage(getattr(exc, "response", None))
            raise ProviderError(self._classify(exc), _safe_message(exc), tin, tout) from None
        tin, tout = self._usage(resp)
        text = "".join(getattr(b, "text", "") or "" for b in (getattr(resp, "content", None) or []))
        return text, tin, tout, getattr(resp, "model", None) or self.cfg.model

    @staticmethod
    def _usage(resp: Any) -> tuple[int, int]:
        usage = getattr(resp, "usage", None)
        return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)

    @staticmethod
    def _classify(exc: BaseException) -> LLMErrorType:
        try:
            import anthropic
            if isinstance(exc, anthropic.APITimeoutError):
                return "timeout"
        except ImportError:  # pragma: no cover
            pass
        if isinstance(exc, TimeoutError) or type(exc).__name__ in ("APITimeoutError", "TimeoutException"):
            return "timeout"
        return "provider_error"


def ollama_error(r: httpx.Response) -> str:
    """``HTTP <status>: <message returned by Ollama>`` (e.g. an out-of-memory error at model load)."""
    try:
        data = r.json()
        detail = str(data.get("error") or "") if isinstance(data, dict) else ""
    except ValueError:
        detail = r.text or ""                       # not JSON: keep the raw body
    detail = detail.strip()
    if len(detail) > 300:
        detail = detail[:297] + "..."
    return f"HTTP {r.status_code}" + (f": {detail}" if detail else "")


class OllamaProvider:
    """Local Ollama server: ``/api/chat`` with a forced JSON answer, no streaming, temperature 0.

    Every request carries ``keep_alive`` so the model stays loaded between calls.
    """

    name = "ollama"

    def __init__(self, cfg: LLMConfig, http: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.http = http or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s, transport=OLLAMA_TRANSPORT)

    def availability(self) -> tuple[bool, str | None]:
        """Server reachable, model pulled, and model loaded in memory (the load fails on lack of memory)."""
        where = f"Ollama unreachable at {self.cfg.base_url}"
        try:
            r = self.http.get("/api/tags", timeout=min(AVAILABILITY_TIMEOUT_S, self.cfg.timeout_s))
        except httpx.HTTPError as exc:
            return False, f"{where} ({type(exc).__name__})"
        if r.status_code != 200:
            return False, f"{where} ({ollama_error(r)})"
        try:
            names = {n for m in r.json().get("models", []) for n in (m.get("name"), m.get("model")) if n}
        except (ValueError, AttributeError):
            return False, f"{where} (invalid /api/tags answer)"
        model = self.cfg.model
        if model not in names and f"{model}:latest" not in names:
            return False, f"Ollama model {model} is not pulled (run: ollama pull {model})"
        # A generate request without a prompt only loads the model (no-op when it is already loaded).
        try:
            r = self.http.post("/api/generate", json={"model": model, "keep_alive": self.cfg.keep_alive})
        except httpx.TimeoutException as exc:
            return False, f"Ollama did not load {model} within {self.cfg.timeout_s:g} s ({type(exc).__name__})"
        except httpx.HTTPError as exc:
            return False, f"{where} ({type(exc).__name__})"
        if r.status_code != 200:
            return False, f"Ollama could not load {model} ({ollama_error(r)})"
        return True, None

    def call(self, system: str, prompt: str, max_tokens: int) -> tuple[str, int, int, str]:
        body = {"model": self.cfg.model, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"temperature": 0, "num_predict": max_tokens + self.cfg.reasoning_tokens}}
        if self.cfg.think is not None:
            body["think"] = self.cfg.think
        body["keep_alive"] = self.cfg.keep_alive
        try:
            r = self.http.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"{type(exc).__name__} after {self.cfg.timeout_s:g} s") from None
        except httpx.HTTPError as exc:
            raise ProviderError("provider_error", type(exc).__name__) from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        tin, tout = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if r.status_code != 200:
            raise ProviderError("provider_error", ollama_error(r), tin, tout)
        text = (data.get("message") or {}).get("content") or ""
        return text, tin, tout, data.get("model") or self.cfg.model


def make_provider(cfg: LLMConfig, client_factory: Callable[[str, float], Any] | None = None,
                  http_client: httpx.Client | None = None):
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg, client_factory or _anthropic_factory)
    return OllamaProvider(cfg, http_client)


# ---------------------------------------------------------------- client
class LLMClient:
    def __init__(self, cfg: LLMConfig, engine: Engine, client_factory: Callable[[str, float], Any] | None = None,
                 http_client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.engine = engine
        self.provider = make_provider(cfg, client_factory, http_client)
        LLM_MD.create_all(engine)

    def availability(self) -> tuple[bool, str | None]:
        """(usable, reason when not usable): Ollama unreachable or model missing, anthropic key absent."""
        return self.provider.availability()

    def is_available(self) -> bool:
        return self.availability()[0]

    def _record(self, run_id: str, purpose: str, status: str, error_type: str | None, tin: int, tout: int,
                latency_ms: int) -> None:
        with self.engine.begin() as c:
            c.execute(insert(llm_call).values(run_id=run_id, purpose=purpose, status=status, error_type=error_type,
                                              input_tokens=tin, output_tokens=tout, latency_ms=latency_ms,
                                              at=naive_utc(utcnow())))

    def complete(self, *, run_id: str, purpose: Purpose, system: str, prompt: str,
                 max_tokens: int | None = None) -> LLMResult:
        if self.cfg.provider == "anthropic" and not AnthropicProvider._key():
            return LLMFailure("missing_key", f"{KEY_ENV} is not set")      # no contact, no call counted
        t0 = time.perf_counter()
        try:
            text, tin, tout, model = self.provider.call(system, prompt, max_tokens or self.cfg.max_tokens)
        except ProviderError as exc:
            self._record(run_id, purpose, "error", exc.error_type, exc.input_tokens, exc.output_tokens,
                         int((time.perf_counter() - t0) * 1000))
            return LLMFailure(exc.error_type, exc.message, exc.input_tokens, exc.output_tokens)
        except Exception as exc:  # noqa: BLE001 - the client never raises
            self._record(run_id, purpose, "error", "provider_error", 0, 0, int((time.perf_counter() - t0) * 1000))
            return LLMFailure("provider_error", _safe_message(exc))
        latency = int((time.perf_counter() - t0) * 1000)
        self._record(run_id, purpose, "ok", None, tin, tout, latency)
        return LLMSuccess(text, tin, tout, model, latency)

    def totals(self, run_id: str) -> RunTokenTotals:
        with self.engine.connect() as c:
            row = c.execute(select(func.coalesce(func.sum(llm_call.c.input_tokens), 0),
                                   func.coalesce(func.sum(llm_call.c.output_tokens), 0), func.count())
                            .where(llm_call.c.run_id == run_id)).one()
        return RunTokenTotals(int(row[0]), int(row[1]), int(row[2]))

    def call_rows(self) -> list[dict]:
        """Every recorded call (run_id, purpose, tokens, at), for the cost breakdown of reporting/cost.py."""
        return read_call_rows(self.engine)

    def warm_up(self) -> tuple[bool, float, str]:
        """Load the model and send one short request, so the first Run does not pay the load time.

        Returns (ready, seconds, detail). For anthropic only the key is checked (no paid call).
        """
        t0 = time.perf_counter()
        ok, reason = self.availability()
        if not ok:
            return False, round(time.perf_counter() - t0, 1), reason or "provider not available"
        if self.cfg.provider == "anthropic":
            return True, round(time.perf_counter() - t0, 1), f"anthropic {self.cfg.model}: key present"
        res = self.complete(run_id="warm-up", purpose="resolver", system="Reply with JSON only.",
                            prompt='Return {"ready": true}.', max_tokens=10)
        secs = round(time.perf_counter() - t0, 1)
        if not res.ok:
            return False, secs, f"{res.error_type}: {res.message}"
        return True, secs, f"ollama {self.cfg.model} at {self.cfg.base_url}"


def read_call_rows(engine: Engine) -> list[dict]:
    LLM_MD.create_all(engine)
    with engine.connect() as c:
        rows = c.execute(select(llm_call.c.run_id, llm_call.c.purpose, llm_call.c.input_tokens,
                                llm_call.c.output_tokens, llm_call.c.at).order_by(llm_call.c.id)).mappings().all()
    return [dict(r) for r in rows]


class NoLLM:
    """Explicitly disabled LLM (``--no-llm``): the Run is in Degraded_Mode and makes no call."""

    def __init__(self, reason: str = "LLM disabled (--no-llm)") -> None:
        self.reason = reason

    def call_rows(self) -> list[dict]:
        return []

    def availability(self) -> tuple[bool, str | None]:
        return False, self.reason

    def is_available(self) -> bool:
        return False

    def complete(self, **_kw) -> LLMResult:
        return LLMFailure("provider_error", self.reason)

    def totals(self, run_id: str) -> RunTokenTotals:
        return RunTokenTotals(0, 0, 0)
