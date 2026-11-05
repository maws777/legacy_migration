"""Guided journey: which step is current (pure logic)."""
from types import SimpleNamespace

from ui.journey import build_steps, percent


def run(status="completed"):
    return SimpleNamespace(status=status)


def keys(steps, status):
    return [s.key for s in steps if s.status == status]


def steps(**kw):
    base = dict(mapping_ok=False, runs=[], open_questions=0, paused=False, aligned=False, phase=1)
    base.update(kw)
    return build_steps(**base)


def test_fresh_start_asks_for_the_schema_first():
    s = steps()
    assert keys(s, "current") == ["schema"] and keys(s, "done") == []


def test_validated_mapping_leads_to_the_migration():
    assert keys(steps(mapping_ok=True), "current") == ["migrate"]


def test_open_questions_keep_step_three_current():
    s = steps(mapping_ok=True, runs=[run()], open_questions=2, paused=True)
    assert keys(s, "done") == ["schema", "migrate"] and keys(s, "current") == ["questions"]


def test_answered_but_paused_campaign_still_needs_a_resume():
    assert keys(steps(mapping_ok=True, runs=[run()], paused=True), "current") == ["questions"]


def test_all_done_when_phase_four():
    s = steps(mapping_ok=True, runs=[run()], aligned=True, phase=4)
    assert keys(s, "done") == ["schema", "migrate", "questions", "verify", "switch"] and not keys(s, "current")


def test_failed_run_is_not_a_finished_migration():
    assert keys(steps(mapping_ok=True, runs=[run("failed")]), "current") == ["migrate"]


def test_percent_handles_zero():
    assert percent(1, 8) == 12.5 and percent(0, 0) == 0.0
