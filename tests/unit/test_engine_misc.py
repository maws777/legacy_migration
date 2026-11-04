"""Determinism, precedence, UUID v5, dates, memo and template completeness."""
from datetime import datetime

import pytest

from rules import fields as F
from rules.engine import decide_row, row_outcome, scan_row
from rules.memo import build_field_memo, distinct_values
from rules.opid import canonical_identity, generate_op_id
from rules.templates import TEMPLATES, all_pairs, template_for
from rules.types import CANONICAL_COLUMNS, CachedResolution, FieldDecision, OperationIdentity, RawRow, RuleContext
from tests.fixtures.make_csv import anomaly_rows
from tests.fixtures.rules_helpers import ctx, decide, human, one, raw


# ---------------------------------------------------------------- determinism (5.2, 5.3)
def test_same_row_and_context_give_equal_decisions(cfg):
    c = ctx(cfg, shared_op_ids=frozenset({"SHARED01"}))
    rows = [RawRow(i, v) for i, v in enumerate(anomaly_rows(), start=1)]
    first = [decide_row(r, c) for r in rows]
    snapshot = [dict(r.values) for r in rows]
    again = [decide_row(r, c) for r in reversed(rows)][::-1]
    assert first == again
    assert [dict(r.values) for r in rows] == snapshot


# ---------------------------------------------------------------- precedence (5.4-5.6, 21.8, A8)
def test_extended_key_beats_group_key(cfg):
    hd = {**human("ecuName|UNREADABLE", "value", "BCM"), **human("ecuName|UNREADABLE#row=1", "value", "ABS")}
    d = one(decide(cfg, raw(1, ecuName="", DescriptionDiagECU="x"), human_decisions=hd), "ecuName")
    assert (d.after, d.decided_by, d.pattern_key) == ("ABS", "human", "ecuName|UNREADABLE#row=1")
    d2 = one(decide(cfg, raw(2, ecuName="", DescriptionDiagECU="x"), human_decisions=hd), "ecuName")
    assert d2.after == "BCM"


def test_human_beats_cache_and_cache_applies(cfg):
    key = "ecuName|UNKNOWN_VARIANT|AIRBAGUNIT"
    cache = {key: CachedResolution(key, "map", "AIRBAG", 0.95, "AIR-BAG UNIT is the airbag ECU")}
    row = raw(ecuName="AIR-BAG UNIT", DescriptionDiagECU="x")
    d = one(decide(cfg, row, cache=cache), "ecuName")
    assert (d.action, d.reason, d.decided_by, d.confidence, d.tokens_used) == ("CORRECT", "CACHE_VALUE", "llm", 0.95, 0)
    d = one(decide(cfg, row, cache=cache, llm_tokens={key: 120}), "ecuName")
    assert (d.reason, d.tokens_used) == ("LLM_VALUE", 120)
    d = one(decide(cfg, row, cache=cache, human_decisions=human(key, "reject")), "ecuName")
    assert (d.action, d.decided_by) == ("REJECT", "human")


def test_unknown_variant_explanations(cfg):
    from rules.types import LLMSuggestion
    key = "ecuName|UNKNOWN_VARIANT|AIRBAGUNIT"
    row = raw(ecuName="AIR-BAG UNIT", DescriptionDiagECU="x")
    s = LLMSuggestion("map", "AIRBAG", 0.6, "looks like airbag")
    d = one(decide(cfg, row, llm_suggestions={key: s}, llm_status={key: "below_threshold"}), "ecuName")
    assert d.action == "ASK_HUMAN" and "0.60" in d.explanation and d.suggestion == s
    d = one(decide(cfg, row, degraded=True), "ecuName")
    assert "Degraded_Mode" in d.explanation


def test_scan_reports_unknown_variants_and_identity(cfg):
    sc = scan_row(raw(ecuName="AIR-BAG UNIT", DescriptionDiagECU="x", diagnosticFieId=" X1 "), ctx(cfg))
    assert [u.pattern_key for u in sc.unknown_variants] == ["ecuName|UNKNOWN_VARIANT|AIRBAGUNIT"]
    assert sc.trimmed_op_id == "X1" and sc.matched_model == "CLIO" and sc.identity.ecu_name is None


# ---------------------------------------------------------------- row outcome
def test_row_outcome_priority():
    def fd(a):
        return FieldDecision("f", a, "R1", "OK", "")
    assert row_outcome([fd("TRANSFORM"), fd("MIGRATE")]) == "MIGRATE"
    assert row_outcome([fd("TRANSFORM"), fd("CORRECT")]) == "CORRECT"
    assert row_outcome([fd("CORRECT"), fd("ASK_HUMAN"), fd("REJECT")]) == "REJECT"
    with pytest.raises(ValueError):
        row_outcome([])


# ---------------------------------------------------------------- UUID v5 (13.5)
def test_generated_id_stable_and_distinct():
    i1 = OperationIdentity("V" * 20, "BCM", "ID0002", "2024-03-15T10:30", "AB12345678", "DDT2000", "v1")
    i2 = OperationIdentity("V" * 20, "BCM", "ID0002", "2024-03-15T10:30", "AB12345679", "DDT2000", "v1")
    assert generate_op_id(i1) == generate_op_id(OperationIdentity(*vars(i1).values()))
    assert generate_op_id(i1) != generate_op_id(i2)
    # JSON serialization prevents concatenation collisions
    a = OperationIdentity("AB", "C", None, None, None)
    b = OperationIdentity("A", "BC", None, None, None)
    assert canonical_identity(a) != canonical_identity(b)


# ---------------------------------------------------------------- dates (14.1-14.4)
@pytest.mark.parametrize("text,ok", [
    ("29/02/2024 10:00", True), ("29/02/2023 10:00", False), ("31/12/2023 23:59", True),
    ("15/03/2024 24:00", False), ("15/03/2024 10:60", False), ("31/04/2024 10:00", False),
    ("15/13/2024 10:00", False), ("1/03/2024 10:00", False), ("15/03/2024  10:00", False),
    (" 15/03/2024 10:00 ", True),
])
def test_parse_creation_date(text, ok):
    assert (F.parse_creation_date(text) is not None) is ok


@pytest.mark.parametrize("dt", [datetime(2024, 2, 29, 0, 0), datetime(2023, 12, 31, 23, 59), datetime(2025, 1, 1, 7, 5)])
def test_date_round_trip(dt):
    assert F.parse_creation_date(F.format_creation_date(dt)) == dt


# ---------------------------------------------------------------- memo (34.2)
def test_memo_one_normalization_per_distinct_value(cfg, monkeypatch):
    calls = {"vin": 0, "r4": 0}
    real_vin, real_r4 = F.normalize_vin, F.r4_classify

    def count_vin(s):
        calls["vin"] += 1
        return real_vin(s)

    def count_r4(s):
        calls["r4"] += 1
        return real_r4(s)

    monkeypatch.setattr(F, "normalize_vin", count_vin)
    monkeypatch.setattr(F, "r4_classify", count_r4)
    rows = [RawRow(i, v) for i, v in enumerate(anomaly_rows() * 3, start=1)]
    distinct = distinct_values(rows, CANONICAL_COLUMNS)
    memo = build_field_memo(distinct, cfg)
    assert calls["vin"] == len(distinct["VIN"]) and calls["r4"] == len(distinct["ValueReference"])
    c = RuleContext(config=cfg, memo=memo)
    with_memo = [decide_row(r, c) for r in rows]
    assert calls["vin"] == len(distinct["VIN"]) and calls["r4"] == len(distinct["ValueReference"])
    assert with_memo == [decide_row(r, RuleContext(config=cfg)) for r in rows]


# ---------------------------------------------------------------- templates
def test_every_rule_reason_pair_has_a_template():
    for rule_id, reason in all_pairs():
        assert template_for(rule_id, reason)
    assert len(TEMPLATES) == len(all_pairs())
