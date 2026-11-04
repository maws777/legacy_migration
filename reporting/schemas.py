"""Reporting and run schemas (frozen contracts 1 and 2)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_serializer, field_validator

from rules.timeutil import as_utc, iso_z
from rules.types import Action, DecidedBy, RowOutcome

DECISION_FIELDS = ("run_id", "source_row", "field", "action", "rule_id", "reason", "before", "after",
                   "decided_by", "confidence", "explanation", "tokens_used", "timestamp")


class DecisionRecord(BaseModel):
    run_id: str
    source_row: int
    field: str
    action: Action
    rule_id: str
    reason: str
    before: str
    after: str
    decided_by: DecidedBy
    confidence: float | None
    explanation: str
    tokens_used: int
    timestamp: datetime

    @field_validator("reason", "explanation")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return as_utc(v)  # type: ignore[return-value]

    @field_serializer("timestamp")
    def _ser_ts(self, v: datetime) -> str:
        return iso_z(v)


class ToolCallRecord(BaseModel):
    """One Resolver_Agent tool call, traced in the decision log (decisions.md)."""
    run_id: str
    pattern_key: str
    field: str
    iteration: int
    tool: str
    params: str                     # JSON of the arguments
    result_summary: str
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return as_utc(v)  # type: ignore[return-value]

    @field_serializer("timestamp")
    def _ser_ts(self, v: datetime) -> str:
        return iso_z(v)


class LogFilters(BaseModel):
    source_row: int | None = None
    field: str | None = None
    action: Action | None = None
    rule_id: str | None = None
    decided_by: DecidedBy | None = None


class DecisionPage(BaseModel):
    total: int
    items: list[DecisionRecord]


class StartRunRequest(BaseModel):
    operator: str | None = None


class RunInfo(BaseModel):
    run_id: str
    campaign_id: str
    kind: Literal["full", "reload"]
    status: Literal["running", "completed", "failed", "mapping_pending"]
    campaign_status: Literal["running", "paused", "done"]
    started_at: datetime
    ended_at: datetime | None
    open_questions: int
    error: str | None = None


class RunReport(BaseModel):
    run_id: str
    text: str
    analysed: int
    migrate: int
    correct: int
    reject: int
    ask_human: int
    out_of_scope: int
    traced_pct: float
    tokens: int
    cost_eur: float
    seconds: float
    notes: list[str]
    # decisions.md: coverage distinct from the Aligned_Percentage, reject split, cold/warm Run.
    loaded: int = 0
    coverage_pct: float = 0.0
    rejected_out_of_scope: int = 0
    rejected_error: int = 0
    pending_human: int = 0
    cache_state: Literal["cold", "warm"] = "cold"


class RunMetrics(BaseModel):
    run_id: str
    total_seconds: float
    stage_seconds: dict[str, float]
    input_tokens: int
    output_tokens: int
    llm_calls: int
    distinct_pattern_keys: int
    cache_hit_rate: float
    batch_size: int
    out_of_scope_rejects: int
    degraded_mode: bool
    legacy_b_lookup: Literal["enabled", "disabled", "failed"]
    counts: dict[RowOutcome, int]
