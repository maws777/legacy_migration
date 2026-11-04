"""Frozen shared types of the migration-engine (design "Public interfaces").

Pure module: no I/O. Every other package may import it to build mocks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Mapping

if TYPE_CHECKING:  # pragma: no cover
    from rules.config import RuleConfig
    from rules.memo import FieldMemo

Action = Literal["MIGRATE", "TRANSFORM", "CORRECT", "REJECT", "ASK_HUMAN"]
RowOutcome = Literal["MIGRATE", "CORRECT", "REJECT", "ASK_HUMAN"]
DecidedBy = Literal["rule", "llm", "human"]
# "human_required": a valid proposal for a field that is never auto-applied (decisions.md).
LLMStatus = Literal["below_threshold", "failed", "degraded", "human_required"]

ACTIONS: tuple[str, ...] = ("MIGRATE", "TRANSFORM", "CORRECT", "REJECT", "ASK_HUMAN")
ROW_OUTCOMES: tuple[str, ...] = ("MIGRATE", "CORRECT", "REJECT", "ASK_HUMAN")
DECIDED_BY: tuple[str, ...] = ("rule", "llm", "human")

# Canonical source column names (migration-rules.md "Column mapping").
COL_TOOL = "originTool"
COL_TOOL_VERSION = "toolVersion"
COL_VIN = "VIN"
COL_OP_ID = "diagnosticFieId"
COL_ECU_NAME = "ecuName"
COL_ECU_CODE = "ecuCode"
COL_LABEL = "DescriptionDiagECU"
COL_VALUE_REF = "ValueReference"
COL_DATE = "Creation_date"
COL_MODEL = "Vehmodel"
CANONICAL_COLUMNS: tuple[str, ...] = (
    COL_TOOL, COL_TOOL_VERSION, COL_VIN, COL_OP_ID, COL_ECU_NAME,
    COL_ECU_CODE, COL_LABEL, COL_VALUE_REF, COL_DATE, COL_MODEL,
)
ROW_FIELD = "_row"
LOAD_FIELD = "_load"


@dataclass(frozen=True)
class RawRow:
    source_row_id: int                 # 1-based ordinal of the data record in the file
    values: Mapping[str, str]          # canonical column name -> raw cell (exact string)
    malformed: bool = False

    def get(self, column: str) -> str:
        return self.values.get(column, "")


@dataclass(frozen=True)
class LLMSuggestion:
    decision: Literal["map", "reject"]
    value: str | None
    confidence: float
    justification: str


@dataclass(frozen=True)
class FieldDecision:
    field: str                          # canonical source column name, "_load" for LOAD
    action: Action
    rule_id: str                        # R1..R8, FC1..FC4, INGEST, LOAD
    reason: str                         # reason code
    before: str                         # raw value as read ("" when the cell is empty)
    after: str = ""                     # "" = no value
    decided_by: DecidedBy = "rule"
    confidence: float | None = None
    explanation: str = ""
    tokens_used: int = 0
    pattern_key: str | None = None      # set when the case needed a Pattern_Key
    suggestion: LLMSuggestion | None = None


@dataclass(frozen=True)
class OperationIdentity:
    """Operation_Identity. Per decisions.md it also carries DiagToolName and DiagToolVersion."""
    vin: str | None
    ecu_name: str | None
    value_description: str | None
    creation_date: str | None           # ISO "YYYY-MM-DDTHH:MM"
    value_reference: str | None
    diag_tool_name: str | None = None
    diag_tool_version: str | None = None

    def is_complete(self) -> bool:
        return all(
            v is not None
            for v in (self.vin, self.ecu_name, self.value_description, self.creation_date,
                      self.value_reference, self.diag_tool_name, self.diag_tool_version)
        )


@dataclass(frozen=True)
class TargetValues:
    vin: str
    vehicle_model: str
    ecu_name: str
    diag_tool_name: str
    diag_tool_version: str
    diagnostic_op_id: str
    op_id_generated: bool
    creation_date: datetime
    value_description: str
    value_reference: str
    source_origin_tool: str             # raw originTool, unmodified


@dataclass(frozen=True)
class RowDecision:
    source_row_id: int
    fields: tuple[FieldDecision, ...]   # deterministic evaluation order, "_row" last
    outcome: RowOutcome
    identity: OperationIdentity
    target: TargetValues | None         # set only when outcome in {MIGRATE, CORRECT}

    @property
    def row_record(self) -> FieldDecision:
        return self.fields[-1]


@dataclass(frozen=True)
class HumanDecisionView:
    pattern_key: str
    kind: Literal["value", "reject", "generate"]
    value: str | None


@dataclass(frozen=True)
class CachedResolution:
    pattern_key: str
    decision: Literal["map", "reject"]
    value: str | None
    confidence: float
    justification: str


@dataclass(frozen=True)
class AllowedValues:
    """Allowed target values of a field: a closed list, or a format for free values."""
    values: tuple[str, ...] | None = None
    format: str | None = None           # e.g. "dd/mm/yyyy hh:mm"


@dataclass(frozen=True)
class UnknownVariant:
    pattern_key: str
    field: str
    normalized_value: str
    allowed: AllowedValues


@dataclass(frozen=True)
class RowScan:
    norm_vin: str
    matched_model: str | None
    trimmed_op_id: str | None           # None when diagnosticFieId is an Unreadable_Value
    identity: OperationIdentity
    unknown_variants: tuple[UnknownVariant, ...]


@dataclass(frozen=True)
class R2Result:
    kind: Literal["mapped", "migrate", "unreadable", "unknown"]
    trimmed: str
    value: str | None                   # target tool for mapped / migrate


@dataclass(frozen=True)
class R4Result:
    """R4 classification of a ValueReference for ID0002-ID0005."""
    reason: str | None                  # EMPTY | NOM | EXCEL_SCI | FORBIDDEN_CHAR | WRONG_LENGTH | None
    value: str                          # raw.strip().upper()


@dataclass(frozen=True)
class LabelFeatures:
    did: str | None
    ecu: str | None
    template: str


@dataclass(frozen=True)
class PatternKeyParts:
    field: str
    reason: str
    detail: str | None
    source_row_id: int | None
    group_key: str                      # key without the "#row=" suffix


@dataclass(frozen=True)
class RuleContext:
    config: "RuleConfig"
    human_decisions: Mapping[str, HumanDecisionView] = field(default_factory=dict)
    cache: Mapping[str, CachedResolution] = field(default_factory=dict)
    llm_suggestions: Mapping[str, LLMSuggestion] = field(default_factory=dict)
    llm_status: Mapping[str, LLMStatus] = field(default_factory=dict)
    llm_tokens: Mapping[str, int] = field(default_factory=dict)
    vin_model_index: Mapping[str, frozenset[str]] = field(default_factory=dict)
    legacy_b_model_index: Mapping[str, str] = field(default_factory=dict)
    shared_op_ids: frozenset[str] = frozenset()
    degraded: bool = False
    memo: "FieldMemo | None" = None
