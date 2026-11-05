"""Question_Advisor: investigates one open Human_Question with read-only tools and recommends an option.

It never decides (the human still answers through the HITL_Queue) and never invents a value: a value can
only be recommended with ``apply_value``, only for a field with a closed list of allowed values, and only
if a tool returned that value during the consultation. Dates, tools, tool versions and identifiers never
get a value. Same provider-neutral JSON protocol as the Resolver_Agent, at most ``max_turns`` (4) turns:

- ``{"tool": "<name>", "args": {...}}`` to call one read-only tool;
- ``{"final": {"option", "value", "confidence", "justification", "evidence", "unknowns"}}`` to answer.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Literal, Mapping

from pydantic import BaseModel, Field, ValidationError

from agents.resolver_agent import first_json
from agents.resolver_tools import ToolCall, _short
from rules.config import RuleConfig
from rules.fields import dict_lookup, is_unreadable, normalize_vin

MAX_TURNS = 4
TOOL_NAMES = ("sample_group_rows", "same_vin_rows", "legacy_b_model", "field_distribution", "test_option")
# decisions.md and the user: never a value for dates, tools, tool versions or identifiers.
NO_VALUE_FIELDS = ("Creation_date", "originTool", "toolVersion", "diagnosticFieId", "VIN")
AdviceStatus = Literal["recommended", "invalid", "no_answer", "llm_failed", "unavailable"]

SYSTEM = (
    "You advise a human who must answer one grouped question of a vehicle-diagnostics data migration.\n"
    "You never decide and never invent data. Each turn, reply with ONE JSON object only, either\n"
    '  {"tool": "<name>", "args": {...}}   to call one read-only tool, or\n'
    '  {"final": {"option": "<one of options>", "value": string|null, "confidence": number, '
    '"justification": string, "evidence": [string], "unknowns": [string]}}   to answer.\n'
    "Tools:\n"
    '- sample_group_rows {"limit"?}: raw rows of the group.\n'
    '- same_vin_rows {"vin"?, "limit"?}: other source rows of the same vehicle (default: VIN of the first row).\n'
    '- legacy_b_model {"vin"?}: vehicle model held by Legacy B for a VIN.\n'
    '- field_distribution {"field"?, "limit"?}: most frequent raw values of a field over the source file.\n'
    '- test_option {"option", "value"?}: deterministic check of an option: allowed or not, rows affected, effect.\n'
    'Rules: "option" must be one of "options". "value" only with apply_value, only one of "allowed_values", '
    "and only a value that a tool returned; never a value for dates, tools, versions or identifiers. "
    '"evidence" lists what the tools showed; "unknowns" lists what you cannot know from the data. '
    'At most 4 turns; when "last_turn" is true you must send "final".'
)


class Recommendation(BaseModel):
    option: str
    value: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    justification: str = Field(min_length=1)
    evidence: list[str] = []
    unknowns: list[str] = []


class AdviceToolCall(BaseModel):
    iteration: int
    tool: str
    params: str
    result_summary: str


class QuestionAdvice(BaseModel):
    advice_id: str
    question_id: str
    pattern_key: str
    status: AdviceStatus
    recommendation: Recommendation | None = None
    failure: str | None = None
    tool_calls: list[AdviceToolCall] = []
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    model: str | None = None
    created_at: datetime | None = None          # parsed from the ISO string of the JSON (the UI formats it)


@dataclass
class AdvisorContext:
    """Read-only data sources of one consultation (providers are injected by the Orchestrator)."""
    cfg: RuleConfig
    question: Any                                                    # hitl.schemas.HumanQuestion
    members: Callable[[], list[tuple[int, dict]]]
    source_rows: Callable[[], Iterable] = lambda: ()                 # canonical RawRows of the source file
    legacy_b_model: Callable[[str], dict] | None = None
    validate_value: Callable[[str, str | None], str | None] = lambda key, value: None
    _rows_cache: list = field(default_factory=list)

    def rows(self) -> list:
        if not self._rows_cache:
            self._rows_cache.extend(r for r in self.source_rows() if not r.malformed)
        return self._rows_cache


def _row_view(rid: int, values: Mapping[str, str]) -> dict:
    return {"source_row_id": rid, "values": dict(values)}


class AdvisorTools:
    def __init__(self, ctx: AdvisorContext) -> None:
        self.ctx = ctx
        self.q = ctx.question

    def call(self, iteration: int, tool: str, args: Any) -> ToolCall:
        args = dict(args) if isinstance(args, Mapping) else {}
        if tool not in TOOL_NAMES:
            result = {"error": f"unknown tool '{tool}'; available: {', '.join(TOOL_NAMES)}"}
        else:
            try:
                result = getattr(self, tool)(**args)
            except (TypeError, ValueError) as exc:
                result = {"error": f"invalid arguments for {tool}: {exc}"}
        return ToolCall(iteration, tool, args, result, _short(self._summary(tool, result)))

    @staticmethod
    def _summary(tool: str, r: dict) -> str:
        if "error" in r:
            return f"error: {r['error']}"
        if tool == "sample_group_rows":
            return f"{len(r['rows'])} of {r['row_count']} row(s): " + "; ".join(
                f"row {x['source_row_id']} {x['values'].get(r['field'], '')!r}" for x in r["rows"])
        if tool == "same_vin_rows":
            return (f"VIN {r['vin']}: {r['other_rows']} other row(s); models {r['models'] or '-'}; "
                    f"ECUs {r['ecus'] or '-'}")
        if tool == "legacy_b_model":
            return f"VIN {r['vin']}: {r['status']}" + (f", model {r['vehicle_model']}" if r.get("vehicle_model") else "")
        if tool == "field_distribution":
            top = ", ".join(f"{k!r} x{v}" for k, v in r["top"].items())
            return f"{r['field']}: {r['rows']} rows, {r['unreadable']} unreadable; top {top}"
        return (("allowed" if r["allowed"] else f"refused: {r['reason']}")
                + f"; {r['rows_affected']} row(s); {r['effect']}")

    # ---------------------------------------------------------------- tools (read-only)
    def sample_group_rows(self, limit: int = 3) -> dict:
        limit = max(1, min(int(limit), 10))
        members = self.ctx.members()
        return {"field": self.q.field, "row_count": len(members),
                "rows": [_row_view(rid, v) for rid, v in members[:limit]]}

    def _default_vin(self) -> str:
        members = self.ctx.members()
        return normalize_vin(members[0][1].get("VIN", "")) if members else ""

    def same_vin_rows(self, vin: str | None = None, limit: int = 5) -> dict:
        v = normalize_vin(vin) if vin else self._default_vin()
        limit = max(1, min(int(limit), 10))
        group_ids = {rid for rid, _ in self.ctx.members()}
        others = [r for r in self.ctx.rows() if normalize_vin(r.values.get("VIN", "")) == v
                  and r.source_row_id not in group_ids]
        cfg = self.ctx.cfg
        models = Counter(m for m in (dict_lookup(r.values.get("Vehmodel", ""), cfg.vehicle_models) for r in others) if m)
        ecus = Counter(e for e in (dict_lookup(r.values.get("ecuName", ""), cfg.ecus) for r in others) if e)
        return {"vin": v, "other_rows": len(others), "models": dict(models), "ecus": dict(ecus),
                "rows": [_row_view(r.source_row_id, r.values) for r in others[:limit]]}

    def legacy_b_model(self, vin: str | None = None) -> dict:
        v = normalize_vin(vin) if vin else self._default_vin()
        if self.ctx.legacy_b_model is None:
            return {"vin": v, "status": "unavailable", "vehicle_model": None}
        return {"vin": v, **self.ctx.legacy_b_model(v)}

    def field_distribution(self, field: str | None = None, limit: int = 8) -> dict:
        f = field or self.q.field
        limit = max(1, min(int(limit), 15))
        rows = self.ctx.rows()
        values = [r.values.get(f, "") for r in rows]
        unreadable = sum(1 for v in values if is_unreadable(v))
        top = Counter(v for v in values if not is_unreadable(v)).most_common(limit)
        return {"field": f, "rows": len(values), "unreadable": unreadable, "top": dict(top)}

    def test_option(self, option: str, value: str | None = None) -> dict:
        q = self.q
        rows = q.row_count
        if option not in q.options:
            return {"allowed": False, "reason": f"option {option} is not offered (options: {', '.join(q.options)})",
                    "rows_affected": rows, "effect": "none"}
        if option == "apply_value":
            if q.field in NO_VALUE_FIELDS:
                return {"allowed": False, "reason": f"the advisor never proposes a value for {q.field}",
                        "rows_affected": rows, "effect": "none"}
            failed = self.ctx.validate_value(q.pattern_key, value)
            if failed:
                return {"allowed": False, "reason": failed, "rows_affected": rows, "effect": "none"}
            return {"allowed": True, "reason": None, "rows_affected": rows,
                    "effect": f"the {rows} row(s) get {q.field} = {value!r} in the next Run (then the other rules "
                              "and the final checks still apply)"}
        effect = {"reject_group": f"the {rows} row(s) become REJECT in the next Run",
                  "case_by_case": f"{rows} questions are created, one per row",
                  "generate_op_id": f"a deterministic diagnosticOpID (UUID v5) is generated for each of the {rows} "
                                    "row(s) whose Operation_Identity is complete"}.get(option, "")
        return {"allowed": True, "reason": None, "rows_affected": rows, "effect": effect}


# ---------------------------------------------------------------- validation (deterministic)
def check_recommendation(rec: Recommendation, question: Any, seen_text: str) -> str | None:
    """The failed rule, or None. ``seen_text`` is the JSON of every tool result of the consultation."""
    if rec.option not in question.options:
        return f"option {rec.option!r} is not one of the offered options ({', '.join(question.options)})"
    if not rec.justification.strip():
        return "empty justification"
    if rec.option != "apply_value":
        return None if rec.value in (None, "") else f"a value is only allowed with apply_value (got {rec.value!r})"
    if question.field in NO_VALUE_FIELDS:
        return f"the advisor never proposes a value for {question.field} (dates, tools, versions, identifiers)"
    if not rec.value:
        return "apply_value without a value"
    if not question.allowed_values or rec.value not in question.allowed_values:
        return f"value {rec.value!r} is not an allowed value of the question"
    if json.dumps(rec.value, ensure_ascii=False) not in seen_text:
        return f"value {rec.value!r} was not returned by any tool: the advisor may not invent values"
    return None


def build_prompt(question: Any, turn: int, max_turns: int, tool_results: list[dict]) -> str:
    q = question
    allowed = q.allowed_values if q.allowed_values is None or len(q.allowed_values) <= 15 else q.allowed_values[:15]
    body = {"question": {"pattern_key": q.pattern_key, "field": q.field, "reason": q.reason, "rule_id": q.rule_id,
                         "row_count": q.row_count, "missing": q.missing, "why": q.why, "options": q.options,
                         "allowed_values": allowed},
            "turn": turn, "max_turns": max_turns, "last_turn": turn >= max_turns, "tool_results": tool_results}
    if q.llm_suggestion is not None:
        body["question"]["llm_suggestion"] = {"value": q.llm_suggestion.value,
                                              "confidence": q.llm_suggestion.confidence}
    return json.dumps(body, ensure_ascii=False)


@dataclass
class AdvisorOutcome:
    status: AdviceStatus
    recommendation: Recommendation | None
    failure: str | None
    tool_calls: list[ToolCall]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    seconds: float
    model: str | None


class QuestionAdvisor:
    def __init__(self, llm, max_turns: int = MAX_TURNS, max_tokens: int = 400) -> None:
        self.llm = llm
        self.max_turns = max(1, min(int(max_turns), MAX_TURNS))
        self.max_tokens = max_tokens

    def advise(self, ctx: AdvisorContext, run_id: str) -> AdvisorOutcome:
        t0 = time.perf_counter()
        calls: list[ToolCall] = []
        results: list[dict] = []
        n = tin = tout = 0
        model = None

        def out(status, rec=None, failure=None):
            return AdvisorOutcome(status, rec, failure, calls, n, tin, tout, round(time.perf_counter() - t0, 2), model)

        if self.llm is None:
            return out("unavailable", failure="no LLM_Client configured")
        if hasattr(self.llm, "availability"):
            ok, reason = self.llm.availability()
        else:
            ok, reason = self.llm.is_available(), None
        if not ok:
            return out("unavailable", failure=reason or "no LLM provider available")
        tools = AdvisorTools(ctx)
        for turn in range(1, self.max_turns + 1):
            res = self.llm.complete(run_id=run_id, purpose="question_advisor", system=SYSTEM,
                                    prompt=build_prompt(ctx.question, turn, self.max_turns, results),
                                    max_tokens=self.max_tokens)
            n += 1
            tin += res.input_tokens
            tout += res.output_tokens
            if not res.ok:
                return out("llm_failed", failure=f"LLM failure: {res.error_type} ({res.message})")
            model = getattr(res, "model", None)
            data = first_json(res.text)
            if isinstance(data, dict) and isinstance(data.get("tool"), str):
                if turn >= self.max_turns:
                    return out("no_answer", failure=f"no final answer after {turn} turn(s)")
                call = tools.call(turn, data["tool"], data.get("args") or {})
                calls.append(call)
                results.append({"turn": turn, "tool": call.tool, "args": call.args, "result": call.result})
                continue
            final = data.get("final") if isinstance(data, dict) and isinstance(data.get("final"), dict) else data
            if not isinstance(final, dict):
                return out("invalid", failure="unparseable answer: no JSON object")
            try:
                rec = Recommendation.model_validate(final)
            except ValidationError as exc:
                return out("invalid", failure=f"invalid answer: {exc.errors()[0]['msg']}")
            failed = check_recommendation(rec, ctx.question, json.dumps(results, ensure_ascii=False))
            if failed:
                return out("invalid", rec, failed)
            return out("recommended", rec)
        return out("no_answer", failure=f"no final answer after {self.max_turns} turn(s)")  # pragma: no cover
