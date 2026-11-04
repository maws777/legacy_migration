"""Official examples of migration-rules.md for R1-R8 and FC1-FC4 (Requirement 5.7)."""
import pytest

from rules.engine import final_checks
from rules.fields import dict_lookup, normalize_vin, r1_vin
from tests.fixtures.make_csv import vin
from tests.fixtures.rules_helpers import decide, one, raw


def test_r1_official_example():
    assert normalize_vin("vf1-ab12 3c4567890def0") == "VF1AB123C4567890DEF0"
    d = r1_vin("vf1-ab12 3c4567890def0")
    assert (d.action, d.rule_id, d.after) == ("CORRECT", "R1", "VF1AB123C4567890DEF0")


@pytest.mark.parametrize("src,dst", [("DiagTool", "DDT2000"), ("AfterSalesDiag", "Clip")])
def test_r2_official_mappings(cfg, src, dst):
    d = one(decide(cfg, raw(originTool=src)), "originTool")
    assert (d.action, d.rule_id, d.before, d.after) == ("TRANSFORM", "R2", src, dst)


@pytest.mark.parametrize("tool", ["CANalyzer", "Clip", "DDT2000"])
def test_r2_unchanged_tools(cfg, tool):
    d = one(decide(cfg, raw(originTool=tool)), "originTool")
    assert (d.action, d.after) == ("MIGRATE", tool)


@pytest.mark.parametrize("did,code", [("F187", "ID0002"), ("F191", "ID0003"), ("F188", "ID0004"), ("F012", "ID0005")])
def test_r3_did_map(cfg, did, code):
    dec = decide(cfg, raw(f2={"did": did}))
    d = one(dec, "ecuCode")
    assert (d.action, d.rule_id, d.after) == ("TRANSFORM", "R3", code)
    assert dec.outcome == "MIGRATE"


def test_r3_f738_out_of_scope(cfg):
    dec = decide(cfg, raw(f2={"did": "F738", "ecu": "HVAC"}))
    d = one(dec, "ecuCode")
    assert (d.action, d.reason) == ("REJECT", "OUT_OF_SCOPE") and dec.outcome == "REJECT"


def test_r4_id0001_value_is_the_normalized_vin(cfg):
    dec = decide(cfg, raw(VIN="vf1-ab12 3c4567890def0", ecuCode="ID0001", ValueReference="whatever"))
    d = one(dec, "ValueReference")
    assert (d.action, d.after, d.before) == ("TRANSFORM", "VF1AB123C4567890DEF0", "whatever")


@pytest.mark.parametrize("value,reason", [
    ("2,04E+12", "EXCEL_SCI"), ("#NOM?", "NOM"), ("", "EMPTY"), ("AB12/45678", "FORBIDDEN_CHAR"),
    ("AB12#45678", "FORBIDDEN_CHAR"), ("AB12@45678", "FORBIDDEN_CHAR"), ("AB12_45678", "FORBIDDEN_CHAR"),
    ("AB12-45678", "FORBIDDEN_CHAR"), ("AB123", "WRONG_LENGTH"),
])
def test_r4_rejections(cfg, value, reason):
    d = one(decide(cfg, raw(ValueReference=value)), "ValueReference")
    assert (d.action, d.rule_id, d.reason) == ("REJECT", "R4", reason)


@pytest.mark.parametrize("src,dst", [("T CM", "TCM"), ("E-PS", "EPS"), ("HVAC-", "HVAC"), (" OBC", "OBC")])
def test_r5_variants(cfg, src, dst):
    dec = decide(cfg, raw(ecuName=src))
    d = one(dec, "ecuName")
    assert (d.action, d.rule_id, d.after) == ("CORRECT", "R5", dst)
    assert dec.outcome == "CORRECT"


@pytest.mark.parametrize("src,dst", [("AR KANA", "ARKANA"), ("TRAFIC-", "TRAFIC"), ("SCENICETECH", "SCENIC E-TECH")])
def test_r6_variants(cfg, src, dst):
    d = one(decide(cfg, raw(Vehmodel=src)), "Vehmodel")
    assert (d.action, d.rule_id, d.after) == ("CORRECT", "R6", dst)
    assert dict_lookup(src, cfg.vehicle_models) == dst


def test_r7_vin_with_two_models(cfg):
    idx = {vin(1): frozenset({"CLIO", "MASTER"})}
    dec = decide(cfg, raw(), vin_model_index=idx)
    r7 = [d for d in dec.fields if d.rule_id == "R7" and d.field != "_row"]
    assert [(d.action, d.reason) for d in r7] == [("ASK_HUMAN", "VIN_MULTI_MODEL")]
    assert dec.outcome == "ASK_HUMAN"


def test_r7_shared_op_id(cfg):
    dec = decide(cfg, raw(diagnosticFieId="SHARED01"), shared_op_ids=frozenset({"SHARED01"}))
    r7 = [d for d in dec.fields if d.rule_id == "R7" and d.field != "_row"]
    assert [(d.action, d.reason, d.pattern_key) for d in r7] == [
        ("ASK_HUMAN", "SHARED_OP_ID", "diagnosticFieId|SHARED_OP_ID")]


@pytest.mark.parametrize("col", ["originTool", "toolVersion", "Creation_date"])
@pytest.mark.parametrize("value", ["", "#NOM?", "  "])
def test_r8_empty_fields_ask_human(cfg, col, value):
    d = one(decide(cfg, raw(**{col: value})), col)
    assert (d.action, d.rule_id, d.after, d.pattern_key) == ("ASK_HUMAN", "R8", "", f"{col}|UNREADABLE")


@pytest.mark.parametrize("value", ["", "#NOM?"])
def test_r8_empty_op_id_generated(cfg, value):
    a = one(decide(cfg, raw(diagnosticFieId=value)), "diagnosticFieId")
    b = one(decide(cfg, raw(diagnosticFieId=value)), "diagnosticFieId")
    assert (a.action, a.rule_id, a.reason) == ("CORRECT", "R8", "GENERATED_ID")
    assert a.after == b.after and len(a.after) == 36


@pytest.mark.parametrize("values,rule", [
    ({"VIN": "VF1AB00000000000000", "originTool": "DDT2000", "ecuCode": "ID0002", "ValueReference": "AB12345678"}, "FC1"),
    ({"VIN": vin(1), "originTool": "DiagTool", "ecuCode": "ID0002", "ValueReference": "AB12345678"}, "FC2"),
    ({"VIN": vin(1), "originTool": "DDT2000", "ecuCode": "ID0009", "ValueReference": "AB12345678"}, "FC3"),
    ({"VIN": vin(1), "originTool": "DDT2000", "ecuCode": "ID0003", "ValueReference": "AB1234567"}, "FC4"),
])
def test_each_final_check_fails_once(cfg, values, rule):
    failed = final_checks(values, cfg)
    assert [(d.rule_id, d.action, d.reason) for d in failed] == [(rule, "REJECT", "FAILED")]
    assert values[failed[0].field] in failed[0].explanation


def test_final_check_fails_inside_the_engine(cfg):
    from tests.fixtures.rules_helpers import human
    dec = decide(cfg, raw(originTool=""), human_decisions=human("originTool|UNREADABLE", "value", "Foo"))
    assert [d.rule_id for d in dec.fields if d.action == "REJECT" and d.field != "_row"] == ["FC2"]
    assert dec.outcome == "REJECT"


def test_clean_row_passes_final_checks(cfg):
    dec = decide(cfg, raw())
    assert dec.outcome == "MIGRATE" and "Final checks FC1-FC4 passed" in dec.row_record.explanation
    assert dec.target is not None and dec.target.source_origin_tool == "DDT2000"
