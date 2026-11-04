"""Resolver_Agent: tool-using agent for one Unknown_Variant Pattern_Key (decisions.md, "Agent de résolution").

Provider-neutral JSON protocol (works with Ollama and Anthropic, no provider-specific tool calling).
Each turn the model replies with exactly one JSON object:

- ``{"tool": "<name>", "args": {...}}`` to call one read-only tool of ``agents.resolver_tools``;
- ``{"final": {"decision": "map"|"reject", "value": ..., "confidence": ..., "justification": ...}}`` to answer.

At most ``max_iterations`` (3) turns per Pattern_Key; the last turn must be a final answer. Every final
answer passes the deterministic validator before anything is applied.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from agents.resolver_tools import TOOL_NAMES, ResolverTools, RunStats, ToolCall, validate_proposal
from rules.config import RuleConfig
from rules.types import LLMSuggestion, UnknownVariant

MAX_ITERATIONS = 3

SYSTEM = (
    "You normalize one legacy data value for a vehicle-diagnostics migration.\n"
    "Each turn, reply with ONE JSON object only, either\n"
    '  {"tool": "<name>", "args": {...}}   to call one read-only tool, or\n'
    '  {"final": {"decision": "map"|"reject", "value": string|null, "confidence": number, '
    '"justification": string}}   to answer.\n'
    "Tools:\n"
    '- lookup_dictionary {"field"?}: allowed values (or the format) of the field.\n'
    '- find_similar_values {"value"?, "field"?, "limit"?}: closest allowed values with a similarity score.\n'
    '- inspect_pattern_frequency {"pattern_key"?}: rows and raw spellings of the pattern in this Run, and the '
    "values of the field decided by rules.\n"
    '- test_proposed_rule {"value", "decision"?}: deterministic check of a candidate answer (allowed set, format, '
    "final checks), rows affected and whether it may be applied automatically.\n"
    'At most 3 turns in total; when "last_turn" is true you must send "final". "value" must be one of '
    '"allowed" (or match "allowed_format"). Confidence is between 0 and 1.'
)


class _Answer(BaseModel):
    decision: Literal["map", "reject"]
    value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    justification: str = Field(min_length=1)

    @field_validator("justification")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty justification")
        return v


@dataclass(frozen=True)
class ResolverOutcome:
    suggestion: LLMSuggestion
    tokens: int
    iterations: int
    tool_calls: tuple[ToolCall, ...] = ()
    failure: str | None = None

    def trace(self, width: int = 90) -> str:
        """Compact trace for Decision_Record explanations (the full trace is in the tool-call log)."""
        if not self.tool_calls:
            return f"Resolver_Agent: {self.iterations} iteration(s), no tool call."
        parts = []
        for c in self.tool_calls:
            summary = c.summary if len(c.summary) <= width else c.summary[:width - 3] + "..."
            parts.append(f"{c.tool}({c.params()}) -> {summary}")
        return (f"Resolver_Agent: {self.iterations} iteration(s), {len(self.tool_calls)} tool call(s): "
                + "; ".join(parts) + ".")


def build_prompt(uv: UnknownVariant, turn: int | None = None, max_turns: int = MAX_ITERATIONS,
                 tool_results: list[dict] | None = None) -> str:
    """Field, reason, normalized value and allowed values (Requirement 24.2), plus the turn and tool results."""
    body: dict = {"field": uv.field, "reason": "UNKNOWN_VARIANT", "value": uv.normalized_value}
    if uv.allowed.values is not None:
        body["allowed"] = list(uv.allowed.values)
    else:
        body["allowed_format"] = uv.allowed.format
    if turn is not None:
        body.update(turn=turn, max_turns=max_turns, last_turn=turn >= max_turns, tool_results=tool_results or [])
    return json.dumps(body, ensure_ascii=False)


def first_json(text: str) -> object | None:
    """First JSON value starting at a ``{`` in the text (nested objects allowed)."""
    decoder = json.JSONDecoder()
    text = text or ""
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text, start)
            return value
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    return None


def _failed(reason: str) -> LLMSuggestion:
    return LLMSuggestion("reject", None, 0.0, reason)


def parse_final(data: object, uv: UnknownVariant, cfg: RuleConfig | None = None) -> LLMSuggestion:
    """Schema check, then the deterministic validator. Invalid -> confidence 0 (Requirement 24.4)."""
    if isinstance(data, dict) and isinstance(data.get("final"), dict):
        data = data["final"]
    if not isinstance(data, dict):
        return _failed("unparseable LLM answer: no JSON object")
    try:
        ans = _Answer.model_validate(data)
    except ValidationError as exc:
        return _failed(f"invalid LLM answer: {exc.errors()[0]['msg']}")
    s = LLMSuggestion(ans.decision, ans.value if ans.decision == "map" else None, ans.confidence,
                      ans.justification.strip())
    failed = validate_proposal(uv, s, cfg)
    return _failed(f"invalid LLM answer: {failed}") if failed else s


def parse_answer(text: str, uv: UnknownVariant, cfg: RuleConfig | None = None) -> LLMSuggestion:
    """Final answer from raw text: ``{"final": {...}}`` or a bare answer object."""
    return parse_final(first_json(text), uv, cfg)


class ResolverAgent:
    def __init__(self, llm, max_tokens: int = 150, max_iterations: int = MAX_ITERATIONS,
                 config: RuleConfig | None = None) -> None:
        self.llm = llm
        self.max_tokens = max_tokens
        self.max_iterations = max(1, min(int(max_iterations), MAX_ITERATIONS))
        self.config = config

    def resolve(self, run_id: str, uv: UnknownVariant, stats: RunStats | None = None,
                cfg: RuleConfig | None = None) -> ResolverOutcome:
        cfg = cfg or self.config
        tools = ResolverTools(cfg, stats or RunStats(), uv) if cfg is not None else None
        calls: list[ToolCall] = []
        results: list[dict] = []
        tokens = 0
        for turn in range(1, self.max_iterations + 1):
            prompt = build_prompt(uv, turn, self.max_iterations, results)
            res = self.llm.complete(run_id=run_id, purpose="resolver", system=SYSTEM, prompt=prompt,
                                    max_tokens=self.max_tokens)
            tokens += res.input_tokens + res.output_tokens
            if not res.ok:
                reason = f"LLM failure: {res.error_type} ({res.message})"
                return ResolverOutcome(_failed(reason), tokens, turn, tuple(calls), reason)
            data = first_json(res.text)
            if not (isinstance(data, dict) and isinstance(data.get("tool"), str)):
                s = parse_final(data, uv, cfg)
                return ResolverOutcome(s, tokens, turn, tuple(calls), None if s.confidence > 0 else s.justification)
            if turn >= self.max_iterations or tools is None:
                reason = f"no final answer after {turn} iteration(s)"
                return ResolverOutcome(_failed(reason), tokens, turn, tuple(calls), reason)
            call = tools.call(turn, data["tool"], data.get("args") or {})
            calls.append(call)
            results.append({"turn": turn, "tool": call.tool, "args": call.args, "result": call.result})
        reason = f"no final answer after {self.max_iterations} iteration(s)"  # pragma: no cover
        return ResolverOutcome(_failed(reason), tokens, self.max_iterations, tuple(calls), reason)

    def propose(self, run_id: str, uv: UnknownVariant) -> tuple[LLMSuggestion, int]:
        """Design signature: (proposal, tokens)."""
        out = self.resolve(run_id, uv)
        return out.suggestion, out.tokens


__all__ = ["MAX_ITERATIONS", "ResolverAgent", "ResolverOutcome", "SYSTEM", "TOOL_NAMES", "build_prompt",
           "first_json", "parse_answer", "parse_final"]
