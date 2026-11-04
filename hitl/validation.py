"""Validation of a human ``apply_value`` answer for each line of the Pattern_Key table."""
from __future__ import annotations

from dataclasses import dataclass, field

from rules.config import RuleConfig
from rules.fields import parse_creation_date
from rules.pattern_key import parse_key

ACCEPT_TARGET = "ACCEPT_TARGET"
DATE_HINT = "dd/mm/yyyy hh:mm"


@dataclass(frozen=True)
class SharedIdView:
    shared: frozenset[str] = frozenset()       # run_shared_op_id of the latest full Run
    used: frozenset[str] = frozenset()         # trimmed ids already carried by source rows


@dataclass(frozen=True)
class FieldRule:
    name: str
    values: tuple[str, ...] | None = None      # closed list
    kind: str = "list"                         # list | version | date | op_id | accept_target


def field_rule(pattern_key: str, cfg: RuleConfig) -> FieldRule:
    parts = parse_key(pattern_key)
    f, reason = parts.field, parts.reason
    if f == "_row" and reason == "RECON_GAP":
        return FieldRule("RECON_GAP: only ACCEPT_TARGET is allowed", (ACCEPT_TARGET,), "accept_target")
    if f == "ecuCode":
        return FieldRule("R3 ValueDescription in ID0001-ID0005", cfg.value_codes)
    if f == "ecuName":
        return FieldRule("R5 ecuName in ECUDico", cfg.ecus)
    if f == "Vehmodel":
        return FieldRule("R6 VehicleModel in VehicleModelsDico", cfg.vehicle_models)
    if f == "originTool":
        return FieldRule("R2 DiagToolName in ToolDico", cfg.tools)
    if f == "toolVersion":
        return FieldRule("R8 toolVersion: non-empty, at most 64 printable characters", None, "version")
    if f == "Creation_date":
        return FieldRule(f"R8 Creation_date: valid {DATE_HINT} date-time", None, "date")
    if f == "diagnosticFieId":
        return FieldRule("R7 diagnosticOpID: non-empty trimmed id of at most 128 characters, not shared and not "
                         "already used", None, "op_id")
    raise ValueError(f"no validation rule for Pattern_Key {pattern_key!r}")


def allowed_values(pattern_key: str, cfg: RuleConfig) -> list[str] | None:
    rule = field_rule(pattern_key, cfg)
    return list(rule.values) if rule.values is not None else None


def validate_value(pattern_key: str, value: str | None, cfg: RuleConfig,
                   shared: SharedIdView = SharedIdView()) -> str | None:
    """Return the failed rule, or None when the value is valid for the Pattern_Key."""
    rule = field_rule(pattern_key, cfg)
    v = value if value is not None else ""
    if rule.kind in ("list", "accept_target"):
        return None if v in (rule.values or ()) else f"{rule.name}: '{v}' is not allowed"
    if rule.kind == "version":
        ok = 0 < len(v.strip()) and len(v) <= 64 and v.isprintable() and v == v.strip()
        return None if ok else f"{rule.name}: '{v}' is invalid"
    if rule.kind == "date":
        return None if parse_creation_date(v) is not None and v == v.strip() else f"{rule.name}: '{v}' is invalid"
    t = v.strip()
    if not t or len(t) > 128 or t != v:
        return f"{rule.name}: '{v}' is empty, too long or not trimmed"
    if t in shared.shared or t in shared.used:
        return f"{rule.name}: '{v}' is already carried by other operations"
    return None
