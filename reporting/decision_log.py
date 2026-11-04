"""Decision_Logger: the only writer of the ``decision_log`` table (ops.db)."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping, Sequence

from sqlalchemy import (
    CheckConstraint, Column, DateTime, Engine, Float, Index, Integer, MetaData, String, Table, Text, func,
    insert, select, text,
)

from reporting.schemas import DECISION_FIELDS, DecisionPage, DecisionRecord, LogFilters
from rules.timeutil import iso_z, naive_utc
from rules.types import ACTIONS, DECIDED_BY, ROW_FIELD, FieldDecision

LOG_MD = MetaData()
decision_log = Table(
    "decision_log", LOG_MD,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False),
    Column("source_row", Integer, nullable=False),
    Column("field", String(32), nullable=False),
    Column("action", String(16), nullable=False),
    Column("rule_id", String(8), nullable=False),
    Column("reason", String(32), nullable=False),
    Column("before", Text, nullable=False),
    Column("after", Text, nullable=False),
    Column("decided_by", String(8), nullable=False),
    Column("confidence", Float),
    Column("explanation", Text, nullable=False),
    Column("tokens_used", Integer, nullable=False),
    Column("timestamp", DateTime, nullable=False),
    # Extra columns (not part of the exported Decision_Record contract).
    Column("pattern_key", Text),
    Column("load_status", String(16)),          # "_row" only: loaded | load_rejected | not_loaded
    CheckConstraint(f"action IN ({', '.join(repr(a) for a in ACTIONS)})", name="ck_decision_action"),
    CheckConstraint(f"decided_by IN ({', '.join(repr(a) for a in DECIDED_BY)})", name="ck_decision_decided_by"),
)
Index("ix_decision_run_row", decision_log.c.run_id, decision_log.c.source_row)
Index("ix_decision_run_action", decision_log.c.run_id, decision_log.c.action)
Index("ix_decision_run_rule", decision_log.c.run_id, decision_log.c.rule_id)
Index("uq_decision_row_record", decision_log.c.run_id, decision_log.c.source_row, unique=True,
      sqlite_where=text("field = '_row'"), postgresql_where=text("field = '_row'"))


class DecisionLogError(Exception):
    def __init__(self, run_id: str, source_row: int, cause: str = "") -> None:
        super().__init__(f"decision log write failed for run {run_id}, source row {source_row}: {cause}")
        self.run_id = run_id
        self.source_row = source_row


class UnknownRunError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"unknown run_id: {run_id} (no Decision_Record)")
        self.run_id = run_id


def to_record(run_id: str, source_row: int, d: FieldDecision, ts: datetime) -> DecisionRecord:
    return DecisionRecord(
        run_id=run_id, source_row=source_row, field=d.field, action=d.action, rule_id=d.rule_id,
        reason=d.reason, before=d.before, after=d.after, decided_by=d.decided_by,
        confidence=d.confidence if d.decided_by == "llm" else None, explanation=d.explanation,
        tokens_used=d.tokens_used if d.decided_by == "llm" else 0, timestamp=ts)


class DecisionLogger:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        LOG_MD.create_all(engine)

    def _rows(self, records: Sequence[DecisionRecord], meta: Sequence[Mapping] | None) -> list[dict]:
        out = []
        for i, r in enumerate(records):
            row = r.model_dump()
            row["timestamp"] = naive_utc(r.timestamp)
            extra = meta[i] if meta else {}
            row["pattern_key"] = extra.get("pattern_key")
            row["load_status"] = extra.get("load_status")
            out.append(row)
        return out

    def _insert(self, conn, rows: list[dict]) -> None:
        conn.execute(insert(decision_log), rows)

    def write(self, records: Sequence[DecisionRecord], meta: Sequence[Mapping] | None = None) -> None:
        """Write all records in one transaction. On failure, nothing is written and the failing row is named."""
        if not records:
            return
        rows = self._rows(records, meta)
        try:
            with self.engine.begin() as conn:
                self._insert(conn, rows)
            return
        except Exception as exc:  # locate the failing record, then raise
            first_error = exc
        try:
            with self.engine.begin() as conn:
                for row in rows:
                    try:
                        self._insert(conn, [row])
                    except Exception as exc:
                        raise DecisionLogError(row["run_id"], row["source_row"], type(exc).__name__) from exc
                raise DecisionLogError(rows[0]["run_id"], rows[0]["source_row"], type(first_error).__name__)
        except DecisionLogError:
            raise
        except Exception as exc:  # pragma: no cover - database unavailable
            raise DecisionLogError(rows[0]["run_id"], rows[0]["source_row"], type(exc).__name__) from exc

    # ---------------------------------------------------------------- reads
    def _filtered(self, run_id: str, filters: LogFilters | None):
        q = select(decision_log).where(decision_log.c.run_id == run_id)
        f = filters or LogFilters()
        for name in ("source_row", "field", "action", "rule_id", "decided_by"):
            value = getattr(f, name)
            if value is not None:
                q = q.where(decision_log.c[name] == value)
        return q

    def query(self, run_id: str, filters: LogFilters | None = None, limit: int = 100, offset: int = 0) -> DecisionPage:
        q = self._filtered(run_id, filters)
        with self.engine.connect() as c:
            total = c.execute(select(func.count()).select_from(q.subquery())).scalar_one()
            rows = c.execute(q.order_by(decision_log.c.source_row, decision_log.c.id)
                             .limit(limit).offset(offset)).mappings().all()
        return DecisionPage(total=total, items=[self._record(r) for r in rows])

    def records(self, run_id: str) -> list[DecisionRecord]:
        with self.engine.connect() as c:
            rows = c.execute(select(decision_log).where(decision_log.c.run_id == run_id)
                             .order_by(decision_log.c.id)).mappings().all()
        return [self._record(r) for r in rows]

    def has_run(self, run_id: str) -> bool:
        with self.engine.connect() as c:
            return c.execute(select(decision_log.c.id).where(decision_log.c.run_id == run_id).limit(1)).first() is not None

    def row_counts(self, run_id: str) -> dict[str, int]:
        """Row_Outcome counts from the ``_row`` records."""
        with self.engine.connect() as c:
            rows = c.execute(select(decision_log.c.action, func.count()).where(
                decision_log.c.run_id == run_id, decision_log.c.field == ROW_FIELD)
                .group_by(decision_log.c.action)).all()
        return {a: n for a, n in rows}

    def count_rows_with(self, run_id: str, **where: str) -> int:
        conds = [decision_log.c.run_id == run_id] + [decision_log.c[k] == v for k, v in where.items()]
        with self.engine.connect() as c:
            return c.execute(select(func.count(func.distinct(decision_log.c.source_row))).where(*conds)).scalar_one()

    def rejected_out_of_scope(self, run_id: str) -> int:
        """REJECT rows that carry an R3 OUT_OF_SCOPE decision."""
        rej = select(decision_log.c.source_row).where(
            decision_log.c.run_id == run_id, decision_log.c.field == ROW_FIELD, decision_log.c.action == "REJECT")
        with self.engine.connect() as c:
            return c.execute(select(func.count(func.distinct(decision_log.c.source_row))).where(
                decision_log.c.run_id == run_id, decision_log.c.reason == "OUT_OF_SCOPE",
                decision_log.c.source_row.in_(rej))).scalar_one()

    def loaded_rows(self, run_id: str) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).where(
                decision_log.c.run_id == run_id, decision_log.c.field == ROW_FIELD,
                decision_log.c.load_status == "loaded")).scalar_one()

    @staticmethod
    def _record(r) -> DecisionRecord:
        return DecisionRecord(**{k: r[k] for k in DECISION_FIELDS})

    # ---------------------------------------------------------------- export / import
    def export(self, run_id: str, fmt: Literal["json", "csv"], out_dir: Path | str) -> Path:
        records = self.records(run_id)
        if not records:
            raise UnknownRunError(run_id)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"decision_log_{run_id}.{fmt}"
        if fmt == "json":
            data = [r.model_dump(mode="json") for r in records]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        elif fmt == "csv":
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
                w.writerow(DECISION_FIELDS)
                for r in records:
                    d = r.model_dump(mode="json")
                    w.writerow(["" if d[k] is None else d[k] for k in DECISION_FIELDS])
        else:
            raise ValueError(f"unknown export format: {fmt}")
        return path

    @staticmethod
    def import_file(path: Path | str) -> list[DecisionRecord]:
        path = Path(path)
        if path.suffix == ".json":
            return [DecisionRecord(**d) for d in json.loads(path.read_text(encoding="utf-8"))]
        with path.open(encoding="utf-8", newline="") as fh:
            out = []
            for d in csv.DictReader(fh):
                d["confidence"] = None if d["confidence"] == "" else float(d["confidence"])
                d["source_row"] = int(d["source_row"])
                d["tokens_used"] = int(d["tokens_used"])
                out.append(DecisionRecord(**d))
            return out


def export_media_type(fmt: str) -> str:
    return "application/json" if fmt == "json" else "text/csv; charset=utf-8"


__all__ = ["DecisionLogError", "DecisionLogger", "UnknownRunError", "decision_log", "export_media_type",
           "iso_z", "to_record"]
