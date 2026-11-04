"""FieldMemo: field-local results computed once per distinct raw value (Requirement 34.2)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping

from rules import fields as F
from rules.config import RuleConfig
from rules.labels import label_features
from rules.types import (
    COL_DATE, COL_ECU_NAME, COL_LABEL, COL_MODEL, COL_TOOL, COL_VALUE_REF, COL_VIN, LabelFeatures,
    R2Result, R4Result,
)


@dataclass(frozen=True)
class FieldMemo:
    vin: Mapping[str, str] = field(default_factory=dict)
    tool: Mapping[str, R2Result] = field(default_factory=dict)
    value_ref: Mapping[str, R4Result] = field(default_factory=dict)
    ecu_name: Mapping[str, str | None] = field(default_factory=dict)
    vehmodel: Mapping[str, str | None] = field(default_factory=dict)
    label: Mapping[str, LabelFeatures] = field(default_factory=dict)
    date: Mapping[str, datetime | None] = field(default_factory=dict)


def build_field_memo(distinct: Mapping[str, Iterable[str]], cfg: RuleConfig) -> FieldMemo:
    """``distinct`` maps canonical column names to their distinct raw values."""
    def values(col: str) -> set[str]:
        return set(distinct.get(col, ()))

    return FieldMemo(
        vin={v: F.normalize_vin(v) for v in values(COL_VIN)},
        tool={v: F.r2_tool(v, cfg) for v in values(COL_TOOL)},
        value_ref={v: F.r4_classify(v) for v in values(COL_VALUE_REF)},
        ecu_name={v: F.dict_lookup(v, cfg.ecus) for v in values(COL_ECU_NAME)},
        vehmodel={v: F.dict_lookup(v, cfg.vehicle_models) for v in values(COL_MODEL)},
        label={v: label_features(v, cfg.ecus) for v in values(COL_LABEL)},
        date={v: F.parse_creation_date(v) for v in values(COL_DATE)},
    )


def distinct_values(rows, columns: Iterable[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {c: set() for c in columns}
    for r in rows:
        for c in out:
            out[c].add(r.values.get(c, ""))
    return out
