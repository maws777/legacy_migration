"""HITL schemas (frozen contract 3)."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from rules.types import LLMSuggestion

Option = Literal["apply_value", "reject_group", "case_by_case", "generate_op_id"]
# "proposed_human_only": valid proposal for a field that is never auto-applied (decisions.md).
QuestionLLMStatus = Literal["proposed_below_threshold", "proposed_human_only", "failed", "degraded", "not_applicable"]


@dataclass(frozen=True)
class QuestionGroup:
    """Rows of one Run sharing one ASK_HUMAN Pattern_Key (built by the Migration_Pipeline)."""
    pattern_key: str
    field: str
    reason: str
    rule_id: str
    rows: tuple[int, ...]                                  # distinct Source_Row_Ids in source order
    examples: tuple[tuple[int, dict], ...] = ()            # first N rows: (Source_Row_Id, raw values)
    llm_suggestion: LLMSuggestion | None = None
    llm_status: str | None = None                          # RuleContext.llm_status value
    homogeneous: bool = False                              # same raw value and context on every row
    explanation: str = ""
    raw_values: tuple[str, ...] = dc_field(default=())
    row_values: tuple[tuple[int, dict], ...] = ()          # every row: (Source_Row_Id, raw values)


class LLMSuggestionOut(BaseModel):
    decision: Literal["map", "reject"]
    value: str | None
    confidence: float
    justification: str


class ExampleRow(BaseModel):
    source_row_id: int
    values: dict[str, str]


class HumanQuestion(BaseModel):
    question_id: str
    run_id: str | None
    pattern_key: str
    field: str
    reason: str
    rule_id: str
    row_count: int
    example_rows: list[ExampleRow]
    missing: str
    why: str
    llm_suggestion: LLMSuggestionOut | None
    llm_status: QuestionLLMStatus
    allowed_values: list[str] | None
    options: list[Option]
    status: Literal["open", "closed", "superseded"]
    parent_question_id: str | None
    parent_pattern_key: str | None = None
    source_row_id: int | None = None
    scope: Literal["group", "row"] = "group"
    created_at: datetime
    closed_at: datetime | None


class HumanAnswer(BaseModel):
    option: Option
    value: str | None = None
    operator: str = Field(min_length=1)


class HumanDecision(BaseModel):
    pattern_key: str
    kind: Literal["value", "reject", "generate"]
    value: str | None
    answered_at: datetime
    operator: str
    question_id: str


class AnswerResult(BaseModel):
    question: HumanQuestion
    decision: HumanDecision | None
    created_questions: list[str] = []
