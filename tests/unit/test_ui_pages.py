"""Streamlit pages with a fake Orchestrator client (streamlit.testing AppTest, no server)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agents.jobs import Job
from agents.schemas import PhaseStatus

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def job(kind="campaign", status="running", **kw):
    base = dict(job_id=f"{kind}-1", kind=kind, key=kind, lane="pipeline", label="start campaign", status=status,
                submitted_at=NOW, started_at=NOW)
    base.update(kw)
    return Job(**base)


class FakeApi:
    def __init__(self, active=(), mapping=None):
        self.active = list(active)
        self.started = []
        self.mapping = mapping

    def jobs(self, active=None, kind=None):
        return list(self.active)

    def runs(self, limit=50):
        return []

    def phase(self):
        return PhaseStatus(current_phase=1, pending_target=None, gate=[], served={})

    def questions(self, status="open", run_id=None):
        return []

    def start_campaign_job(self, operator):
        self.started.append(operator)
        j = job(status="queued", operator=operator)
        self.active = [j]
        return j

    def job(self, job_id):
        return next((j for j in self.active if j.job_id == job_id), job(status="succeeded", result={
            "run_id": "r1", "status": "completed", "campaign_status": "paused", "open_questions": 3}, seconds=4.0))


@pytest.fixture(autouse=True)
def _no_refresh(monkeypatch):
    monkeypatch.setenv("UI_REFRESH_SECONDS", "0")


def app(api, page="ui/app.py", operator=None):
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    at.session_state["api"] = api
    if operator:
        at.query_params["operator"] = operator
    return at


def button(at, label):
    return next(b for b in at.button if b.label.startswith(label))


def test_operator_comes_from_the_url_and_is_written_back():
    at = app(FakeApi(), operator="alice").run()
    assert at.sidebar.text_input[0].value == "alice"
    at.sidebar.text_input[0].input("bob").run()
    assert at.query_params["operator"] == "bob"


def test_campaign_button_launches_a_job_and_is_disabled_while_it_runs():
    api = FakeApi()
    at = app(api, operator="ann").run()
    assert not button(at, "Start a campaign").disabled
    button(at, "Start a campaign").click().run()
    assert api.started == ["ann"]
    assert button(at, "Start a campaign").disabled
    assert any("en cours" in i.value for i in at.info)


def test_finished_job_result_is_shown():
    api = FakeApi()
    at = app(api).run()
    button(at, "Start a campaign").click().run()
    api.active = []                                     # the job ended on the server
    at.run()
    assert any("Run r1 completed" in s.value and "3 open question" in s.value for s in at.success)
    assert not button(at, "Start a campaign").disabled
