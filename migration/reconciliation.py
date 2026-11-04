"""Reconciler (phase 3): expected records from Legacy A vs Target_DB records.

decisions.md: records are matched by diagnosticOpID, then values are compared. Gap kinds:
missing_in_target, unexpected_in_target, value_mismatch, duplicate_in_target. A VIN that cannot be
read is "not performed" (excluded from the counts, but it fails the cutover gate).
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import Column, DateTime, Engine, Float, Integer, MetaData, String, Table, Text, insert, select

from agents.schemas import ReconResult
from hitl.schemas import QuestionGroup
from migration.expected import ExpectedRecord, ExpectedRecordBuilder
from rules.fields import normalize_vin
from rules.pattern_key import make_key
from rules.timeutil import as_utc, naive_utc, utcnow
from target.schemas import DiagnosticItem

RECON_MD = MetaData()
reconciliation = Table(
    "reconciliation", RECON_MD,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scope", String(16), nullable=False),
    Column("vin", String(32)),
    Column("compared", Integer, nullable=False),
    Column("gaps", Integer, nullable=False),
    Column("aligned_pct", Float),
    Column("not_performed", Text, nullable=False),
    Column("missing_in_target", Integer, nullable=False),
    Column("unexpected_in_target", Integer, nullable=False),
    Column("value_mismatch", Integer, nullable=False),
    Column("duplicate_in_target", Integer, nullable=False),
    Column("at", DateTime, nullable=False),
)
COMPARED_FIELDS = ("creation_date", "ecu_name", "value_description", "value_reference", "diag_tool_name",
                   "diag_tool_version", "vehicle_model")


class ReadFailed(Exception):
    pass


@dataclass
class VinComparison:
    vin: str
    compared: int = 0
    missing: list[ExpectedRecord] = field(default_factory=list)
    mismatch: list[ExpectedRecord] = field(default_factory=list)
    unexpected: list[DiagnosticItem] = field(default_factory=list)
    duplicates: int = 0

    @property
    def gaps(self) -> int:
        return len(self.missing) + len(self.mismatch) + len(self.unexpected) + self.duplicates


def _identity(i: DiagnosticItem) -> tuple:
    return tuple(getattr(i, f) for f in COMPARED_FIELDS if f != "vehicle_model")


def _values(item: DiagnosticItem) -> dict[str, str]:
    return {k: (v if v is not None else "") for k, v in item.model_dump().items()}


def gap_key(detail: str) -> str:
    return make_key("_row", "RECON_GAP", detail)


def compare_records(vin: str, expected: list[ExpectedRecord], target: list[DiagnosticItem],
                    decisions: dict) -> VinComparison:
    """Match by diagnosticOpID, compare values; Human_Decisions on RECON_GAP keys settle a Gap."""
    def settled(detail: str) -> str | None:
        d = decisions.get(gap_key(detail))
        return None if d is None else ("accept" if d.kind == "value" else "exclude")

    res = VinComparison(vin)
    expected = [e for e in expected if settled(str(e.source_row_id)) != "exclude"]
    by_id: dict[str, list[DiagnosticItem]] = {}
    for t in target:
        by_id.setdefault(t.diagnostic_op_id or "", []).append(t)
    res.duplicates = sum(len(v) - 1 for v in by_id.values() if len(v) > 1)
    res.duplicates += sum(n - 1 for n in Counter(_identity(t) for t in target).values() if n > 1)
    expected_ids = set()
    for e in expected:
        res.compared += 1
        op = e.item.diagnostic_op_id or ""
        expected_ids.add(op)
        found = by_id.get(op)
        if settled(str(e.source_row_id)) == "accept":
            continue
        if not found:
            res.missing.append(e)
        elif any(getattr(found[0], f) != getattr(e.item, f) for f in COMPARED_FIELDS):
            res.mismatch.append(e)
    for op, items in by_id.items():
        if op not in expected_ids and settled(f"op={op}") is None:
            res.unexpected.extend(items[:1])
            res.compared += 1
    return res


class Reconciler:
    def __init__(self, engine: Engine, builder: ExpectedRecordBuilder, legacy_rows: Callable[[str], list],
                 target_items: Callable[[str], list[DiagnosticItem]], all_vins: Callable[[], list[str]],
                 sample_size: Callable[[], tuple[int, int]], decisions: Callable[[], dict],
                 reload_rows: Callable[[list[int]], object] | None = None, hitl=None) -> None:
        self.engine = engine
        self.builder = builder
        self._legacy_rows, self._target_items, self._all_vins = legacy_rows, target_items, all_vins
        self._sample_size, self._decisions = sample_size, decisions
        self._reload, self._hitl = reload_rows, hitl
        RECON_MD.create_all(engine)

    @classmethod
    def factory(cls, services) -> "Reconciler":
        """Build from the Orchestrator services (Legacy_A_API client, Target_API client, pipeline)."""
        import httpx
        pipeline = services.pipeline

        def legacy_rows(vin):
            try:
                return services.legacy_a.rows_by_vin(vin).rows
            except httpx.HTTPError as exc:
                raise ReadFailed(type(exc).__name__) from None

        def target_items(vin):
            try:
                r = services.target.get(f"/target/vehicles/{vin}/diagnostics")
            except httpx.HTTPError as exc:
                raise ReadFailed(type(exc).__name__) from None
            if r.status_code == 404:
                return []
            if r.status_code != 200:
                raise ReadFailed(f"HTTP {r.status_code}")
            return [DiagnosticItem(**o) for o in r.json()["operations"]]

        cache: dict[str, list[str]] = {}

        def all_vins():
            if "v" not in cache:
                vins, page = set(), 1
                while True:
                    p = services.legacy_a.rows_page(page, 1000)
                    vins.update(normalize_vin(r.values.get("VIN", "")) for r in p.rows if not r.malformed)
                    if page * 1000 >= p.total_rows:
                        break
                    page += 1
                cache["v"] = sorted(v for v in vins if v)
            return cache["v"]

        def sample_size():
            c = pipeline.config().pipeline.control_sample
            return c.size, c.seed

        return cls(pipeline.ops_engine, services.expected, legacy_rows, target_items, all_vins, sample_size,
                   pipeline.hitl.decisions.views, lambda ids: pipeline.reload_rows(ids), pipeline.hitl)

    # ---------------------------------------------------------------- comparisons
    def control_sample(self) -> list[str]:
        size, seed = self._sample_size()
        vins = sorted(self._all_vins())
        return random.Random(seed).sample(vins, min(size, len(vins)))

    def _compare_once(self, vin: str) -> VinComparison:
        expected = self.builder.for_rows(vin, self._legacy_rows(vin))
        return compare_records(vin, expected, self._target_items(vin), self._decisions())

    def _compare(self, vin: str) -> VinComparison:
        res = self._compare_once(vin)
        rows = sorted({e.source_row_id for e in res.missing + res.mismatch})
        if rows and self._reload is not None:
            try:
                self._reload(rows)                       # exactly one reload per Gap row
            except Exception:  # noqa: BLE001 - a failed reload leaves the Gap for a human
                pass
            res = self._compare_once(vin)
        self._ask(res)
        return res

    def _ask(self, res: VinComparison) -> None:
        if self._hitl is None or not res.gaps:
            return
        groups = {}
        for e in res.missing + res.mismatch:
            key = gap_key(str(e.source_row_id))
            what = "missing in the Target_DB" if e in res.missing else "different in the Target_DB"
            groups[key] = QuestionGroup(
                pattern_key=key, field="_row", reason="RECON_GAP", rule_id="LOAD", rows=(e.source_row_id,),
                examples=((e.source_row_id, _values(e.item)),), homogeneous=True,
                explanation=f"Expected record of row {e.source_row_id} (VIN {res.vin}) is {what} after one reload.",
                row_values=((e.source_row_id, _values(e.item)),))
        for item in res.unexpected:
            key = gap_key(f"op={item.diagnostic_op_id}")
            groups[key] = QuestionGroup(
                pattern_key=key, field="_row", reason="RECON_GAP", rule_id="LOAD", rows=(0,),
                examples=((0, _values(item)),), homogeneous=True,
                explanation=f"Target record {item.diagnostic_op_id} of VIN {res.vin} has no Legacy A counterpart.",
                row_values=((0, _values(item)),))
        self._hitl.create_questions(None, groups)

    def _publish(self, scope: str, vin: str | None, parts: list[VinComparison], not_performed: list[str]) -> ReconResult:
        compared = sum(p.compared for p in parts)
        gaps = sum(p.gaps for p in parts)
        aligned = max(compared - gaps, 0)
        res = ReconResult(
            scope=scope, compared=compared, gaps=gaps,
            aligned_pct=None if compared == 0 else round(100.0 * aligned / compared, 1),
            not_performed=not_performed, at=utcnow(),
            missing_in_target=sum(len(p.missing) for p in parts),
            unexpected_in_target=sum(len(p.unexpected) for p in parts),
            value_mismatch=sum(len(p.mismatch) for p in parts),
            duplicate_in_target=sum(p.duplicates for p in parts))
        with self.engine.begin() as c:
            c.execute(insert(reconciliation).values(
                scope=scope, vin=vin, compared=res.compared, gaps=res.gaps, aligned_pct=res.aligned_pct,
                not_performed=json.dumps(not_performed), missing_in_target=res.missing_in_target,
                unexpected_in_target=res.unexpected_in_target, value_mismatch=res.value_mismatch,
                duplicate_in_target=res.duplicate_in_target, at=naive_utc(res.at)))
        return res

    def compare_vin(self, vin: str) -> ReconResult:
        v = normalize_vin(vin)
        try:
            return self._publish("vin", v, [self._compare(v)], [])
        except ReadFailed:
            return self._publish("vin", v, [], [v])

    def compare_vin_safely(self, vin: str) -> None:
        try:
            self.compare_vin(vin)
        except Exception:  # noqa: BLE001 - background task: never break the client request
            pass

    def compare_control_sample(self) -> ReconResult:
        parts, not_performed = [], []
        for v in self.control_sample():
            try:
                parts.append(self._compare(v))
            except ReadFailed:
                not_performed.append(v)
        return self._publish("control_sample", None, parts, not_performed)

    def latest(self, scope: str) -> ReconResult | None:
        with self.engine.connect() as c:
            r = c.execute(select(reconciliation).where(reconciliation.c.scope == scope)
                          .order_by(reconciliation.c.id.desc()).limit(1)).mappings().first()
        if r is None:
            return None
        return ReconResult(scope=r["scope"], compared=r["compared"], gaps=r["gaps"], aligned_pct=r["aligned_pct"],
                           not_performed=json.loads(r["not_performed"]), at=as_utc(r["at"]),
                           missing_in_target=r["missing_in_target"], unexpected_in_target=r["unexpected_in_target"],
                           value_mismatch=r["value_mismatch"], duplicate_in_target=r["duplicate_in_target"])
