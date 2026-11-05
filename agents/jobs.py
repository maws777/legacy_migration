"""Background jobs for the long Orchestrator operations (campaign, resume, schema discovery, advice).

The launch request returns at once with a queued Job (HTTP 202); the UI polls ``GET /ops/jobs/{job_id}``.
Each lane runs one job at a time, so two campaigns never run the Run_Workflow concurrently, and a job
whose key is already queued or running is refused (no double launch). Jobs are kept in memory: an
Orchestrator restart forgets them (the Runs themselves are persisted).
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Literal

from pydantic import BaseModel

from rules.errors import ErrorBody
from rules.timeutil import utcnow

JobStatus = Literal["queued", "running", "succeeded", "failed"]
ACTIVE = ("queued", "running")
LANES = ("pipeline", "llm")
KEEP_FINISHED = 200


class Job(BaseModel):
    job_id: str
    kind: str                         # campaign | resume | schema_discover | advise
    key: str                          # duplicate-launch key, e.g. "campaign" or "advise:<question_id>"
    lane: str
    label: str
    status: JobStatus
    operator: str | None = None
    submitted_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    seconds: float | None = None
    result: Any = None
    error: ErrorBody | None = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE


class JobConflict(Exception):
    def __init__(self, job: Job) -> None:
        super().__init__(f"{job.label} is already {job.status} (job {job.job_id})")
        self.job = job


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _error_of(exc: BaseException) -> ErrorBody:
    code = getattr(exc, "code", None)
    if code in ("NOT_FOUND", "BACKEND_UNAVAILABLE", "VALIDATION_ERROR", "CONFLICT", "UNKNOWN_RUN", "REFUSED"):
        return ErrorBody(code=code, message=getattr(exc, "message", str(exc)), details=getattr(exc, "details", None))
    return ErrorBody(code="INTERNAL", message=f"{type(exc).__name__}: {exc}")


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._executors = {lane: ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"job-{lane}")
                           for lane in LANES}

    def submit(self, kind: str, key: str, lane: str, fn: Callable[[], Any], *, label: str,
               operator: str | None = None) -> Job:
        if lane not in self._executors:
            raise ValueError(f"unknown job lane {lane!r}")
        with self._lock:
            for j in self._jobs.values():
                if j.key == key and j.active:
                    raise JobConflict(j.model_copy())
            job = Job(job_id=str(uuid.uuid4()), kind=kind, key=key, lane=lane, label=label, status="queued",
                      operator=operator, submitted_at=utcnow())
            self._jobs[job.job_id] = job
            self._trim()
            snapshot = job.model_copy()
        self._executors[lane].submit(self._run, job.job_id, fn)
        return snapshot

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for k, v in values.items():
                setattr(job, k, v)

    def _run(self, job_id: str, fn: Callable[[], Any]) -> None:
        self._update(job_id, status="running", started_at=utcnow())
        t0 = time.perf_counter()
        try:
            result = _jsonable(fn())
        except BaseException as exc:  # noqa: BLE001 - every failure is reported in the job
            self._update(job_id, status="failed", error=_error_of(exc), ended_at=utcnow(),
                         seconds=round(time.perf_counter() - t0, 2))
            return
        self._update(job_id, status="succeeded", result=result, ended_at=utcnow(),
                     seconds=round(time.perf_counter() - t0, 2))

    def _trim(self) -> None:
        """Keep the KEEP_FINISHED most recent finished jobs (active jobs are never dropped)."""
        finished = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.submitted_at)
        for j in finished[:max(0, len(finished) - KEEP_FINISHED)]:
            del self._jobs[j.job_id]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy() if job is not None else None

    def list(self, active: bool | None = None, kind: str | None = None) -> list[Job]:
        with self._lock:
            jobs = [j.model_copy() for j in self._jobs.values()]
        if active is not None:
            jobs = [j for j in jobs if j.active == active]
        if kind is not None:
            jobs = [j for j in jobs if j.kind == kind]
        return sorted(jobs, key=lambda j: j.submitted_at, reverse=True)

    def wait(self, job_id: str, timeout_s: float = 30.0) -> Job | None:
        """Test and script helper: poll until the job is finished or the timeout expires."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if job is None or not job.active:
                return job
            time.sleep(0.02)
        return self.get(job_id)
