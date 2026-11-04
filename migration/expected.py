"""Expected target records of a VIN, derived from its Legacy A rows by ``decide_row``.

Used by the Orchestrator in phases 1-3 (decisions.md: same response before and after cutover) and by
the Reconciler in phase 3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from rules.config import RuleConfig
from rules.engine import decide_row
from rules.fields import dict_lookup, normalize_vin
from rules.types import CachedResolution, HumanDecisionView, RawRow, RowDecision, RuleContext
from target.schemas import DiagnosticItem


@dataclass(frozen=True)
class ExpectedRecord:
    source_row_id: int
    decision: RowDecision
    item: DiagnosticItem


def item_of(decision: RowDecision) -> DiagnosticItem:
    t = decision.target
    return DiagnosticItem(
        diagnostic_op_id=t.diagnostic_op_id, creation_date=t.creation_date.strftime("%Y-%m-%dT%H:%M"),
        ecu_name=t.ecu_name, value_description=t.value_description, value_reference=t.value_reference,
        diag_tool_name=t.diag_tool_name, diag_tool_version=t.diag_tool_version, vehicle_model=t.vehicle_model)


class ExpectedRecordBuilder:
    def __init__(self, config: Callable[[], RuleConfig], mapping: Callable[[], Mapping[str, str | None]],
                 human_decisions: Callable[[], Mapping[str, HumanDecisionView]],
                 cache: Callable[[], Mapping[str, CachedResolution]], shared_op_ids: Callable[[], frozenset[str]],
                 legacy_b_model: Callable[[str], str | None] | None = None) -> None:
        self._config, self._mapping, self._human = config, mapping, human_decisions
        self._cache, self._shared, self._legacy_b = cache, shared_op_ids, legacy_b_model

    def canonical_rows(self, raw_rows: Iterable) -> list[RawRow]:
        """Rename Legacy A columns to canonical names through the Column_Mapping."""
        cfg = self._config()
        rename = {c: cfg.column_mapping.canonical_for_target(t) for c, t in self._mapping().items() if t}
        out = []
        for r in raw_rows:
            values = {rename[c]: v for c, v in r.values.items() if c in rename}
            out.append(RawRow(r.source_row_id, values, r.malformed))
        return out

    def context(self, vin: str, rows: list[RawRow]) -> RuleContext:
        cfg = self._config()
        models = {dict_lookup(r.values.get("Vehmodel", ""), cfg.vehicle_models) for r in rows if not r.malformed}
        models.discard(None)
        lb = None
        if cfg.pipeline.legacy_b_lookup and self._legacy_b is not None:
            lb = self._legacy_b(vin)
        return RuleContext(config=cfg, human_decisions=self._human(), cache=self._cache(),
                           vin_model_index={vin: frozenset(models)} if models else {},
                           legacy_b_model_index={vin: lb} if lb else {}, shared_op_ids=self._shared())

    def for_rows(self, vin: str, raw_rows: Iterable) -> list[ExpectedRecord]:
        vin = normalize_vin(vin)
        rows = self.canonical_rows(raw_rows)
        ctx = self.context(vin, rows)
        by_op: dict[str, ExpectedRecord] = {}
        for r in rows:
            d = decide_row(r, ctx)
            if d.outcome in ("MIGRATE", "CORRECT") and d.target is not None:
                # Upsert semantics: the last row of an operation id wins, as in the Target_DB.
                by_op.pop(d.target.diagnostic_op_id, None)
                by_op[d.target.diagnostic_op_id] = ExpectedRecord(r.source_row_id, d, item_of(d))
        return sorted(by_op.values(), key=lambda e: e.source_row_id)
