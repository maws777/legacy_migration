"""Open Human_Questions: all contents, one option per submission."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import act, client, load, operator  # noqa: E402

LABELS = {"apply_value": "Apply a value to the group", "reject_group": "Reject the group",
          "case_by_case": "Case by case", "generate_op_id": "Generate deterministic diagnosticOpIDs (UUID v5)"}

st.title("Human questions")
api = client()
op = operator()
questions = load("open_questions", lambda: api.questions("open")) or []
st.caption(f"{len(questions)} open question(s)")
for q in questions:
    title = f"{q.pattern_key} | {q.row_count} row(s) | {q.rule_id} {q.reason}"
    with st.expander(title):
        st.markdown(f"**Missing:** {q.missing}")
        st.markdown(f"**Why the agent could not decide:** {q.why}")
        if q.llm_suggestion:
            s = q.llm_suggestion
            st.markdown(f"**LLM suggestion** ({q.llm_status}): {s.decision} `{s.value}` confidence "
                        f"{s.confidence:.2f} - {s.justification}")
        else:
            st.markdown(f"**LLM:** {q.llm_status}")
        st.dataframe([{"source_row_id": e.source_row_id, **e.values} for e in q.example_rows],
                     width="stretch")
        with st.form(f"form_{q.question_id}"):
            option = st.radio("Option", q.options, format_func=LABELS.get, key=f"opt_{q.question_id}")
            value = None
            if "apply_value" in q.options:
                value = (st.selectbox("Value (apply_value only)", q.allowed_values, key=f"val_{q.question_id}")
                         if q.allowed_values else st.text_input("Value (apply_value only)", key=f"val_{q.question_id}"))
            if st.form_submit_button("Submit"):
                res = act(lambda: api.answer(q.question_id, option, op, value if option == "apply_value" else None))
                if res is not None:
                    st.success(f"Saved: {res.question.status}" + (f", {len(res.created_questions)} row question(s)"
                                                                 if res.created_questions else ""))
                    st.session_state.open_questions = [x for x in questions if x.question_id != q.question_id]
                    st.rerun()
