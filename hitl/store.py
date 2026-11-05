"""Human_Decision_Store: persisted human answers, independent of the Run_Workflow checkpoints."""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Engine, MetaData, String, Table, Text, delete, insert, select

from hitl.schemas import HumanDecision
from rules.timeutil import as_utc, naive_utc
from rules.types import HumanDecisionView

STORE_MD = MetaData()
human_decision = Table(
    "human_decision", STORE_MD,
    Column("pattern_key", Text, primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("value", Text),
    Column("answered_at", DateTime, nullable=False),
    Column("operator", String(128), nullable=False),
    Column("question_id", String(36), nullable=False),
)


class HumanDecisionStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        STORE_MD.create_all(engine)

    def put(self, d: HumanDecision, conn=None) -> None:
        """At most one Human_Decision per Pattern_Key (a new one replaces the old one)."""
        row = d.model_dump()
        row["answered_at"] = naive_utc(d.answered_at)

        def _do(c):
            c.execute(delete(human_decision).where(human_decision.c.pattern_key == d.pattern_key))
            c.execute(insert(human_decision).values(**row))

        if conn is not None:
            _do(conn)
        else:
            with self.engine.begin() as c:
                _do(c)

    def delete(self, pattern_key: str) -> bool:
        with self.engine.begin() as c:
            res = c.execute(delete(human_decision).where(human_decision.c.pattern_key == pattern_key))
        return bool(res.rowcount)

    def get(self, pattern_key: str) -> HumanDecision | None:
        with self.engine.connect() as c:
            r = c.execute(select(human_decision).where(human_decision.c.pattern_key == pattern_key)).mappings().first()
        return self._model(r) if r else None

    def all(self) -> dict[str, HumanDecision]:
        with self.engine.connect() as c:
            rows = c.execute(select(human_decision)).mappings().all()
        return {r["pattern_key"]: self._model(r) for r in rows}

    def views(self) -> dict[str, HumanDecisionView]:
        return {k: HumanDecisionView(k, d.kind, d.value) for k, d in self.all().items()}

    @staticmethod
    def _model(r) -> HumanDecision:
        d = dict(r)
        d["answered_at"] = as_utc(d["answered_at"])
        return HumanDecision(**d)
