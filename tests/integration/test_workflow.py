import shutil

import pytest
from sqlalchemy import inspect

from migration.pipeline import MigrationPipeline
from reporting.schemas import LogFilters
from rules.config import CONFIG_FILES, DEFAULT_CONFIG_DIR
from tests.fixtures.make_csv import anomaly_rows, write_csv

pytestmark = pytest.mark.integration


@pytest.fixture
def source(tmp_path):
    return write_csv(tmp_path / "src.csv", anomaly_rows())


@pytest.fixture
def pipeline(tmp_path, source):
    p = MigrationPipeline(source, var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    yield p
    p.close()


def test_distinct_run_ids(pipeline):
    a = pipeline.start_campaign("op")
    b = pipeline.start_campaign("op")
    assert a.run_id != b.run_id and a.campaign_id != b.campaign_id
    assert a.status == "completed"
    run = pipeline.runs.get_run(a.run_id)
    assert run["rows_read"] == len(anomaly_rows()) and run["ingestion_ts"] is not None


def test_invalid_config_stops_before_any_row(tmp_path, source):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    (cfg_dir / "pipeline.yaml").write_text(
        (cfg_dir / "pipeline.yaml").read_text(encoding="utf-8").replace("batch_size: 1000", "batch_size: 0"),
        encoding="utf-8")
    p = MigrationPipeline(source, var_dir=tmp_path / "var", config_dir=cfg_dir)
    try:
        info = p.start_campaign()
        assert info.status == "failed" and "pipeline.yaml" in info.error
        run = p.runs.get_run(info.run_id)
        assert run["rows_read"] is None
        tables = inspect(p.ops_engine).get_table_names()
        assert "decision_log" not in tables or p.ops_engine.connect().exec_driver_sql(
            "select count(*) from decision_log").scalar() == 0
        assert not (tmp_path / "var" / "target.db").exists()
    finally:
        p.close()


def test_cli_exit_code_on_invalid_config(tmp_path, source):
    from migration.cli import main
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    (cfg_dir / "mappings.yaml").unlink()
    assert main(["run", "--source", str(source), "--config", str(cfg_dir), "--var", str(tmp_path / "v")]) == 1


def test_every_row_gets_exactly_one_row_record(pipeline):
    info = pipeline.start_campaign("op")
    assert info.status == "completed"
    page = pipeline.decision_logger.query(info.run_id, limit=1000)
    rows = [r for r in page.items if r.field == "_row"]
    assert sorted(r.source_row for r in rows) == list(range(1, len(anomaly_rows()) + 1))
    assert all(r.explanation for r in page.items)
    counts = pipeline.decision_logger.row_counts(info.run_id)
    assert sum(counts.values()) == len(anomaly_rows())
    assert pipeline.deps.shared_ids.load(info.run_id) == frozenset({"SHARED01"})


def test_decision_log_write_failure_stops_the_run(pipeline, monkeypatch):
    from reporting.decision_log import DecisionLogError

    def boom(records, meta=None):
        raise DecisionLogError(records[0].run_id, records[0].source_row, "disk full")

    monkeypatch.setattr(pipeline.decision_logger, "write", boom)
    info = pipeline.start_campaign()
    assert info.status == "failed" and info.run_id in info.error and "source row 1" in info.error


def test_multi_batch_run_matches_single_batch(tmp_path, source):
    cfg_dir = tmp_path / "cfg3"
    cfg_dir.mkdir()
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    (cfg_dir / "pipeline.yaml").write_text(
        (cfg_dir / "pipeline.yaml").read_text(encoding="utf-8").replace("batch_size: 1000", "batch_size: 4"),
        encoding="utf-8")
    p1 = MigrationPipeline(source, var_dir=tmp_path / "v1")
    p2 = MigrationPipeline(source, var_dir=tmp_path / "v2", config_dir=cfg_dir)
    p1.seed_reference_mapping()
    p2.seed_reference_mapping()
    try:
        a, b = p1.start_campaign(), p2.start_campaign()
        assert p2.run_data(b.run_id).batch_bounds[:2] == [(0, 4), (4, 8)]
        assert p1.decision_logger.row_counts(a.run_id) == p2.decision_logger.row_counts(b.run_id)
    finally:
        p1.close()
        p2.close()


# ---------------------------------------------------------------- load, report, metrics (task 5.1)
def test_second_run_same_counts_no_duplicates(pipeline):
    a = pipeline.start_campaign()
    counts1 = pipeline.target_repo.table_counts()
    b = pipeline.start_campaign()
    assert pipeline.target_repo.table_counts() == counts1
    assert counts1["diagnostic_operation"] == counts1["diag_value_result"] > 0
    ra, rb = pipeline.reports.build(a.run_id), pipeline.reports.build(b.run_id)
    assert (ra.migrate, ra.correct, ra.reject, ra.ask_human) == (rb.migrate, rb.correct, rb.reject, rb.ask_human)
    assert ra.migrate + ra.correct + ra.reject + ra.ask_human == ra.analysed == len(anomaly_rows())
    assert ra.loaded == ra.migrate + ra.correct and ra.out_of_scope == 1
    m = pipeline.metrics.get(a.run_id)
    assert m.counts == {"MIGRATE": ra.migrate, "CORRECT": ra.correct, "REJECT": ra.reject, "ASK_HUMAN": ra.ask_human}
    assert m.total_seconds == ra.seconds and m.input_tokens + m.output_tokens == ra.tokens == 0
    assert m.cache_hit_rate == 0 and m.legacy_b_lookup == "disabled" and m.batch_size == 1000


def test_forced_batch_failure_is_retried_row_by_row(pipeline, monkeypatch):
    from dataclasses import replace
    repo = pipeline.target_repo
    real = repo.load_rows

    def corrupt(run_id, ts, decisions):
        decisions = list(decisions)
        decisions[0] = replace(decisions[0], target=replace(decisions[0].target, vehicle_model="BAD"))
        return real(run_id, ts, decisions)

    monkeypatch.setattr(repo, "load_rows", corrupt)
    info = pipeline.start_campaign()
    first_loadable = min(rid for rid, d in pipeline.run_data(info.run_id).decisions.items() if d.outcome == "REJECT"
                         and d.fields[-1].rule_id == "LOAD")
    recs = pipeline.decision_logger.query(info.run_id, LogFilters(source_row=first_loadable)).items
    row = [r for r in recs if r.field == "_row"][0]
    assert (row.action, row.rule_id) == ("REJECT", "LOAD") and "ck_vehicle_model_dico" in row.explanation
    assert any(r.field == "_load" and r.rule_id == "LOAD" for r in recs)
    report = pipeline.reports.build(info.run_id)
    assert report.loaded == report.migrate + report.correct and report.loaded > 0


def test_ops_routes(pipeline, tmp_path):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    client = TestClient(create_app(Services(pipeline=pipeline, export_dir=tmp_path / "exp")))
    r = client.post("/ops/campaigns", json={"operator": "alice"})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert client.get("/ops/runs").json()[0]["run_id"] == run_id
    assert client.get(f"/ops/runs/{run_id}").json()["status"] == "completed"
    rep = client.get(f"/ops/runs/{run_id}/report").json()
    assert rep["analysed"] == len(anomaly_rows()) and "records analysed" in rep["text"]
    assert client.get(f"/ops/runs/{run_id}/metrics").json()["batch_size"] == 1000
    page = client.get(f"/ops/runs/{run_id}/decisions", params={"field": "_row", "limit": 5}).json()
    assert page["total"] == len(anomaly_rows()) and len(page["items"]) == 5
    assert client.get(f"/ops/runs/{run_id}/decisions", params={"limit": 1001}).status_code == 422
    exp = client.get(f"/ops/runs/{run_id}/decisions/export", params={"format": "csv"})
    assert exp.status_code == 200 and exp.headers["content-type"].startswith("text/csv")
    assert client.get(f"/ops/runs/{run_id}/decisions/export").headers["content-type"] == "application/json"
    missing = client.get("/ops/runs/nope/decisions/export")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "UNKNOWN_RUN"
    assert client.get("/ops/runs/nope").json()["error"]["code"] == "UNKNOWN_RUN"


# ---------------------------------------------------------------- HITL pause and resume (task 6)
def test_pause_resume_new_run_fewer_questions(pipeline):
    from hitl.schemas import HumanAnswer
    first = pipeline.start_campaign("op")
    assert first.campaign_status == "paused" and first.open_questions == 6
    snap = pipeline.graph.get_state(pipeline._config(first.campaign_id))
    assert snap.next == ("await_humans",)
    counts_before = pipeline.target_repo.table_counts()
    hitl = pipeline.hitl
    by_key = {q.pattern_key: q for q in hitl.open_questions(first.run_id)}
    hitl.answer(by_key["ecuCode|FORMAT2_NO_DID"].question_id,
                HumanAnswer(option="apply_value", value="ID0004", operator="op"))
    hitl.answer(by_key["diagnosticFieId|SHARED_OP_ID"].question_id,
                HumanAnswer(option="generate_op_id", operator="op"))
    second = pipeline.resume(first.campaign_id, "op")
    assert second.run_id != first.run_id and second.campaign_id == first.campaign_id
    assert second.open_questions == 4
    assert {q.status for q in hitl.list(run_id=first.run_id)} <= {"closed", "superseded"}
    decs = pipeline.run_data(second.run_id).decisions
    assert decs[24].outcome == "CORRECT" and decs[25].outcome == "CORRECT" and decs[26].outcome == "CORRECT"
    assert decs[25].target.op_id_generated and decs[25].target.diagnostic_op_id != decs[26].target.diagnostic_op_id
    after = pipeline.target_repo.table_counts()
    assert after["diagnostic_operation"] == counts_before["diagnostic_operation"] + 3
    assert after["diagnostic_operation"] == after["diag_value_result"]
    with pytest.raises(Exception):
        pipeline.resume("not-a-campaign", "op")


def test_hitl_routes(pipeline):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    client = TestClient(create_app(Services(pipeline=pipeline)))
    run = client.post("/ops/campaigns", json={}).json()
    qs = client.get("/ops/questions", params={"status": "open"}).json()
    assert len(qs) == 6
    q = next(x for x in qs if x["pattern_key"] == "ecuCode|FORMAT2_NO_DID")
    assert client.get(f"/ops/questions/{q['question_id']}").json()["pattern_key"] == q["pattern_key"]
    bad = client.post(f"/ops/questions/{q['question_id']}/answer",
                      json={"option": "apply_value", "value": "X", "operator": "a"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    ok = client.post(f"/ops/questions/{q['question_id']}/answer",
                     json={"option": "apply_value", "value": "ID0002", "operator": "a"})
    assert ok.status_code == 200 and ok.json()["decision"]["value"] == "ID0002"
    again = client.post(f"/ops/questions/{q['question_id']}/answer", json={"option": "reject_group", "operator": "a"})
    assert again.status_code == 409
    assert client.get("/ops/questions/nope").status_code == 404
    r = client.post(f"/ops/campaigns/{run['campaign_id']}/resume", json={"operator": "a"})
    assert r.status_code == 202 and r.json()["run_id"] != run["run_id"]
    assert client.post("/ops/campaigns/unknown/resume", json={"operator": "a"}).status_code == 409


def test_revoked_decision_is_asked_again_by_a_new_full_run(pipeline):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    client = TestClient(create_app(Services(pipeline=pipeline)))
    client.post("/ops/campaigns", json={})
    key = "ecuCode|FORMAT2_NO_DID"
    q = next(x for x in client.get("/ops/questions", params={"status": "open"}).json() if x["pattern_key"] == key)
    client.post(f"/ops/questions/{q['question_id']}/answer",
                json={"option": "apply_value", "value": "ID0002", "operator": "a"})
    listed = client.get("/ops/decisions").json()
    assert [d["pattern_key"] for d in listed] == [key] and listed[0]["field"] == q["field"]
    assert client.post("/ops/decisions/revoke", json={"pattern_key": "nope", "operator": "a"}).status_code == 404
    assert client.post("/ops/decisions/revoke", json={"pattern_key": key, "operator": "a"}).json() == []
    second = client.post("/ops/campaigns", json={}).json()
    reasked = client.get("/ops/questions", params={"status": "open", "run_id": second["run_id"]}).json()
    assert key in {x["pattern_key"] for x in reasked}


# ---------------------------------------------------------------- Legacy B lookup (task 8)
class _LB:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def model_index(self, vins):
        self.calls += 1
        self.vins = list(vins)
        return self.result


def _lb_pipeline(tmp_path, source, agent, lookup=True):
    cfg_dir = tmp_path / ("cfg_on" if lookup else "cfg_off")
    cfg_dir.mkdir(exist_ok=True)
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    if not lookup:
        text = (cfg_dir / "pipeline.yaml").read_text(encoding="utf-8")
        (cfg_dir / "pipeline.yaml").write_text(text.replace("legacy_b_lookup: true", "legacy_b_lookup: false"),
                                               encoding="utf-8")
    p = MigrationPipeline(source, var_dir=tmp_path / f"v{lookup}", config_dir=cfg_dir, legacy_b=agent)
    p.seed_reference_mapping()
    return p


def test_legacy_b_lookup_enabled(tmp_path, source):
    from agents.api_agent_legacy_b import ModelIndexResult
    from tests.fixtures.make_csv import vin
    agent = _LB(ModelIndexResult({vin(11): "KANGOO", vin(1): "MASTER"}, "ok"))
    p = _lb_pipeline(tmp_path, source, agent)
    try:
        info = p.start_campaign()
        decs = p.run_data(info.run_id).decisions
        assert agent.calls == 1 and vin(1) in agent.vins
        model11 = [d for d in decs[11].fields if d.field == "Vehmodel"][0]
        assert (model11.reason, model11.after) == ("LEGACY_B", "KANGOO")
        assert any(d.reason == "LEGACY_B_CONFLICT" for d in decs[1].fields[:-1])
        assert p.metrics.get(info.run_id).legacy_b_lookup == "enabled"
    finally:
        p.close()


def test_legacy_b_lookup_disabled_sends_no_request(tmp_path, source):
    from agents.api_agent_legacy_b import ModelIndexResult
    agent = _LB(ModelIndexResult({}, "ok"))
    p = _lb_pipeline(tmp_path, source, agent, lookup=False)
    try:
        info = p.start_campaign()
        assert agent.calls == 0 and p.metrics.get(info.run_id).legacy_b_lookup == "disabled"
    finally:
        p.close()


def test_legacy_b_lookup_failed_uses_empty_index(tmp_path, source):
    from agents.api_agent_legacy_b import ModelIndexResult
    agent = _LB(ModelIndexResult({}, "failed", ("ConnectError",)))
    p = _lb_pipeline(tmp_path, source, agent)
    try:
        info = p.start_campaign()
        assert info.status == "completed"
        assert p.metrics.get(info.run_id).legacy_b_lookup == "failed"
        assert "Legacy B lookup failed" in p.reports.build(info.run_id).text
        model11 = [d for d in p.run_data(info.run_id).decisions[11].fields if d.field == "Vehmodel"][0]
        assert model11.action == "ASK_HUMAN"
    finally:
        p.close()
