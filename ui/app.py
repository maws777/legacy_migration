"""Streamlit UI home. Run: ``streamlit run ui/app.py`` (Orchestrator on ``ORCHESTRATOR_URL``)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from ui.common import act, client, load, operator  # noqa: E402

st.set_page_config(page_title="Migration engine", layout="wide")
st.title("Legacy A -> target migration")
st.caption("Strangler Fig migration with rules first, LLM on distinct patterns, grouped human questions.")

op = operator()
api = client()
runs = load("runs", api.runs) or []
phase = load("phase_home", api.phase)

c1, c2, c3 = st.columns(3)
c1.metric("Current phase", phase.current_phase if phase else "?")
c2.metric("Runs", len(runs))
c3.metric("Open questions", sum(1 for _ in (load("q_home", api.questions) or [])))

st.subheader("Campaigns")
if st.button("Start a campaign (Run 1)"):
    info = act(lambda: api.start_campaign(op))
    if info:
        st.success(f"Run {info.run_id} {info.status}; campaign {info.campaign_status}")
paused = sorted({r.campaign_id for r in runs if r.campaign_status == "paused"})
if paused:
    cid = st.selectbox("Paused campaign", paused)
    if st.button("Resume after human answers (next Run)"):
        info = act(lambda: api.resume(cid, op))
        if info:
            st.success(f"Run {info.run_id} {info.status}; {info.open_questions} open question(s)")
st.dataframe([r.model_dump(mode="json") for r in runs], use_container_width=True)
st.info("Pages: Stats, Questions, Decision log, Phases (left sidebar).")
