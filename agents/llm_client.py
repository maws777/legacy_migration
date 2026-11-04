"""LLM_Client: the only component that calls the LLM provider (Anthropic). Never raises to callers.

The key is read from ``ANTHROPIC_API_KEY`` at each call and never written anywhere.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

from sqlalchemy import Column, DateTime, Engine, Integer, MetaData, String, Table, func, insert, select

from rules.config import LLMConfig
from rules.timeutil import naive_utc, utcnow

LLMErrorType = Literal["missing_key", "timeout", "provider_error"]
Purpose = Literal["schema_discovery", "resolver", "question_rephrase", "query_agent"]
KEY_ENV = "ANTHROPIC_API_KEY"

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


def _anthropic_factory(api_key: str, timeout_s: float) -> Any:
    import anthropic
    return anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=0)


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


def _safe_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status is not None else "")


def _usage(resp: Any) -> tuple[int, int]:
    usage = getattr(resp, "usage", None)
    return int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)


def _text(resp: Any) -> str:
    parts = []
    for block in getattr(resp, "content", None) or []:
        t = getattr(block, "text", None)
        if t:
            parts.append(t)
    return "".join(parts)


class LLMClient:
    def __init__(self, cfg: LLMConfig, engine: Engine,
                 client_factory: Callable[[str, float], Any] = _anthropic_factory) -> None:
        self.cfg = cfg
        self.engine = engine
        self._factory = client_factory
        LLM_MD.create_all(engine)

    def is_available(self) -> bool:
        return bool(os.environ.get(KEY_ENV, "").strip())

    def _record(self, run_id: str, purpose: str, status: str, error_type: str | None, tin: int, tout: int,
                latency_ms: int) -> None:
        with self.engine.begin() as c:
            c.execute(insert(llm_call).values(run_id=run_id, purpose=purpose, status=status, error_type=error_type,
                                              input_tokens=tin, output_tokens=tout, latency_ms=latency_ms,
                                              at=naive_utc(utcnow())))

    def complete(self, *, run_id: str, purpose: Purpose, system: str, prompt: str,
                 max_tokens: int | None = None) -> LLMResult:
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            return LLMFailure("missing_key", f"{KEY_ENV} is not set")
        t0 = time.perf_counter()
        try:
            client = self._factory(key, self.cfg.timeout_s)
            resp = client.messages.create(model=self.cfg.model, max_tokens=max_tokens or self.cfg.max_tokens,
                                          system=system, messages=[{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001 - the client never raises
            latency = int((time.perf_counter() - t0) * 1000)
            tin, tout = _usage(getattr(exc, "response", None))
            error_type = _classify(exc)
            self._record(run_id, purpose, "error", error_type, tin, tout, latency)
            return LLMFailure(error_type, _safe_message(exc), tin, tout)
        latency = int((time.perf_counter() - t0) * 1000)
        tin, tout = _usage(resp)
        self._record(run_id, purpose, "ok", None, tin, tout, latency)
        return LLMSuccess(_text(resp), tin, tout, getattr(resp, "model", None) or self.cfg.model, latency)

    def totals(self, run_id: str) -> RunTokenTotals:
        with self.engine.connect() as c:
            row = c.execute(select(func.coalesce(func.sum(llm_call.c.input_tokens), 0),
                                   func.coalesce(func.sum(llm_call.c.output_tokens), 0), func.count())
                            .where(llm_call.c.run_id == run_id)).one()
        return RunTokenTotals(int(row[0]), int(row[1]), int(row[2]))
