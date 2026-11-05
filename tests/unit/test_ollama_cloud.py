"""Ollama cloud: OLLAMA_API_KEY (environment or .env) and OLLAMA_MODEL / OLLAMA_BASE_URL overrides."""
import json

import httpx

from agents import llm_client
from agents.llm_client import OllamaProvider
from rules import config as rule_config
from rules.config import load_rule_config


def _mock(monkeypatch, models=("gpt-oss:120b",)):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        return httpx.Response(200, json={"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2,
                                         "model": "gpt-oss:120b"})

    monkeypatch.setattr(llm_client, "OLLAMA_TRANSPORT", httpx.MockTransport(handler))
    return seen


def test_key_selects_the_cloud_and_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "k-123")
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:120b")
    llm = load_rule_config().pipeline.llm
    assert llm.base_url == "https://ollama.com" and llm.model == "gpt-oss:120b"
    seen = _mock(monkeypatch)
    provider = OllamaProvider(llm)
    assert provider.availability() == (True, None)
    assert [r.url.path for r in seen] == ["/api/tags"]                 # nothing to load in the cloud
    text, tin, tout, model = provider.call("s", "p", 20)
    assert (tin, tout, model) == (3, 2, "gpt-oss:120b")
    chat = seen[-1]
    assert chat.headers["authorization"] == "Bearer k-123"
    assert "keep_alive" not in json.loads(chat.content)


def test_without_key_nothing_changes_and_base_url_can_be_overridden(monkeypatch):
    llm = load_rule_config().pipeline.llm
    assert llm.base_url == "http://localhost:11434" and llm.model == "qwen2.5:7b"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    assert load_rule_config().pipeline.llm.base_url == "http://gpu-box:11434"


def test_cloud_reports_a_refused_key_and_an_unknown_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "bad")
    monkeypatch.setenv("OLLAMA_MODEL", "nope:1b")
    llm = load_rule_config().pipeline.llm
    monkeypatch.setattr(llm_client, "OLLAMA_TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": "unauthorized"})))
    ok, reason = OllamaProvider(llm).availability()
    assert not ok and "refused OLLAMA_API_KEY" in reason and "unauthorized" in reason
    _mock(monkeypatch)
    ok, reason = OllamaProvider(llm).availability()
    assert not ok and "no model nope:1b" in reason and "gpt-oss:120b" in reason


def test_dotenv_fills_missing_variables_only(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('\ufeff# comment\nOLLAMA_API_KEY="from-file"\nOLLAMA_MODEL = file-model\n', encoding="utf-8")
    monkeypatch.delenv("LEGACY_NO_DOTENV")
    monkeypatch.setattr(rule_config, "DOTENV_PATH", env)
    monkeypatch.setattr(rule_config, "_dotenv_loaded", False)
    monkeypatch.setenv("OLLAMA_MODEL", "real-env-wins")
    try:
        assert rule_config.ollama_api_key() == "from-file"
        assert load_rule_config().pipeline.llm.model == "real-env-wins"
    finally:
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)            # setdefault wrote it outside monkeypatch
