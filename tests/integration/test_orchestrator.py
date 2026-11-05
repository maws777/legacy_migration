import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.api_agent_legacy import LegacyAClient
from agents.api_agent_legacy_b import ApiAgentLegacyB
from agents.orchestrator import build_services, create_app
from agents.schemas import GateCheck
from legacy_a.api import create_app as legacy_a_app
from legacy_a.store import LegacyAStore
from legacy_b.api import create_app as legacy_b_app
from legacy_b.schemas import LegacyBRecord
from legacy_b.store import LegacyBStore
from migration.pipeline import MigrationPipeline
from target.api import create_app as target_app
from tests.fixtures.make_csv import anomaly_rows, vin, write_csv

pytestmark = pytest.mark.integration
PASS = [GateCheck(name="open_questions", passed=True, count=0, message="ok"),
        GateCheck(name="control_sample_gaps", passed=True, count=0, message="ok")]


class Down(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("down", request=request)


@pytest.fixture
def env(tmp_path, cfg):
    src = write_csv(tmp_path / "a.csv", anomaly_rows())
    p = MigrationPipeline(src, var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    p.start_campaign()
    la = TestClient(legacy_a_app(LegacyAStore.from_path(src)))
    lb = TestClient(legacy_b_app(LegacyBStore([LegacyBRecord(CHASSIS_NO=vin(1), MODELE="Clio", DATE_MAJ="20240101")])))
    tgt = TestClient(target_app(p.target_repo))
    s = build_services(p, legacy_a=LegacyAClient(la), legacy_b=ApiAgentLegacyB(lb, cfg), target=tgt)
    s.phase.gate = lambda: PASS
    yield s, TestClient(create_app(s))
    p.close()


def to_phase(s, n):
    while s.phase.current() < n:
        nxt = s.phase.current() + 1
        r = s.phase.request(nxt, "op")
        if r.status == "pending_approval":
            s.phase.approve("lead")


def test_same_response_in_every_phase(env):
    s, client = env
    bodies, misses, served = [], [], []
    for phase in (1, 2, 3, 4):
        to_phase(s, phase)
        r = client.get(f"/api/vehicles/{vin(1).lower()}/diagnostics")
        assert r.status_code == 200
        served.append(r.headers["X-Served-By"])
        bodies.append(r.json())
        m = client.get("/api/vehicles/VF1AB999999999999999/diagnostics")
        misses.append((m.status_code, m.json()["error"]["code"], m.headers["X-Served-By"]))
    assert all(b == bodies[0] for b in bodies) and bodies[0]["operations"][0]["creation_date"] == "2024-03-15T10:30"
    assert served == ["legacy_a", "legacy_a", "legacy_a", "target"]
    assert set(misses) == {(404, "NOT_FOUND", "legacy_a"), (404, "NOT_FOUND", "target")}
    assert s.phase.served["target"] == 2 and s.phase.served["legacy_a"] == 6


def test_phase4_sends_zero_legacy_a_requests(env):
    s, client = env
    to_phase(s, 4)
    s.legacy_a = LegacyAClient(httpx.Client(transport=Down(), base_url="http://legacy-a"))
    assert client.get(f"/api/vehicles/{vin(1)}/diagnostics").status_code == 200
    s.phase.legacy_a_reachable = lambda: True
    s.phase.rollback(3, "ops")
    r = client.get(f"/api/vehicles/{vin(1)}/diagnostics")
    assert r.status_code == 503 and r.json()["error"]["code"] == "BACKEND_UNAVAILABLE"


def test_backend_down_gives_503_without_fallback(env):
    s, client = env
    s.legacy_a = LegacyAClient(httpx.Client(transport=Down(), base_url="http://legacy-a"))
    assert client.get(f"/api/vehicles/{vin(1)}/diagnostics").status_code == 503
    assert s.phase.served["target"] == 0
    to_phase(s, 4)
    s.target = httpx.Client(transport=Down(), base_url="http://target")
    assert client.get(f"/api/vehicles/{vin(1)}/diagnostics").json()["error"]["code"] == "BACKEND_UNAVAILABLE"


def test_model_reference_route(env):
    s, client = env
    r = client.get(f"/api/vehicles/{vin(1)}/model")
    assert r.status_code == 200 and r.json()["vehicle_model"] == "CLIO" and r.json()["updated_on"] == "2024-01-01"
    assert client.get(f"/api/vehicles/{vin(2)}/model").status_code == 404
    to_phase(s, 4)
    assert client.get(f"/api/vehicles/{vin(1)}/model").json()["vehicle_model"] == "CLIO"


def test_in_flight_request_finishes_on_its_backend(env):
    s, client = env
    real = s.legacy_a

    class Slow:
        def rows_by_vin(self, v):
            to_phase(s, 4)                    # the switch happens while the request is in flight
            return real.rows_by_vin(v)

        def is_reachable(self):
            return True

    s.legacy_a = Slow()
    r = client.get(f"/api/vehicles/{vin(1)}/diagnostics")
    assert r.status_code == 200 and s.phase.current() == 4 and s.phase.served["legacy_a"] == 1
    s.legacy_a = real


def test_only_200_or_404_during_moves_and_rollbacks(env):
    s, client = env
    statuses, stop = [], threading.Event()

    def loop():
        while not stop.is_set():
            statuses.append(client.get(f"/api/vehicles/{vin(1)}/diagnostics").status_code)
            statuses.append(client.get("/api/vehicles/NOPE/diagnostics").status_code)

    t = threading.Thread(target=loop)
    t.start()
    for _ in range(2):
        to_phase(s, 4)
        s.phase.rollback(3, "ops")
        s.phase.rollback(2, "ops")
    stop.set()
    t.join()
    assert statuses and set(statuses) <= {200, 404}


def test_phase_ops_routes(env):
    s, client = env
    s.phase.gate = lambda: [PASS[0], GateCheck(name="control_sample_gaps", passed=False, count=None,
                                               message="no reconciliation available")]
    assert client.get("/ops/phase").json()["current_phase"] == 1
    assert client.post("/ops/phase/transition", json={"target_phase": 3}).json()["status"] == "refused"
    assert client.post("/ops/phase/transition", json={"target_phase": 2}).json()["status"] == "switched"
    assert client.post("/ops/phase/transition", json={"target_phase": 3}).json()["status"] == "switched"
    r = client.post("/ops/phase/transition", json={"target_phase": 4}).json()
    assert r["status"] == "refused" and r["checks"][1]["message"] == "no reconciliation available"
    s.phase.gate = lambda: PASS
    r = client.post("/ops/phase/transition", json={"target_phase": 4}).json()
    assert r["status"] == "pending_approval" and r["summary"]["analysed"] == len(anomaly_rows())
    assert client.post("/ops/phase/approve", json={"operator": "lead"}).json()["current_phase"] == 4
    assert client.post("/ops/phase/rollback", json={"target_phase": 3, "operator": ""}).status_code == 422
    assert client.post("/ops/phase/rollback", json={"target_phase": 3, "operator": "ops"}).json()["status"] == "switched"
    assert client.post("/ops/phase/reject", json={"operator": "lead"}).json()["status"] == "refused"
    assert [e["rollback"] for e in client.get("/ops/phase/transitions").json()] == [False, False, False, True]
