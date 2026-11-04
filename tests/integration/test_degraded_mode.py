from pathlib import Path

import pytest

from agents.llm_client import LLMClient
from migration.pipeline import MigrationPipeline
from rules.config import load_rule_config
from tests.fixtures.fake_llm import FakeLLM, answer

pytestmark = pytest.mark.integration
EDGE = Path(__file__).resolve().parents[2] / "data" / "synthetic_edge_cases.csv"
KEY = "sk-test-NEVER-LOGGED-42"


def _never_called(*_a, **_k):
    raise AssertionError("the provider must not be contacted in Degraded_Mode")


def test_degraded_mode_same_rule_decisions_and_ask_human(tmp_path):
    cfg = load_rule_config()
    deg = MigrationPipeline(EDGE, var_dir=tmp_path / "deg")
    deg.deps.llm = LLMClient(cfg.pipeline.llm, deg.ops_engine, _never_called)
    deg.seed_reference_mapping()
    normal = MigrationPipeline(EDGE, var_dir=tmp_path / "ok", llm=FakeLLM(lambda b: answer("map", None, 0.1)))
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


def test_key_value_never_reaches_logs_or_exports(tmp_path, monkeypatch, caplog):
    import logging
    from types import SimpleNamespace
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    caplog.set_level(logging.DEBUG)

    def provider(api_key, timeout_s):
        def create(**kw):
            return SimpleNamespace(content=[SimpleNamespace(text=answer("map", "Clip", 0.95))], model="m",
                                   usage=SimpleNamespace(input_tokens=5, output_tokens=3))
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    cfg = load_rule_config()
    p = MigrationPipeline(EDGE, var_dir=tmp_path / "v")
    p.deps.llm = LLMClient(cfg.pipeline.llm, p.ops_engine, provider)
    p.seed_reference_mapping()
    try:
        info = p.start_campaign()
        assert p.deps.llm.totals(info.run_id).calls > 0
        exports = [p.decision_logger.export(info.run_id, f, tmp_path / "exp") for f in ("json", "csv")]
        assert KEY not in caplog.text
        for path in exports + [tmp_path / "v" / "ops.db"]:
            assert KEY.encode() not in path.read_bytes()
    finally:
        p.close()
