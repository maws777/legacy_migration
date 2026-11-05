"""AI usage attribution, real cost and cloud-equivalent cost (reporting/cost.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from reporting.cost import attribute, breakdown
from reporting.report import ReportGenerator
from rules.config import LLMConfig

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
RUNS = [{"run_id": "r1", "kind": "full", "started_at": T0},
        {"run_id": "rl", "kind": "reload", "started_at": T0 + timedelta(minutes=5)},
        {"run_id": "r2", "kind": "full", "started_at": T0 + timedelta(minutes=10)}]


def call(run_id, purpose, minutes, tin=100, tout=20):
    return {"run_id": run_id, "purpose": purpose, "input_tokens": tin, "output_tokens": tout,
            "at": T0 + timedelta(minutes=minutes)}


CALLS = [call("schema-discovery", "schema_discovery", -5, 2000, 200),   # before the first Run -> r1
         call("r1", "resolver", 1), call("r1", "question_advisor", 20, 900, 100),   # advice on r1's question later
         call("chat", "query_agent", 3, 500, 50),                       # between r1 and r2 -> r1
         call("chat", "query_agent", 12, 400, 40),                      # after r2 started -> r2
         call("legacy-b", "legacy_b_query", 15, 300, 30),
         call("rl", "resolver", 6)]                                     # reload Run: its own run_id


def test_attribution_rule():
    assert {(c["purpose"], c["at"].minute) for c in attribute("r1", RUNS, CALLS)} == {
        ("schema_discovery", 55), ("resolver", 1), ("question_advisor", 20), ("query_agent", 3)}
    assert {c["purpose"] for c in attribute("r2", RUNS, CALLS)} == {"query_agent", "legacy_b_query"}
    assert [c["run_id"] for c in attribute("rl", RUNS, CALLS)] == ["rl"]
    assert attribute("unknown", RUNS, CALLS) == []


def test_breakdown_costs():
    ollama = LLMConfig(price_eur_per_input_token=0.000001, price_eur_per_output_token=0.000005)
    b = breakdown("r1", RUNS, CALLS, ollama)
    by = {u.purpose: (u.input_tokens, u.output_tokens, u.calls) for u in b.by_purpose}
    assert by == {"resolver": (100, 20, 1), "schema_discovery": (2000, 200, 1), "question_advisor": (900, 100, 1),
                  "query_agent": (500, 50, 1)}
    assert (b.input_tokens, b.output_tokens, b.tokens, b.calls) == (3500, 370, 3870, 4)
    assert b.real_cost_eur == 0.0 and b.cloud_reference == "anthropic claude-haiku-4-5"
    assert b.cloud_equivalent_cost_eur == pytest.approx(3500 * 0.000001 + 370 * 0.000005)
    anthropic = ollama.model_copy(update={"provider": "anthropic", "model": "claude-haiku-4-5"})
    a = breakdown("r1", RUNS, CALLS, anthropic)
    assert a.real_cost_eur == pytest.approx(a.cloud_equivalent_cost_eur) and a.real_cost_eur > 0


def test_report_lines_and_zero_purposes():
    llm = LLMConfig(price_eur_per_input_token=0.000001, price_eur_per_output_token=0.000005)
    empty = breakdown("r1", RUNS, [], llm)
    assert [u.purpose for u in empty.by_purpose] == ["resolver", "schema_discovery", "question_advisor", "query_agent"]
    r = ReportGenerator.compose("r1", analysed=8, counts={"MIGRATE": 8}, out_of_scope=0, traced_rows=8,
                                input_tokens=0, output_tokens=0, price_in=0, price_out=0, seconds=1, loaded=8,
                                cache_state="cold", notes=[], cost=breakdown("r1", RUNS, CALLS, llm))
    assert "Estimated AI consumption: 3,870 tokens (≈ 0.0000 €)" in r.text
    assert ("AI usage by purpose (tokens): Resolver_Agent 120, schema discovery 2,200, Question_Advisor 1,000, "
            "chat (Query_Agent) 550") in r.text
    assert "Cloud-equivalent cost (anthropic claude-haiku-4-5 prices): 0.0053 € (real cost 0.0000 € with " \
           "ollama qwen2.5:7b)" in r.text
