import httpx
import pytest

from client_app.main import ClientApp

pytestmark = pytest.mark.integration
BODY = {"vin": "V1", "operations": [{"diagnostic_op_id": "OP1"}]}


def app_with(script, interval=0.5):
    calls = iter(script)
    sleeps = []

    def handler(request: httpx.Request):
        step = next(calls)
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://orchestrator")
    return ClientApp("http://orchestrator", ["V1", "V2"], interval_s=interval, client=client,
                     sleep=sleeps.append, out=lambda s: None), sleeps


def test_counts_reasons_and_interval():
    req = httpx.Request("GET", "http://orchestrator/x")
    app, sleeps = app_with([
        (200, BODY), (404, {"error": {"code": "NOT_FOUND"}}), (503, {"error": {"code": "BACKEND_UNAVAILABLE"}}),
        httpx.ConnectError("refused", request=req), httpx.ReadTimeout("slow", request=req), (200, BODY),
        (200, {"vin": "V1", "operations": []}),
    ])
    stats = app.run(7)
    assert stats.queries == 7 and sleeps == [0.5] * 6
    assert (stats.available, stats.unavailable) == (4, 3)
    assert (stats.parity_ok, stats.parity_failed) == (2, 5)
    assert stats.reasons["HTTP 503"] == 1 and stats.reasons["ConnectError"] == 1 and stats.reasons["timeout"] == 1
    assert stats.reasons["parity: control VIN answered 404"] == 1
    assert stats.reasons["parity: body differs from the expected answer"] == 1
    assert [r.vin for r in app.results[:3]] == ["V1", "V2", "V1"]
    assert all(r.latency_ms >= 0 for r in app.results)


def test_expected_answers_and_interval_bounds():
    app, _ = app_with([(200, {"vin": "V1", "operations": []})])
    app.expected = {"V1": BODY}
    res = app.query("V1")
    assert res.available and not res.parity
    with pytest.raises(ValueError):
        ClientApp("http://x", ["V1"], interval_s=0.05)
    with pytest.raises(ValueError):
        ClientApp("http://x", ["V1"], interval_s=61)
