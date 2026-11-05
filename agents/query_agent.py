"""Query_Agent (Requirement 33, task 14.2): natural-language question -> one read-only SQL SELECT.

LangGraph graph of four steps: check_input -> generate_sql (LLM_Client) -> validate_sql (sqlglot) ->
execute (read-only SQLite connection with an authorizer). Any failed step ends the graph with an error
naming the step. The SQL is never executed unless it is exactly one SELECT over allow-listed tables.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

import sqlglot
from langgraph.graph import END, START, StateGraph
from sqlglot import exp

from agents.resolver_agent import first_json
from agents.schemas import ChatResponse
from rules.config import RuleConfig
from rules.errors import ErrorBody

MAX_QUESTION = 1000
EXEC_TIMEOUT_S = 10.0
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Pragma,
                   exp.Merge, exp.TruncateTable)
SET_OPERATIONS = (exp.Union, exp.Intersect, exp.Except)
_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)

SYSTEM = (
    "You translate one question about a vehicle-diagnostics database into ONE SQLite SELECT statement.\n"
    'Reply with JSON only: {"sql": "<one SELECT statement>"}.\n'
    "Use only the tables and columns listed in \"schema\". Never write INSERT, UPDATE, DELETE, DROP, PRAGMA or "
    "ATTACH. Prefer explicit column names, add ORDER BY when the order matters, and LIMIT for top-N questions."
)


def schema_description(cfg: RuleConfig) -> dict:
    """Tables, columns and closed values of the Target_DB, as given to the model."""
    from target.schema import build_metadata
    md = build_metadata(cfg)
    allowed = set(cfg.pipeline.query_agent.allowed_tables)
    tables = {}
    for name, t in md.tables.items():
        if name not in allowed:
            continue
        cols = [f"{c.name} {c.type}" for c in t.columns]
        fks = [f"({', '.join(fk.column_keys)}) -> {fk.referred_table.name}" for fk in t.foreign_key_constraints]
        tables[name] = {"columns": cols, **({"foreign_keys": fks} if fks else {})}
    return {"tables": tables,
            "values": {"vehicle.vehicle_model": list(cfg.vehicle_models), "diagnozed_ecu.ecu_name": list(cfg.ecus),
                       "diag_tools_versions.diag_tool_name": list(cfg.tools),
                       "diag_value_result.value_description": dict(cfg.dictionaries.value_descriptions)},
            "notes": "diagnostic_operation.creation_date is a datetime 'YYYY-MM-DD HH:MM:SS'; one diag_value_result "
                     "per diagnostic_operation; join vehicle_ecu, vehicle, diagnozed_ecu on vin and ecu_name."}


def validate_sql(sql: str, allowed_tables) -> tuple[str | None, str | None, list[str]]:
    """(failed check, reason, tables). Checks: parse, one statement, SELECT only, allow-listed tables."""
    allowed = {t.lower() for t in allowed_tables}
    try:
        statements = [s for s in sqlglot.parse(sql, read="sqlite") if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        return "parse", f"the SQL cannot be parsed: {str(exc).splitlines()[0]}", []
    if not statements:
        return "statement_count", "no SQL statement", []
    if len(statements) > 1:
        return "statement_count", f"more than one statement ({len(statements)})", []
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, *SET_OPERATIONS)):
        return "select_only", f"non-SELECT statement ({type(stmt).__name__.upper()})", []
    bad = next((n for n in stmt.walk() if isinstance(n, FORBIDDEN_NODES)), None)
    if bad is not None:
        return "select_only", f"non-SELECT statement ({type(bad).__name__.upper()}) inside the query", []
    ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    tables = sorted({t.name for t in stmt.find_all(exp.Table) if t.name.lower() not in ctes})
    other_schema = sorted({f"{t.db}.{t.name}" for t in stmt.find_all(exp.Table) if t.db and t.db.lower() != "main"})
    if other_schema:
        return "allow_list", f"table outside the allow-list: {', '.join(other_schema)}", tables
    foreign = [t for t in tables if t.lower() not in allowed]
    if foreign:
        return "allow_list", f"table outside the allow-list: {', '.join(foreign)}", tables
    return None, None, tables


def extract_sql(text: str) -> str | None:
    data = first_json(text)
    if isinstance(data, dict) and isinstance(data.get("sql"), str):
        return data["sql"].strip()
    m = _FENCE.search(text or "")
    if m:
        return m.group(1).strip()
    stripped = (text or "").strip()
    return stripped if re.match(r"(?is)^(select|with)\b", stripped) else None


def run_readonly(db_path: Path, sql: str, allowed_tables, row_limit: int,
                 timeout_s: float = EXEC_TIMEOUT_S) -> tuple[list[str], list[dict], bool]:
    """Execute on ``file:<db>?mode=ro`` with an authorizer allowing only SELECT and reads of allowed tables."""
    allowed = {t.lower() for t in allowed_tables}
    stmt = sqlglot.parse_one(sql, read="sqlite")
    allowed |= {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True, check_same_thread=False)
    try:
        def authorizer(action, arg1, arg2, dbname, source):
            if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and (arg1 or "").lower() in allowed:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        deadline = time.monotonic() + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(row_limit + 1)
    finally:
        conn.close()
    truncated = len(rows) > row_limit
    return columns, [dict(zip(columns, r)) for r in rows[:row_limit]], truncated


class QueryState(TypedDict, total=False):
    question: str
    consumer: str
    sql: str | None
    tables: list[str]
    columns: list[str]
    rows: list[dict]
    truncated: bool
    input_tokens: int
    output_tokens: int
    error: dict | None


def _error(code: str, step: str, message: str, **details: Any) -> dict:
    return {"code": code, "message": f"{step}: {message}", "details": {"step": step, **details}}


class QueryAgent:
    def __init__(self, llm, cfg: RuleConfig, db_path: Path | str,
                 availability: Callable[[], tuple[bool, str | None]] | None = None) -> None:
        self.llm, self.cfg, self.db_path = llm, cfg, Path(db_path)
        self.qcfg = cfg.pipeline.query_agent
        self._availability = availability
        self.graph = self._build()

    def _available(self) -> tuple[bool, str | None]:
        if self._availability is not None:
            return self._availability()
        if self.llm is None:
            return False, "no LLM_Client configured"
        if hasattr(self.llm, "availability"):
            return self.llm.availability()
        return self.llm.is_available(), None

    def _build(self):
        def check_input(state: QueryState) -> dict:
            q = (state.get("question") or "").strip()
            if not 1 <= len(q) <= MAX_QUESTION:
                return {"error": _error("VALIDATION_ERROR", "check_input",
                                        f"the question must have 1 to {MAX_QUESTION} characters after trimming "
                                        f"(got {len(q)})")}
            ok, reason = self._available()
            if not ok:
                return {"error": _error("BACKEND_UNAVAILABLE", "check_input",
                                        f"chat unavailable (Degraded_Mode: {reason or 'no LLM provider'})")}
            return {"question": q}

        def generate_sql(state: QueryState) -> dict:
            import json
            prompt = json.dumps({"question": state["question"], "schema": schema_description(self.cfg),
                                 "row_limit": self.qcfg.row_limit}, ensure_ascii=False)
            run_id = "legacy-b" if state.get("consumer") == "legacy_b" else "chat"
            res = self.llm.complete(run_id=run_id, purpose="query_agent", system=SYSTEM, prompt=prompt,
                                    max_tokens=400)
            tokens = {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens}
            if not res.ok:
                return {**tokens, "error": _error("BACKEND_UNAVAILABLE", "generate_sql",
                                                  f"LLM failure {res.error_type} ({res.message})")}
            sql = extract_sql(res.text)
            if not sql:
                return {**tokens, "error": _error("REFUSED", "generate_sql", "the model returned no SQL",
                                                  answer=res.text[:300])}
            return {**tokens, "sql": sql}

        def validate(state: QueryState) -> dict:
            check, reason, tables = validate_sql(state["sql"], self.qcfg.allowed_tables)
            if check:
                return {"tables": tables, "error": _error("REFUSED", "validate_sql", reason, check=check)}
            return {"tables": tables}

        def execute(state: QueryState) -> dict:
            try:
                columns, rows, truncated = run_readonly(self.db_path, state["sql"], self.qcfg.allowed_tables,
                                                        self.qcfg.row_limit)
            except sqlite3.Error as exc:
                return {"error": _error("INTERNAL", "execute", f"database error: {exc}")}
            return {"columns": columns, "rows": rows, "truncated": truncated}

        def route(state: QueryState) -> str:
            return "end" if state.get("error") else "next"

        g = StateGraph(QueryState)
        for name, fn in (("check_input", check_input), ("generate_sql", generate_sql), ("validate_sql", validate),
                         ("execute", execute)):
            g.add_node(name, fn)
        g.add_edge(START, "check_input")
        g.add_conditional_edges("check_input", route, {"end": END, "next": "generate_sql"})
        g.add_conditional_edges("generate_sql", route, {"end": END, "next": "validate_sql"})
        g.add_conditional_edges("validate_sql", route, {"end": END, "next": "execute"})
        g.add_edge("execute", END)
        return g.compile()

    def ask(self, question: str, consumer: Literal["ui", "legacy_b"] = "ui") -> ChatResponse:
        t0 = time.perf_counter()
        if not self.qcfg.enabled:
            return ChatResponse(sql=None, rows=[], truncated=False, input_tokens=0, output_tokens=0,
                                error=ErrorBody(code="REFUSED", message="check_input: the chat is disabled "
                                                "(query_agent.enabled is false in Rule_Config)",
                                                details={"step": "check_input"}))
        s = self.graph.invoke({"question": question, "consumer": consumer, "input_tokens": 0, "output_tokens": 0,
                               "rows": [], "columns": [], "truncated": False, "sql": None, "error": None})
        err = s.get("error")
        return ChatResponse(sql=s.get("sql"), rows=[] if err else s.get("rows", []), columns=s.get("columns", []),
                            truncated=bool(s.get("truncated")) and not err, input_tokens=s.get("input_tokens", 0),
                            output_tokens=s.get("output_tokens", 0), error=ErrorBody(**err) if err else None,
                            seconds=round(time.perf_counter() - t0, 2))
