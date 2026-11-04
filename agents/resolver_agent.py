"""Resolver_Agent: one LLM call per Unknown_Variant Pattern_Key, checked by a deterministic validator."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from rules.fields import parse_creation_date
from rules.types import LLMSuggestion, UnknownVariant

SYSTEM = (
    "You normalize one legacy data value for a vehicle-diagnostics migration.\n"
    'Reply with one JSON object only: {"decision":"map"|"reject","value":string|null,"confidence":number,'
    '"justification":string}.\n'
    '"value" must be one of "allowed" (or match "allowed_format"). Confidence is between 0 and 1.'
)
_JSON_BLOCK = re.compile(r"\{.*?\}", re.DOTALL)


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


def build_prompt(uv: UnknownVariant) -> str:
    """Only field, reason, normalized value and allowed values (Requirement 24.2)."""
    body: dict = {"field": uv.field, "reason": "UNKNOWN_VARIANT", "value": uv.normalized_value}
    if uv.allowed.values is not None:
        body["allowed"] = list(uv.allowed.values)
    else:
        body["allowed_format"] = uv.allowed.format
    return json.dumps(body, ensure_ascii=False)


def _failed(reason: str) -> LLMSuggestion:
    return LLMSuggestion("reject", None, 0.0, reason)


def parse_answer(text: str, uv: UnknownVariant) -> LLMSuggestion:
    """First ``{...}`` block, validated; deterministic check of the value against the allowed set or format."""
    m = _JSON_BLOCK.search(text or "")
    if not m:
        return _failed("unparseable LLM answer: no JSON object")
    try:
        ans = _Answer.model_validate(json.loads(m.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        first = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else "invalid JSON"
        return _failed(f"invalid LLM answer: {first}")
    if ans.decision == "map":
        if not ans.value:
            return _failed("invalid LLM answer: map without a value")
        if uv.allowed.values is not None and ans.value not in uv.allowed.values:
            return _failed(f"invalid LLM answer: value '{ans.value}' is not an allowed value")
        if uv.allowed.values is None and parse_creation_date(ans.value) is None:
            return _failed(f"invalid LLM answer: value '{ans.value}' does not match {uv.allowed.format}")
        return LLMSuggestion("map", ans.value, ans.confidence, ans.justification.strip())
    return LLMSuggestion("reject", None, ans.confidence, ans.justification.strip())


class ResolverAgent:
    def __init__(self, llm, max_tokens: int = 150) -> None:
        self.llm = llm
        self.max_tokens = max_tokens

    def propose(self, run_id: str, uv: UnknownVariant) -> tuple[LLMSuggestion, int]:
        res = self.llm.complete(run_id=run_id, purpose="resolver", system=SYSTEM, prompt=build_prompt(uv),
                                max_tokens=self.max_tokens)
        tokens = res.input_tokens + res.output_tokens
        if not res.ok:
            return _failed(f"LLM failure: {res.error_type} ({res.message})"), tokens
        return parse_answer(res.text, uv), tokens
