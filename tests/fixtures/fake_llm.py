"""Scripted LLM client for tests: no network, same interface as agents.llm_client.LLMClient."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Callable

from agents.llm_client import LLMFailure, LLMSuccess, RunTokenTotals


class FakeLLM:
    def __init__(self, answer: Callable[[dict], str | LLMFailure] | dict[str, str] | None = None,
                 available: bool = True, tokens: tuple[int, int] = (100, 20)) -> None:
        self.answer = answer
        self.available = available
        self.tokens = tokens
        self.calls: list[dict] = []
        self._totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])

    def is_available(self) -> bool:
        return self.available

    def complete(self, *, run_id, purpose, system, prompt, max_tokens=None):
        self.calls.append({"run_id": run_id, "purpose": purpose, "system": system, "prompt": prompt})
        try:
            body = json.loads(prompt)
        except ValueError:
            body = {"prompt": prompt}
        a = self.answer
        out = a(body) if callable(a) else (a or {}).get(body.get("value", ""), "no answer")
        t = self._totals[run_id]
        t[2] += 1
        if isinstance(out, LLMFailure):
            return out
        t[0] += self.tokens[0]
        t[1] += self.tokens[1]
        return LLMSuccess(out, self.tokens[0], self.tokens[1], "fake-model", 1)

    def totals(self, run_id: str) -> RunTokenTotals:
        t = self._totals.get(run_id, [0, 0, 0])
        return RunTokenTotals(t[0], t[1], t[2])


def answer(decision: str, value: str | None, confidence: float, why: str = "matches by spelling") -> str:
    return json.dumps({"decision": decision, "value": value, "confidence": confidence, "justification": why})
