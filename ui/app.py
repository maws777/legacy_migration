"""Streamlit UI home. Run: ``streamlit run ui/app.py`` (Orchestrator on ``ORCHESTRATOR_URL``)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from ui.common import (  # noqa: E402
    active_jobs, auto_refresh, client, finished, launch, load, operator, running, show_failure, show_running,
)
from ui.mapping import mapping_step  # noqa: E402

st.set_page_config(page_title="Migration engine", layout="wide")
st.title("Legacy A -> target migration")
st.caption("Strangler Fig migration with rules first, LLM on distinct patterns, grouped human questions.")

op = operator()
api = client()
jobs = active_jobs()
runs = load("runs", api.runs) or []
phase = load("phase_home", api.phase)

c1, c2, c3 = st.columns(3)
c1.metric("Current phase", phase.current_phase if phase else "?")
c2.metric("Runs", len(runs))
c3.metric("Open questions", len(load("q_home", api.questions) or []))

validated = mapping_step(api, op, jobs)

# ---------------------------------------------------------------- campaigns
st.subheader("Étape 2 : campagnes de migration")
pipeline_job = running(jobs, "campaign") or running(jobs, "resume")
if pipeline_job is not None:
    show_running(pipeline_job)
for slot in ("campaign", "resume"):
    done = finished(slot)
    if done is not None:
        if done.status == "succeeded":
            r = done.result
            st.success(f"{done.label} done in {done.seconds:g} s: Run {r['run_id']} {r['status']}, campaign "
                       f"{r['campaign_status']}, {r['open_questions']} open question(s).")
        else:
            show_failure(done)

if validated is None:
    st.caption("« Start a campaign » est désactivé tant qu'aucun mapping n'est validé (étape 1).")
if st.button("Start a campaign (Run 1)", disabled=pipeline_job is not None or validated is None):
    launch("campaign", lambda: api.start_campaign_job(op))
paused = sorted({r.campaign_id for r in runs if r.campaign_status == "paused"})
if paused:
    cid = st.selectbox("Paused campaign", paused)
    if st.button("Resume after human answers (next Run)", disabled=pipeline_job is not None):
        launch("resume", lambda: api.resume_job(cid, op))
st.dataframe([r.model_dump(mode="json") for r in runs], width="stretch")
st.info("Pages: Stats, Questions, Decision log, Phases, Chat (left sidebar).")
auto_refresh(jobs)
