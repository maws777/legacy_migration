"""POST /ops/chat on a migrated Target_DB: answers, refusals, consumers and metering in the cost."""
import json

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import Services, create_app
from migration.pipeline import MigrationPipeline
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.make_csv import anomaly_rows, write_csv

pytestmark = pytest.mark.integration


def answer(body):
    if "schema" not in body or "question" not in body:          # not a Query_Agent prompt
        return "{}"
    if "drop" in body["question"].lower():
        return json.dumps({"sql": "DROP TABLE vehicle"})
    return json.dumps({"sql": "SELECT vehicle_model, COUNT(*) AS n FROM vehicle GROUP BY vehicle_model"})


@pytest.fixture
def env(tmp_path):
    llm = FakeLLM(answer)
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", anomaly_rows()), var_dir=tmp_path / "var", llm=llm)
    p.seed_reference_mapping()
    p.start_campaign("op")
    yield p, llm, TestClient(create_app(Services(pipeline=p)))
    p.close()


def test_chat_answers_from_the_target_db(env):
    p, llm, client = env
    r = client.post("/ops/chat", json={"question": "How many vehicles per model?"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None and body["columns"] == ["vehicle_model", "n"]
    assert sum(row["n"] for row in body["rows"]) == client.get("/ops/target/stats").json()["tables"]["vehicle"] > 0
    refused = client.post("/ops/chat", json={"question": "drop the vehicles"}).json()
    assert refused["error"]["code"] == "REFUSED" and refused["rows"] == []
    assert client.get("/ops/target/stats").json()["tables"]["vehicle"] > 0
    assert client.post("/ops/chat", json={"question": "x", "consumer": "someone"}).status_code == 422


def test_legacy_b_consumer_and_cost(env):
    p, llm, client = env
    run_id = p.runs.latest_full_run()["run_id"]
    before = client.get(f"/ops/runs/{run_id}/cost").json()["tokens"]
    client.post("/ops/chat", json={"question": "models?"})
    client.post("/ops/chat", json={"question": "models?", "consumer": "legacy_b"})
    assert [c["run_id"] for c in llm.calls if c["purpose"] == "query_agent"] == ["chat", "legacy-b"]
    cost = client.get(f"/ops/runs/{run_id}/cost").json()
    by = {u["purpose"]: u for u in cost["by_purpose"]}
    assert by["query_agent"]["calls"] == 2 and cost["tokens"] == before + 240
    report = client.get(f"/ops/runs/{run_id}/report").json()
    assert report["tokens_by_purpose"]["query_agent"] == 240 and "chat (Query_Agent) 240" in report["text"]
