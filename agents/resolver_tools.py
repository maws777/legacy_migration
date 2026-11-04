"""Read-only tools of the Resolver_Agent and the deterministic validator of its proposals.

The tools only read Rule_Config and aggregate statistics of the current Run (row counts and raw
spellings of one field). They write nothing and never expose other row data.
"""
from __future__ import annotations

import json
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Mapping

from rules.config import RuleConfig
from rules.fields import dict_lookup, iso_minute, normalized_key, parse_creation_date
from rules.types import LLMSuggestion, UnknownVariant

TOOL_NAMES = ("lookup_dictionary", "find_similar_values", "inspect_pattern_frequency", "test_proposed_rule")
FIELD_RULE = {"originTool": "R2", "ecuCode": "R3", "ecuName": "R5", "Vehmodel": "R6", "Creation_date": "R8"}
DATE_FORMAT = "dd/mm/yyyy hh:mm"
_DATE_CANDIDATES = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d-%m-%Y %H:%M", "%d.%m.%Y %H:%M", "%Y/%m/%d %H:%M",
                    "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%y %H:%M")
SUMMARY_MAX = 200


def closed_values(field_name: str, cfg: RuleConfig) -> tuple[str, ...] | None:
    return {"originTool": cfg.tools, "ecuCode": cfg.value_codes, "ecuName": cfg.ecus,
            "Vehmodel": cfg.vehicle_models}.get(field_name)


def validate_proposal(uv: UnknownVariant, s: LLMSuggestion, cfg: RuleConfig | None = None) -> str | None:
    """Deterministic check before any application: the failed rule, or None when the proposal is valid."""
    if not 0.0 <= s.confidence <= 1.0:
        return f"confidence {s.confidence} outside [0, 1]"
    if not s.justification or not s.justification.strip():
        return "empty justification"
    if s.decision == "reject":
        return None
    if not s.value:
        return "map without a value"
    allowed = uv.allowed.values
    if allowed is not None and s.value not in allowed:
        return f"value '{s.value}' is not an allowed value of {uv.field}"
    if allowed is None and parse_creation_date(s.value) is None:
        return f"value '{s.value}' does not match {uv.allowed.format or DATE_FORMAT}"
    if cfg is not None:
        dico = closed_values(uv.field, cfg)
        if dico is not None and s.value not in dico:
            rule = FIELD_RULE.get(uv.field, "")
            return f"{rule} final check: '{s.value}' is not in the {uv.field} dictionary"
    return None


@dataclass(frozen=True)
class RunStats:
    """Aggregates of the current Run used by ``inspect_pattern_frequency`` (no row identifiers)."""
    key_rows: Mapping[str, int] = field(default_factory=dict)
    key_spellings: Mapping[str, Mapping[str, int]] = field(default_factory=dict)
    field_values: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    iteration: int
    tool: str
    args: dict
    result: dict
    summary: str

    def params(self) -> str:
        return json.dumps(self.args, ensure_ascii=False, sort_keys=True)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).upper()


def _short(text: str) -> str:
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX - 3] + "..."


class ResolverTools:
    """Tools bound to one Unknown_Variant of one Run. Every method is read-only."""

    def __init__(self, cfg: RuleConfig, stats: RunStats, uv: UnknownVariant) -> None:
        self.cfg, self.stats, self.uv = cfg, stats, uv

    # ---------------------------------------------------------------- dispatch
    def call(self, iteration: int, tool: str, args: Any) -> ToolCall:
        args = dict(args) if isinstance(args, Mapping) else {}
        if tool not in TOOL_NAMES:
            result = {"error": f"unknown tool '{tool}'; available: {', '.join(TOOL_NAMES)}"}
        else:
            try:
                result = getattr(self, tool)(**args)
            except TypeError as exc:
                result = {"error": f"invalid arguments for {tool}: {exc}"}
        return ToolCall(iteration, tool, args, result, _short(self._summary(tool, result)))

    @staticmethod
    def _summary(tool: str, r: dict) -> str:
        if "error" in r:
            return f"error: {r['error']}"
        if tool == "lookup_dictionary":
            if r.get("format"):
                return f"free value, format {r['format']}"
            return f"{len(r['values'])} values: {', '.join(r['values'])}"
        if tool == "find_similar_values":
            return ", ".join(f"{c['value']} ({c['score']:.2f}{', via ' + c['via'] if c.get('via') else ''})"
                             for c in r["candidates"]) or "no candidate"
        if tool == "inspect_pattern_frequency":
            spell = ", ".join(f"'{k}' x{v}" for k, v in r["spellings"].items())
            top = ", ".join(f"{k} x{v}" for k, v in r["field_top"].items())
            return f"{r['rows']} row(s), spellings {spell or '-'}; {r['field']} decided by rules: {top or '-'}"
        if r["valid"]:
            return (f"valid: {r['action']} on {r['rows_affected']} row(s)"
                    + (f" with '{r['target_value']}'" if r.get("target_value") else "")
                    + ("; auto-apply allowed" if r["auto_apply"] else "; a human must confirm"))
        return f"invalid: {r['failed_rule']}"

    def _field(self, field_name: str | None) -> str:
        return field_name or self.uv.field

    # ---------------------------------------------------------------- tools
    def lookup_dictionary(self, field: str | None = None) -> dict:
        f = self._field(field)
        values = closed_values(f, self.cfg)
        if values is not None:
            return {"field": f, "values": list(values)}
        if f == "Creation_date":
            return {"field": f, "values": [], "format": DATE_FORMAT}
        return {"error": f"no dictionary for field '{f}'"}

    def find_similar_values(self, value: str | None = None, field: str | None = None, limit: int = 3) -> dict:
        f = self._field(field)
        v = self.uv.normalized_value if value is None else str(value)
        limit = max(1, min(int(limit), 10))
        if f == "Creation_date":
            out = []
            for fmt in _DATE_CANDIDATES:
                try:
                    dt = datetime.strptime(v.strip(), fmt)
                except ValueError:
                    continue
                text = dt.strftime("%d/%m/%Y %H:%M")
                if all(c["value"] != text for c in out):
                    out.append({"value": text, "score": 1.0, "via": f"format {fmt}"})
            return {"field": f, "value": v, "candidates": out[:limit]}
        values = closed_values(f, self.cfg)
        if values is None:
            return {"error": f"no dictionary for field '{f}'"}
        key = _fold(normalized_key(v))
        scored: dict[str, dict] = {}
        for e in values:
            score = SequenceMatcher(None, key, _fold(normalized_key(e))).ratio()
            scored[e] = {"value": e, "score": round(score, 2)}
        if f == "originTool":                         # R2 aliases (e.g. DiagTool -> DDT2000)
            for alias, target in self.cfg.mappings.tool_map.items():
                score = round(SequenceMatcher(None, key, _fold(normalized_key(alias))).ratio(), 2)
                if score > scored[target]["score"]:
                    scored[target] = {"value": target, "score": score, "via": alias}
        ranked = sorted(scored.values(), key=lambda c: (-c["score"], c["value"]))
        return {"field": f, "value": v, "candidates": ranked[:limit]}

    def inspect_pattern_frequency(self, pattern_key: str | None = None) -> dict:
        key = pattern_key or self.uv.pattern_key
        f = key.split("|", 1)[0]
        spellings = dict(Counter(self.stats.key_spellings.get(key, {})).most_common(5))
        top = dict(Counter(self.stats.field_values.get(f, {})).most_common(5))
        return {"pattern_key": key, "field": f, "rows": int(self.stats.key_rows.get(key, 0)),
                "spellings": spellings, "field_top": top}

    def test_proposed_rule(self, value: str | None = None, decision: str = "map") -> dict:
        if decision not in ("map", "reject"):
            return {"error": "decision must be 'map' or 'reject'"}
        s = LLMSuggestion(decision, value, 1.0, "tool check")  # type: ignore[arg-type]
        failed = validate_proposal(self.uv, s, self.cfg)
        rows = int(self.stats.key_rows.get(self.uv.pattern_key, 0))
        auto = self.uv.field in self.cfg.pipeline.resolver_auto_apply_fields
        if failed:
            return {"valid": False, "failed_rule": failed, "rows_affected": rows, "auto_apply": auto}
        target = None
        if decision == "map":
            dt = parse_creation_date(value) if self.uv.field == "Creation_date" else None
            target = iso_minute(dt) if dt else dict_lookup(value, closed_values(self.uv.field, self.cfg) or ()) or value
        return {"valid": True, "failed_rule": None, "action": "CORRECT" if decision == "map" else "REJECT",
                "target_value": target, "rows_affected": rows, "auto_apply": auto}
