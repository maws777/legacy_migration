"""Resolution_Cache: persistent Resolver_Agent proposals applied at or above the threshold."""
from __future__ import annotations

from typing import Iterable

from sqlalchemy import Column, DateTime, Engine, Float, MetaData, String, Table, Text, delete, func, insert, select

from rules.pattern_key import parse_key
from rules.timeutil import naive_utc, utcnow
from rules.types import CachedResolution

CACHE_MD = MetaData()
resolution_cache = Table(
    "resolution_cache", CACHE_MD,
    Column("pattern_key", Text, primary_key=True),
    Column("field", String(32), nullable=False),
    Column("normalized_value", Text, nullable=False),
    Column("decision", String(8), nullable=False),
    Column("value", Text),
    Column("confidence", Float, nullable=False),
    Column("justification", Text, nullable=False),
    Column("model", String(64), nullable=False),
    Column("created_run_id", String(36), nullable=False),
    Column("created_at", DateTime, nullable=False),
)


class ResolutionCache:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        CACHE_MD.create_all(engine)

    def get_many(self, keys: Iterable[str]) -> dict[str, CachedResolution]:
        keys = list(keys)
        if not keys:
            return {}
        with self.engine.connect() as c:
            rows = c.execute(select(resolution_cache).where(resolution_cache.c.pattern_key.in_(keys))).mappings().all()
        return {r["pattern_key"]: CachedResolution(r["pattern_key"], r["decision"], r["value"], r["confidence"],
                                                   r["justification"]) for r in rows}

    def put(self, res: CachedResolution, run_id: str, model: str, *, field: str | None = None,
            normalized_value: str | None = None) -> None:
        parts = parse_key(res.pattern_key)
        with self.engine.begin() as c:
            c.execute(delete(resolution_cache).where(resolution_cache.c.pattern_key == res.pattern_key))
            c.execute(insert(resolution_cache).values(
                pattern_key=res.pattern_key, field=field or parts.field,
                normalized_value=normalized_value if normalized_value is not None else (parts.detail or ""),
                decision=res.decision, value=res.value, confidence=res.confidence,
                justification=res.justification, model=model, created_run_id=run_id,
                created_at=naive_utc(utcnow())))

    def count(self) -> int:
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(resolution_cache)).scalar_one()

    def all(self) -> dict[str, CachedResolution]:
        with self.engine.connect() as c:
            rows = c.execute(select(resolution_cache)).mappings().all()
        return {r["pattern_key"]: CachedResolution(r["pattern_key"], r["decision"], r["value"], r["confidence"],
                                                   r["justification"]) for r in rows}
