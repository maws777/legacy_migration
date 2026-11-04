from datetime import datetime

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from target.schema import create_target_schema, make_engine, sqlite_url

pytestmark = pytest.mark.integration

VIN = "VF1AB000000000000001"


@pytest.fixture
def db(tmp_path, cfg):
    engine = make_engine(sqlite_url(tmp_path / "target.db"))
    md = create_target_schema(engine, cfg)
    return engine, md


def _seed(conn, md, vin=VIN):
    t = md.tables
    conn.execute(insert(t["vehicle"]).values(vin=vin, vehicle_model="CLIO"))
    conn.execute(insert(t["diagnozed_ecu"]).values(ecu_name="BCM"))
    conn.execute(insert(t["vehicle_ecu"]).values(vin=vin, ecu_name="BCM"))
    conn.execute(insert(t["diag_tools_versions"]).values(diag_tool_name="DDT2000", diag_tool_version="v1"))
    conn.execute(insert(t["diagnostic_operation"]).values(
        diagnostic_op_id="OP1", creation_date=datetime(2024, 3, 15, 10, 30), vin=vin, ecu_name="BCM",
        diag_tool_name="DDT2000", diag_tool_version="v1", source_origin_tool="DiagTool",
        ingestion_ts=datetime(2026, 1, 1), run_id="r" * 36, source_row_id=1, op_id_generated=False))
    conn.execute(insert(t["diag_value_result"]).values(
        diagnostic_op_id="OP1", value_reference="AB12345678", value_description="ID0002"))


def _refused(engine, stmt, expected: str):
    with pytest.raises(IntegrityError) as exc:
        with engine.begin() as conn:
            conn.execute(stmt)
    assert expected in str(exc.value.orig)


def test_valid_rows_are_accepted_and_pragmas_set(db):
    engine, md = db
    with engine.begin() as conn:
        _seed(conn, md)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(text("SELECT count(*) FROM diag_value_result")).scalar() == 1


@pytest.mark.parametrize("table,values,constraint", [
    ("vehicle", {"vin": "VF1AB00000000000000", "vehicle_model": "CLIO"}, "ck_vehicle_vin_format"),
    ("vehicle", {"vin": "vf1ab000000000000009", "vehicle_model": "CLIO"}, "ck_vehicle_vin_format"),
    ("vehicle", {"vin": "VF1AB00000000000000-", "vehicle_model": "CLIO"}, "ck_vehicle_vin_format"),
    ("vehicle", {"vin": "VF1AB000000000000009", "vehicle_model": "AR KANA"}, "ck_vehicle_model_dico"),
    ("diagnozed_ecu", {"ecu_name": "T CM"}, "ck_ecu_name_dico"),
    ("diag_tools_versions", {"diag_tool_name": "DiagTool", "diag_tool_version": "v1"}, "ck_tool_name_dico"),
])
def test_named_check_constraints(db, table, values, constraint):
    engine, md = db
    _refused(engine, insert(md.tables[table]).values(**values), constraint)


@pytest.mark.parametrize("values,constraint", [
    ({"value_reference": "AB12345678", "value_description": "ID0006"}, "ck_value_description_dico"),
    ({"value_reference": "AB1234567", "value_description": "ID0003"}, "ck_value_reference_format"),
    ({"value_reference": "AB12/45678", "value_description": "ID0004"}, "ck_value_reference_format"),
    ({"value_reference": "AB12345678\n", "value_description": "ID0005"}, "ck_value_reference_format"),
])
def test_value_result_checks(db, values, constraint):
    engine, md = db
    with engine.begin() as conn:
        _seed(conn, md)
        conn.execute(text("DELETE FROM diag_value_result"))
    _refused(engine, insert(md.tables["diag_value_result"]).values(diagnostic_op_id="OP1", **values), constraint)


def test_id0001_value_reference_is_free(db):
    engine, md = db
    with engine.begin() as conn:
        _seed(conn, md)
        conn.execute(text("DELETE FROM diag_value_result"))
        conn.execute(insert(md.tables["diag_value_result"]).values(
            diagnostic_op_id="OP1", value_reference=VIN, value_description="ID0001"))


def test_foreign_keys_enforced(db):
    engine, md = db
    _refused(engine, insert(md.tables["vehicle_ecu"]).values(vin=VIN, ecu_name="BCM"), "FOREIGN KEY")


def test_second_vehicle_and_second_result_refused(db):
    engine, md = db
    with engine.begin() as conn:
        _seed(conn, md)
    _refused(engine, insert(md.tables["vehicle"]).values(vin=VIN, vehicle_model="MASTER"), "UNIQUE")
    _refused(engine, insert(md.tables["diag_value_result"]).values(
        diagnostic_op_id="OP1", value_reference="CD12345678", value_description="ID0003"), "UNIQUE")


def test_vehicle_without_model_refused(db):
    engine, md = db
    _refused(engine, insert(md.tables["vehicle"]).values(vin=VIN, vehicle_model=None), "NOT NULL")


def test_in_memory_engine_has_foreign_keys(cfg):
    engine = make_engine("sqlite:///:memory:")
    create_target_schema(engine, cfg)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


# ---------------------------------------------------------------- Target_Repository (task 5.1)
from dataclasses import replace as _replace  # noqa: E402

from rules.types import TargetValues  # noqa: E402
from target.repository import LoadRow, TargetRepository, constraint_of  # noqa: E402


def _tv(n: int, **kw) -> TargetValues:
    base = dict(vin=f"VF1AB{n:015d}", vehicle_model="CLIO", ecu_name="BCM", diag_tool_name="DDT2000",
                diag_tool_version="v1", diagnostic_op_id=f"OP{n}", op_id_generated=False,
                creation_date=datetime(2024, 3, 15, 10, 30), value_description="ID0002",
                value_reference="AB12345678", source_origin_tool="DiagTool")
    base.update(kw)
    return TargetValues(**base)


def _rows(*values: TargetValues) -> list[LoadRow]:
    return [LoadRow(i, v, "run-1", datetime(2026, 1, 1)) for i, v in enumerate(values, start=1)]


def test_batch_upsert_is_idempotent(db):
    engine, md = db
    repo = TargetRepository(engine, md)
    rows = _rows(_tv(1), _tv(2), _tv(2, diagnostic_op_id="OP2b", ecu_name="ABS"), _tv(3))
    assert repo.load_batch(rows).failed == ()
    first = repo.table_counts()
    res = repo.load_batch(rows)
    assert not res.retried and repo.table_counts() == first
    assert first == {"vehicle": 3, "diagnozed_ecu": 2, "vehicle_ecu": 4, "diag_tools_versions": 1,
                     "diagnostic_operation": 4, "diag_value_result": 4}


@pytest.mark.parametrize("bad,constraint", [
    ({"vehicle_model": "AR KANA"}, "ck_vehicle_model_dico"),
    ({"ecu_name": "T CM"}, "ck_ecu_name_dico"),
    ({"diag_tool_name": "DiagTool"}, "ck_tool_name_dico"),
    ({"value_description": "ID0007"}, "ck_value_description_dico"),
    ({"value_reference": "SHORT"}, "ck_value_reference_format"),
    ({"vin": "BAD"}, "ck_vehicle_vin_format"),
])
def test_failed_batch_is_retried_row_by_row(db, bad, constraint):
    engine, md = db
    repo = TargetRepository(engine, md)
    res = repo.load_batch(_rows(_tv(1), _tv(2, **bad), _tv(3)))
    assert res.retried and res.loaded == (1, 3)
    assert [(f.source_row_id, f.constraint) for f in res.failed] == [(2, constraint)]
    counts = repo.table_counts()
    assert counts["diagnostic_operation"] == 2 and counts["diag_value_result"] == 2


def test_diagnostics_by_vin(db):
    engine, md = db
    repo = TargetRepository(engine, md)
    repo.load_batch(_rows(_tv(1), _tv(1, diagnostic_op_id="OP1b", value_description="ID0001",
                                     value_reference="VF1AB000000000000001")))
    items = repo.diagnostics_by_vin("VF1AB000000000000001")
    assert [i.diagnostic_op_id for i in items] == ["OP1", "OP1b"]
    assert items[0].creation_date == "2024-03-15T10:30" and items[0].vehicle_model == "CLIO"
    assert repo.diagnostics_by_vin("NOPE") == []


def test_constraint_names_from_messages():
    class E(Exception):
        pass
    assert constraint_of(E("FOREIGN KEY constraint failed")) == "FOREIGN KEY"
    assert constraint_of(E("UNIQUE constraint failed: vehicle.vin")) == "UNIQUE vehicle.vin"
    assert constraint_of(E("NOT NULL constraint failed: vehicle.vehicle_model")) == "NOT NULL vehicle.vehicle_model"
