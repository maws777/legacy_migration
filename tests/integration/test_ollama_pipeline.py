"""Full Runs on data/synthetic_edge_cases.csv with simulated Ollama answers (no real call)."""
import json
import shutil
from pathlib import Path

import httpx
import pytest

from agents.llm_client import LLMClient
from migration.pipeline import MigrationPipeline
from rules.config import CONFIG_FILES, DEFAULT_CONFIG_DIR, load_rule_config

pytestmark = pytest.mark.integration
EDGE = Path(__file__).resolve().parents[2] / "data" / "synthetic_edge_cases.csv"
TAGS = {"models": [{"name": "qwen2.5:7b", "model": "qwen2.5:7b"}]}


def final(value, confidence, why="closest allowed value"):
    return {"final": {"decision": "map", "value": value, "confidence": confidence, "justification": why}}


SCRIPT = {   # normalized value -> answers per turn (JSON protocol of the Resolver_Agent)
    "MÉGANEETECH": [{"tool": "inspect_pattern_frequency", "args": {}}, final("MEGANE E-TECH", 0.96)],
    "CLIP": [{"tool": "find_similar_values", "args": {}}, final("Clip", 0.97)],
    "Diag Tool": [final("DDT2000", 0.6, "probably the DiagTool alias")],
    "AIRBAGUNIT": [{"tool": "lookup_dictionary", "args": {}}, {"tool": "test_proposed_rule", "args": {"value": "AIRBAG"}},
                   final("AIRBAG", 0.95)],
    "2024-03-15 10:30": [final("15/03/2024 10:30", 0.99)],
    "ID 0004": [{"tool": "find_similar_values", "args": {}}] * 3,
}


class FakeOllama:
    def __init__(self, down=False, oom=False):
        self.down, self.oom, self.chats = down, oom, []

    def __call__(self, req: httpx.Request):
        if self.down:
            raise httpx.ConnectError("connection refused", request=req)
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        if req.url.path == "/api/generate":
            if self.oom:
                return httpx.Response(500, json={"error": "llama-server reported out-of-memory during startup"})
            return httpx.Response(200, json={"model": "qwen2.5:7b", "done": True, "done_reason": "load"})
        body = json.loads(req.content)
        self.chats.append(body)
        try:
            prompt = json.loads(body["messages"][1]["content"])
            content = json.dumps(SCRIPT[prompt["value"]][prompt["turn"] - 1], ensure_ascii=False)
        except (ValueError, KeyError):
            content = "{}"                                  # schema discovery: reference fallback
        return httpx.Response(200, json={"model": body["model"], "done": True, "prompt_eval_count": 50,
                                         "eval_count": 10, "message": {"role": "assistant", "content": content}})


def make(tmp_path, fake, *, extra_auto=False, provider=None):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, cfg_dir / name)
    text = (cfg_dir / "pipeline.yaml").read_text(encoding="utf-8").replace("legacy_b_lookup: true", "legacy_b_lookup: false")
    if extra_auto:   # even if listed, a field without a closed dictionary is never auto-applied
        text = text.replace("[originTool, ecuName, Vehmodel, ecuCode]", "[originTool, ecuName, Vehmodel, ecuCode, Creation_date]")
    if provider:
        text = text.replace("provider: ollama", f"provider: {provider}").replace("model: qwen2.5:7b", "model: claude-haiku-4-5")
    (cfg_dir / "pipeline.yaml").write_text(text, encoding="utf-8")
    cfg = load_rule_config(cfg_dir)
    p = MigrationPipeline(EDGE, var_dir=tmp_path / "var", config_dir=cfg_dir)
    if fake is not None:
        http = httpx.Client(transport=httpx.MockTransport(fake), base_url=cfg.pipeline.llm.base_url)
        p.deps.llm = LLMClient(cfg.pipeline.llm, p.ops_engine, http_client=http)
        p.schema_agent.llm = p.deps.llm
    p.seed_reference_mapping()
    return p


def test_agent_with_simulated_ollama(tmp_path):
    fake = FakeOllama()
    p = make(tmp_path, fake, extra_auto=True)
    try:
        info = p.start_campaign()
        resolver_chats = [c for c in fake.chats if c["messages"][1]["content"].startswith("{")]
        assert len(resolver_chats) == 12 and all(c["format"] == "json" for c in fake.chats)
        decs = p.run_data(info.run_id).decisions
        assert [decs[i].outcome for i in range(1, 9)] == [
            "CORRECT", "CORRECT", "ASK_HUMAN", "CORRECT", "ASK_HUMAN", "CORRECT", "ASK_HUMAN", "MIGRATE"]
        ecu = [d for d in decs[4].fields if d.field == "ecuName"][0]
        assert (ecu.reason, ecu.after, ecu.decided_by, ecu.confidence, ecu.tokens_used) == (
            "LLM_VALUE", "AIRBAG", "llm", 0.95, 180)
        assert "Resolver_Agent: 3 iteration(s), 2 tool call(s): lookup_dictionary({}) -> 12 values" in ecu.explanation
        stuck = [d for d in decs[7].fields if d.field == "ecuCode"][0]
        assert stuck.action == "ASK_HUMAN" and "no final answer after 3 iteration(s)" in stuck.explanation
        # every tool call is traced: tool, parameters, summarized result
        calls = p.decision_logger.tool_calls(info.run_id)
        assert [(c.pattern_key.split("|")[0], c.iteration, c.tool) for c in calls] == [
            ("Vehmodel", 1, "inspect_pattern_frequency"), ("originTool", 1, "find_similar_values"),
            ("ecuName", 1, "lookup_dictionary"), ("ecuName", 2, "test_proposed_rule"),
            ("ecuCode", 1, "find_similar_values"), ("ecuCode", 2, "find_similar_values")]
        assert calls[3].params == '{"value": "AIRBAG"}' and calls[3].result_summary.startswith("valid: CORRECT")
        qs = {q.pattern_key: q for q in p.hitl.open_questions(info.run_id)}
        assert qs["Creation_date|UNKNOWN_VARIANT|2024-03-15 10:30"].llm_status == "proposed_human_only"
        assert qs["originTool|UNKNOWN_VARIANT|Diag Tool"].llm_status == "proposed_below_threshold"
        assert qs["ecuCode|UNKNOWN_VARIANT|ID 0004"].llm_status == "failed"
        report = p.reports.build(info.run_id)
        m = p.metrics.get(info.run_id)
        # 12 Resolver_Agent calls (720 tokens) + the schema discovery made before the Run (60 tokens).
        assert (m.llm_calls, m.input_tokens, m.output_tokens, m.degraded_mode) == (13, 650, 130, False)
        assert report.tokens == 780 and report.cost_eur == 0.0 and "780 tokens (≈ 0.0000 €)" in report.text
        assert "AI usage by purpose (tokens): Resolver_Agent 720, schema discovery 60" in report.text
        cloud = 650 * 0.00000092 + 130 * 0.0000046
        assert report.cloud_equivalent_cost_eur == pytest.approx(cloud)
        assert (f"Cloud-equivalent cost (anthropic claude-haiku-4-5 prices): {cloud:.4f} € (real cost 0.0000 € "
                "with ollama qwen2.5:7b)") in report.text
    finally:
        p.close()


def test_tool_call_route(tmp_path):
    from fastapi.testclient import TestClient
    from agents.orchestrator import Services, create_app
    p = make(tmp_path, FakeOllama())
    try:
        run = TestClient(create_app(Services(pipeline=p))).post("/ops/campaigns", json={}).json()
        client = TestClient(create_app(Services(pipeline=p)))
        rows = client.get(f"/ops/runs/{run['run_id']}/tool-calls").json()
        assert len(rows) == 6 and rows[0]["tool"] == "inspect_pattern_frequency" and rows[0]["result_summary"]
        one = client.get(f"/ops/runs/{run['run_id']}/tool-calls",
                         params={"pattern_key": "ecuName|UNKNOWN_VARIANT|AIRBAGUNIT"}).json()
        assert [r["iteration"] for r in one] == [1, 2]
    finally:
        p.close()


def test_ollama_down_gives_degraded_mode_with_reason(tmp_path):
    fake = FakeOllama(down=True)
    p = make(tmp_path, fake)
    try:
        info = p.start_campaign()
        report = p.reports.build(info.run_id)
        assert p.metrics.get(info.run_id).degraded_mode and p.metrics.get(info.run_id).llm_calls == 0
        assert ("Degraded_Mode: Ollama unreachable at http://localhost:11434 (ConnectError); Unknown_Variants were "
                "sent to humans.") in report.text
        assert fake.chats == [] and report.ask_human == 7
    finally:
        p.close()


def test_anthropic_without_key_gives_degraded_mode_with_reason(tmp_path):
    p = make(tmp_path, None, provider="anthropic")
    try:
        info = p.start_campaign()
        assert "Degraded_Mode: anthropic: ANTHROPIC_API_KEY is not set" in p.reports.build(info.run_id).text
    finally:
        p.close()


def test_ollama_out_of_memory_message_reaches_the_report(tmp_path):
    fake = FakeOllama(oom=True)
    p = make(tmp_path, fake)
    try:
        info = p.start_campaign()
        text = p.reports.build(info.run_id).text
        assert ("Degraded_Mode: Ollama could not load qwen2.5:7b (HTTP 500: llama-server reported out-of-memory "
                "during startup); Unknown_Variants were sent to humans.") in text
        assert fake.chats == []
    finally:
        p.close()
