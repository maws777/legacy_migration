"""Local Ollama: OLLAMA_MODEL / OLLAMA_BASE_URL / OLLAMA_THINK / OLLAMA_REASONING_TOKENS (environment or .env)."""
import json

import httpx

from agents import llm_client
from agents.llm_client import OllamaProvider
from rules import config as rule_config
from rules.config import load_rule_config


def _capture(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"message": {"content": "{}"}, "prompt_eval_count": 3, "eval_count": 2,
                                         "model": "m"})

    monkeypatch.setattr(llm_client, "OLLAMA_TRANSPORT", httpx.MockTransport(handler))
    return seen


def test_model_and_base_url_are_configurable(monkeypatch):
    assert load_rule_config().pipeline.llm.base_url == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://gpu-box:11434")
    llm = load_rule_config().pipeline.llm
    assert (llm.model, llm.base_url) == ("qwen3:8b", "http://gpu-box:11434")


def test_think_and_reasoning_budget_reach_the_request(monkeypatch):
    seen = _capture(monkeypatch)
    body = lambda: json.loads(seen[-1].content)
    OllamaProvider(load_rule_config().pipeline.llm).call("s", "p", 20)
    assert "think" not in body() and body()["options"]["num_predict"] == 20 and body()["keep_alive"]
    monkeypatch.setenv("OLLAMA_THINK", "false")
    monkeypatch.setenv("OLLAMA_REASONING_TOKENS", "300")
    OllamaProvider(load_rule_config().pipeline.llm).call("s", "p", 20)
    assert body()["think"] is False and body()["options"]["num_predict"] == 320


def test_dotenv_fills_missing_variables_only(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('\ufeff# comment\nOLLAMA_MODEL = file-model\nOLLAMA_THINK=false\n', encoding="utf-8")
    monkeypatch.delenv("LEGACY_NO_DOTENV")
    monkeypatch.setattr(rule_config, "DOTENV_PATH", env)
    monkeypatch.setattr(rule_config, "_dotenv_loaded", False)
    monkeypatch.setenv("OLLAMA_MODEL", "real-env-wins")
    try:
        llm = load_rule_config().pipeline.llm
        assert llm.model == "real-env-wins" and llm.think is False
    finally:
        monkeypatch.delenv("OLLAMA_THINK", raising=False)              # setdefault wrote it outside monkeypatch
