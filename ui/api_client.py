"""UI client of the Orchestrator ops routes only (the UI never opens a database)."""
from __future__ import annotations

from typing import Any

import httpx

from agents.jobs import Job
from agents.schemas import PhaseStatus, ReconResult, TransitionLogEntry, TransitionResult
from hitl.schemas import AnswerResult, HumanQuestion
from reporting.schemas import DecisionPage, RunInfo, RunMetrics, RunReport, ToolCallRecord


class ApiCallError(Exception):
    """A failed UI call; ``operation`` names it for the error banner."""

    def __init__(self, operation: str, message: str, status: int | None = None, details: Any = None) -> None:
        super().__init__(f"{operation} failed: {message}")
        self.operation, self.message, self.status, self.details = operation, message, status, details


class OrchestratorClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout_s: float = 30.0,
                 client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def _call(self, operation: str, method: str, path: str, *, ok: tuple[int, ...] = (200,), **kw) -> Any:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ApiCallError(operation, f"API unreachable ({type(exc).__name__})") from None
        if r.status_code not in ok:
            try:
                body = r.json()
                err = body.get("error") or {}
                msg = err.get("message") or str(body.get("detail") or body)
                details = err.get("details")
            except ValueError:
                msg, details = r.text or f"HTTP {r.status_code}", None
            raise ApiCallError(operation, f"HTTP {r.status_code}: {msg}", r.status_code, details)
        return r.json()

    # ---------------------------------------------------------------- runs
    # ---------------------------------------------------------------- background jobs
    def start_campaign_job(self, operator: str | None) -> Job:
        return Job(**self._call("start campaign", "POST", "/ops/jobs/campaign", ok=(202,), json={"operator": operator}))

    def resume_job(self, campaign_id: str, operator: str) -> Job:
        return Job(**self._call("resume campaign", "POST", f"/ops/jobs/resume/{campaign_id}", ok=(202,),
                                json={"operator": operator}))

    def schema_discover_job(self, source: str = "llm") -> Job:
        return Job(**self._call("propose the mapping", "POST", "/ops/jobs/schema-discover", ok=(202,),
                                params={"source": source}))

    def job(self, job_id: str) -> Job:
        return Job(**self._call("load job", "GET", f"/ops/jobs/{job_id}"))

    def jobs(self, active: bool | None = None, kind: str | None = None) -> list[Job]:
        params = {k: v for k, v in (("active", active), ("kind", kind)) if v is not None}
        return [Job(**j) for j in self._call("list jobs", "GET", "/ops/jobs", params=params)]

    # ---------------------------------------------------------------- runs (synchronous routes)
    def start_campaign(self, operator: str | None) -> RunInfo:
        return RunInfo(**self._call("start campaign", "POST", "/ops/campaigns", ok=(202,), json={"operator": operator}))

    def resume(self, campaign_id: str, operator: str) -> RunInfo:
        return RunInfo(**self._call("resume campaign", "POST", f"/ops/campaigns/{campaign_id}/resume", ok=(202,),
                                    json={"operator": operator}))

    def runs(self, limit: int = 50) -> list[RunInfo]:
        return [RunInfo(**r) for r in self._call("list runs", "GET", "/ops/runs", params={"limit": limit})]

    def report(self, run_id: str) -> RunReport:
        return RunReport(**self._call("load report", "GET", f"/ops/runs/{run_id}/report"))

    def metrics(self, run_id: str) -> RunMetrics:
        return RunMetrics(**self._call("load metrics", "GET", f"/ops/runs/{run_id}/metrics"))

    def decisions(self, run_id: str, *, limit: int = 200, offset: int = 0, **filters) -> DecisionPage:
        params = {k: v for k, v in filters.items() if v not in (None, "")}
        params.update(limit=limit, offset=offset)
        return DecisionPage(**self._call("query decision log", "GET", f"/ops/runs/{run_id}/decisions", params=params))

    def tool_calls(self, run_id: str, pattern_key: str | None = None) -> list[ToolCallRecord]:
        params = {"pattern_key": pattern_key} if pattern_key else None
        return [ToolCallRecord(**r) for r in self._call("load agent tool calls", "GET",
                                                        f"/ops/runs/{run_id}/tool-calls", params=params)]

    # ---------------------------------------------------------------- questions
    def questions(self, status: str = "open", run_id: str | None = None) -> list[HumanQuestion]:
        params = {"status": status, **({"run_id": run_id} if run_id else {})}
        return [HumanQuestion(**q) for q in self._call("list questions", "GET", "/ops/questions", params=params)]

    def answer(self, question_id: str, option: str, operator: str, value: str | None = None) -> AnswerResult:
        body = {"option": option, "operator": operator, "value": value}
        return AnswerResult(**self._call("answer question", "POST", f"/ops/questions/{question_id}/answer", json=body))

    # ---------------------------------------------------------------- phases and reconciliation
    def phase(self) -> PhaseStatus:
        return PhaseStatus(**self._call("load phase", "GET", "/ops/phase"))

    def transition(self, target_phase: int, operator: str | None) -> TransitionResult:
        return TransitionResult(**self._call("request transition", "POST", "/ops/phase/transition",
                                             json={"target_phase": target_phase, "operator": operator}))

    def approve(self, operator: str) -> TransitionResult:
        return TransitionResult(**self._call("approve cutover", "POST", "/ops/phase/approve", json={"operator": operator}))

    def reject(self, operator: str) -> TransitionResult:
        return TransitionResult(**self._call("reject cutover", "POST", "/ops/phase/reject", json={"operator": operator}))

    def rollback(self, target_phase: int, operator: str) -> TransitionResult:
        return TransitionResult(**self._call("roll back", "POST", "/ops/phase/rollback",
                                             json={"target_phase": target_phase, "operator": operator}))

    def transitions(self) -> list[TransitionLogEntry]:
        return [TransitionLogEntry(**e) for e in self._call("load transitions", "GET", "/ops/phase/transitions")]

    def reconcile_control_sample(self) -> ReconResult:
        return ReconResult(**self._call("reconcile control sample", "POST", "/ops/reconciliation/control-sample"))

    def latest_reconciliation(self, scope: str = "control_sample") -> ReconResult | None:
        try:
            return ReconResult(**self._call("load reconciliation", "GET", "/ops/reconciliation/latest",
                                            params={"scope": scope}))
        except ApiCallError as exc:
            if exc.status == 404:
                return None
            raise
