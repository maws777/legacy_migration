import logging
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from agents.llm_client import LLMClient, llm_call
from rules.config import LLMConfig
from target.schema import make_engine

KEY = "sk-test-SECRET-123456"


class Provider:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.created = 0
        self.kwargs = None

    def __call__(self, api_key, timeout_s):
        self.created += 1
        assert api_key == KEY
        outer = self

        class Messages:
            def create(self, **kw):
                outer.kwargs = kw
                return outer.behaviour()

        return SimpleNamespace(messages=Messages())


def ok():
    return SimpleNamespace(content=[SimpleNamespace(text='{"a": 1}')], model="claude-x",
                           usage=SimpleNamespace(input_tokens=11, output_tokens=7))


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def raise_timeout():
    raise anthropic.APITimeoutError(request=_req())


def raise_provider():
    raise anthropic.APIConnectionError(message=f"boom {KEY}", request=_req())


def raise_status():
    raise anthropic.InternalServerError("server", response=httpx.Response(500, request=_req()), body=None)


@pytest.fixture
def make(tmp_path):
    def _make(behaviour):
        provider = Provider(behaviour)
        cfg = LLMConfig(price_eur_per_input_token=0.0, price_eur_per_output_token=0.0, timeout_s=3)
        return LLMClient(cfg, make_engine("sqlite:///:memory:"), provider), provider
    return _make


def test_success_returns_text_and_tokens(make, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    client, provider = make(ok)
    res = client.complete(run_id="r1", purpose="resolver", system="s", prompt="p", max_tokens=50)
    assert res.ok and res.text == '{"a": 1}' and (res.input_tokens, res.output_tokens) == (11, 7)
    assert provider.kwargs["max_tokens"] == 50 and provider.kwargs["messages"][0]["content"] == "p"
    t = client.totals("r1")
    assert (t.input_tokens, t.output_tokens, t.calls) == (11, 7, 1)


@pytest.mark.parametrize("behaviour,etype", [(raise_timeout, "timeout"), (raise_provider, "provider_error"),
                                             (raise_status, "provider_error")])
def test_failures_are_typed_and_counted(make, monkeypatch, behaviour, etype):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    client, _ = make(behaviour)
    res = client.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    assert not res.ok and res.error_type == etype and KEY not in res.message
    assert client.totals("r1").calls == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_no_provider_contact(make, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    client, provider = make(ok)
    assert not client.is_available()
    res = client.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    assert (res.ok, res.error_type) == (False, "missing_key") and provider.created == 0
    assert client.totals("r1").calls == 0


def test_totals_unknown_run_are_zero(make):
    client, _ = make(ok)
    t = client.totals("nope")
    assert (t.input_tokens, t.output_tokens, t.calls) == (0, 0, 0)


def test_key_never_written(make, monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    caplog.set_level(logging.DEBUG)
    client, _ = make(raise_provider)
    client.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    client2, _ = make(ok)
    client2.complete(run_id="r1", purpose="resolver", system="s", prompt="p")
    assert KEY not in caplog.text
    with client.engine.connect() as c:
        assert KEY not in repr(c.execute(llm_call.select()).all())
