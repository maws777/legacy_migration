import threading

import pytest

from agents.jobs import JobConflict, JobManager
from agents.orchestrator import ApiError


def test_job_lifecycle_and_result():
    jm = JobManager()
    gate = threading.Event()
    job = jm.submit("campaign", "campaign", "pipeline", lambda: (gate.wait(5), {"run_id": "r1"})[1],
                    label="start campaign", operator="ann")
    assert job.status == "queued" and job.operator == "ann"
    assert jm.list(active=True)[0].job_id == job.job_id
    gate.set()
    done = jm.wait(job.job_id, 5)
    assert done.status == "succeeded" and done.result == {"run_id": "r1"} and done.seconds >= 0
    assert done.started_at and done.ended_at and jm.list(active=True) == []


def test_double_launch_is_refused_until_the_job_ends():
    jm = JobManager()
    gate = threading.Event()
    first = jm.submit("campaign", "campaign", "pipeline", lambda: gate.wait(5), label="start campaign")
    with pytest.raises(JobConflict) as exc:
        jm.submit("campaign", "campaign", "pipeline", lambda: None, label="start campaign")
    assert exc.value.job.job_id == first.job_id
    gate.set()
    jm.wait(first.job_id, 5)
    assert jm.submit("campaign", "campaign", "pipeline", lambda: None, label="start campaign").status == "queued"


def test_one_job_at_a_time_per_lane():
    jm = JobManager()
    gate, order = threading.Event(), []
    a = jm.submit("campaign", "campaign", "pipeline", lambda: (gate.wait(5), order.append("a")),
                  label="start campaign")
    b = jm.submit("resume", "resume:c1", "pipeline", lambda: order.append("b"), label="resume campaign")
    other = jm.submit("schema_discover", "schema_discover", "llm", lambda: order.append("llm"), label="schema")
    assert jm.wait(other.job_id, 5).status == "succeeded"            # other lane is not blocked
    assert jm.get(b.job_id).status == "queued"
    gate.set()
    jm.wait(b.job_id, 5)
    assert order == ["llm", "a", "b"] and jm.get(a.job_id).status == "succeeded"


def test_failures_are_captured():
    jm = JobManager()

    def conflict():
        raise ApiError(409, "CONFLICT", "campaign c1 is not paused")

    f1 = jm.wait(jm.submit("resume", "resume:c1", "pipeline", conflict, label="resume campaign").job_id, 5)
    assert f1.status == "failed" and (f1.error.code, f1.error.message) == ("CONFLICT", "campaign c1 is not paused")
    f2 = jm.wait(jm.submit("campaign", "campaign", "pipeline", lambda: 1 / 0, label="start campaign").job_id, 5)
    assert f2.error.code == "INTERNAL" and "ZeroDivisionError" in f2.error.message
    with pytest.raises(ValueError):
        jm.submit("x", "x", "nope", lambda: None, label="x")
