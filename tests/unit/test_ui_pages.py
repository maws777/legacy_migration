"""Streamlit pages with a fake Orchestrator client (streamlit.testing AppTest, no server)."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from agents.jobs import Job
from agents.schemas import ColumnMapping, PhaseStatus, SchemaProposal

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)


def job(kind="campaign", status="running", **kw):
    base = dict(job_id=f"{kind}-1", kind=kind, key=kind, lane="pipeline", label="start campaign", status=status,
                submitted_at=NOW, started_at=NOW)
    base.update(kw)
    return Job(**base)


REFERENCE = {"originTool": "DiagToolsVersions.DiagToolName", "VIN": "Vehicle.VIN"}
TARGETS = ["Vehicle.VIN", "DiagToolsVersions.DiagToolName", "Label"]


def validated(by="ann"):
    return ColumnMapping(mapping=REFERENCE, validated_by=by, validated_at=NOW)


def proposal(mapping, conflicts=(), fallback=False):
    diffs = [{"column": c, "proposed": t, "reference": REFERENCE.get(c)} for c, t in mapping.items()
             if t != REFERENCE.get(c)]
    return SchemaProposal(proposal_id="p1", column_mapping=mapping, formats=[], format_criterion="x",
                          conflicts=list(conflicts), fallback=fallback, created_at=NOW,
                          differences_from_reference=diffs)


class FakeApi:
    def __init__(self, active=(), mapping="validated", runs=(), open_questions=0, recon=None, phase=1):
        self.active = list(active)
        self._runs = list(runs)
        self._open = open_questions
        self._recon = recon
        self._phase = phase
        self.started = []
        self.validated = []
        self.mapping = (ColumnMapping(mapping={"VIN": "vin"}, validated_by="ann", validated_at=NOW)
                        if mapping == "validated" else mapping)

    def schema_mapping(self):
        return self.mapping

    def latest_reconciliation(self, scope="control_sample"):
        return self._recon

    def report(self, run_id):
        return getattr(self, "_report", None)

    def metrics(self, run_id):
        return None

    def cost(self, run_id):
        return None

    def target_stats(self):
        return {}

    def latest_advice(self):
        return getattr(self, "_advice", {})

    def advise_job(self, question_id, operator):
        self.advised = getattr(self, "advised", []) + [question_id]
        return job("advise", "queued", key=f"advise:{question_id}", lane="llm")

    def transitions(self):
        return []

    def schema_discover_job(self, source="llm"):
        j = job("schema_discover", "succeeded", result=SchemaProposal(
            proposal_id="p1", column_mapping={"VIN": "vin", "x": None}, formats=[], format_criterion="",
            conflicts=[], fallback=False, created_at=NOW).model_dump(mode="json"), seconds=1.0)
        self.active = [j]
        return j

    def validate_schema(self, proposal_id, operator):
        self.validated.append((proposal_id, operator))
        self.mapping = ColumnMapping(mapping={"VIN": "vin"}, validated_by=operator, validated_at=NOW)
        return self.mapping

    def jobs(self, active=None, kind=None):
        return list(self.active)

    def runs(self, limit=50):
        return self._runs

    def phase(self):
        return PhaseStatus(current_phase=self._phase, pending_target=None, gate=[], served={})

    def questions(self, status="open", run_id=None):
        return [SimpleNamespace(row_count=10)] * self._open

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


def app(api, page="ui/pages/0_Accueil.py", operator=None):
    at = AppTest.from_file(str(ROOT / page), default_timeout=30)
    at.session_state["api"] = api
    if operator:
        at.session_state["_operator"] = operator
    return at


def button(at, label):
    return next(b for b in at.button if label in b.label)


def test_operator_comes_from_the_url_and_is_written_back():
    def frame_only():
        from ui.common import frame
        frame()

    at = AppTest.from_function(frame_only, default_timeout=30)
    at.query_params["operator"] = "alice"
    at.run()
    assert at.sidebar.text_input[0].value == "alice"
    at.sidebar.text_input[0].input("bob").run()
    assert at.query_params["operator"] == "bob"


def test_campaign_button_launches_a_job_and_is_disabled_while_it_runs():
    api = FakeApi()
    at = app(api, operator="ann").run()
    assert not button(at, "Lancer la migration").disabled
    button(at, "Lancer la migration").click().run()
    assert api.started == ["ann"]
    assert button(at, "Lancer la migration").disabled
    assert any("en cours" in i.value for i in at.info)


def test_finished_job_result_is_shown():
    api = FakeApi()
    at = app(api).run()
    button(at, "Lancer la migration").click().run()
    api.active = []                                     # the job ended on the server
    at.run()
    assert any("Traitement termin" in s.value and "3 question" in s.value for s in at.success)
    assert not button(at, "Lancer la migration").disabled


def test_without_a_validated_mapping_the_first_step_is_the_schema_and_it_can_be_validated():
    api = FakeApi(mapping=None)
    at = app(api, operator="ann").run()
    assert any("tape 1" in m.value for m in at.markdown)
    button(at, "Utiliser la correspondance").click().run()
    button(at, "valide cette").click().run()
    assert api.validated == [("p1", "ann")]
    assert any("tape 2" in m.value for m in at.markdown)


def test_every_page_renders_without_error():
    for page in ("ui/pages/1_Resultats.py", "ui/pages/2_Questions.py", "ui/pages/3_Journal.py",
                 "ui/pages/4_Bascule.py"):
        at = app(FakeApi(), page=page).run()
        assert not at.exception, (page, at.exception)


def _run(campaign_status):
    return SimpleNamespace(run_id="r1", campaign_id="c1", started_at=NOW, status="completed", kind="full",
                           campaign_status=campaign_status, open_questions=0)


@pytest.mark.parametrize("name, api", [
    ("questions", FakeApi(runs=[_run("paused")], open_questions=2)),
    ("resume", FakeApi(runs=[_run("paused")])),
    ("verify", FakeApi(runs=[_run("done")])),
    ("switch", FakeApi(runs=[_run("done")], recon=SimpleNamespace(compared=5, gaps=0), phase=3)),
    ("finished", FakeApi(runs=[_run("done")], recon=SimpleNamespace(compared=5, gaps=0), phase=4)),
])
def test_every_step_panel_of_the_home_page_renders(name, api):
    at = app(api, operator="ann").run()
    assert not at.exception, (name, at.exception)


def test_home_page_can_rerun_a_full_treatment_at_any_time():
    api = FakeApi(runs=[_run("done")], recon=SimpleNamespace(compared=5, gaps=0), phase=4)
    at = app(api, operator="ann").run()
    button(at, "Relancer un traitement complet").click().run()
    assert api.started == ["ann"] and not at.exception


def test_decisions_page_lists_and_cancels_a_decision():
    d = SimpleNamespace(pattern_key="ecuCode|X", kind="value", value="ID0004", operator="bob", answered_at=NOW,
                        field="ecuCode", row_count=12)
    api = FakeApi()
    api.decisions_made = lambda: [d] if not api.revoked else []
    api.revoked = []
    api.revoke_decision = lambda key, op: api.revoked.append((key, op)) or []
    at = app(api, page="ui/pages/5_Decisions.py", operator="ann").run()
    assert not at.exception
    button(at, "Annuler").click().run()
    assert api.revoked == [("ecuCode|X", "ann")]
    assert any("Aucune décision" in i.value for i in at.info)


def _question(i, decision="map", value="ID0004", confidence=0.8, issue=None, suggestion=True):
    s = SimpleNamespace(decision=decision, value=value, confidence=confidence, justification="j") if suggestion else None
    return SimpleNamespace(question_id=f"q{i}", field="ecuCode", row_count=10 - i, scope="group", source_row_id=None,
                           missing="m", why="w", example_rows=[], llm_suggestion=s, suggestion_issue=issue,
                           llm_status="degraded", options=["apply_value", "reject_group", "case_by_case"],
                           allowed_values=["ID0004"])


def test_batch_validation_answers_the_ticked_suggestions_only():
    api = FakeApi()
    api.questions = lambda status="open", run_id=None: [
        _question(1, confidence=0.9), _question(2, decision="reject", value=None, confidence=0.8),
        _question(3, confidence=0.4), _question(4, value="ID0009", issue="R3: not allowed"),
        _question(5, suggestion=False)]
    api.answered = []
    api.answer = lambda qid, option, op, value=None: api.answered.append((qid, option, value)) or SimpleNamespace(created_questions=[])
    at = app(api, page="ui/pages/2_Questions.py", operator="ann").run()
    assert not at.exception
    assert at.tabs[1].label.endswith("(3)")
    button(at, "Valider les suggestions cochées").click().run()
    assert api.answered == [("q1", "apply_value", "ID0004"), ("q2", "reject_group", None)]


def test_batch_uses_the_advisor_recommendation_for_questions_without_resolver_suggestion():
    api = FakeApi()
    api.questions = lambda status="open", run_id=None: [_question(5, suggestion=False)]
    rec = SimpleNamespace(option="apply_value", value="ID0007", confidence=0.85, justification="seen in Legacy B",
                          evidence=["row 3"], unknowns=[])
    api._advice = {"q5": SimpleNamespace(status="recommended", recommendation=rec, failure=None)}
    api.answered = []
    api.answer = lambda qid, option, op, value=None: api.answered.append((qid, option, value)) or SimpleNamespace(created_questions=[])
    at = app(api, page="ui/pages/2_Questions.py", operator="ann").run()
    assert not at.exception
    button(at, "Valider les suggestions cochées").click().run()
    assert api.answered == [("q5", "apply_value", "ID0007")]

@pytest.mark.parametrize("pending", [0, 5])
def test_results_page_shows_a_verdict_and_the_table(pending):
    api = FakeApi(runs=[_run("done")])
    api._report = SimpleNamespace(
        analysed=100, loaded=80, coverage_pct=80.0, migrate=60, correct=20, reject=15, ask_human=pending,
        pending_human=pending, rejected_out_of_scope=10, rejected_error=5, traced_pct=100.0, seconds=1.5,
        tokens=0, cost_eur=0.0, notes=[], text="rapport")
    at = app(api, page="ui/pages/1_Resultats.py").run()
    assert not at.exception
    assert (len(at.warning) == 1) == bool(pending) and (len(at.success) == 1) == (not pending)
