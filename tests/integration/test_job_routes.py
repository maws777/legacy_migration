"""Background launch routes: the request returns at once, the UI polls the job."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import Services, create_app
from migration.pipeline import MigrationPipeline
from tests.fixtures.make_csv import anomaly_rows, write_csv

pytestmark = pytest.mark.integration


@pytest.fixture
def env(tmp_path):
    p = MigrationPipeline(write_csv(tmp_path / "s.csv", anomaly_rows()), var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    s = Services(pipeline=p)
    yield s, TestClient(create_app(s))
    p.close()


def poll(client, job_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/ops/jobs/{job_id}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_campaign_job_answers_at_once_and_refuses_a_double_launch(env, monkeypatch):
    s, client = env
    gate = threading.Event()
    real = s.pipeline.start_campaign

    def slow(operator=None):
        gate.wait(10)
        return real(operator)

    monkeypatch.setattr(s.pipeline, "start_campaign", slow)
    t0 = time.monotonic()
    r = client.post("/ops/jobs/campaign", json={"operator": "ann"})
    assert r.status_code == 202 and time.monotonic() - t0 < 2 and r.json()["status"] in ("queued", "running")
    job_id = r.json()["job_id"]
    again = client.post("/ops/jobs/campaign", json={"operator": "bob"})
    assert again.status_code == 409 and again.json()["error"]["details"]["job"]["job_id"] == job_id
    assert [j["job_id"] for j in client.get("/ops/jobs", params={"active": True}).json()] == [job_id]
    gate.set()
    job = poll(client, job_id)
    assert job["status"] == "succeeded" and job["result"]["campaign_status"] == "paused"
    assert job["result"]["open_questions"] == 6 and job["operator"] == "ann"
    assert client.get("/ops/jobs", params={"active": True}).json() == []


def test_resume_job_and_refusals(env):
    s, client = env
    first = poll(client, client.post("/ops/jobs/campaign", json={}).json()["job_id"])
    cid = first["result"]["campaign_id"]
    assert client.post("/ops/jobs/resume/unknown", json={"operator": "a"}).status_code == 409
    assert client.post(f"/ops/jobs/resume/{cid}", json={"operator": ""}).status_code == 422
    job = poll(client, client.post(f"/ops/jobs/resume/{cid}", json={"operator": "a"}).json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["run_id"] != first["result"]["run_id"]
    assert client.get("/ops/jobs/nope").status_code == 404


def test_schema_discover_job(env):
    _, client = env
    job = poll(client, client.post("/ops/jobs/schema-discover", params={"source": "reference"}).json()["job_id"])
    assert job["kind"] == "schema_discover" and job["result"]["fallback"] is True
    assert job["result"]["differences_from_reference"] == []
