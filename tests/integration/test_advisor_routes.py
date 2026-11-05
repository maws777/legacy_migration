"""POST /ops/questions/{id}/advise as a background job; advice traced and readable; no decision taken."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import Services, create_app
from migration.pipeline import MigrationPipeline
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.make_csv import anomaly_rows, write_csv

pytestmark = pytest.mark.integration


def answer(body):
    if "question" not in body:                     # schema discovery or other prompts: no use here
        return "{}"
    if body["turn"] == 1:
        return json.dumps({"tool": "sample_group_rows", "args": {"limit": 2}})
    if body["turn"] == 2:
        return json.dumps({"tool": "test_option", "args": {"option": "generate_op_id"}})
    return json.dumps({"final": {"option": "generate_op_id", "value": None, "confidence": 0.85,
                                 "justification": "the two rows are different operations",
                                 "evidence": ["rows 25 and 26 differ on ValueReference"],
                                 "unknowns": ["the original id of each operation"]}})


@pytest.fixture
def env(tmp_path):
    llm = FakeLLM(answer)
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", anomaly_rows()), var_dir=tmp_path / "var", llm=llm)
    p.seed_reference_mapping()
    p.start_campaign("op")
    yield p, llm, TestClient(create_app(Services(pipeline=p)))
    p.close()


def poll(client, job_id):
    for _ in range(600):
        job = client.get(f"/ops/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_advise_job_trace_and_no_decision(env):
    p, llm, client = env
    q = next(x for x in client.get("/ops/questions", params={"status": "open"}).json()
             if x["pattern_key"] == "diagnosticFieId|SHARED_OP_ID")
    r = client.post(f"/ops/questions/{q['question_id']}/advise", json={"operator": "ann"})
    assert r.status_code == 202 and r.json()["kind"] == "advise"
    job = poll(client, r.json()["job_id"])
    assert job["status"] == "succeeded"
    advice = job["result"]
    assert advice["status"] == "recommended" and advice["recommendation"]["option"] == "generate_op_id"
    assert [c["tool"] for c in advice["tool_calls"]] == ["sample_group_rows", "test_option"]
    assert advice["tool_calls"][1]["result_summary"].startswith("allowed; 2 row(s)")
    assert (advice["llm_calls"], advice["input_tokens"], advice["output_tokens"]) == (3, 300, 60)
    stored = client.get(f"/ops/questions/{q['question_id']}/advice").json()
    assert len(stored) == 1 and stored[0]["advice_id"] == advice["advice_id"]
    assert stored[0]["recommendation"]["unknowns"] == ["the original id of each operation"]
    assert client.get("/ops/advice/latest").json()[q["question_id"]]["advice_id"] == advice["advice_id"]
    assert client.get(f"/ops/questions/{q['question_id']}").json()["status"] == "open"     # never decides
    assert p.hitl.decisions.all() == {}
    assert p.deps.llm.totals(q["run_id"]).calls == 3
    assert [c["purpose"] for c in llm.calls if c["run_id"] == q["run_id"]] == ["question_advisor"] * 3


def test_advise_refusals(env):
    _, _, client = env
    assert client.post("/ops/questions/nope/advise", json={}).status_code == 404
    q = client.get("/ops/questions", params={"status": "open"}).json()[0]
    client.post(f"/ops/questions/{q['question_id']}/answer", json={"option": "reject_group", "operator": "a"})
    assert client.post(f"/ops/questions/{q['question_id']}/advise", json={}).status_code == 409
