import logging

import httpx
import pytest

from agents.llm_client import LLMClient, llm_call
from rules.config import LLMConfig
from target.schema import make_engine


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            request = httpx.Request("POST", "http://localhost:11434/api/chat")
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("provider error", request=request, response=response)

    def json(self):
        return self.body


class Ollama:
    def __init__(self, behaviour, models=("qwen2.5:3b",)):
        self.behaviour = behaviour
        self.models = models
        self.created = 0
        self.kwargs = None
        self.closed = 0

    def __call__(self, base_url, timeout_s):
        self.created += 1
        assert base_url == "http://127.0.0.1:11434"
        assert timeout_s == 3
        outer = self

        class Client:
            def get(self, path):
                assert path == "/api/tags"
                return Response({"models": [{"name": model} for model in outer.models]})

            def post(self, path, *, json):
                assert path == "/api/chat"
                outer.kwargs = json
                return outer.behaviour()

            def close(self):
                outer.closed += 1

        return Client()


def ok():
    return Response({
        "message": {"content": '{"a": 1}'},
        "model": "qwen2.5:3b",
        "prompt_eval_count": 11,
        "eval_count": 7,
    })


def raise_timeout():
    raise httpx.ReadTimeout("timeout")


def raise_connection():
    raise httpx.ConnectError("connection refused")


def raise_status():
    return Response({}, status=500)


def malformed_response():
    return Response({"message": None})


@pytest.fixture
def make():
    def _make(behaviour, models=("qwen2.5:3b",)):
        provider = Ollama(behaviour, models)
        cfg = LLMConfig(price_eur_per_input_token=0.0, price_eur_per_output_token=0.0,
                        timeout_s=3, base_url="http://127.0.0.1:11434", model="qwen2.5:3b")
        return LLMClient(cfg, make_engine("sqlite:///:memory:"), provider), provider
    return _make


def test_success_returns_text_and_token_counts(make):
    client, provider = make(ok)
    res = client.complete(run_id="r1", purpose="resolver", system="system", prompt="prompt", max_tokens=50)
    assert res.ok and res.text == '{"a": 1}' and (res.input_tokens, res.output_tokens) == (11, 7)
    assert provider.kwargs["options"]["num_predict"] == 50
    assert provider.kwargs["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]
    assert "Authorization" not in provider.kwargs
    totals = client.totals("r1")
    assert (totals.input_tokens, totals.output_tokens, totals.calls) == (11, 7, 1)


@pytest.mark.parametrize("behaviour,etype", [
    (raise_timeout, "timeout"),
    (raise_connection, "service_unavailable"),
    (raise_status, "provider_error"),
    (malformed_response, "provider_error"),
])
def test_failures_are_typed_and_counted(make, behaviour, etype):
    client, _ = make(behaviour)
    res = client.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    assert not res.ok and res.error_type == etype
    assert client.totals("r1").calls == 1


def test_availability_requires_the_configured_model(make):
    client, provider = make(ok, models=("other:latest",))
    assert not client.is_available()
    assert provider.closed == 1


def test_availability_reports_installed_model(make):
    client, provider = make(ok)
    assert client.is_available()
    assert provider.closed == 1


def test_totals_unknown_run_are_zero(make):
    client, _ = make(ok)
    totals = client.totals("nope")
    assert (totals.input_tokens, totals.output_tokens, totals.calls) == (0, 0, 0)


def test_no_credentials_or_prompts_are_written_to_call_log(make, caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = make(ok)
    client.complete(run_id="r1", purpose="resolver", system="system", prompt="private prompt")
    assert "private prompt" not in caplog.text
    with client.engine.connect() as connection:
        record = repr(connection.execute(llm_call.select()).all())
        assert "private prompt" not in record
