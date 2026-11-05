"""Query_Agent (task 14.2) with scripted LLM answers: SQL checks, read-only execution, error codes."""
import json
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import insert

from agents.llm_client import LLMFailure
from agents.query_agent import QueryAgent, extract_sql, run_readonly, validate_sql
from target.schema import build_metadata, make_engine, sqlite_url
from tests.fixtures.fake_llm import FakeLLM

TABLES = ("vehicle", "diagnozed_ecu", "vehicle_ecu", "diag_tools_versions", "diagnostic_operation",
          "diag_value_result")
VINS = [f"VF1AB000000000000{i:03d}" for i in range(3)]


@pytest.fixture
def db(tmp_path, cfg):
    path = tmp_path / "target.db"
    engine = make_engine(sqlite_url(path))
    md = build_metadata(cfg)
    md.create_all(engine)
    with engine.begin() as c:
        c.execute(insert(md.tables["vehicle"]), [{"vin": v, "vehicle_model": m}
                                                 for v, m in zip(VINS, ["CLIO", "CLIO", "MASTER"])])
        c.execute(insert(md.tables["diagnozed_ecu"]), [{"ecu_name": "ABS"}])
        c.execute(insert(md.tables["vehicle_ecu"]), [{"vin": VINS[0], "ecu_name": "ABS"}])
        c.execute(insert(md.tables["diag_tools_versions"]), [{"diag_tool_name": "Clip", "diag_tool_version": "1"}])
        c.execute(insert(md.tables["diagnostic_operation"]), [{
            "diagnostic_op_id": "op1", "creation_date": datetime(2026, 1, 2, 3, 4), "vin": VINS[0], "ecu_name": "ABS",
            "diag_tool_name": "Clip", "diag_tool_version": "1", "source_origin_tool": "AfterSalesDiag",
            "ingestion_ts": datetime(2026, 11, 4), "run_id": "r1", "source_row_id": 1, "op_id_generated": False}])
    engine.dispose()
    return path


def sql_llm(sql, **kw):
    return FakeLLM(lambda body: json.dumps({"sql": sql}), **kw)


def with_query(cfg, **update):
    q = cfg.pipeline.query_agent.model_copy(update=update)
    return cfg.model_copy(update={"pipeline": cfg.pipeline.model_copy(update={"query_agent": q})})


@pytest.mark.parametrize("sql", [
    "SELECT vehicle_model, COUNT(*) FROM vehicle GROUP BY vehicle_model",
    "SELECT o.vin, v.vehicle_model FROM diagnostic_operation o JOIN vehicle v ON v.vin = o.vin",
    "WITH c AS (SELECT vin FROM vehicle) SELECT * FROM c",
    "SELECT * FROM vehicle WHERE vin IN (SELECT vin FROM vehicle_ecu)",
    "SELECT * FROM main.vehicle",
    "SELECT vin FROM vehicle UNION SELECT vin FROM vehicle_ecu",
])
def test_select_statements_are_accepted(sql):
    assert validate_sql(sql, TABLES)[:2] == (None, None)


@pytest.mark.parametrize("sql, check", [
    ("DELETE FROM vehicle", "select_only"),
    ("DROP TABLE vehicle", "select_only"),
    ("INSERT INTO vehicle VALUES ('x', 'CLIO')", "select_only"),
    ("UPDATE vehicle SET vehicle_model = 'CLIO'", "select_only"),
    ("PRAGMA table_info(vehicle)", "select_only"),
    ("ATTACH DATABASE 'x.db' AS y", "select_only"),
    ("SELECT 1; DROP TABLE vehicle", "statement_count"),
    ("SELECT * FROM sqlite_master", "allow_list"),
    ("SELECT * FROM llm_call", "allow_list"),
    ("SELECT * FROM other.vehicle", "allow_list"),
    ("SELEC * FROM vehicle", "parse"),
])
def test_unsafe_statements_are_refused_with_the_check_name(sql, check):
    assert validate_sql(sql, TABLES)[0] == check


def test_extract_sql_from_json_fence_or_raw_text():
    assert extract_sql('{"sql": " SELECT 1 "}') == "SELECT 1"
    assert extract_sql("Here:\n```sql\nSELECT vin FROM vehicle\n```") == "SELECT vin FROM vehicle"
    assert extract_sql("WITH c AS (SELECT 1) SELECT * FROM c") == "WITH c AS (SELECT 1) SELECT * FROM c"
    assert extract_sql("I cannot answer that.") is None


def test_answer_runs_read_only_with_tokens(cfg, db):
    llm = sql_llm("SELECT vehicle_model, COUNT(*) AS n FROM vehicle GROUP BY vehicle_model ORDER BY n DESC")
    r = QueryAgent(llm, cfg, db).ask("How many vehicles per model?")
    assert r.error is None and r.columns == ["vehicle_model", "n"]
    assert r.rows == [{"vehicle_model": "CLIO", "n": 2}, {"vehicle_model": "MASTER", "n": 1}]
    assert (r.input_tokens, r.output_tokens, r.truncated) == (100, 20, False)
    assert [(c["run_id"], c["purpose"]) for c in llm.calls] == [("chat", "query_agent")]
    assert "vehicle" in json.loads(llm.calls[0]["prompt"])["schema"]["tables"]


def test_row_limit_truncates(cfg, db):
    r = QueryAgent(sql_llm("SELECT vin FROM vehicle ORDER BY vin"), with_query(cfg, row_limit=2), db).ask("vins")
    assert [x["vin"] for x in r.rows] == VINS[:2] and r.truncated


def test_refused_sql_is_never_executed(cfg, db):
    r = QueryAgent(sql_llm("DELETE FROM vehicle"), cfg, db).ask("remove every vehicle")
    assert r.error.code == "REFUSED" and r.error.details == {"step": "validate_sql", "check": "select_only"}
    assert r.error.message.startswith("validate_sql: ") and r.rows == [] and r.sql == "DELETE FROM vehicle"
    assert r.input_tokens == 100                                     # the generation is still counted
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM vehicle").fetchone()[0] == 3


def test_connection_is_read_only_even_without_the_validator(db):
    with pytest.raises(sqlite3.DatabaseError):
        run_readonly(db, "DELETE FROM vehicle", TABLES, 10)
    with pytest.raises(sqlite3.DatabaseError):                       # authorizer: table not allow-listed
        run_readonly(db, "SELECT * FROM vehicle", ("diagnozed_ecu",), 10)
    with pytest.raises(sqlite3.OperationalError):                    # progress-handler timeout
        run_readonly(db, "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
                     TABLES, 10, timeout_s=0.2)


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_question_length_is_checked_before_any_call(cfg, db, question):
    llm = sql_llm("SELECT 1")
    r = QueryAgent(llm, cfg, db).ask(question)
    assert r.error.code == "VALIDATION_ERROR" and r.error.details["step"] == "check_input" and llm.calls == []


def test_degraded_mode_and_llm_failure(cfg, db):
    llm = sql_llm("SELECT 1", available=False)
    r = QueryAgent(llm, cfg, db).ask("models?")
    assert r.error.code == "BACKEND_UNAVAILABLE" and "Degraded_Mode" in r.error.message and llm.calls == []
    failing = FakeLLM(lambda body: LLMFailure("timeout", "ReadTimeout"))
    r = QueryAgent(failing, cfg, db).ask("models?")
    assert r.error.code == "BACKEND_UNAVAILABLE" and r.error.details["step"] == "generate_sql"
    r = QueryAgent(FakeLLM(lambda body: "no idea"), cfg, db).ask("models?")
    assert r.error.code == "REFUSED" and r.error.details["step"] == "generate_sql"


def test_database_error_is_internal(cfg, db):
    r = QueryAgent(sql_llm("SELECT no_such_column FROM vehicle"), cfg, db).ask("?")
    assert r.error.code == "INTERNAL" and r.error.details["step"] == "execute" and r.rows == []


def test_disabled_chat_is_refused_without_a_call(cfg, db):
    llm = sql_llm("SELECT 1")
    r = QueryAgent(llm, with_query(cfg, enabled=False), db).ask("models?")
    assert r.error.code == "REFUSED" and "disabled" in r.error.message and llm.calls == []


def test_legacy_b_consumer_is_metered_under_its_own_run_id(cfg, db):
    llm = sql_llm("SELECT vin, vehicle_model FROM vehicle")
    r = QueryAgent(llm, cfg, db).ask("models of my VINs", consumer="legacy_b")
    assert r.error is None and len(r.rows) == 3
    assert [(c["run_id"], c["purpose"]) for c in llm.calls] == [("legacy-b", "query_agent")]
