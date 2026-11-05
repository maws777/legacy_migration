"""Bonus chat: questions in natural language on the Target_DB, answered by the Query_Agent."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os  # noqa: E402
import time  # noqa: E402

import streamlit as st  # noqa: E402

from ui import legacy_b_runner  # noqa: E402
from ui.api_client import ApiCallError  # noqa: E402
from ui.common import client, hero, refresh_seconds, setup  # noqa: E402

EXAMPLES = ["How many vehicles are there for each vehicle model?",
            "Which ECU has the most diagnostic operations?",
            "List the 5 most recent diagnostic operations with their VIN and ECU."]

setup("Interroger les données", "💭")
hero("Interroger les données", "Posez une question en langage naturel sur les données migrées (lecture seule).")
st.caption("The Query_Agent turns your question into one SQL SELECT, checks it (one statement, SELECT only, "
           "allowed tables) and runs it read-only on the Target_DB. It never changes data.")
api = client()
history = st.session_state.setdefault("chat_history", [])


def show(entry: dict) -> None:
    with st.chat_message("user"):
        st.markdown(entry["question"])
    with st.chat_message("assistant"):
        r = entry.get("response")
        if r is None:
            st.error(entry["error"])
            return
        if r.sql:
            st.code(r.sql, language="sql")
        if r.error:
            st.error(r.error.message)
        else:
            st.dataframe(r.rows, width="stretch")
            if r.truncated:
                st.caption(f"Only the first {len(r.rows)} rows are shown (row limit).")
        st.caption(f"{r.input_tokens + r.output_tokens:,} tokens ({r.input_tokens:,} in, {r.output_tokens:,} out), "
                   f"{r.seconds:.1f} s")


for entry in history:
    show(entry)
cols = st.columns(len(EXAMPLES))
picked = next((q for c, q in zip(cols, EXAMPLES) if c.button(q, key=f"ex_{q}")), None)
question = st.chat_input("Ask a question about the migrated data") or picked
if question:
    with st.spinner("The Query_Agent is writing and checking the SQL..."):
        try:
            entry = {"question": question, "response": api.chat(question)}
        except ApiCallError as exc:
            entry = {"question": question, "response": None, "error": f"{exc.operation} failed: {exc.message}"}
    history.append(entry)
    show(entry)

st.divider()
st.subheader("Legacy B interroge la cible")
st.caption("The Legacy B agent runs as its own process (same as scripts/legacy_b_asks.ps1): it writes three "
           "questions from its own records, sends them to this chat as a legacy_b consumer and compares the "
           "returned models with its own.")
busy = legacy_b_runner.running()
if st.button("Lancer l'agent Legacy B (3 questions)", disabled=busy):
    legacy_b_runner.start(os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8000"))
    st.rerun()
launch = legacy_b_runner.current()
if launch is not None:
    if busy:
        period = refresh_seconds()
        refresh = f", rafraîchissement automatique toutes les {period:g} s" if period > 0 else ""
        st.info(f"Agent Legacy B : en cours ({int(time.time() - launch.started)} s){refresh}.")
    elif launch.proc.returncode:
        st.error(f"The Legacy B agent stopped with exit code {launch.proc.returncode}.")
    st.code(legacy_b_runner.output() or "(no output yet)", language="text")
    if busy and refresh_seconds() > 0:
        time.sleep(refresh_seconds())
        st.rerun()
