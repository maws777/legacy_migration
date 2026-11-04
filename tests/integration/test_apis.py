import pytest
from fastapi.testclient import TestClient

from legacy_a.api import create_app as create_legacy_a
from legacy_a.store import LegacyAStore
from tests.fixtures.make_csv import base_row, vin, write_csv

pytestmark = pytest.mark.integration


@pytest.fixture
def legacy_a(tmp_path):
    rows = [base_row(i) for i in range(1, 8)] + [base_row(99, VIN=" vf1-ab" + vin(3)[5:].lower())]
    store = LegacyAStore.from_path(write_csv(tmp_path / "a.csv", rows))
    return TestClient(create_legacy_a(store))


def test_legacy_a_paging_covers_every_row_once(legacy_a):
    seen = []
    for page in range(1, 5):
        r = legacy_a.get("/legacy-a/rows", params={"page": page, "page_size": 3})
        assert r.status_code == 200
        body = r.json()
        assert body["total_rows"] == 8
        seen += [row["source_row_id"] for row in body["rows"]]
    assert seen == list(range(1, 9))


def test_legacy_a_default_page_size_and_past_last_page(legacy_a):
    assert legacy_a.get("/legacy-a/rows").json()["page_size"] == 100
    r = legacy_a.get("/legacy-a/rows", params={"page": 50, "page_size": 10})
    assert r.status_code == 200 and r.json()["rows"] == []


@pytest.mark.parametrize("params,name", [
    ({"page": 0}, "page"), ({"page": "x"}, "page"), ({"page_size": 0}, "page_size"),
    ({"page_size": 1001}, "page_size"), ({"page_size": 1.5}, "page_size"),
])
def test_legacy_a_validation_errors_name_the_parameter(legacy_a, params, name):
    r = legacy_a.get("/legacy-a/rows", params=params)
    assert r.status_code == 422
    assert any(name in e["loc"] for e in r.json()["detail"])


def test_legacy_a_rows_by_normalized_vin(legacy_a):
    r = legacy_a.get(f"/legacy-a/vehicles/{vin(3).lower()}/rows")
    body = r.json()
    assert r.status_code == 200 and body["vin"] == vin(3)
    assert [x["source_row_id"] for x in body["rows"]] == [3, 8]
    assert body["rows"][1]["values"]["VIN"].startswith(" vf1-ab")       # raw value unchanged
    assert legacy_a.get("/legacy-a/vehicles/UNKNOWN/rows").json()["rows"] == []


def test_legacy_a_header_health_and_405(legacy_a):
    assert legacy_a.get("/legacy-a/header").json()["columns"][2] == "VIN"
    assert legacy_a.get("/legacy-a/health").json() == {"status": "ok"}
    for method in ("post", "put", "delete", "patch"):
        assert getattr(legacy_a, method)("/legacy-a/rows").status_code == 405


# ---------------------------------------------------------------- Legacy_B_API (task 8)
from legacy_b.api import create_app as create_legacy_b  # noqa: E402
from legacy_b.schemas import LegacyBRecord  # noqa: E402
from legacy_b.store import LegacyBStore  # noqa: E402


@pytest.fixture
def legacy_b():
    store = LegacyBStore([LegacyBRecord(CHASSIS_NO=vin(1), MODELE="Clio", DATE_MAJ="20240101"),
                          LegacyBRecord(CHASSIS_NO=vin(2), MODELE="Megane E-Tech", DATE_MAJ="20231231")])
    return TestClient(create_legacy_b(store))


def test_legacy_b_one_vin_and_404(legacy_b):
    r = legacy_b.get(f"/legacy-b/vehicles/{vin(2)}")
    assert r.status_code == 200 and r.json() == {"CHASSIS_NO": vin(2), "MODELE": "Megane E-Tech", "DATE_MAJ": "20231231"}
    miss = legacy_b.get(f"/legacy-b/vehicles/{vin(9)}")
    assert miss.status_code == 404 and "CHASSIS_NO" not in miss.text and miss.json()["error"]["code"] == "NOT_FOUND"
    assert legacy_b.get(f"/legacy-b/vehicles/{vin(2)}").json() == r.json()       # same answer every time


def test_legacy_b_bulk_and_limits(legacy_b):
    r = legacy_b.get("/legacy-b/vehicles", params=[("chassis_no", vin(1)), ("chassis_no", vin(9))])
    assert r.status_code == 200 and [x["CHASSIS_NO"] for x in r.json()["records"]] == [vin(1)]
    assert r.json()["missing"] == [vin(9)]
    assert legacy_b.get("/legacy-b/vehicles").status_code == 422
    too_many = [("chassis_no", vin(i)) for i in range(201)]
    assert legacy_b.get("/legacy-b/vehicles", params=too_many).status_code == 422


def test_legacy_b_get_only(legacy_b):
    for method in ("post", "put", "delete", "patch"):
        assert getattr(legacy_b, method)(f"/legacy-b/vehicles/{vin(1)}").status_code == 405
    assert legacy_b.get(f"/legacy-b/vehicles/{vin(1)}").status_code == 200
    assert legacy_b.get("/legacy-b/health").json() == {"status": "ok"}


def test_api_agent_legacy_b_index_and_reference(legacy_b, cfg):
    from agents.api_agent_legacy_b import ApiAgentLegacyB
    agent = ApiAgentLegacyB(legacy_b, cfg)
    res = agent.model_index([vin(1), vin(2).lower(), vin(9)])
    assert res.status == "ok" and res.index == {vin(1): "CLIO", vin(2): "MEGANE E-TECH"}
    assert agent.model_reference(vin(9)) is None
    assert agent.model_reference(vin(2)).updated_on == "2023-12-31"


# ---------------------------------------------------------------- Target_API (task 9)
def test_target_api_routes(tmp_path):
    from migration.pipeline import MigrationPipeline
    from target.api import create_app as create_target
    from target.repository import TargetRepository
    from target.schema import make_engine, sqlite_url
    src = write_csv(tmp_path / "s.csv", [base_row(1), base_row(2)])
    p = MigrationPipeline(src, var_dir=tmp_path / "var")
    p.seed_reference_mapping()
    p.start_campaign()
    p.close()
    repo = TargetRepository(make_engine(sqlite_url(tmp_path / "var" / "target.db"), read_only=True))
    client = TestClient(create_target(repo))
    r = client.get(f"/target/vehicles/{vin(1).lower()}/diagnostics")
    assert r.status_code == 200 and r.json()["vin"] == vin(1) and len(r.json()["operations"]) == 1
    miss = client.get("/target/vehicles/NOPE/diagnostics")
    assert miss.status_code == 404 and miss.json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/target/stats").json()["tables"]["vehicle"] == 2
    assert client.get("/target/health").json() == {"status": "ok"}
    assert client.post("/target/stats").status_code == 405
    import sqlalchemy
    with pytest.raises(sqlalchemy.exc.OperationalError):          # read-only connection
        with repo.engine.begin() as c:
            c.exec_driver_sql("DELETE FROM vehicle")
