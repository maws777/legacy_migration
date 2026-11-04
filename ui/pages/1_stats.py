"""Run statistics: outcomes, traced %, tokens, cost, time, Degraded_Mode, phase, Aligned_Percentage."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import client, load  # noqa: E402

st.title("Run statistics")
api = client()
runs = load("runs", api.runs) or []
if not runs:
    st.warning("No Run yet.")
    st.stop()
run_id = st.selectbox("Run", [r.run_id for r in runs], format_func=lambda r: next(
    f"{x.started_at:%Y-%m-%d %H:%M:%S} {x.kind} {x.status} ({x.run_id[:8]})" for x in runs if x.run_id == r))
report = load(f"report_{run_id}", lambda: api.report(run_id))
metrics = load(f"metrics_{run_id}", lambda: api.metrics(run_id))
phase = load("phase_stats", api.phase)
recon = load("recon_stats", lambda: api.latest_reconciliation("control_sample"))
if report:
    cols = st.columns(4)
    cols[0].metric("MIGRATE", f"{report.migrate:,}")
    cols[1].metric("CORRECT", f"{report.correct:,}")
    cols[2].metric("REJECT", f"{report.reject:,}")
    cols[3].metric("ASK_HUMAN", f"{report.ask_human:,}")
    cols = st.columns(4)
    cols[0].metric("Traced", f"{report.traced_pct}%")
    cols[1].metric("Tokens", f"{report.tokens:,}")
    cols[2].metric("Cost", f"{report.cost_eur:.4f} €")
    cols[3].metric("Seconds", f"{report.seconds:.1f}")
    cols = st.columns(4)
    cols[0].metric("Degraded_Mode", "yes" if metrics and metrics.degraded_mode else "no")
    cols[1].metric("Phase", phase.current_phase if phase else "?")
    cols[2].metric("Aligned %", "n/a" if not recon or recon.aligned_pct is None else f"{recon.aligned_pct}%")
    cols[3].metric("Coverage", f"{report.coverage_pct}%")
    st.caption(f"Rejected: {report.rejected_out_of_scope:,} out of scope, {report.rejected_error:,} in error; "
               f"{report.pending_human:,} pending human decision; {report.cache_state} Run.")
    st.code(report.text, language="text")
if metrics:
    st.subheader("Metrics")
    st.json(metrics.model_dump(mode="json"))
