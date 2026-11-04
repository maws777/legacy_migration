"""Stage timing and the ``run_metrics`` table."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Boolean, Column, Engine, Float, Integer, MetaData, String, Table, Text, delete, insert, select

from reporting.schemas import RunMetrics

STAGES = ("ingestion", "rules", "ai_resolution", "human_routing", "load", "reporting")

METRICS_MD = MetaData()
run_metrics = Table(
    "run_metrics", METRICS_MD,
    Column("run_id", String(36), primary_key=True),
    Column("total_seconds", Float, nullable=False),
    Column("stage_seconds", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("llm_calls", Integer, nullable=False),
    Column("distinct_pattern_keys", Integer, nullable=False),
    Column("cache_hit_rate", Float, nullable=False),
    Column("batch_size", Integer, nullable=False),
    Column("out_of_scope_rejects", Integer, nullable=False),
    Column("degraded_mode", Boolean(create_constraint=False), nullable=False),
    Column("legacy_b_lookup", String(8), nullable=False),
    Column("counts", Text, nullable=False),
)


class StageTimer:
    """Accumulates wall-clock seconds per stage name."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {s: 0.0 for s in STAGES}
        self.started = time.perf_counter()

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - t0

    def total(self) -> float:
        return time.perf_counter() - self.started


class MetricsStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        METRICS_MD.create_all(engine)

    def save(self, m: RunMetrics) -> None:
        row = m.model_dump()
        row["stage_seconds"] = json.dumps(m.stage_seconds)
        row["counts"] = json.dumps(m.counts)
        with self.engine.begin() as c:
            c.execute(delete(run_metrics).where(run_metrics.c.run_id == m.run_id))
            c.execute(insert(run_metrics).values(**row))

    def get(self, run_id: str) -> RunMetrics | None:
        with self.engine.connect() as c:
            r = c.execute(select(run_metrics).where(run_metrics.c.run_id == run_id)).mappings().first()
        if r is None:
            return None
        d = dict(r)
        d["stage_seconds"] = json.loads(d["stage_seconds"])
        d["counts"] = json.loads(d["counts"])
        return RunMetrics(**d)
