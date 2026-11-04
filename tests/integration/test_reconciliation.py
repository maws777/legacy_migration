import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from agents.api_agent_legacy import LegacyAClient
from agents.orchestrator import build_services, create_app
from hitl.schemas import HumanAnswer, HumanDecision
from legacy_a.api import create_app as legacy_a_app
from legacy_a.store import LegacyAStore
from migration.pipeline import MigrationPipeline
from migration.reconciliation import Reconciler
from rules.timeutil import utcnow
from target.api import create_app as target_app
from tests.fixtures.make_csv import anomaly_rows, vin, write_csv

pytestmark = pytest.mark.integration


class Down(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("down", request=request)


@pytest.fixture
def env(tmp_path):
    src = write_csv(tmp_path / "a.csv", anomaly_rows())
    p = MigrationPipeline(src, var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    p.start_campaign()
    s = build_services(p, legacy_a=LegacyAClient(TestClient(legacy_a_app(LegacyAStore.from_path(src)))),
                       target=TestClient(target_app(p.target_repo)), reconciler_factory=Reconciler.factory)
    yield s
    p.close()


def tamper(p, op_id: str):
    with p.target_engine.begin() as c:
        c.execute(text("UPDATE diag_value_result SET value_reference = 'ZZ99999999' WHERE diagnostic_op_id = :i"),
                  {"i": op_id})


def gap_questions(p):
    return [q for q in p.hitl.list("open") if q.reason == "RECON_GAP"]


def test_aligned_vin(env):
    r = env.reconciler.compare_vin(vin(1))
    assert (r.scope, r.compared, r.gaps, r.aligned_pct, r.not_performed) == ("vin", 1, 0, 100.0, [])


def test_gap_fixed_by_one_reload(env, monkeypatch):
    p = env.pipeline
    tamper(p, "OP000001")
    calls = []
    real = p.reload_rows
    monkeypatch.setattr(p, "reload_rows", lambda ids: calls.append(list(ids)) or real(ids))
    r = env.reconciler.compare_vin(vin(1))
    assert calls == [[1]] and r.gaps == 0 and r.aligned_pct == 100.0 and gap_questions(p) == []
    run = p.runs.list_runs(limit=1)[0]
    assert run["kind"] == "reload" and p.decision_logger.row_counts(run["run_id"]) == {"MIGRATE": 1}


def test_remaining_gap_creates_one_question_then_accept_target(env, monkeypatch):
    p = env.pipeline
    tamper(p, "OP000001")
    monkeypatch.setattr(p, "reload_rows", lambda ids: None)          # the reload does not fix the Gap
    r = env.reconciler.compare_vin(vin(1))
    assert (r.gaps, r.value_mismatch, r.aligned_pct) == (1, 1, 0.0)
    env.reconciler.compare_vin(vin(1))
    qs = gap_questions(p)
    assert [q.pattern_key for q in qs] == ["_row|RECON_GAP|1"] and qs[0].allowed_values == ["ACCEPT_TARGET"]
    assert qs[0].options == ["apply_value", "reject_group"] and qs[0].run_id is None
    p.hitl.answer(qs[0].question_id, HumanAnswer(option="apply_value", value="ACCEPT_TARGET", operator="ops"))
    assert env.reconciler.compare_vin(vin(1)).gaps == 0


def test_unexpected_and_not_applicable(env):
    p = env.pipeline
    p.hitl.decisions.put(HumanDecision(pattern_key="VIN|X", kind="reject", value=None, answered_at=utcnow(),
                                       operator="o", question_id="q"))
    # Row 3 (DiagTool) is loaded; a later human rejection of its R2 key is simulated by a reject on the
    # R8 tool-version group of row 3 only.
    p.hitl.decisions.put(HumanDecision(pattern_key="toolVersion|UNREADABLE#row=3", kind="reject", value=None,
                                       answered_at=utcnow(), operator="o", question_id="q"))
    r = env.reconciler.compare_vin(vin(3))
    assert r.gaps == 0                                               # the rule decision is unchanged
    with p.target_engine.begin() as c:
        c.execute(text("INSERT INTO diagnostic_operation SELECT 'EXTRA', creation_date, vin, ecu_name, diag_tool_name, "
                       "diag_tool_version, source_origin_tool, ingestion_ts, run_id, 999, op_id_generated "
                       "FROM diagnostic_operation WHERE diagnostic_op_id = 'OP000003'"))
        c.execute(text("INSERT INTO diag_value_result VALUES ('EXTRA', 'XX00000000', 'ID0003')"))
    r = env.reconciler.compare_vin(vin(3))
    assert r.unexpected_in_target == 1 and r.gaps == 1
    assert "_row|RECON_GAP|op=EXTRA" in [q.pattern_key for q in gap_questions(p)]
    na = env.reconciler.compare_vin("VF1AB999999999999999")
    assert (na.compared, na.aligned_pct) == (0, None)


def test_not_performed_when_a_backend_is_down(env):
    env.legacy_a = LegacyAClient(httpx.Client(transport=Down(), base_url="http://x"))
    r = env.reconciler.compare_vin(vin(1))
    assert (r.not_performed, r.compared, r.gaps, r.aligned_pct) == ([vin(1)], 0, 0, None)


def test_control_sample_and_gate(env):
    rec = env.reconciler
    sample = rec.control_sample()
    assert sample == rec.control_sample() and len(sample) == len({vin(i) for i in range(1, 27)})
    checks = {c.name: c for c in env.phase.gate()}
    assert checks["control_sample_gaps"].message == "no reconciliation available"
    res = rec.compare_control_sample()
    assert res.scope == "control_sample" and res.gaps == 0 and res.aligned_pct == 100.0
    assert rec.latest("control_sample") == res
    checks = {c.name: c for c in env.phase.gate()}
    assert checks["control_sample_gaps"].passed and not checks["open_questions"].passed


def test_reconciliation_routes_and_phase3_background(env):
    client = TestClient(create_app(env))
    assert client.get("/ops/reconciliation/latest").status_code == 404
    assert client.post("/ops/reconciliation/control-sample").json()["gaps"] == 0
    assert client.get("/ops/reconciliation/latest", params={"scope": "control_sample"}).status_code == 200
    env.phase.request(2), env.phase.request(3)
    assert client.get(f"/api/vehicles/{vin(1)}/diagnostics").status_code == 200
    latest = client.get("/ops/reconciliation/latest", params={"scope": "vin"}).json()
    assert latest["compared"] == 1 and latest["aligned_pct"] == 100.0
