"""LLM_Client: the only component that calls an LLM provider. Never raises to callers.

Providers (Rule_Config ``llm.provider``):

- ``ollama`` (default): local HTTP API, ``POST {base_url}/api/chat`` with ``"format": "json"``; tokens
  from ``prompt_eval_count`` and ``eval_count``; cost 0 EUR.
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
Purpose = Literal["schema_discovery", "resolver", "question_rephrase", "query_agent"]
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


class OllamaProvider:
    """Local Ollama server: ``/api/chat`` with a forced JSON answer, no streaming, temperature 0."""

    name = "ollama"

    def __init__(self, cfg: LLMConfig, http: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.http = http or httpx.Client(base_url=cfg.base_url, timeout=cfg.timeout_s, transport=OLLAMA_TRANSPORT)

    def availability(self) -> tuple[bool, str | None]:
        where = f"Ollama unreachable at {self.cfg.base_url}"
        try:
            r = self.http.get("/api/tags", timeout=min(AVAILABILITY_TIMEOUT_S, self.cfg.timeout_s))
        except httpx.HTTPError as exc:
            return False, f"{where} ({type(exc).__name__})"
        if r.status_code != 200:
            return False, f"{where} (HTTP {r.status_code})"
        try:
            names = {n for m in r.json().get("models", []) for n in (m.get("name"), m.get("model")) if n}
        except (ValueError, AttributeError):
            return False, f"{where} (invalid /api/tags answer)"
        model = self.cfg.model
        if model not in names and f"{model}:latest" not in names:
            return False, f"Ollama model {model} is not pulled (run: ollama pull {model})"
        return True, None

    def call(self, system: str, prompt: str, max_tokens: int) -> tuple[str, int, int, str]:
        body = {"model": self.cfg.model, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"temperature": 0, "num_predict": max_tokens}}
        try:
            r = self.http.post("/api/chat", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", type(exc).__name__) from None
        except httpx.HTTPError as exc:
            raise ProviderError("provider_error", type(exc).__name__) from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        tin, tout = int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0)
        if r.status_code != 200:
            raise ProviderError("provider_error", f"HTTPStatusError (HTTP {r.status_code})", tin, tout)
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


class NoLLM:
    """Explicitly disabled LLM (``--no-llm``): the Run is in Degraded_Mode and makes no call."""

    def __init__(self, reason: str = "LLM disabled (--no-llm)") -> None:
        self.reason = reason

    def availability(self) -> tuple[bool, str | None]:
        return False, self.reason

    def is_available(self) -> bool:
        return False

    def complete(self, **_kw) -> LLMResult:
        return LLMFailure("provider_error", self.reason)

    def totals(self, run_id: str) -> RunTokenTotals:
        return RunTokenTotals(0, 0, 0)
