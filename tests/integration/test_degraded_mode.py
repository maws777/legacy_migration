from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agents.llm_client import LLMClient
from migration.pipeline import MigrationPipeline
from rules.config import load_rule_config
from tests.fixtures.fake_llm import FakeLLM, answer

pytestmark = pytest.mark.integration
EDGE = Path(__file__).resolve().parents[2] / "data" / "synthetic_edge_cases.csv"


def _offline_ollama(base_url, timeout_s):
    assert base_url == "http://127.0.0.1:11434" and timeout_s == 120

    class Client:
        def get(self, path):
            assert path == "/api/tags"
            raise httpx.ConnectError("offline")

        def post(self, path, *, json):
            assert path == "/api/chat" and json["model"] == "qwen2.5:3b"
            raise AssertionError("chat must not be called when Ollama is offline")

        def close(self):
            pass

    return Client()


def test_degraded_mode_same_rule_decisions_and_ask_human(tmp_path):
    cfg = load_rule_config()
    deg = MigrationPipeline(EDGE, var_dir=tmp_path / "deg")
    deg.deps.llm = LLMClient(cfg.pipeline.llm, deg.ops_engine, _offline_ollama)
    deg.seed_reference_mapping()
    normal = MigrationPipeline(
        EDGE, var_dir=tmp_path / "ok",
        llm=FakeLLM(lambda body: answer("map", None, 0.1, why=f"field {body.get('field', 'schema')}")),
    )
    normal.seed_reference_mapping()
    try:
        a, b = deg.start_campaign(), normal.start_campaign()
        da, db = deg.run_data(a.run_id).decisions, normal.run_data(b.run_id).decisions
        for rid in da:
            rule_a = [d for d in da[rid].fields[:-1] if d.reason != "UNKNOWN_VARIANT"]
            rule_b = [d for d in db[rid].fields[:-1] if d.reason != "UNKNOWN_VARIANT"]
            assert rule_a == rule_b
        uv = [d for dec in da.values() for d in dec.fields[:-1] if d.reason == "UNKNOWN_VARIANT"]
        assert len(uv) == 7 and all(d.action == "ASK_HUMAN" and "Degraded_Mode" in d.explanation for d in uv)
        assert deg.deps.llm.totals(a.run_id).calls == 0
        m = deg.metrics.get(a.run_id)
        assert m.degraded_mode and m.llm_calls == 0
        assert "Degraded_Mode" in deg.reports.build(a.run_id).text
        assert sum(m.counts.values()) == 8
    finally:
        deg.close()
        normal.close()


def test_local_ollama_calls_are_metered_and_prompt_is_not_logged(tmp_path, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    payloads = []

    def provider(base_url, timeout_s):
        assert base_url == "http://127.0.0.1:11434" and timeout_s == 120

        class Client:
            def get(self, path):
                assert path == "/api/tags"
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {"models": [{"name": "qwen2.5:3b"}]},
                )

            def post(self, path, *, json):
                assert path == "/api/chat" and json["model"] == "qwen2.5:3b"
                payloads.append(json)
                return SimpleNamespace(
                    raise_for_status=lambda: None,
                    json=lambda: {
                        "message": {"content": answer("reject", None, 0.1)},
                        "model": "qwen2.5:3b",
                        "prompt_eval_count": 5,
                        "eval_count": 3,
                    },
                )

            def close(self):
                pass

        return Client()

    cfg = load_rule_config()
    p = MigrationPipeline(EDGE, var_dir=tmp_path / "v")
    p.deps.llm = LLMClient(cfg.pipeline.llm, p.ops_engine, provider)
    p.seed_reference_mapping()
    try:
        info = p.start_campaign()
        totals = p.deps.llm.totals(info.run_id)
        assert totals.calls > 0
        assert (totals.input_tokens, totals.output_tokens) == (totals.calls * 5, totals.calls * 3)
        assert all("api_key" not in request and "Authorization" not in request for request in payloads)
        exports = [p.decision_logger.export(info.run_id, f, tmp_path / "exp") for f in ("json", "csv")]
        for path in exports + [tmp_path / "v" / "ops.db"]:
            assert b"private prompt" not in path.read_bytes()
    finally:
        p.close()
