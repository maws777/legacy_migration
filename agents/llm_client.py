"""LLM_Client: the only component that calls the local Ollama service."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Literal

import httpx
from sqlalchemy import Column, DateTime, Engine, Integer, MetaData, String, Table, func, insert, select

from rules.config import LLMConfig
from rules.timeutil import naive_utc, utcnow

LLMErrorType = Literal["service_unavailable", "timeout", "provider_error"]
Purpose = Literal["schema_discovery", "resolver", "question_rephrase", "query_agent"]

LLM_MD = MetaData()
llm_call = Table(
    "llm_call", LLM_MD,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(64), nullable=False, index=True),
    Column("purpose", String(24), nullable=False),
    Column("status", String(8), nullable=False),          # ok | error
    Column("error_type", String(24)),
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
    message: str
    input_tokens: int = 0
    output_tokens: int = 0
    ok: Literal[False] = False


LLMResult = LLMSuccess | LLMFailure


@dataclass(frozen=True)
class RunTokenTotals:
    input_tokens: int
    output_tokens: int
    calls: int


def _ollama_factory(base_url: str, timeout_s: float) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout_s)


def _classify(exc: BaseException) -> LLMErrorType:
    if isinstance(exc, httpx.TimeoutException) or isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, httpx.ConnectError):
        return "service_unavailable"
    return "provider_error"


def _safe_message(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status is not None else "")


def _usage(resp: dict[str, Any]) -> tuple[int, int]:
    return int(resp.get("prompt_eval_count", 0) or 0), int(resp.get("eval_count", 0) or 0)


class LLMClient:
    def __init__(self, cfg: LLMConfig, engine: Engine,
                 client_factory: Callable[[str, float], Any] = _ollama_factory) -> None:
        self.cfg = cfg
        self.engine = engine
        self._factory = client_factory
        LLM_MD.create_all(engine)

    def is_available(self) -> bool:
        client = None
        try:
            client = self._factory(self.cfg.base_url, self.cfg.timeout_s)
            response = client.get("/api/tags")
            response.raise_for_status()
            body = response.json()
            models = body.get("models") if isinstance(body, dict) else None
            return isinstance(models, list) and any(
                isinstance(model, dict) and self.cfg.model in (model.get("name"), model.get("model"))
                for model in models
            )
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError):
            return False
        finally:
            if client is not None:
                client.close()

    def _record(self, run_id: str, purpose: str, status: str, error_type: str | None, tin: int, tout: int,
                latency_ms: int) -> None:
        with self.engine.begin() as c:
            c.execute(insert(llm_call).values(run_id=run_id, purpose=purpose, status=status, error_type=error_type,
                                              input_tokens=tin, output_tokens=tout, latency_ms=latency_ms,
                                              at=naive_utc(utcnow())))

    def complete(self, *, run_id: str, purpose: Purpose, system: str, prompt: str,
                 max_tokens: int | None = None) -> LLMResult:
        t0 = time.perf_counter()
        client = None
        try:
            client = self._factory(self.cfg.base_url, self.cfg.timeout_s)
            response = client.post(
                "/api/chat",
                json={
                    "model": self.cfg.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {"num_predict": max_tokens or self.cfg.max_tokens},
                },
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("invalid Ollama response")
            tin, tout = _usage(body)
            message = body.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ValueError("invalid Ollama response")
            text = message["content"]
            latency = int((time.perf_counter() - t0) * 1000)
        except Exception as exc:  # noqa: BLE001 - the client returns typed failures
            latency = int((time.perf_counter() - t0) * 1000)
            error_type = _classify(exc)
            self._record(run_id, purpose, "error", error_type, 0, 0, latency)
            return LLMFailure(error_type, _safe_message(exc))
        finally:
            if client is not None:
                client.close()
        self._record(run_id, purpose, "ok", None, tin, tout, latency)
        model = body.get("model")
        return LLMSuccess(text, tin, tout, model if isinstance(model, str) else self.cfg.model, latency)

    def totals(self, run_id: str) -> RunTokenTotals:
        with self.engine.connect() as c:
            row = c.execute(select(func.coalesce(func.sum(llm_call.c.input_tokens), 0),
                                   func.coalesce(func.sum(llm_call.c.output_tokens), 0), func.count())
                            .where(llm_call.c.run_id == run_id)).one()
        return RunTokenTotals(int(row[0]), int(row[1]), int(row[2]))
