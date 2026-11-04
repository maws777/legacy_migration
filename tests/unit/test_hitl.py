import pytest

from hitl.queue import AnswerRefused, ValueRejected
from hitl.schemas import HumanAnswer
from hitl.validation import SharedIdView, validate_value
from migration.pipeline import MigrationPipeline
from tests.fixtures.make_csv import anomaly_rows, base_row, write_csv

EXPECTED_KEYS = {"Vehmodel|UNREADABLE", "originTool|UNREADABLE", "toolVersion|UNREADABLE",
                 "Creation_date|UNREADABLE", "ecuCode|FORMAT2_NO_DID", "diagnosticFieId|SHARED_OP_ID"}


@pytest.fixture
def run(tmp_path):
    rows = anomaly_rows() + [base_row(27, ecuName="", DescriptionDiagECU="Lecture"),
                             base_row(28, ecuName="#NOM?", DescriptionDiagECU="Lecture")]
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", rows), var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    info = p.start_campaign("op")
    yield p, info
    p.close()


def q_by_key(p, key, status="open"):
    return next(q for q in p.hitl.list(status) if q.pattern_key == key)


def test_one_question_per_pattern_key_with_counts_examples_options(run):
    p, info = run
    qs = p.hitl.open_questions(info.run_id)
    assert {q.pattern_key for q in qs} == EXPECTED_KEYS | {"ecuName|UNREADABLE"}
    assert len(qs) == len({q.pattern_key for q in qs})
    # row counts sum to the distinct (Source_Row_Id, Pattern_Key) pairs of the ASK_HUMAN decisions
    ask = [d for dec in p.run_data(info.run_id).decisions.values() for d in dec.fields[:-1]
           if d.action == "ASK_HUMAN"]
    pairs = {(dec_id, d.pattern_key) for dec_id, dec in p.run_data(info.run_id).decisions.items()
             for d in dec.fields[:-1] if d.action == "ASK_HUMAN"}
    assert sum(q.row_count for q in qs) == len(pairs) and ask
    shared = q_by_key(p, "diagnosticFieId|SHARED_OP_ID")
    assert shared.row_count == 2 and [e.source_row_id for e in shared.example_rows] == [25, 26]
    assert shared.options == ["reject_group", "case_by_case", "generate_op_id"]
    no_did = q_by_key(p, "ecuCode|FORMAT2_NO_DID")
    assert no_did.options == ["apply_value", "reject_group", "case_by_case"]
    assert no_did.allowed_values == ["ID0001", "ID0002", "ID0003", "ID0004", "ID0005"]
    assert no_did.missing and "R3 FORMAT2_NO_DID" in no_did.why and no_did.llm_status == "not_applicable"
    # R8 empty fields: no group value (decisions.md); ecuName group mixes "" and "#NOM?": not homogeneous
    assert "apply_value" not in q_by_key(p, "toolVersion|UNREADABLE").options
    assert "apply_value" not in q_by_key(p, "ecuName|UNREADABLE").options
    assert q_by_key(p, "ecuName|UNREADABLE").row_count == 2


def test_apply_value_reject_and_generate(run):
    p, _ = run
    q = q_by_key(p, "ecuCode|FORMAT2_NO_DID")
    with pytest.raises(ValueRejected) as exc:
        p.hitl.answer(q.question_id, HumanAnswer(option="apply_value", value="ID0009", operator="bob"))
    assert "R3" in exc.value.failed_rule and p.hitl.get(q.question_id).status == "open"
    res = p.hitl.answer(q.question_id, HumanAnswer(option="apply_value", value="ID0004", operator="bob"))
    assert res.decision.kind == "value" and res.question.status == "closed" and res.question.closed_at
    with pytest.raises(AnswerRefused):                              # closed question
        p.hitl.answer(q.question_id, HumanAnswer(option="reject_group", operator="bob"))
    assert p.hitl.decisions.get("ecuCode|FORMAT2_NO_DID").value == "ID0004"
    v = q_by_key(p, "Vehmodel|UNREADABLE")
    with pytest.raises(AnswerRefused):                              # generate only on SHARED_OP_ID
        p.hitl.answer(v.question_id, HumanAnswer(option="generate_op_id", operator="bob"))
    with pytest.raises(AnswerRefused):                              # no group value for R6
        p.hitl.answer(v.question_id, HumanAnswer(option="apply_value", value="CLIO", operator="bob"))
    assert p.hitl.answer(v.question_id, HumanAnswer(option="reject_group", operator="bob")).decision.kind == "reject"
    s = q_by_key(p, "diagnosticFieId|SHARED_OP_ID")
    assert p.hitl.answer(s.question_id, HumanAnswer(option="generate_op_id", operator="bob")).decision.kind == "generate"


def test_case_by_case_creates_one_question_per_row(run):
    p, info = run
    s = q_by_key(p, "diagnosticFieId|SHARED_OP_ID")
    res = p.hitl.answer(s.question_id, HumanAnswer(option="case_by_case", operator="ann"))
    assert res.decision is None and res.question.status == "closed" and len(res.created_questions) == 2
    rows = [p.hitl.get(i) for i in res.created_questions]
    assert [r.pattern_key for r in rows] == ["diagnosticFieId|SHARED_OP_ID#row=25", "diagnosticFieId|SHARED_OP_ID#row=26"]
    assert all(r.scope == "row" and r.parent_question_id == s.question_id and r.row_count == 1 for r in rows)
    assert rows[0].parent_pattern_key == s.pattern_key and rows[0].source_row_id == 25
    assert "case_by_case" not in rows[0].options and "generate_op_id" in rows[0].options
    with pytest.raises(AnswerRefused):
        p.hitl.answer(rows[0].question_id, HumanAnswer(option="case_by_case", operator="ann"))
    assert p.hitl.decisions.get(s.pattern_key) is None
    ok = p.hitl.answer(rows[0].question_id, HumanAnswer(option="apply_value", value="NEWOP25", operator="ann"))
    assert ok.decision.pattern_key.endswith("#row=25")
    with pytest.raises(ValueRejected):                              # id already carried by source rows
        p.hitl.answer(rows[1].question_id, HumanAnswer(option="apply_value", value="OP000001", operator="ann"))


def test_decisions_survive_restart_and_checkpoint_deletion(tmp_path, run):
    p, info = run
    q = q_by_key(p, "originTool|UNREADABLE")
    p.hitl.answer(q.question_id, HumanAnswer(option="reject_group", operator="z"))
    var = p.var_dir
    src = p.deps.source_path
    p.close()
    (var / "checkpoints.db").unlink()
    p2 = MigrationPipeline(src, var_dir=var)
    try:
        d = p2.hitl.decisions.get("originTool|UNREADABLE")
        assert d is not None and d.kind == "reject" and d.operator == "z"
        info2 = p2.start_campaign()
        dec = p2.run_data(info2.run_id).decisions[17]
        assert dec.outcome == "REJECT" and any(f.decided_by == "human" for f in dec.fields)
    finally:
        p2.close()


def test_validate_value_rules(cfg):
    assert validate_value("toolVersion|UNREADABLE", "v1.2", cfg) is None
    assert validate_value("toolVersion|UNREADABLE", " ", cfg)
    assert validate_value("Creation_date|UNREADABLE", "29/02/2024 10:00", cfg) is None
    assert validate_value("Creation_date|UNREADABLE", "29/02/2023 10:00", cfg)
    assert validate_value("originTool|UNREADABLE", "Clip", cfg) is None
    assert validate_value("originTool|UNREADABLE", "clip", cfg)
    view = SharedIdView(shared=frozenset({"S1"}), used=frozenset({"U1"}))
    assert validate_value("diagnosticFieId|SHARED_OP_ID", "NEW", cfg, view) is None
    assert validate_value("diagnosticFieId|SHARED_OP_ID", "S1", cfg, view)
    assert validate_value("diagnosticFieId|SHARED_OP_ID", "U1", cfg, view)
    assert validate_value("diagnosticFieId|SHARED_OP_ID", "x" * 129, cfg, view)
    assert validate_value("_row|RECON_GAP|12", "ACCEPT_TARGET", cfg) is None
