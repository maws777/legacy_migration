"""One example per line of the R3, R6/R7 and diagnosticOpID tables, plus R2, R5, R8 and malformed rows."""
import pytest

from rules.opid import generate_op_id
from rules.types import RawRow
from tests.fixtures.make_csv import base_row, vin
from tests.fixtures.rules_helpers import decide, field, human, one, raw


# ---------------------------------------------------------------- R3
def test_r3_format1_inconsistency_note(cfg):
    d = one(decide(cfg, raw(ecuCode="ID0002", DescriptionDiagECU="Donnée diagnostic BCM (F188)")), "ecuCode")
    assert (d.action, d.reason, d.after) == ("MIGRATE", "FORMAT1", "ID0002")
    assert "F188" in d.explanation and "ID0004" in d.explanation


def test_r3_format2_no_did_shares_one_key(cfg):
    a = one(decide(cfg, raw(1, f2={"did": None, "ecu": "BCM"})), "ecuCode")
    b = one(decide(cfg, raw(2, f2={"did": None, "ecu": "ABS", "code": "Id999"})), "ecuCode")
    assert a.action == "ASK_HUMAN" and a.pattern_key == b.pattern_key == "ecuCode|FORMAT2_NO_DID"


@pytest.mark.parametrize("label,action,reason,after", [
    ("Donnée diagnostic AIRBAG (F191)", "CORRECT", "DEDUCED_DID", "ID0003"),
    ("Paramètre diagnostic HVAC (F738)", "REJECT", "OUT_OF_SCOPE", ""),
    ("Lecture identifiant châssis", "CORRECT", "DEDUCED_VIN_LABEL", "ID0001"),
    ("Identifiant véhicule usine", "CORRECT", "DEDUCED_VIN_LABEL", "ID0001"),
])
def test_r3_unreadable_ecu_code(cfg, label, action, reason, after):
    d = one(decide(cfg, raw(ecuCode="", DescriptionDiagECU=label)), "ecuCode")
    assert (d.action, d.reason, d.after) == (action, reason, after)


def test_r3_unreadable_key_uses_label_template_without_ecu_code(cfg):
    a = one(decide(cfg, raw(1, ecuCode="#NOM?", DescriptionDiagECU="Lecture référence logiciel ADAS")), "ecuCode")
    b = one(decide(cfg, raw(2, ecuCode="", DescriptionDiagECU="Lecture référence logiciel BCM")), "ecuCode")
    assert a.action == "ASK_HUMAN" and a.reason == "UNREADABLE"
    assert a.pattern_key == b.pattern_key == "ecuCode|UNREADABLE|Lecture référence logiciel <ECU>"


def test_r3_unknown_variant(cfg):
    d = one(decide(cfg, raw(ecuCode="X12")), "ecuCode")
    assert (d.action, d.reason, d.pattern_key) == ("ASK_HUMAN", "UNKNOWN_VARIANT", "ecuCode|UNKNOWN_VARIANT|X12")


def test_value_reference_skipped_when_description_undecided(cfg):
    dec = decide(cfg, raw(f2={"did": "F738", "ecu": "HVAC"}))
    assert field(dec, "ValueReference") == []


# ---------------------------------------------------------------- R2, R4, R5
def test_r2_unknown_variant_keeps_trimmed_value(cfg):
    d = one(decide(cfg, raw(originTool=" Diag Tool ")), "originTool")
    assert (d.action, d.rule_id, d.pattern_key) == ("ASK_HUMAN", "R2", "originTool|UNKNOWN_VARIANT|Diag Tool")


def test_r4_migrate_and_correct(cfg):
    assert one(decide(cfg, raw(ValueReference="AB12345678")), "ValueReference").action == "MIGRATE"
    d = one(decide(cfg, raw(ValueReference=" ab12345678 ")), "ValueReference")
    assert (d.action, d.after) == ("CORRECT", "AB12345678")


def test_r4_id0001_identical_is_migrate(cfg):
    d = one(decide(cfg, raw(ecuCode="ID0001", ValueReference=vin(1))), "ValueReference")
    assert (d.action, d.reason) == ("MIGRATE", "VIN_AS_VALUE")


def test_r5_label_ecu_and_unreadable(cfg):
    d = one(decide(cfg, raw(ecuName="#NOM?", DescriptionDiagECU="Lecture référence pièce ABS")), "ecuName")
    assert (d.action, d.reason, d.after) == ("CORRECT", "LABEL_ECU", "ABS")
    d = one(decide(cfg, raw(ecuName="", DescriptionDiagECU="Lecture référence pièce")), "ecuName")
    assert (d.action, d.pattern_key) == ("ASK_HUMAN", "ecuName|UNREADABLE")
    d = one(decide(cfg, raw(ecuName="", DescriptionDiagECU="BCM et ABS")), "ecuName")
    assert d.action == "ASK_HUMAN"                                   # two ECUs: no Label_ECU


def test_r5_unknown_variant_key_is_normalized(cfg):
    d = one(decide(cfg, raw(ecuName="air-bag unit", DescriptionDiagECU="Lecture")), "ecuName")
    assert (d.action, d.pattern_key) == ("ASK_HUMAN", "ecuName|UNKNOWN_VARIANT|AIRBAGUNIT")


# ---------------------------------------------------------------- R6 / R7
def test_r6_ok(cfg):
    d = one(decide(cfg, raw(Vehmodel="CLIO")), "Vehmodel")
    assert (d.action, d.reason) == ("MIGRATE", "OK")


def test_r6_vin_sibling(cfg):
    d = one(decide(cfg, raw(Vehmodel="#NOM?"), vin_model_index={vin(1): frozenset({"MASTER"})}), "Vehmodel")
    assert (d.action, d.reason, d.after) == ("CORRECT", "VIN_SIBLING", "MASTER")


def test_r6_legacy_b(cfg):
    d = one(decide(cfg, raw(Vehmodel=""), legacy_b_model_index={vin(1): "KANGOO"}), "Vehmodel")
    assert (d.action, d.reason, d.after) == ("CORRECT", "LEGACY_B", "KANGOO") and "Legacy B" in d.explanation


def test_r6_unreadable_ask_human(cfg):
    d = one(decide(cfg, raw(Vehmodel="#NOM?")), "Vehmodel")
    assert (d.action, d.pattern_key) == ("ASK_HUMAN", "Vehmodel|UNREADABLE")


def test_r6_unknown_variant(cfg):
    d = one(decide(cfg, raw(Vehmodel="Mégane E-Tech")), "Vehmodel")
    assert (d.action, d.pattern_key) == ("ASK_HUMAN", "Vehmodel|UNKNOWN_VARIANT|MÉGANEETECH")


def test_r7_multi_model_with_a_matched_row(cfg):
    dec = decide(cfg, raw(Vehmodel="CLIO"), vin_model_index={vin(1): frozenset({"CLIO", "MASTER"})})
    assert [(d.rule_id, d.action, d.reason) for d in field(dec, "Vehmodel")] == [
        ("R6", "MIGRATE", "OK"), ("R7", "ASK_HUMAN", "VIN_MULTI_MODEL")]


def test_r7_legacy_b_conflict(cfg):
    dec = decide(cfg, raw(Vehmodel="CLIO"), legacy_b_model_index={vin(1): "MASTER"})
    assert [(d.rule_id, d.action, d.reason) for d in field(dec, "Vehmodel")] == [
        ("R6", "MIGRATE", "OK"), ("R7", "ASK_HUMAN", "LEGACY_B_CONFLICT")]
    same = decide(cfg, raw(Vehmodel="CLIO"), legacy_b_model_index={vin(1): "CLIO"})
    assert same.outcome == "MIGRATE"


def test_r7_human_value_becomes_the_model(cfg):
    dec = decide(cfg, raw(Vehmodel="CLIO"), legacy_b_model_index={vin(1): "MASTER"},
                 human_decisions=human("Vehmodel|LEGACY_B_CONFLICT", "value", "MASTER"))
    assert dec.outcome == "CORRECT" and dec.target.vehicle_model == "MASTER"


# ---------------------------------------------------------------- diagnosticOpID table
def test_op_id_unreadable_with_ask_human_component_is_skipped(cfg):
    dec = decide(cfg, raw(diagnosticFieId="", ecuName="#NOM?", DescriptionDiagECU="x"))
    assert field(dec, "diagnosticFieId") == []


def test_op_id_copied_when_not_shared(cfg):
    d = one(decide(cfg, raw(diagnosticFieId=" OP1 ")), "diagnosticFieId")
    assert (d.action, d.reason, d.after) == ("MIGRATE", "COPIED", " OP1 ")


def test_op_id_shared_with_generate_decision(cfg):
    dec = decide(cfg, raw(diagnosticFieId="S1"), shared_op_ids=frozenset({"S1"}),
                 human_decisions=human("diagnosticFieId|SHARED_OP_ID", "generate"))
    ds = field(dec, "diagnosticFieId")
    assert [(d.rule_id, d.action, d.reason) for d in ds] == [("R8", "MIGRATE", "COPIED"), ("R7", "CORRECT", "HUMAN_GENERATE")]
    assert dec.target.diagnostic_op_id == generate_op_id(dec.identity) and dec.target.op_id_generated


def test_op_id_shared_generate_with_ask_human_component_routes_nothing(cfg):
    dec = decide(cfg, raw(diagnosticFieId="S1", toolVersion=""), shared_op_ids=frozenset({"S1"}),
                 human_decisions=human("diagnosticFieId|SHARED_OP_ID", "generate"))
    assert [(d.rule_id, d.action) for d in field(dec, "diagnosticFieId")] == [("R8", "MIGRATE")]


@pytest.mark.parametrize("kind,value,action,reason", [
    ("value", "NEWID01", "CORRECT", "HUMAN_VALUE"), ("reject", None, "REJECT", "HUMAN_REJECT")])
def test_op_id_shared_with_other_decisions(cfg, kind, value, action, reason):
    dec = decide(cfg, raw(diagnosticFieId="S1"), shared_op_ids=frozenset({"S1"}),
                 human_decisions=human("diagnosticFieId|SHARED_OP_ID", kind, value))
    d = field(dec, "diagnosticFieId")[-1]
    assert (d.rule_id, d.action, d.reason, d.decided_by) == ("R7", action, reason, "human")


def test_identity_includes_tool_name_and_version(cfg):
    a = decide(cfg, raw(diagnosticFieId=""))
    b = decide(cfg, raw(diagnosticFieId="", toolVersion="v9"))
    assert a.identity.diag_tool_version == "v1.2"
    assert a.target.diagnostic_op_id != b.target.diagnostic_op_id


# ---------------------------------------------------------------- R8 dates and malformed rows
def test_r8_date_parsed_and_unknown_variant(cfg):
    d = one(decide(cfg, raw(Creation_date="29/02/2024 23:59")), "Creation_date")
    assert (d.action, d.after) == ("TRANSFORM", "2024-02-29T23:59")
    d = one(decide(cfg, raw(Creation_date="2024-02-29")), "Creation_date")
    assert (d.action, d.rule_id, d.pattern_key) == ("ASK_HUMAN", "R8", "Creation_date|UNKNOWN_VARIANT|2024-02-29")


def test_malformed_row_single_decision(cfg):
    dec = decide(cfg, RawRow(7, base_row(7), malformed=True))
    assert [(d.field, d.action, d.rule_id, d.reason) for d in dec.fields] == [("_row", "REJECT", "INGEST", "MALFORMED_ROW")]


def test_reject_does_not_stop_later_steps(cfg):
    dec = decide(cfg, raw(VIN="SHORT", ecuName="T CM"))
    assert one(dec, "VIN").action == "REJECT" and one(dec, "ecuName").action == "CORRECT"


def test_every_decision_has_an_explanation(cfg):
    from tests.fixtures.make_csv import anomaly_rows
    from rules.engine import decide_row
    from rules.types import RuleContext
    for i, values in enumerate(anomaly_rows(), start=1):
        dec = decide_row(RawRow(i, values), RuleContext(config=cfg, shared_op_ids=frozenset({"SHARED01"})))
        assert dec.fields[-1].field == "_row" and dec.fields[-1].action == dec.outcome
        assert all(d.explanation and d.reason for d in dec.fields)
