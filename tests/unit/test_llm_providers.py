"""LLM_Client providers with simulated Ollama answers (httpx mock transport, no real call)."""
import json
import shutil

import httpx
import pytest

from agents.llm_client import LLMClient, NoLLM
from rules.config import CONFIG_FILES, DEFAULT_CONFIG_DIR, ConfigError, LLMConfig, load_rule_config
from target.schema import make_engine

TAGS = {"models": [{"name": "qwen2.5:7b", "model": "qwen2.5:7b"}, {"name": "llama3:latest", "model": "llama3:latest"}]}


def ollama(handler, **cfg_kw):
    cfg = LLMConfig(price_eur_per_input_token=0.001, price_eur_per_output_token=0.002, **cfg_kw)
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url=cfg.base_url)
    return LLMClient(cfg, make_engine("sqlite:///:memory:"), http_client=http)


def chat_ok(seen):
    def handler(req: httpx.Request):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"model": "qwen2.5:7b", "done": True, "prompt_eval_count": 42,
                                         "eval_count": 17, "message": {"role": "assistant", "content": '{"a": 1}'}})
    return handler


def test_config_defaults_and_validation(tmp_path):
    llm = load_rule_config().pipeline.llm
    assert (llm.provider, llm.model, llm.base_url) == ("ollama", "qwen2.5:7b", "http://localhost:11434")
    assert llm.price_per_token() == (0.0, 0.0)
    assert llm.model_copy(update={"provider": "anthropic"}).price_per_token() == (0.00000092, 0.0000046)
    for name in CONFIG_FILES:
        shutil.copy(DEFAULT_CONFIG_DIR / name, tmp_path / name)
    text = (tmp_path / "pipeline.yaml").read_text(encoding="utf-8")
    (tmp_path / "pipeline.yaml").write_text(text.replace("provider: ollama", "provider: openai"), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_rule_config(tmp_path)
    assert exc.value.file == "pipeline.yaml"


def test_ollama_chat_request_and_tokens():
    seen = []
    client = ollama(chat_ok(seen))
    assert client.availability() == (True, None) and client.is_available()
    res = client.complete(run_id="r1", purpose="resolver", system="sys", prompt="usr", max_tokens=99)
    assert res.ok and res.text == '{"a": 1}' and (res.input_tokens, res.output_tokens) == (42, 17)
    body = seen[0]
    assert body["model"] == "qwen2.5:7b" and body["format"] == "json" and body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"] and body["messages"][1]["content"] == "usr"
    assert body["options"]["num_predict"] == 99 and body["options"]["temperature"] == 0
    t = client.totals("r1")
    assert (t.input_tokens, t.output_tokens, t.calls) == (42, 17, 1)


def test_ollama_needs_no_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ollama(chat_ok([])).complete(run_id="r", purpose="resolver", system="s", prompt="p").ok


@pytest.mark.parametrize("handler,fragment", [
    (lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
     "Ollama unreachable at http://localhost:11434 (ConnectError)"),
    (lambda req: httpx.Response(500, json={}), "Ollama unreachable at http://localhost:11434 (HTTP 500)"),
    (lambda req: httpx.Response(200, json={"models": [{"name": "llama3:latest"}]}),
     "Ollama model qwen2.5:7b is not pulled (run: ollama pull qwen2.5:7b)"),
])
def test_ollama_unavailable_reasons(handler, fragment):
    ok, reason = ollama(handler).availability()
    assert not ok and reason == fragment


def test_ollama_model_with_latest_tag():
    client = ollama(lambda req: httpx.Response(200, json=TAGS), model="llama3")
    assert client.is_available()


@pytest.mark.parametrize("exc_or_status,etype", [
    (httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "provider_error"), (500, "provider_error"),
])
def test_ollama_failures_are_typed_and_counted(exc_or_status, etype):
    def handler(req):
        if isinstance(exc_or_status, int):
            return httpx.Response(exc_or_status, json={"error": "boom", "prompt_eval_count": 3})
        raise exc_or_status("x", request=req)

    client = ollama(handler)
    res = client.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    assert not res.ok and res.error_type == etype
    assert client.totals("r1").calls == 1
    if exc_or_status == 500:
        assert res.input_tokens == 3 and "HTTP 500" in res.message


def test_anthropic_without_key_is_unavailable_with_reason():
    cfg = LLMConfig(provider="anthropic", model="claude-haiku-4-5", price_eur_per_input_token=0,
                    price_eur_per_output_token=0)
    client = LLMClient(cfg, make_engine("sqlite:///:memory:"), lambda key, t: pytest.fail("no contact expected"))
    assert client.availability() == (False, "anthropic: ANTHROPIC_API_KEY is not set")
    assert client.complete(run_id="r", purpose="resolver", system="s", prompt="p").error_type == "missing_key"
    assert client.totals("r").calls == 0


def test_default_ollama_is_blocked_in_tests():
    """The suite replaces the Ollama transport: a default client never reaches a real server."""
    client = LLMClient(load_rule_config().pipeline.llm, make_engine("sqlite:///:memory:"))
    ok, reason = client.availability()
    assert not ok and "ConnectError" in reason


def test_no_llm():
    n = NoLLM()
    assert n.availability() == (False, "LLM disabled (--no-llm)") and not n.is_available()
    assert not n.complete(run_id="r").ok and n.totals("r").calls == 0
