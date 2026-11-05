"""Decision log with filters; all records of one source row with their explanations."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import client, load, operator  # noqa: E402

st.title("Decision log")
api = client()
operator()                                   # keeps ?operator= in the URL on this page too
runs = load("runs", api.runs) or []
if not runs:
    st.warning("No Run yet.")
    st.stop()
c = st.columns(6)
run_id = c[0].selectbox("run_id", [r.run_id for r in runs])
row = c[1].text_input("Source_Row_Id")
field = c[2].text_input("field")
action = c[3].selectbox("Action", ["", "MIGRATE", "TRANSFORM", "CORRECT", "REJECT", "ASK_HUMAN"])
rule_id = c[4].text_input("rule_id")
decided_by = c[5].selectbox("decided_by", ["", "rule", "llm", "human"])
filters = {"source_row": int(row) if row.strip().isdigit() else None, "field": field.strip() or None,
           "action": action or None, "rule_id": rule_id.strip() or None, "decided_by": decided_by or None}
page = load("log_page", lambda: api.decisions(run_id, limit=500, **filters))
if page is not None:
    if page.total == 0:
        st.info("No Decision_Record matches these filters.")
    else:
        st.caption(f"{page.total:,} matching record(s), first {len(page.items)} shown")
        st.dataframe([r.model_dump(mode="json") for r in page.items], width="stretch")
st.subheader("One source row")
sel = st.number_input("Source_Row_Id", min_value=1, step=1, value=int(row) if row.strip().isdigit() else 1)
detail = load("log_row", lambda: api.decisions(run_id, source_row=int(sel), limit=1000))
if detail is not None:
    for r in detail.items:
        st.markdown(f"**{r.field}** {r.action} `{r.rule_id}` {r.reason} by {r.decided_by}"
                    f"{'' if r.confidence is None else f' (confidence {r.confidence:.2f})'}: "
                    f"`{r.before}` -> `{r.after}` - {r.explanation}")
st.subheader("Resolver_Agent tool calls")
calls = load("tool_calls", lambda: api.tool_calls(run_id))
if calls is not None:
    if not calls:
        st.info("No tool call in this Run (no Unknown_Variant sent to the agent, or Degraded_Mode).")
    else:
        st.dataframe([c.model_dump(mode="json") for c in calls], width="stretch")
