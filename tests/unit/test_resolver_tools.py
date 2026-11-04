"""Read-only tools, the JSON tool protocol and the deterministic validator of the Resolver_Agent."""
import json

import pytest

from agents.resolver_agent import ResolverAgent, parse_answer
from agents.resolver_tools import ResolverTools, RunStats, validate_proposal
from rules.types import AllowedValues, LLMSuggestion, UnknownVariant
from tests.fixtures.fake_llm import FakeLLM


@pytest.fixture
def ecu_uv(cfg):
    return UnknownVariant("ecuName|UNKNOWN_VARIANT|AIRBAGUNIT", "ecuName", "AIRBAGUNIT", AllowedValues(values=cfg.ecus))


STATS = RunStats(key_rows={"ecuName|UNKNOWN_VARIANT|AIRBAGUNIT": 3},
                 key_spellings={"ecuName|UNKNOWN_VARIANT|AIRBAGUNIT": {"AIR-BAG UNIT": 2, "airbag unit": 1}},
                 field_values={"ecuName": {"BCM": 40, "AIRBAG": 12}})


def test_tools_are_read_only_and_summarized(cfg, ecu_uv):
    tools = ResolverTools(cfg, STATS, ecu_uv)
    d = tools.call(1, "lookup_dictionary", {})
    assert d.result["values"] == list(cfg.ecus) and d.summary.startswith("12 values: BCM")
    s = tools.call(1, "find_similar_values", {"limit": 2})
    assert [c["value"] for c in s.result["candidates"]][0] == "AIRBAG" and len(s.result["candidates"]) == 2
    f = tools.call(2, "inspect_pattern_frequency", {})
    assert f.result["rows"] == 3 and f.result["spellings"] == {"AIR-BAG UNIT": 2, "airbag unit": 1}
    assert "'AIR-BAG UNIT' x2" in f.summary and "AIRBAG x12" in f.summary
    t = tools.call(2, "test_proposed_rule", {"value": "AIRBAG"})
    assert t.result["valid"] and t.result["rows_affected"] == 3 and t.result["auto_apply"]
    bad = tools.call(2, "test_proposed_rule", {"value": "AIR BAG"})
    assert not bad.result["valid"] and "not an allowed value" in bad.summary
    assert tools.call(3, "drop_table", {}).result["error"].startswith("unknown tool")
    assert "invalid arguments" in tools.call(3, "lookup_dictionary", {"nope": 1}).result["error"]
    assert STATS.key_rows == {"ecuName|UNKNOWN_VARIANT|AIRBAGUNIT": 3}            # unchanged


def test_tool_aliases_and_dates(cfg):
    tool_uv = UnknownVariant("originTool|UNKNOWN_VARIANT|Diag Tool", "originTool", "Diag Tool",
                             AllowedValues(values=cfg.tools))
    best = ResolverTools(cfg, RunStats(), tool_uv).find_similar_values()["candidates"][0]
    assert best == {"value": "DDT2000", "score": 1.0, "via": "DiagTool"}
    date_uv = UnknownVariant("Creation_date|UNKNOWN_VARIANT|x", "Creation_date", "2024-03-15 10:30",
                             AllowedValues(format="dd/mm/yyyy hh:mm"))
    tools = ResolverTools(cfg, RunStats(), date_uv)
    assert tools.find_similar_values()["candidates"][0]["value"] == "15/03/2024 10:30"
    t = tools.test_proposed_rule("15/03/2024 10:30")
    assert t["valid"] and t["target_value"] == "2024-03-15T10:30" and not t["auto_apply"]


def test_validator(cfg, ecu_uv):
    ok = LLMSuggestion("map", "AIRBAG", 0.9, "x")
    assert validate_proposal(ecu_uv, ok, cfg) is None
    assert "not an allowed value" in validate_proposal(ecu_uv, LLMSuggestion("map", "AIR-BAG", 0.99, "x"), cfg)
    assert validate_proposal(ecu_uv, LLMSuggestion("map", "AIRBAG", 1.2, "x"), cfg)
    assert validate_proposal(ecu_uv, LLMSuggestion("map", "AIRBAG", 0.9, " "), cfg)
    assert validate_proposal(ecu_uv, LLMSuggestion("reject", None, 0.9, "x"), cfg) is None


def scripted(steps):
    """FakeLLM answering the turns of one Pattern_Key in order; records the prompts."""
    def answer(body):
        return json.dumps(steps[body["turn"] - 1])
    return FakeLLM(answer)


def test_protocol_tool_then_final(cfg, ecu_uv):
    llm = scripted([{"tool": "find_similar_values", "args": {"limit": 1}},
                    {"final": {"decision": "map", "value": "AIRBAG", "confidence": 0.93, "justification": "close"}}])
    out = ResolverAgent(llm).resolve("r", ecu_uv, STATS, cfg)
    assert (out.suggestion.value, out.suggestion.confidence, out.iterations, out.tokens) == ("AIRBAG", 0.93, 2, 240)
    assert [c.tool for c in out.tool_calls] == ["find_similar_values"]
    second = json.loads(llm.calls[1]["prompt"])
    assert second["turn"] == 2 and second["tool_results"][0]["result"]["candidates"][0]["value"] == "AIRBAG"
    assert '"tool"' in llm.calls[0]["system"] and '"final"' in llm.calls[0]["system"]
    assert "find_similar_values" in out.trace() and "2 iteration(s), 1 tool call(s)" in out.trace()


def test_at_most_three_iterations(cfg, ecu_uv):
    llm = scripted([{"tool": "lookup_dictionary", "args": {}}] * 5)
    out = ResolverAgent(llm).resolve("r", ecu_uv, STATS, cfg)
    assert len(llm.calls) == 3 and out.iterations == 3 and len(out.tool_calls) == 2
    assert out.suggestion.confidence == 0 and "no final answer after 3 iteration(s)" in out.failure
    assert json.loads(llm.calls[2]["prompt"])["last_turn"] is True


def test_final_answer_is_validated(cfg, ecu_uv):
    llm = scripted([{"final": {"decision": "map", "value": "AIRBAG UNIT", "confidence": 0.99, "justification": "x"}}])
    out = ResolverAgent(llm).resolve("r", ecu_uv, STATS, cfg)
    assert out.suggestion.confidence == 0 and "not an allowed value" in out.suggestion.justification
    assert parse_answer('{"final": {"decision": "reject", "value": null, "confidence": 0.8, '
                        '"justification": "not an ECU"}}', ecu_uv, cfg).decision == "reject"
