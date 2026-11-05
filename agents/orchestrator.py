"""Orchestrator (port 8000): client routes routed per phase, plus the ops routes used by the UI.

Run: ``uvicorn agents.orchestrator:app --port 8000``. Services are built on the first request.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from agents.jobs import Job, JobConflict, JobManager
from agents.question_advisor import AdviceToolCall, AdvisorContext, QuestionAdvice, QuestionAdvisor
from agents.schemas import (
    ColumnMapping, DiagnosticsResponse, ModelReference, OperatorRequest, PhaseStatus, ReconResult, RollbackRequest,
    SchemaProposal, SchemaReference, TransitionLogEntry, TransitionRequest, TransitionResult, ValidateMappingRequest,
)
from rules.fields import normalize_vin
from hitl.schemas import AnswerResult, HumanAnswer, HumanQuestion
from reporting.decision_log import UnknownRunError, export_media_type
from reporting.schemas import (
    DecisionPage, LogFilters, RunInfo, RunMetrics, RunReport, StartRunRequest, ToolCallRecord,
)
from rules.errors import ErrorCode, error_payload
from rules.types import Action, DecidedBy


class ApiError(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


@dataclass
class Services:
    pipeline: Any                                   # migration.pipeline.MigrationPipeline
    export_dir: Path = Path("var/exports")
    phase: Any = None                               # migration.phase_controller.PhaseController
    legacy_a: Any = None                            # agents.api_agent_legacy.LegacyAClient
    legacy_b: Any = None                            # agents.api_agent_legacy_b.ApiAgentLegacyB
    target: httpx.Client | None = None              # client of the Target_API
    expected: Any = None                            # migration.expected.ExpectedRecordBuilder
    reconciler: Any = None                          # migration.reconciliation.Reconciler
    jobs: JobManager = field(default_factory=JobManager)   # background jobs of the long operations
    extra: dict[str, Any] = field(default_factory=dict)


def latest_summary(pipeline) -> dict | None:
    """Coverage and exclusion summary of the latest completed full Run (shown at cutover approval)."""
    run = pipeline.runs.latest_full_run()
    if run is None:
        return None
    try:
        r = pipeline.reports.build(run["run_id"])
    except UnknownRunError:
        return None
    return {"run_id": r.run_id, "analysed": r.analysed, "loaded": r.loaded, "coverage_pct": r.coverage_pct,
            "rejected_out_of_scope": r.rejected_out_of_scope, "rejected_error": r.rejected_error,
            "pending_human": r.pending_human}


def build_services(pipeline, *, legacy_a=None, legacy_b=None, target: httpx.Client | None = None,
                   export_dir: Path = Path("var/exports"), reconciler_factory=None) -> Services:
    """Wire the Phase_Controller, the expected-record builder and the backends around a pipeline."""
    from migration.expected import ExpectedRecordBuilder
    from migration.phase_controller import PhaseController, make_gate

    def mapping():
        m = pipeline.schema_agent.validated_mapping()
        return m.mapping if m is not None else dict(pipeline.config().column_mapping.mapping)

    def shared():
        run = pipeline.runs.latest_full_run()
        return pipeline.deps.shared_ids.load(run["run_id"]) if run else frozenset()

    def legacy_b_model(vin: str):
        if legacy_b is None:
            return None
        try:
            t = legacy_b.model_reference(vin)
        except Exception:  # noqa: BLE001 - Legacy B down: no model reference
            return None
        return t.vehicle_model if t is not None else None

    s = Services(pipeline=pipeline, export_dir=export_dir, legacy_a=legacy_a, legacy_b=legacy_b, target=target)
    s.expected = ExpectedRecordBuilder(pipeline.config, mapping, pipeline.hitl.decisions.views,
                                       pipeline.cache.all, shared, legacy_b_model)
    if reconciler_factory is not None:
        s.reconciler = reconciler_factory(s)
    latest = (lambda: s.reconciler.latest("control_sample")) if s.reconciler is not None else (lambda: None)
    s.phase = PhaseController(pipeline.ops_engine, make_gate(pipeline.hitl.open_count, latest),
                              legacy_a.is_reachable if legacy_a is not None else (lambda: False),
                              lambda: latest_summary(pipeline))
    pipeline.deps.phase = s.phase.current
    return s


def default_services() -> Services:
    from agents.api_agent_legacy import LegacyAClient
    from agents.api_agent_legacy_b import ApiAgentLegacyB
    from migration.pipeline import MigrationPipeline
    from rules.config import load_rule_config
    cfg = load_rule_config()
    paths, urls = cfg.pipeline.paths, cfg.pipeline.services
    # MIGRATION_SOURCE / MIGRATION_VAR_DIR let scripts/start_all.ps1 serve another campaign (e.g. edge cases).
    source = os.environ.get("MIGRATION_SOURCE") or paths.source_csv
    var_dir = Path(os.environ.get("MIGRATION_VAR_DIR") or Path(paths.ops_db).parent)
    export_dir = var_dir / "exports" if os.environ.get("MIGRATION_VAR_DIR") else Path(paths.export_dir)
    legacy_b = ApiAgentLegacyB.from_url(urls.legacy_b, cfg)
    components: dict[str, Any] = {"legacy_b": legacy_b} if cfg.pipeline.legacy_b_lookup else {}
    if os.environ.get("MIGRATION_NO_LLM", "").strip() in ("1", "true", "yes"):
        from agents.llm_client import NoLLM
        components["llm"] = NoLLM("LLM disabled (MIGRATION_NO_LLM)")
    pipeline = MigrationPipeline(source, var_dir=var_dir, **components)
    try:
        from migration.reconciliation import Reconciler
        factory = Reconciler.factory
    except ImportError:  # pragma: no cover - before task 10
        factory = None
    return build_services(pipeline, legacy_a=LegacyAClient.from_url(urls.legacy_a), legacy_b=legacy_b,
                          target=httpx.Client(base_url=urls.target, timeout=5.0),
                          export_dir=export_dir, reconciler_factory=factory)


class UTF8JSONResponse(JSONResponse):
    """Explicit charset so Windows PowerShell 5.1 ``Invoke-RestMethod`` decodes "≈", "€" and accents."""
    media_type = "application/json; charset=utf-8"


def create_app(services: Services | Callable[[], Services] | None = None) -> FastAPI:
    app = FastAPI(title="Orchestrator", default_response_class=UTF8JSONResponse)
    lock = threading.Lock()
    holder: dict[str, Services] = {}
    factory = services if callable(services) else (lambda: services) if services is not None else default_services

    def svc() -> Services:
        with lock:
            if "s" not in holder:
                holder["s"] = factory()
            return holder["s"]

    app.state.services = svc

    @app.exception_handler(ApiError)
    def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=error_payload(exc.code, exc.message, exc.details))

    @app.exception_handler(UnknownRunError)
    def _unknown_run(_request: Request, exc: UnknownRunError) -> JSONResponse:
        return JSONResponse(status_code=404, content=error_payload("UNKNOWN_RUN", str(exc), {"run_id": exc.run_id}))

    def run_or_404(run_id: str) -> RunInfo:
        try:
            return svc().pipeline.run_info(run_id)
        except KeyError:
            raise UnknownRunError(run_id) from None

    # ---------------------------------------------------------------- runs
    @app.post("/ops/campaigns", status_code=202, response_model=RunInfo)
    def start_campaign(body: StartRunRequest | None = None) -> RunInfo:
        return svc().pipeline.start_campaign((body or StartRunRequest()).operator)

    @app.get("/ops/runs", response_model=list[RunInfo])
    def list_runs(limit: int = Query(50, ge=1, le=1000), offset: int = Query(0, ge=0)) -> list[RunInfo]:
        p = svc().pipeline
        return [p.run_info(r["run_id"]) for r in p.runs.list_runs(limit=limit, offset=offset)]

    @app.get("/ops/runs/{run_id}", response_model=RunInfo)
    def get_run(run_id: str) -> RunInfo:
        return run_or_404(run_id)

    @app.get("/ops/runs/{run_id}/report", response_model=RunReport)
    def get_report(run_id: str) -> RunReport:
        run_or_404(run_id)
        return svc().pipeline.reports.build(run_id)

    @app.get("/ops/runs/{run_id}/metrics", response_model=RunMetrics)
    def get_metrics(run_id: str) -> RunMetrics:
        run_or_404(run_id)
        m = svc().pipeline.metrics.get(run_id)
        if m is None:
            raise UnknownRunError(run_id)
        return m

    @app.get("/ops/runs/{run_id}/decisions", response_model=DecisionPage)
    def get_decisions(run_id: str, source_row: int | None = None, field: str | None = None,
                      action: Action | None = None, rule_id: str | None = None, decided_by: DecidedBy | None = None,
                      limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> DecisionPage:
        run_or_404(run_id)
        filters = LogFilters(source_row=source_row, field=field, action=action, rule_id=rule_id, decided_by=decided_by)
        return svc().pipeline.decision_logger.query(run_id, filters, limit, offset)

    @app.get("/ops/runs/{run_id}/tool-calls", response_model=list[ToolCallRecord])
    def get_tool_calls(run_id: str, pattern_key: str | None = None) -> list[ToolCallRecord]:
        """Resolver_Agent tool calls of a Run (tool, parameters, summarized result)."""
        run_or_404(run_id)
        return svc().pipeline.decision_logger.tool_calls(run_id, pattern_key)

    @app.get("/ops/runs/{run_id}/decisions/export")
    def export_decisions(run_id: str, format: Literal["json", "csv"] = "json") -> FileResponse:
        s = svc()
        path = s.pipeline.decision_logger.export(run_id, format, s.export_dir)
        return FileResponse(path, media_type=export_media_type(format), filename=path.name)

    # ---------------------------------------------------------------- HITL (task 6)
    @app.post("/ops/campaigns/{campaign_id}/resume", status_code=202, response_model=RunInfo)
    def resume_campaign(campaign_id: str, body: OperatorBody) -> RunInfo:
        from migration.pipeline import CampaignNotPaused
        try:
            return svc().pipeline.resume(campaign_id, body.operator)
        except CampaignNotPaused as exc:
            raise ApiError(409, "CONFLICT", str(exc)) from None

    @app.get("/ops/questions", response_model=list[HumanQuestion])
    def list_questions(status: Literal["open", "closed", "superseded"] | None = None,
                       run_id: str | None = None) -> list[HumanQuestion]:
        return svc().pipeline.hitl.list(status, run_id)

    @app.get("/ops/questions/{question_id}", response_model=HumanQuestion)
    def get_question(question_id: str) -> HumanQuestion:
        q = svc().pipeline.hitl.get(question_id)
        if q is None:
            raise ApiError(404, "NOT_FOUND", f"unknown question {question_id}")
        return q

    @app.post("/ops/questions/{question_id}/answer", response_model=AnswerResult)
    def answer_question(question_id: str, body: HumanAnswer) -> AnswerResult:
        from hitl.queue import AnswerRefused, ValueRejected
        try:
            return svc().pipeline.hitl.answer(question_id, body)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", f"unknown question {question_id}") from None
        except AnswerRefused as exc:
            raise ApiError(409, "CONFLICT", str(exc)) from None
        except ValueRejected as exc:
            raise ApiError(422, "VALIDATION_ERROR", exc.failed_rule, {"failed_rule": exc.failed_rule}) from None

    # ---------------------------------------------------------------- Question_Advisor
    @app.post("/ops/questions/{question_id}/advise", status_code=202, response_model=Job)
    def advise_question(question_id: str, body: StartRunRequest | None = None) -> Job:
        """Ask the Question_Advisor in the background (result: QuestionAdvice). It never answers the question."""
        s = svc()
        q = s.pipeline.hitl.get(question_id)
        if q is None:
            raise ApiError(404, "NOT_FOUND", f"unknown question {question_id}")
        if q.status != "open":
            raise ApiError(409, "CONFLICT", f"question {question_id} is {q.status}")
        operator = (body or StartRunRequest()).operator
        return submit("advise", f"advise:{question_id}", "llm", lambda: consult_advisor(s, q, operator),
                      "agent advice", operator)

    @app.get("/ops/questions/{question_id}/advice", response_model=list[QuestionAdvice])
    def question_advice(question_id: str) -> list[QuestionAdvice]:
        return [QuestionAdvice(**_advice_model(r)) for r in svc().pipeline.decision_logger.advice(question_id)]

    @app.get("/ops/advice/latest", response_model=dict[str, QuestionAdvice])
    def latest_advice() -> dict[str, QuestionAdvice]:
        rows = svc().pipeline.decision_logger.advice(latest_only=True)
        return {r["question_id"]: QuestionAdvice(**_advice_model(r)) for r in rows}

    # ---------------------------------------------------------------- schema discovery (task 7)
    @app.post("/ops/schema/discover", response_model=SchemaProposal)
    def discover_schema(source: Literal["llm", "reference"] = "llm") -> SchemaProposal:
        """``source=llm`` (default): proposal by the LLM (reference fallback if unavailable);
        ``source=reference``: the reference Column_Mapping of Rule_Config, without any LLM call."""
        p = svc().pipeline
        p.ensure_llm()
        return p.schema_agent.discover(use_llm=source == "llm")

    @app.post("/ops/schema/validate", response_model=ColumnMapping)
    def validate_schema(body: ValidateMappingRequest) -> ColumnMapping:
        """Validate a proposal, or the operator's column-by-column correction of it (``mapping``)."""
        from agents.api_agent_legacy import MappingConflictError, MappingValueError
        try:
            return svc().pipeline.schema_agent.validate(body.proposal_id, body.operator, body.mapping)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", f"unknown proposal {body.proposal_id}") from None
        except MappingValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", str(exc)) from None
        except MappingConflictError as exc:
            raise ApiError(409, "CONFLICT", str(exc), [c.model_dump() for c in exc.conflicts]) from None

    @app.get("/ops/schema/proposals/latest", response_model=SchemaProposal)
    def latest_proposal() -> SchemaProposal:
        p = svc().pipeline.schema_agent.latest_proposal()
        if p is None:
            raise ApiError(404, "NOT_FOUND", "no Column_Mapping proposal waiting for validation")
        return p

    @app.get("/ops/schema/reference", response_model=SchemaReference)
    def schema_reference() -> SchemaReference:
        from agents.api_agent_legacy import TARGET_ATTRIBUTES
        cfg = svc().pipeline.config()
        return SchemaReference(targets=list(TARGET_ATTRIBUTES), required_targets=list(cfg.column_mapping.required_targets),
                               mapping=dict(cfg.column_mapping.mapping))

    @app.get("/ops/schema/mapping", response_model=ColumnMapping)
    def get_mapping() -> ColumnMapping:
        m = svc().pipeline.schema_agent.validated_mapping()
        if m is None:
            raise ApiError(404, "NOT_FOUND", "no validated Column_Mapping")
        return m

    # ---------------------------------------------------------------- background jobs
    def submit(kind: str, key: str, lane: str, fn: Callable[[], Any], label: str, operator: str | None) -> Job:
        try:
            return svc().jobs.submit(kind, key, lane, fn, label=label, operator=operator)
        except JobConflict as exc:
            raise ApiError(409, "CONFLICT", str(exc), {"job": exc.job.model_dump(mode="json")}) from None

    @app.post("/ops/jobs/campaign", status_code=202, response_model=Job)
    def job_campaign(body: StartRunRequest | None = None) -> Job:
        """Start a campaign in the background; poll ``GET /ops/jobs/{job_id}`` (result: RunInfo)."""
        operator = (body or StartRunRequest()).operator
        p = svc().pipeline
        return submit("campaign", "campaign", "pipeline", lambda: p.start_campaign(operator),
                      "start campaign", operator)

    @app.post("/ops/jobs/resume/{campaign_id}", status_code=202, response_model=Job)
    def job_resume(campaign_id: str, body: OperatorBody) -> Job:
        from migration.pipeline import CampaignNotPaused
        p = svc().pipeline
        if p.runs.campaign_status(campaign_id) != "paused":
            raise ApiError(409, "CONFLICT", str(CampaignNotPaused(campaign_id)))

        def run():
            try:
                return p.resume(campaign_id, body.operator)
            except CampaignNotPaused as exc:
                raise ApiError(409, "CONFLICT", str(exc)) from None

        return submit("resume", f"resume:{campaign_id}", "pipeline", run, "resume campaign", body.operator)

    @app.post("/ops/jobs/schema-discover", status_code=202, response_model=Job)
    def job_schema_discover(source: Literal["llm", "reference"] = "llm") -> Job:
        """Schema discovery in the background (result: SchemaProposal)."""
        p = svc().pipeline
        p.ensure_llm()
        return submit("schema_discover", "schema_discover", "llm",
                      lambda: p.schema_agent.discover(use_llm=source == "llm"), "schema discovery", None)

    @app.get("/ops/jobs", response_model=list[Job])
    def list_jobs(active: bool | None = None, kind: str | None = None) -> list[Job]:
        return svc().jobs.list(active, kind)

    @app.get("/ops/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str) -> Job:
        job = svc().jobs.get(job_id)
        if job is None:
            raise ApiError(404, "NOT_FOUND", f"unknown job {job_id} (jobs are kept in memory)")
        return job

    # ---------------------------------------------------------------- client routes (task 9)
    def unavailable(backend: str, why: str) -> ApiError:
        return ApiError(503, "BACKEND_UNAVAILABLE", f"backend unavailable: {why}", {"backend": backend})

    @app.get("/api/vehicles/{vin}/diagnostics", response_model=DiagnosticsResponse)
    def client_diagnostics(vin: str, background: BackgroundTasks) -> DiagnosticsResponse:
        s = svc()
        phase = s.phase.current()                    # read once: the request ends on this backend
        v = normalize_vin(vin)
        if phase == 4:
            try:
                r = s.target.get(f"/target/vehicles/{v}/diagnostics")
            except httpx.HTTPError as exc:
                raise unavailable("target", type(exc).__name__) from None
            if r.status_code not in (200, 404):
                raise unavailable("target", f"HTTP {r.status_code}")
            s.phase.served["target"] += 1
            if r.status_code == 404:
                raise ApiError(404, "NOT_FOUND", f"no diagnostics for VIN {v}")
            return DiagnosticsResponse.model_validate(r.json())
        try:
            rows = s.legacy_a.rows_by_vin(v).rows
        except httpx.HTTPError as exc:
            raise unavailable("legacy_a", type(exc).__name__) from None
        expected = s.expected.for_rows(v, rows)
        s.phase.served["legacy_a"] += 1
        if phase == 3 and s.reconciler is not None:
            background.add_task(s.reconciler.compare_vin_safely, v)
        if not expected:
            raise ApiError(404, "NOT_FOUND", f"no diagnostics for VIN {v}")
        return DiagnosticsResponse(vin=v, operations=[e.item for e in expected])

    @app.get("/api/vehicles/{vin}/model", response_model=ModelReference)
    def client_model(vin: str) -> ModelReference:
        from agents.api_agent_legacy_b import LegacyBUnavailable
        s = svc()
        try:
            t = s.legacy_b.model_reference(vin)
        except LegacyBUnavailable as exc:
            raise unavailable("legacy_b", str(exc)) from None
        if t is None:
            raise ApiError(404, "NOT_FOUND", f"no model reference for VIN {normalize_vin(vin)}")
        s.phase.served["legacy_b"] += 1
        return ModelReference(vin=t.vin, vehicle_model=t.vehicle_model, updated_on=t.updated_on, errors=list(t.errors))

    # ---------------------------------------------------------------- phase ops routes (task 9)
    @app.get("/ops/phase", response_model=PhaseStatus)
    def phase_status() -> PhaseStatus:
        return svc().phase.status()

    @app.post("/ops/phase/transition", response_model=TransitionResult)
    def phase_transition(body: TransitionRequest) -> TransitionResult:
        return svc().phase.request(body.target_phase, body.operator)

    @app.post("/ops/phase/approve", response_model=TransitionResult)
    def phase_approve(body: OperatorRequest) -> TransitionResult:
        return svc().phase.approve(body.operator)

    @app.post("/ops/phase/reject", response_model=TransitionResult)
    def phase_reject(body: OperatorRequest) -> TransitionResult:
        return svc().phase.reject(body.operator)

    @app.post("/ops/phase/rollback", response_model=TransitionResult)
    def phase_rollback(body: RollbackRequest) -> TransitionResult:
        return svc().phase.rollback(body.target_phase, body.operator)

    @app.get("/ops/phase/transitions", response_model=list[TransitionLogEntry])
    def phase_transitions() -> list[TransitionLogEntry]:
        return svc().phase.transitions()

    # ---------------------------------------------------------------- reconciliation (task 10)
    def reconciler():
        r = svc().reconciler
        if r is None:
            raise ApiError(503, "BACKEND_UNAVAILABLE", "no Reconciler configured")
        return r

    @app.post("/ops/reconciliation/control-sample", response_model=ReconResult)
    def reconcile_control_sample() -> ReconResult:
        return reconciler().compare_control_sample()

    @app.get("/ops/reconciliation/latest", response_model=ReconResult)
    def reconciliation_latest(scope: Literal["vin", "control_sample"] = "control_sample") -> ReconResult:
        res = reconciler().latest(scope)
        if res is None:
            raise ApiError(404, "NOT_FOUND", f"no {scope} reconciliation yet")
        return res

    return app


class OperatorBody(BaseModel):
    operator: str = Field(min_length=1)


# ---------------------------------------------------------------- Question_Advisor wiring
def advisor_context(s: Services, question) -> AdvisorContext:
    """Read-only providers of one consultation: group rows, source rows, Legacy B, value validation."""
    from hitl.validation import validate_value
    from migration.ingestion import ingest
    p = s.pipeline
    cfg = p.config()

    def source_rows():
        m = p.schema_agent.validated_mapping()
        mapping = m.mapping if m is not None else dict(cfg.column_mapping.mapping)
        return ingest(p.deps.source_path, mapping, cfg.column_mapping).rows

    def legacy_b_model(vin: str) -> dict:
        if s.legacy_b is None:
            return {"status": "unavailable", "vehicle_model": None}
        try:
            t = s.legacy_b.model_reference(vin)
        except Exception as exc:  # noqa: BLE001 - Legacy B down: the advisor says so
            return {"status": f"unavailable ({type(exc).__name__})", "vehicle_model": None}
        if t is None:
            return {"status": "not_found", "vehicle_model": None}
        return {"status": "found", "vehicle_model": t.vehicle_model, "updated_on": t.updated_on}

    return AdvisorContext(cfg=cfg, question=question, members=lambda: p.hitl.members(question.question_id),
                          source_rows=source_rows, legacy_b_model=legacy_b_model,
                          validate_value=lambda key, value: validate_value(key, value, cfg, p._shared_view()))


def consult_advisor(s: Services, question, operator: str | None) -> QuestionAdvice:
    """Run the Question_Advisor and trace the advice, its tool calls and its tokens in the journal."""
    import uuid
    from rules.timeutil import utcnow
    p = s.pipeline
    cfg = p.config()
    advisor = QuestionAdvisor(p.ensure_llm(), cfg.pipeline.advisor_max_turns, cfg.pipeline.advisor_max_tokens)
    out = advisor.advise(advisor_context(s, question), run_id=question.run_id or "advisor")
    advice = QuestionAdvice(
        advice_id=str(uuid.uuid4()), question_id=question.question_id, pattern_key=question.pattern_key,
        status=out.status, recommendation=out.recommendation, failure=out.failure,
        tool_calls=[AdviceToolCall(iteration=c.iteration, tool=c.tool, params=c.params(), result_summary=c.summary)
                    for c in out.tool_calls],
        llm_calls=out.llm_calls, input_tokens=out.input_tokens, output_tokens=out.output_tokens,
        seconds=out.seconds, model=out.model, created_at=utcnow())
    p.decision_logger.write_advice(
        {"advice_id": advice.advice_id, "question_id": advice.question_id, "pattern_key": advice.pattern_key,
         "run_id": question.run_id, "status": advice.status,
         "recommendation": advice.recommendation.model_dump_json() if advice.recommendation else None,
         "failure": advice.failure, "llm_calls": advice.llm_calls, "input_tokens": advice.input_tokens,
         "output_tokens": advice.output_tokens, "seconds": advice.seconds, "model": advice.model,
         "operator": operator, "created_at": advice.created_at},
        [c.model_dump() for c in advice.tool_calls])
    return advice


def _advice_model(row: dict) -> dict:
    import json
    rec = json.loads(row["recommendation"]) if row.get("recommendation") else None
    return {**{k: row[k] for k in ("advice_id", "question_id", "pattern_key", "status", "failure", "llm_calls",
                                   "input_tokens", "output_tokens", "seconds", "model", "created_at")},
            "recommendation": rec, "tool_calls": row.get("tool_calls", [])}


app = create_app()
