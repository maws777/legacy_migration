"""Helpers to build rows and contexts for Rule_Engine unit tests."""
from __future__ import annotations

from rules.engine import decide_row
from rules.types import FieldDecision, HumanDecisionView, RawRow, RuleContext
from tests.fixtures.make_csv import base_row, format2_row


def raw(n: int = 1, *, malformed: bool = False, f2: dict | None = None, **overrides: str) -> RawRow:
    values = format2_row(n, **f2) if f2 is not None else base_row(n)
    values.update(overrides)
    return RawRow(n, values, malformed)


def ctx(cfg, **kw) -> RuleContext:
    return RuleContext(config=cfg, **kw)


def human(key: str, kind: str = "value", value: str | None = None) -> dict[str, HumanDecisionView]:
    return {key: HumanDecisionView(key, kind, value)}  # type: ignore[arg-type]


def field(decision, name: str) -> list[FieldDecision]:
    return [d for d in decision.fields if d.field == name]


def one(decision, name: str) -> FieldDecision:
    found = field(decision, name)
    assert len(found) == 1, found
    return found[0]


def decide(cfg, row: RawRow | None = None, **ctx_kw):
    return decide_row(row or raw(), ctx(cfg, **ctx_kw))
