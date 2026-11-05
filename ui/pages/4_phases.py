"""Strangler Fig phases: current phase, gate, transitions, approval, rollback."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import act, client, load, operator  # noqa: E402

st.title("Phases")
api = client()
op = operator()
status = load("phase", api.phase)
if status:
    st.metric("Current phase", status.current_phase)
    for chk in status.gate:
        (st.success if chk.passed else st.warning)(f"Gate {chk.name}: {chk.message}")
    st.caption(f"Reads served: {status.served}")
    if status.summary:
        s = status.summary
        st.info(f"Latest full Run {s['run_id'][:8]}: coverage {s['coverage_pct']}% ({s['loaded']:,} of "
                f"{s['analysed']:,} rows loaded); excluded: {s['rejected_out_of_scope']:,} out of scope, "
                f"{s['rejected_error']:,} in error, {s['pending_human']:,} pending human decision.")


def show(result):
    if result is None:
        return
    if result.status == "refused":
        st.error(f"Refused: {result.reason}")
    elif result.status == "pending_approval":
        st.warning(f"{result.reason}. Approving also validates the exclusions shown above.")
    else:
        st.success(f"{result.status}: phase {result.current_phase}")
    st.session_state.pop("phase", None)


target = st.selectbox("Target phase", [p for p in (1, 2, 3, 4) if not status or p != status.current_phase])
if st.button("Request transition"):
    show(act(lambda: api.transition(target, op)))
if status and status.pending_target:
    c1, c2 = st.columns(2)
    if c1.button("Approve cutover"):
        show(act(lambda: api.approve(op)))
    if c2.button("Reject cutover"):
        show(act(lambda: api.reject(op)))
if status and status.current_phase in (3, 4):
    if st.button(f"Roll back to phase {status.current_phase - 1}"):
        show(act(lambda: api.rollback(status.current_phase - 1, op)))
if st.button("Reconcile the Control_Sample"):
    r = act(api.reconcile_control_sample)
    if r:
        st.write(r.model_dump(mode="json"))
st.subheader("Transition log")
st.dataframe([e.model_dump(mode="json") for e in (load("transitions", api.transitions) or [])],
             width="stretch")
