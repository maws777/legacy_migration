"""Agent and Orchestrator schemas (frozen contracts 1 and 4)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from legacy_a.schemas import Header, RawRowOut, RowsPage, VinRows  # noqa: F401 (re-exported)
from rules.errors import ErrorBody
from target.schemas import DiagnosticItem, DiagnosticsResponse  # noqa: F401 (re-exported)


class FormatSpec(BaseModel):
    name: Literal["Format_1", "Format_2"]
    ecuCode_pattern: str


class MappingConflict(BaseModel):
    target_attribute: str
    source_columns: list[str]
    # "target_count": a required target mapped by zero or several columns;
    # "duplicate_column": a column given several times in the LLM answer (JSON with duplicate keys).
    kind: Literal["target_count", "duplicate_column"] = "target_count"
    message: str = ""


class MappingDifference(BaseModel):
    """One source column whose proposed target differs from the reference Column_Mapping of Rule_Config."""
    column: str
    proposed: str | None
    reference: str | None


class SchemaProposal(BaseModel):
    proposal_id: str
    column_mapping: dict[str, str | None]
    formats: list[FormatSpec]
    format_criterion: str
    conflicts: list[MappingConflict]
    fallback: bool
    created_at: datetime
    differences_from_reference: list[MappingDifference] = []


class ColumnMapping(BaseModel):
    mapping: dict[str, str | None]
    validated_by: str
    validated_at: datetime
    differences_from_reference: list[MappingDifference] = []

    def source_for(self, target_attribute: str) -> str:
        for column, target in self.mapping.items():
            if target == target_attribute:
                return column
        raise KeyError(target_attribute)


class ValidateMappingRequest(BaseModel):
    proposal_id: str
    operator: str = Field(min_length=1)


class FieldError(BaseModel):
    field: str
    raw: str


class ModelReference(BaseModel):
    vin: str | None
    vehicle_model: str | None
    updated_on: str | None
    errors: list[FieldError] = []


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    sql: str | None
    rows: list[dict[str, Any]]
    truncated: bool
    input_tokens: int
    output_tokens: int
    error: ErrorBody | None = None


# ---------------------------------------------------------------- phases (task 9)
class TransitionRequest(BaseModel):
    target_phase: int
    operator: str | None = None


class RollbackRequest(BaseModel):
    target_phase: int
    operator: str = Field(min_length=1)


class OperatorRequest(BaseModel):
    operator: str = Field(min_length=1)


class GateCheck(BaseModel):
    name: Literal["open_questions", "control_sample_gaps"]
    passed: bool
    count: int | None
    message: str


class TransitionResult(BaseModel):
    status: Literal["switched", "pending_approval", "refused", "cancelled"]
    current_phase: int
    previous_phase: int | None
    reason: str | None
    checks: list[GateCheck] = []
    # decisions.md: approval shows coverage and the exclusion summary; approving validates them.
    summary: dict[str, Any] | None = None


class PhaseStatus(BaseModel):
    current_phase: int
    pending_target: int | None
    gate: list[GateCheck]
    served: dict[str, int]
    summary: dict[str, Any] | None = None


class TransitionLogEntry(BaseModel):
    from_phase: int
    to_phase: int
    rollback: bool
    operator: str | None
    at: datetime


class ReconResult(BaseModel):
    scope: Literal["vin", "control_sample"]
    compared: int
    gaps: int
    aligned_pct: float | None
    not_performed: list[str]
    at: datetime
    missing_in_target: int = 0
    unexpected_in_target: int = 0
    value_mismatch: int = 0
    duplicate_in_target: int = 0
