"""Streamlit pages with a fake Orchestrator client (streamlit.testing AppTest, no server)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agents.jobs import Job
from agents.schemas import ColumnMapping, MappingConflict, PhaseStatus, SchemaProposal, SchemaReference
from ui.api_client import ApiCallError

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
    def __init__(self, active=(), mapping="validated", latest=None):
        self.active = list(active)
        self.started = []
        self.mapping = validated() if mapping == "validated" else mapping
        self.latest = latest
        self.validations = []
        self.discover_jobs = []

    def validated_mapping(self):
        return self.mapping

    def latest_proposal(self):
        return self.latest

    def schema_reference(self):
        return SchemaReference(targets=TARGETS, required_targets=TARGETS[:2], mapping=REFERENCE)

    def schema_discover(self, source="reference"):
        self.latest = proposal(REFERENCE, fallback=True)
        return self.latest

    def schema_discover_job(self, source="llm"):
        self.discover_jobs.append(source)
        j = job("schema_discover", status="queued", label="schema discovery", lane="llm")
        self.active = [j]
        return j

    def validate_mapping(self, proposal_id, operator, mapping=None):
        self.validations.append((proposal_id, operator, mapping))
        if mapping.get("originTool") is None:
            raise ApiCallError("validate the mapping", "HTTP 409: 1 Column_Mapping conflict(s): "
                               "DiagToolsVersions.DiagToolName is mapped by 0 column(s)", 409)
        self.mapping = validated(operator)
        return self.mapping

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


# ---------------------------------------------------------------- mapping step (part 2)
def test_no_validated_mapping_disables_the_campaign():
    at = app(FakeApi(mapping=None)).run()
    assert any("Aucun mapping validé" in w.value for w in at.warning)
    assert button(at, "Start a campaign").disabled


def test_validated_mapping_status():
    at = app(FakeApi()).run()
    assert any("Mapping validé par ann le 04/11/2026" in s.value for s in at.success)
    assert not button(at, "Start a campaign").disabled


def test_ai_proposal_is_a_job_and_shows_conflicts_and_differences():
    api = FakeApi(mapping=None)
    at = app(api).run()
    button(at, "Proposer le mapping avec l'IA").click().run()
    assert api.discover_jobs == ["llm"] and button(at, "Proposer le mapping avec l'IA").disabled
    wrong = proposal({"originTool": None, "VIN": "Vehicle.VIN"}, conflicts=[MappingConflict(
        target_attribute="DiagToolsVersions.DiagToolName", source_columns=[],
        message="DiagToolsVersions.DiagToolName is mapped by 0 column(s)")])
    api.active = []
    api.job = lambda job_id: job("schema_discover", status="succeeded", label="schema discovery",
                                 result=wrong.model_dump(mode="json"), seconds=31.0)
    at.run()
    assert any("1 différence(s)" in m.value for m in at.markdown)
    assert any("Validation refusée tant qu'il reste 1 conflit" in e.value for e in at.error)
    assert [s.label for s in at.selectbox][:2] == ["originTool", "VIN"]


def test_operator_corrects_then_validates():
    api = FakeApi(mapping=None, latest=proposal({"originTool": None, "VIN": "Vehicle.VIN"}))
    at = app(api, operator="carla").run()
    at.button(key="FormSubmitter:mapping_form-Valider le mapping").click().run()
    assert api.validations[-1] == ("p1", "carla", {"originTool": None, "VIN": "Vehicle.VIN"})
    assert any("Validation refusée" in e.value for e in at.error) and api.mapping is None
    at.selectbox[0].select("DiagToolsVersions.DiagToolName")          # form: sent with the submit click
    at.button(key="FormSubmitter:mapping_form-Valider le mapping").click().run()
    assert api.validations[-1][2] == {"originTool": "DiagToolsVersions.DiagToolName", "VIN": "Vehicle.VIN"}
    assert any("Mapping validé par carla" in s.value for s in at.success)
    assert not button(at, "Start a campaign").disabled


def test_reference_mapping_button():
    api = FakeApi(mapping=None)
    at = app(api).run()
    button(at, "Utiliser le mapping de référence").click().run()
    assert any("Mapping de référence (Rule_Config)" in m.value for m in at.markdown)
    assert any("Aucune différence" in m.value for m in at.markdown)


# ---------------------------------------------------------------- Question_Advisor (part 3)
def _question():
    from hitl.schemas import HumanQuestion
    return HumanQuestion(question_id="q1", run_id="r1", pattern_key="Vehmodel|LEGACY_B_CONFLICT", field="Vehmodel",
                         reason="LEGACY_B_CONFLICT", rule_id="R7", row_count=2, example_rows=[], missing="m", why="w",
                         llm_suggestion=None, llm_status="not_applicable", allowed_values=None,
                         options=["reject_group", "case_by_case"], status="open", parent_question_id=None,
                         created_at=NOW, closed_at=None)


class AdviceApi(FakeApi):
    def __init__(self, advice=None):
        super().__init__()
        self.advice = advice or {}
        self.asked = []

    def questions(self, status="open", run_id=None):
        return [_question()]

    def latest_advice(self):
        return self.advice

    def advise_job(self, question_id, operator):
        self.asked.append((question_id, operator))
        j = job("advise", status="queued", key=f"advise:{question_id}", lane="llm", label="agent advice")
        self.active = [j]
        return j


def test_advise_button_runs_a_job_and_shows_the_advice():
    from agents.question_advisor import QuestionAdvice
    api = AdviceApi()
    at = app(api, page="ui/pages/2_questions.py", operator="ann").run()
    button(at, "Demander l'avis de l'agent").click().run()
    assert api.asked == [("q1", "ann")] and button(at, "Demander l'avis de l'agent").disabled
    assert any("agent advice : en cours" in i.value for i in at.info)
    api.active = []
    api.advice = {"q1": QuestionAdvice(
        advice_id="a1", question_id="q1", pattern_key="Vehmodel|LEGACY_B_CONFLICT", status="recommended",
        recommendation={"option": "case_by_case", "value": None, "confidence": 0.7, "justification": "two sources",
                        "evidence": ["Legacy B holds MASTER"], "unknowns": ["which system is right"]},
        tool_calls=[{"iteration": 1, "tool": "legacy_b_model", "params": "{}", "result_summary": "found"}],
        llm_calls=2, input_tokens=900, output_tokens=120, seconds=14.2, created_at=NOW)}
    api.job = lambda job_id: job("advise", status="succeeded", key="advise:q1", label="agent advice", result={})
    at.run()
    assert any("Option conseillée : **Case by case**" in s.value for s in at.success)
    assert any("1,020 tokens" in m.value for m in at.markdown)
    assert any("which system is right" in m.value for m in at.markdown)
    assert not button(at, "Demander l'avis de l'agent").disabled
