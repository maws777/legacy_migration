"""Target_Repository: the only writer of the Target_DB. Idempotent upserts, never deletes."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import Engine, MetaData, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import DBAPIError

from rules.timeutil import naive_utc
from rules.types import RowDecision, TargetValues
from target.schema import TABLE_ORDER
from target.schemas import DiagnosticItem


@dataclass(frozen=True)
class LoadRow:
    source_row_id: int
    values: TargetValues
    run_id: str
    ingestion_ts: datetime


@dataclass(frozen=True)
class LoadFailure:
    source_row_id: int
    constraint: str                     # constraint name, or FOREIGN KEY | UNIQUE | NOT NULL | CHECK


@dataclass(frozen=True)
class BatchResult:
    loaded: tuple[int, ...]
    failed: tuple[LoadFailure, ...]
    retried: bool


_CHECK = re.compile(r"CHECK constraint failed: (\w+)")
_TYPED = re.compile(r"(UNIQUE|NOT NULL) constraint failed: ([\w., ]+)")


def constraint_of(exc: BaseException) -> str:
    """Constraint name from a SQLite integrity error, else its type."""
    msg = str(getattr(exc, "orig", exc))
    m = _CHECK.search(msg)
    if m:
        return m.group(1)
    m = _TYPED.search(msg)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}"
    if "FOREIGN KEY" in msg:
        return "FOREIGN KEY"
    if "CHECK" in msg:
        return "CHECK"
    return type(getattr(exc, "orig", exc)).__name__


class TargetRepository:
    def __init__(self, engine: Engine, metadata: MetaData | None = None) -> None:
        self.engine = engine
        self._md = metadata

    @property
    def tables(self):
        if self._md is None:
            md = MetaData()
            md.reflect(self.engine, only=list(TABLE_ORDER))
            self._md = md
        return self._md.tables

    # ---------------------------------------------------------------- writes
    def _statements(self, rows: Sequence[LoadRow]):
        t = self.tables
        vehicles, ecus, links, tools, ops, results = {}, {}, {}, {}, {}, {}
        for r in rows:
            v = r.values
            vehicles[v.vin] = {"vin": v.vin, "vehicle_model": v.vehicle_model}
            ecus[v.ecu_name] = {"ecu_name": v.ecu_name}
            links[(v.vin, v.ecu_name)] = {"vin": v.vin, "ecu_name": v.ecu_name}
            tools[(v.diag_tool_name, v.diag_tool_version)] = {
                "diag_tool_name": v.diag_tool_name, "diag_tool_version": v.diag_tool_version}
            ops[v.diagnostic_op_id] = {
                "diagnostic_op_id": v.diagnostic_op_id, "creation_date": v.creation_date, "vin": v.vin,
                "ecu_name": v.ecu_name, "diag_tool_name": v.diag_tool_name,
                "diag_tool_version": v.diag_tool_version, "source_origin_tool": v.source_origin_tool,
                "ingestion_ts": naive_utc(r.ingestion_ts), "run_id": r.run_id, "source_row_id": r.source_row_id,
                "op_id_generated": v.op_id_generated}
            results[v.diagnostic_op_id] = {"diagnostic_op_id": v.diagnostic_op_id,
                                           "value_reference": v.value_reference,
                                           "value_description": v.value_description}

        def upsert(table, keys, params, update: bool):
            if not params:
                return None
            stmt = sqlite_insert(table)
            if update:
                cols = [c.name for c in table.columns if c.name not in keys]
                stmt = stmt.on_conflict_do_update(index_elements=keys,
                                                  set_={c: stmt.excluded[c] for c in cols})
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=keys)
            return stmt, list(params.values())

        return [s for s in (
            upsert(t["vehicle"], ["vin"], vehicles, True),
            upsert(t["diagnozed_ecu"], ["ecu_name"], ecus, False),
            upsert(t["vehicle_ecu"], ["vin", "ecu_name"], links, False),
            upsert(t["diag_tools_versions"], ["diag_tool_name", "diag_tool_version"], tools, False),
            upsert(t["diagnostic_operation"], ["diagnostic_op_id"], ops, True),
            upsert(t["diag_value_result"], ["diagnostic_op_id"], results, True),
        ) if s is not None]

    def _write(self, rows: Sequence[LoadRow]) -> None:
        with self.engine.begin() as conn:
            for stmt, params in self._statements(rows):
                conn.execute(stmt, params)

    def load_batch(self, rows: Sequence[LoadRow]) -> BatchResult:
        """One transaction per batch; on error, roll back and retry each row in its own transaction."""
        if not rows:
            return BatchResult((), (), False)
        try:
            self._write(rows)
            return BatchResult(tuple(r.source_row_id for r in rows), (), False)
        except (DBAPIError, sqlite3.Error):
            pass
        loaded, failed = [], []
        for r in rows:
            try:
                self._write([r])
                loaded.append(r.source_row_id)
            except (DBAPIError, sqlite3.Error) as exc:
                failed.append(LoadFailure(r.source_row_id, constraint_of(exc)))
        return BatchResult(tuple(loaded), tuple(failed), True)

    def load_rows(self, run_id: str, ingestion_ts: datetime, decisions: Iterable[RowDecision]) -> BatchResult:
        rows = [LoadRow(d.source_row_id, d.target, run_id, ingestion_ts) for d in decisions if d.target]
        return self.load_batch(rows)

    # ---------------------------------------------------------------- reads
    def diagnostics_by_vin(self, vin: str) -> list[DiagnosticItem]:
        t = self.tables
        op, res, veh = t["diagnostic_operation"], t["diag_value_result"], t["vehicle"]
        q = (select(op, res.c.value_reference, res.c.value_description, veh.c.vehicle_model)
             .join(res, res.c.diagnostic_op_id == op.c.diagnostic_op_id)
             .join(veh, veh.c.vin == op.c.vin)
             .where(op.c.vin == vin).order_by(op.c.source_row_id, op.c.diagnostic_op_id))
        with self.engine.connect() as c:
            rows = c.execute(q).mappings().all()
        return [DiagnosticItem(
            diagnostic_op_id=r["diagnostic_op_id"], creation_date=r["creation_date"].strftime("%Y-%m-%dT%H:%M"),
            ecu_name=r["ecu_name"], value_description=r["value_description"], value_reference=r["value_reference"],
            diag_tool_name=r["diag_tool_name"], diag_tool_version=r["diag_tool_version"],
            vehicle_model=r["vehicle_model"]) for r in rows]

    def table_counts(self) -> dict[str, int]:
        with self.engine.connect() as c:
            return {name: c.execute(select(func.count()).select_from(self.tables[name])).scalar_one()
                    for name in TABLE_ORDER}
