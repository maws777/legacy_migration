import pytest

from agents.schemas import GateCheck
from migration.phase_controller import PhaseController, make_gate
from target.schema import make_engine, sqlite_url

PASS = [GateCheck(name="open_questions", passed=True, count=0, message="ok"),
        GateCheck(name="control_sample_gaps", passed=True, count=0, message="ok")]


@pytest.fixture
def engine(tmp_path):
    return make_engine(sqlite_url(tmp_path / "ops.db"))


def ctl(engine, gate=lambda: PASS, reachable=lambda: True):
    return PhaseController(engine, gate, reachable, lambda: {"coverage_pct": 75.7})


def test_forward_moves_one_at_a_time_and_log(engine):
    c = ctl(engine)
    assert c.current() == 1
    for target in (2, 3):
        r = c.request(target, "op")
        assert (r.status, r.current_phase, r.previous_phase) == ("switched", target, target - 1)
    log = c.transitions()
    assert [(e.from_phase, e.to_phase, e.rollback) for e in log] == [(1, 2, False), (2, 3, False)]
    assert ctl(engine).current() == 3                                  # restart: last persisted phase


@pytest.mark.parametrize("start,target,fragment", [
    (1, 3, "cannot be skipped"), (1, 1, "already"), (1, 0, "outside"), (1, 5, "outside"),
    (3, 2, "rollback"), (3, 1, "rollback"),
])
def test_refused_requests(engine, start, target, fragment):
    c = ctl(engine)
    for p in range(2, start + 1):
        c.request(p)
    n = len(c.transitions())
    r = c.request(target, "op")
    assert r.status == "refused" and fragment in r.reason and c.current() == start
    assert len(c.transitions()) == n


def test_cutover_needs_gate_then_approval(engine):
    c = ctl(engine)
    c.request(2), c.request(3)
    r = c.request(4, "op")
    assert r.status == "pending_approval" and c.current() == 3 and c.status().pending_target == 4
    assert r.summary == {"coverage_pct": 75.7}
    a = c.approve("lead")
    assert (a.status, a.current_phase) == ("switched", 4) and c.transitions()[-1].operator == "lead"


def test_failing_gate_refuses_with_counts(engine):
    gate = make_gate(lambda: 3, lambda: None)
    c = ctl(engine, gate)
    c.request(2), c.request(3)
    r = c.request(4, "op")
    assert r.status == "refused" and c.current() == 3
    checks = {x.name: x for x in r.checks}
    assert checks["open_questions"].count == 3 and not checks["open_questions"].passed
    assert checks["control_sample_gaps"].message == "no reconciliation available"


def test_approve_reevaluates_and_reject_cancels(engine):
    state = {"ok": True}
    c = ctl(engine, lambda: PASS if state["ok"] else [PASS[0].model_copy(update={"passed": False, "count": 1})])
    c.request(2), c.request(3)
    assert c.request(4).status == "pending_approval"
    state["ok"] = False
    r = c.approve("lead")
    assert r.status == "refused" and c.current() == 3 and c.status().pending_target is None
    state["ok"] = True
    c.request(4)
    assert c.reject("lead").status == "cancelled" and c.current() == 3
    assert c.approve("lead").status == "refused"                       # nothing pending any more


def test_rollbacks(engine):
    reachable = {"v": True}
    c = ctl(engine, reachable=lambda: reachable["v"])
    c.request(2), c.request(3), c.request(4), c.approve("lead")
    reachable["v"] = False
    r = c.rollback(3, "ops")
    assert r.status == "refused" and "Legacy_A_API" in r.reason and c.current() == 4
    reachable["v"] = True
    assert c.rollback(3, "ops").status == "switched"
    assert c.rollback(1, "ops").status == "refused"
    assert c.rollback(2, "").status == "refused"
    assert c.rollback(2, "ops").status == "switched" and c.current() == 2
    assert c.rollback(1, "ops").status == "refused"
    log = c.transitions()
    assert [(e.from_phase, e.to_phase, e.rollback) for e in log[-2:]] == [(4, 3, True), (3, 2, True)]


def test_persistence_failure_keeps_the_phase(engine, monkeypatch):
    c = ctl(engine)

    def boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(c, "_persist", boom)
    r = c.request(2, "op")
    assert r.status == "refused" and "persistence" in r.reason and c.current() == 1
    assert c.transitions() == []
