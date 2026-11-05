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
st.subheader("Source schema")
mapping = load("schema_mapping", api.schema_mapping)
if mapping:
    st.success(f"Column mapping validated by {mapping.validated_by}.")
else:
    st.info("Discover and validate the source-column mapping before starting a migration.")

if st.button("Discover schema mapping"):
    proposal = act(api.discover_schema)
    if proposal:
        st.session_state["schema_proposal"] = proposal

proposal = st.session_state.get("schema_proposal")
if proposal:
    st.caption("Proposal source: reference mapping (LLM unavailable or invalid response)." if proposal.fallback
               else "Proposal source: local LLM.")
    st.dataframe([{"Source column": source, "Target attribute": target or "—"}
                  for source, target in proposal.column_mapping.items()], use_container_width=True, hide_index=True)
    if proposal.conflicts:
        st.error("This proposal has conflicts and cannot be validated.")
        st.dataframe([conflict.model_dump() for conflict in proposal.conflicts], use_container_width=True,
                     hide_index=True)
    elif st.button("Validate this mapping"):
        validated = act(lambda: api.validate_schema(proposal.proposal_id, op))
        if validated:
            st.session_state["schema_mapping"] = validated
            st.session_state.pop("schema_proposal", None)
            st.success(f"Column mapping validated by {validated.validated_by}.")

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
