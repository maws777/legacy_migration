"""Phase_Controller: Strangler Fig phases 1-4, forward moves one at a time, gated cutover, operator rollbacks."""
from __future__ import annotations

import threading
from typing import Any, Callable

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Engine, Integer, MetaData, String, Table, insert, select, update

from agents.schemas import GateCheck, PhaseStatus, TransitionLogEntry, TransitionResult
from rules.timeutil import as_utc, naive_utc, utcnow

PHASE_MD = MetaData()
phase_state = Table(
    "phase_state", PHASE_MD,
    Column("id", Integer, primary_key=True),
    Column("current_phase", Integer, nullable=False),
    Column("pending_target", Integer),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint("id = 1", name="ck_phase_state_singleton"),
    CheckConstraint("current_phase BETWEEN 1 AND 4", name="ck_phase_state_phase"),
)
transition_log = Table(
    "transition_log", PHASE_MD,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("from_phase", Integer, nullable=False),
    Column("to_phase", Integer, nullable=False),
    Column("rollback", Boolean(create_constraint=False), nullable=False),
    Column("operator", String(128)),
    Column("at", DateTime, nullable=False),
)

GateFn = Callable[[], list[GateCheck]]


def make_gate(open_questions: Callable[[], int], latest_control_sample: Callable[[], Any]) -> GateFn:
    """(a) zero open Human_Questions; (b) latest Control_Sample comparison with zero Gaps and no VIN
    left uncompared (decisions.md: an impossible comparison fails the gate)."""
    def gate() -> list[GateCheck]:
        n = open_questions()
        checks = [GateCheck(name="open_questions", passed=n == 0, count=n,
                            message="no open Human_Question" if n == 0 else f"{n} open Human_Question(s)")]
        recon = latest_control_sample()
        if recon is None:
            checks.append(GateCheck(name="control_sample_gaps", passed=False, count=None,
                                    message="no reconciliation available"))
        else:
            ok = recon.gaps == 0 and not recon.not_performed and recon.compared > 0
            msg = (f"{recon.gaps} Gap(s) on the Control_Sample" if recon.gaps else
                   f"{len(recon.not_performed)} VIN(s) could not be compared" if recon.not_performed else
                   "nothing compared" if recon.compared == 0 else "zero Gap on the Control_Sample")
            checks.append(GateCheck(name="control_sample_gaps", passed=ok, count=recon.gaps, message=msg))
        return checks
    return gate


def _no_reconciliation() -> list[GateCheck]:
    return [GateCheck(name="control_sample_gaps", passed=False, count=None, message="no reconciliation available")]


class PhaseController:
    def __init__(self, engine: Engine, gate: GateFn = _no_reconciliation,
                 legacy_a_reachable: Callable[[], bool] = lambda: True,
                 summary: Callable[[], dict | None] = lambda: None) -> None:
        self.engine = engine
        self.gate = gate
        self.legacy_a_reachable = legacy_a_reachable
        self.summary = summary
        self._lock = threading.RLock()
        PHASE_MD.create_all(engine)
        with engine.connect() as c:
            row = c.execute(select(phase_state).where(phase_state.c.id == 1)).mappings().first()
        self._phase = row["current_phase"] if row else 1
        self._pending = row["pending_target"] if row else None
        self.served: dict[str, int] = {"legacy_a": 0, "target": 0, "legacy_b": 0}

    # ---------------------------------------------------------------- reads
    def current(self) -> int:
        with self._lock:
            return self._phase

    def status(self) -> PhaseStatus:
        with self._lock:
            phase, pending = self._phase, self._pending
        return PhaseStatus(current_phase=phase, pending_target=pending, gate=self.gate(), served=dict(self.served),
                           summary=self.summary())

    def transitions(self) -> list[TransitionLogEntry]:
        with self.engine.connect() as c:
            rows = c.execute(select(transition_log).order_by(transition_log.c.id)).mappings().all()
        return [TransitionLogEntry(from_phase=r["from_phase"], to_phase=r["to_phase"], rollback=r["rollback"],
                                   operator=r["operator"], at=as_utc(r["at"])) for r in rows]

    # ---------------------------------------------------------------- persistence
    def _persist(self, phase: int, pending: int | None, log: tuple[int, int, bool, str | None] | None) -> None:
        now = naive_utc(utcnow())
        with self.engine.begin() as c:
            exists = c.execute(select(phase_state.c.id).where(phase_state.c.id == 1)).first()
            values = {"current_phase": phase, "pending_target": pending, "updated_at": now}
            if exists:
                c.execute(update(phase_state).where(phase_state.c.id == 1).values(**values))
            else:
                c.execute(insert(phase_state).values(id=1, **values))
            if log is not None:
                c.execute(insert(transition_log).values(from_phase=log[0], to_phase=log[1], rollback=log[2],
                                                        operator=log[3], at=now))

    def _refused(self, reason: str, checks: list[GateCheck] | None = None) -> TransitionResult:
        return TransitionResult(status="refused", current_phase=self.current(), previous_phase=None, reason=reason,
                                checks=checks or [])

    def _switch(self, new: int, rollback: bool, operator: str | None) -> TransitionResult:
        with self._lock:
            old = self._phase
            try:
                self._persist(new, None, (old, new, rollback, operator))
            except Exception as exc:  # noqa: BLE001 - keep the previous phase
                return TransitionResult(status="refused", current_phase=old, previous_phase=None,
                                        reason=f"phase persistence failed: {type(exc).__name__}")
            self._phase, self._pending = new, None
        return TransitionResult(status="switched", current_phase=new, previous_phase=old, reason=None)

    # ---------------------------------------------------------------- transitions
    def request(self, target_phase: int, operator: str | None = None) -> TransitionResult:
        cur = self.current()
        if not isinstance(target_phase, int) or not 1 <= target_phase <= 4:
            return self._refused(f"phase {target_phase} is outside 1-4")
        if target_phase == cur:
            return self._refused(f"phase {cur} is already the current phase")
        if target_phase < cur:
            return self._refused("a move back is a rollback: it must be requested by a human operator through "
                                 "the rollback route")
        if target_phase > cur + 1:
            return self._refused(f"phase {cur + 1} cannot be skipped")
        if target_phase == 4:
            checks = self.gate()
            failing = [c for c in checks if not c.passed]
            if failing:
                return self._refused("cutover gate failed: " + "; ".join(c.message for c in failing), checks)
            with self._lock:
                try:
                    self._persist(self._phase, 4, None)
                except Exception as exc:  # noqa: BLE001
                    return self._refused(f"phase persistence failed: {type(exc).__name__}")
                self._pending = 4
            return TransitionResult(status="pending_approval", current_phase=cur, previous_phase=None,
                                    reason="cutover gate passed: waiting for explicit approval by a human operator",
                                    checks=checks, summary=self.summary())
        return self._switch(target_phase, False, operator)

    def approve(self, operator: str) -> TransitionResult:
        with self._lock:
            pending = self._pending
        if pending is None:
            return self._refused("no pending transition to approve")
        checks = self.gate()
        failing = [c for c in checks if not c.passed]
        if failing:
            self._clear_pending()
            return self._refused("cutover gate failed at approval: " + "; ".join(c.message for c in failing), checks)
        result = self._switch(pending, False, operator)
        return result.model_copy(update={"checks": checks, "summary": self.summary()})

    def _clear_pending(self) -> None:
        with self._lock:
            try:
                self._persist(self._phase, None, None)
            except Exception:  # noqa: BLE001
                return
            self._pending = None

    def reject(self, operator: str) -> TransitionResult:
        with self._lock:
            pending = self._pending
        if pending is None:
            return self._refused("no pending transition to reject")
        self._clear_pending()
        return TransitionResult(status="cancelled", current_phase=self.current(), previous_phase=None,
                                reason=f"pending transition to phase {pending} cancelled by {operator}")

    def rollback(self, target_phase: int, operator: str) -> TransitionResult:
        cur = self.current()
        if not operator or not operator.strip():
            return self._refused("a rollback must be requested by a human operator")
        if (cur, target_phase) not in ((4, 3), (3, 2)):
            if target_phase == 1:
                return self._refused("rollback to phase 1 is not allowed")
            return self._refused(f"rollback from phase {cur} to phase {target_phase} is not allowed "
                                 "(only 4 -> 3 and 3 -> 2)")
        if (cur, target_phase) == (4, 3) and not self.legacy_a_reachable():
            return self._refused("Legacy_A_API is unavailable: rollback to phase 3 refused")
        return self._switch(target_phase, True, operator)
