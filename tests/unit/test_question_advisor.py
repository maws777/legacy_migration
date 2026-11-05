"""Question_Advisor with scripted LLM answers: tools, protocol, deterministic checks, trace."""
import json
from datetime import datetime, timezone

import pytest

from agents.question_advisor import AdvisorContext, AdvisorTools, QuestionAdvisor, check_recommendation, Recommendation
from hitl.schemas import HumanQuestion
from rules.types import RawRow
from tests.fixtures.fake_llm import FakeLLM
from tests.fixtures.make_csv import base_row, vin

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
MODELS = ["KANGOO", "MASTER", "CLIO", "SCENIC E-TECH", "ARKANA", "CAPTUR", "TRAFIC", "AUSTRAL", "MEGANE E-TECH",
          "RAFALE"]


def question(field="Vehmodel", reason="LEGACY_B_CONFLICT", options=("reject_group", "case_by_case"),
             allowed=MODELS, rows=2, key=None):
    return HumanQuestion(question_id="q1", run_id="r1", pattern_key=key or f"{field}|{reason}", field=field,
                         reason=reason, rule_id="R7", row_count=rows, example_rows=[], missing="m", why="w",
                         llm_suggestion=None, llm_status="not_applicable", allowed_values=list(allowed) if allowed else None,
                         options=list(options), status="open", parent_question_id=None, created_at=NOW, closed_at=None)


MEMBERS = [(1, base_row(1, Vehmodel="CLIO")), (2, base_row(2, Vehmodel="CLIO"))]
SOURCE = [RawRow(i, v) for i, v in enumerate([base_row(1, Vehmodel="CLIO"), base_row(2, Vehmodel="CLIO"),
                                               base_row(1, Vehmodel="CLIO", ecuName="ABS"),
                                               base_row(3, Vehmodel="#NOM?"), base_row(4, Vehmodel="MASTER")], 1)]


def ctx(cfg, q, validate=lambda key, value: None, legacy_b=None):
    return AdvisorContext(cfg=cfg, question=q, members=lambda: MEMBERS, source_rows=lambda: SOURCE,
                          legacy_b_model=legacy_b or (lambda v: {"status": "found", "vehicle_model": "MASTER"}),
                          validate_value=validate)


def scripted(steps):
    return FakeLLM(lambda body: json.dumps(steps[body["turn"] - 1]))


def final(option, value=None, confidence=0.8, **kw):
    return {"final": {"option": option, "value": value, "confidence": confidence,
                      "justification": kw.get("why", "the evidence points to it"),
                      "evidence": kw.get("evidence", ["Legacy B holds MASTER"]),
                      "unknowns": kw.get("unknowns", ["which system was updated last"])}}


def test_tools_are_read_only_and_summarized(cfg):
    q = question()
    tools = AdvisorTools(ctx(cfg, q))
    s = tools.call(1, "sample_group_rows", {"limit": 1})
    assert s.result["row_count"] == 2 and len(s.result["rows"]) == 1 and "row 1 'CLIO'" in s.summary
    v = tools.call(1, "same_vin_rows", {})
    assert v.result["vin"] == vin(1) and v.result["other_rows"] == 1 and v.result["ecus"] == {"ABS": 1}
    b = tools.call(2, "legacy_b_model", {})
    assert b.result == {"vin": vin(1), "status": "found", "vehicle_model": "MASTER"} and "model MASTER" in b.summary
    d = tools.call(2, "field_distribution", {"limit": 2})
    assert d.result["rows"] == 5 and d.result["unreadable"] == 1 and d.result["top"] == {"CLIO": 3, "MASTER": 1}
    t = tools.call(3, "test_option", {"option": "reject_group"})
    assert t.result["allowed"] and "2 row(s) become REJECT" in t.result["effect"]
    assert not tools.call(3, "test_option", {"option": "apply_value", "value": "MASTER"}).result["allowed"]
    assert tools.call(3, "drop_table", {}).result["error"].startswith("unknown tool")
    assert "invalid arguments" in tools.call(3, "sample_group_rows", {"bogus": 1}).result["error"]
    assert MEMBERS[0][1]["Vehmodel"] == "CLIO"                       # nothing changed


def test_recommendation_after_tools(cfg):
    llm = scripted([{"tool": "legacy_b_model", "args": {}}, {"tool": "same_vin_rows", "args": {}},
                    final("case_by_case", confidence=0.7)])
    out = QuestionAdvisor(llm).advise(ctx(cfg, question()), "r1")
    assert out.status == "recommended" and out.recommendation.option == "case_by_case"
    assert out.recommendation.unknowns == ["which system was updated last"]
    assert [c.tool for c in out.tool_calls] == ["legacy_b_model", "same_vin_rows"]
    assert (out.llm_calls, out.input_tokens, out.output_tokens) == (3, 300, 60)
    assert all(c["purpose"] == "question_advisor" for c in llm.calls)
    second = json.loads(llm.calls[1]["prompt"])
    assert second["tool_results"][0]["result"]["vehicle_model"] == "MASTER" and second["question"]["options"] == [
        "reject_group", "case_by_case"]


def test_value_must_come_from_a_tool(cfg):
    q = question(field="ecuName", reason="UNREADABLE", options=("apply_value", "reject_group", "case_by_case"),
                 allowed=cfg.ecus, key="ecuName|UNREADABLE")
    invented = QuestionAdvisor(scripted([final("apply_value", "BCM")])).advise(ctx(cfg, q), "r1")
    assert invented.status == "invalid" and "not returned by any tool" in invented.failure
    seen = QuestionAdvisor(scripted([{"tool": "same_vin_rows", "args": {}}, final("apply_value", "ABS")])).advise(
        ctx(cfg, q), "r1")
    assert seen.status == "recommended" and seen.recommendation.value == "ABS"
    outside = QuestionAdvisor(scripted([{"tool": "same_vin_rows", "args": {}}, final("apply_value", "ABSX")])).advise(
        ctx(cfg, q), "r1")
    assert outside.status == "invalid" and "not an allowed value" in outside.failure


@pytest.mark.parametrize("field", ["Creation_date", "originTool", "toolVersion", "diagnosticFieId"])
def test_never_a_value_for_dates_tools_versions_ids(cfg, field):
    q = question(field=field, reason="UNREADABLE", options=("apply_value", "reject_group"), allowed=cfg.tools)
    rec = Recommendation(option="apply_value", value="DDT2000", confidence=0.9, justification="x")
    assert "never proposes a value" in check_recommendation(rec, q, json.dumps({"x": "DDT2000"}))


def test_option_must_be_offered_and_value_only_with_apply(cfg):
    q = question()
    assert "not one of the offered options" in check_recommendation(
        Recommendation(option="apply_value", value="CLIO", confidence=0.9, justification="x"), q, "")
    assert "only allowed with apply_value" in check_recommendation(
        Recommendation(option="reject_group", value="CLIO", confidence=0.9, justification="x"), q, "")
    assert check_recommendation(Recommendation(option="reject_group", confidence=0.9, justification="x"), q, "") is None


def test_turn_limit_failures_and_unavailable(cfg):
    llm = scripted([{"tool": "sample_group_rows", "args": {}}] * 6)
    out = QuestionAdvisor(llm).advise(ctx(cfg, question()), "r1")
    assert out.status == "no_answer" and len(llm.calls) == 4 and len(out.tool_calls) == 3
    assert json.loads(llm.calls[3]["prompt"])["last_turn"] is True
    bad = QuestionAdvisor(FakeLLM(lambda b: "not json")).advise(ctx(cfg, question()), "r1")
    assert bad.status == "invalid" and "unparseable" in bad.failure
    schema = QuestionAdvisor(scripted([{"final": {"option": "reject_group", "confidence": 3}}])).advise(
        ctx(cfg, question()), "r1")
    assert schema.status == "invalid"
    down = QuestionAdvisor(FakeLLM(available=False)).advise(ctx(cfg, question()), "r1")
    assert down.status == "unavailable" and down.llm_calls == 0
    from agents.llm_client import LLMFailure
    fail = QuestionAdvisor(FakeLLM(lambda b: LLMFailure("timeout", "ReadTimeout after 180 s"))).advise(
        ctx(cfg, question()), "r1")
    assert fail.status == "llm_failed" and "ReadTimeout" in fail.failure
