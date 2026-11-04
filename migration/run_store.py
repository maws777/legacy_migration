"""Run bookkeeping: process-local RunStore (rows, memo, decisions) and the ``run``/``campaign`` tables."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column, DateTime, Engine, Integer, MetaData, String, Table, Text, insert, select, update,
)

from rules.timeutil import as_utc, naive_utc, utcnow
from rules.types import RawRow

OPS_MD = MetaData()
run_table = Table(
    "run", OPS_MD,
    Column("run_id", String(36), primary_key=True),
    Column("campaign_id", String(64), nullable=False, index=True),
    Column("kind", String(8), nullable=False),
    Column("status", String(16), nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("ended_at", DateTime),
    Column("ingestion_ts", DateTime, nullable=False),
    Column("source_sha256", String(64)),
    Column("config_hash", String(64)),
    Column("phase_at_start", Integer),
    Column("operator", String(128)),
    Column("rows_read", Integer),
    Column("open_questions", Integer, nullable=False, default=0),
    Column("error", Text),
)
campaign_table = Table(
    "campaign", OPS_MD,
    Column("campaign_id", String(64), primary_key=True),
    Column("status", String(16), nullable=False),          # running | paused | done
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    Column("operator", String(128)),
)


class RunRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        OPS_MD.create_all(engine)

    # --- campaigns ---
    def create_campaign(self, campaign_id: str, operator: str | None) -> None:
        now = naive_utc(utcnow())
        with self.engine.begin() as c:
            c.execute(insert(campaign_table).values(campaign_id=campaign_id, status="running",
                                                    created_at=now, updated_at=now, operator=operator))

    def set_campaign_status(self, campaign_id: str, status: str) -> None:
        with self.engine.begin() as c:
            c.execute(update(campaign_table).where(campaign_table.c.campaign_id == campaign_id)
                      .values(status=status, updated_at=naive_utc(utcnow())))

    def campaign_status(self, campaign_id: str) -> str | None:
        with self.engine.connect() as c:
            return c.execute(select(campaign_table.c.status)
                             .where(campaign_table.c.campaign_id == campaign_id)).scalar()

    # --- runs ---
    def create_run(self, run_id: str, campaign_id: str, kind: str, ingestion_ts: datetime,
                   operator: str | None, phase_at_start: int | None = None) -> None:
        with self.engine.begin() as c:
            c.execute(insert(run_table).values(
                run_id=run_id, campaign_id=campaign_id, kind=kind, status="running",
                started_at=naive_utc(utcnow()), ingestion_ts=naive_utc(ingestion_ts),
                operator=operator, phase_at_start=phase_at_start, open_questions=0))

    def update_run(self, run_id: str, **values: Any) -> None:
        if "ended_at" in values and values["ended_at"] is not None:
            values["ended_at"] = naive_utc(values["ended_at"])
        with self.engine.begin() as c:
            c.execute(update(run_table).where(run_table.c.run_id == run_id).values(**values))

    def finish_run(self, run_id: str, status: str, **values: Any) -> None:
        self.update_run(run_id, status=status, ended_at=utcnow(), **values)

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.connect() as c:
            row = c.execute(select(run_table).where(run_table.c.run_id == run_id)).mappings().first()
        return self._norm(row) if row else None

    def list_runs(self, limit: int = 50, offset: int = 0, campaign_id: str | None = None) -> list[dict]:
        q = select(run_table).order_by(run_table.c.started_at.desc(), run_table.c.run_id)
        if campaign_id:
            q = q.where(run_table.c.campaign_id == campaign_id)
        with self.engine.connect() as c:
            rows = c.execute(q.limit(limit).offset(offset)).mappings().all()
        return [self._norm(r) for r in rows]

    def latest_run(self, campaign_id: str) -> dict | None:
        runs = self.list_runs(limit=1, campaign_id=campaign_id)
        return runs[0] if runs else None

    def latest_full_run(self, status: str = "completed", before: str | None = None) -> dict | None:
        q = (select(run_table).where(run_table.c.kind == "full", run_table.c.status == status)
             .order_by(run_table.c.started_at.desc()))
        with self.engine.connect() as c:
            rows = c.execute(q).mappings().all()
        for r in rows:
            if r["run_id"] != before:
                return self._norm(r)
        return None

    def full_runs_started_before(self, run_id: str) -> list[str]:
        me = self.get_run(run_id)
        if me is None:
            return []
        q = select(run_table.c.run_id).where(run_table.c.kind == "full",
                                             run_table.c.started_at <= naive_utc(me["started_at"]),
                                             run_table.c.run_id != run_id)
        with self.engine.connect() as c:
            return list(c.execute(q).scalars())

    @staticmethod
    def _norm(row) -> dict:
        d = dict(row)
        for k in ("started_at", "ended_at", "ingestion_ts"):
            d[k] = as_utc(d[k])
        return d


@dataclass
class RunData:
    """Process-local data of one Run. The LangGraph checkpoint holds only small ids and counters."""
    run_id: str
    campaign_id: str
    kind: str
    ingestion_ts: datetime
    operator: str | None = None
    cfg: Any = None
    rows: tuple[RawRow, ...] = ()
    header: tuple[str, ...] = ()
    sha256: str = ""
    batch_bounds: list[tuple[int, int]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    timer: Any = None                                   # reporting.metrics.StageTimer
    memo: Any = None                                    # rules.memo.FieldMemo
    human: dict = field(default_factory=dict)           # Pattern_Key -> HumanDecisionView
    legacy_b_index: dict = field(default_factory=dict)
    legacy_b_status: str = "disabled"
    degraded: bool = False
    scans: dict = field(default_factory=dict)           # source_row_id -> RowScan
    unknown: dict = field(default_factory=dict)         # Pattern_Key -> UnknownVariant
    cache_lookups: int = 0
    cache_hits: int = 0
    resolved: dict = field(default_factory=dict)        # Pattern_Key -> CachedResolution used this Run
    llm_suggestions: dict = field(default_factory=dict)
    llm_status: dict = field(default_factory=dict)
    llm_tokens: dict = field(default_factory=dict)
    partials: dict = field(default_factory=dict)        # batch -> (vin models, op id usage)
    ctx: Any = None                                     # frozen rules.types.RuleContext
    decisions: dict = field(default_factory=dict)       # source_row_id -> RowDecision
    groups: dict = field(default_factory=dict)          # Pattern_Key -> hitl.schemas.QuestionGroup
    reload_ids: frozenset = frozenset()

    def batch(self, i: int) -> tuple[RawRow, ...]:
        lo, hi = self.batch_bounds[i]
        return self.rows[lo:hi]


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunData] = {}
        self._lock = threading.Lock()

    def put(self, data: RunData) -> None:
        with self._lock:
            self._runs[data.run_id] = data

    def get(self, run_id: str) -> RunData:
        with self._lock:
            return self._runs[run_id]

    def has(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs

    def drop(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
