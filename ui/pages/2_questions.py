"""Open Human_Questions: all contents, the agent advice on request, one option per submission."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import (  # noqa: E402
    act, active_jobs, auto_refresh, client, finished, launch, load, operator, running, show_failure, show_running,
)

LABELS = {"apply_value": "Apply a value to the group", "reject_group": "Reject the group",
          "case_by_case": "Case by case", "generate_op_id": "Generate deterministic diagnosticOpIDs (UUID v5)"}
STATUS = {"recommended": "recommandation", "invalid": "bloquée par le validateur", "no_answer": "pas de réponse finale",
          "llm_failed": "échec de l'appel au modèle", "unavailable": "agent indisponible"}


def show_advice(a) -> None:
    head = (f"**Avis de l'agent** ({STATUS.get(a.status, a.status)}, {a.llm_calls} appel(s), "
            f"{a.input_tokens + a.output_tokens:,} tokens, {a.seconds:.1f} s, {a.created_at:%d/%m %H:%M} UTC)")
    st.markdown(head)
    r = a.recommendation
    if a.status == "recommended" and r is not None:
        value = f" avec la valeur `{r.value}`" if r.value else ""
        st.success(f"Option conseillée : **{LABELS.get(r.option, r.option)}**{value} (confiance {r.confidence:.2f}). "
                   f"{r.justification}")
        st.caption("L'agent conseille seulement : c'est vous qui répondez ci-dessous.")
    else:
        st.warning(f"Pas de recommandation : {a.failure or 'raison inconnue'}"
                   + (f" (réponse du modèle : {LABELS.get(r.option, r.option)} {r.value or ''})" if r else ""))
    if r is not None:
        if r.evidence:
            st.markdown("Preuves :\n" + "\n".join(f"- {e}" for e in r.evidence))
        st.markdown("Ce que l'agent ne peut pas savoir :\n"
                    + ("\n".join(f"- {u}" for u in r.unknowns) if r.unknowns else "- non précisé"))
    if a.tool_calls:
        st.dataframe([c.model_dump() for c in a.tool_calls], width="stretch", hide_index=True)


st.title("Human questions")
api = client()
op = operator()
jobs = active_jobs()
questions = load("open_questions", lambda: api.questions("open")) or []
advice = load("latest_advice", api.latest_advice) or {}
st.caption(f"{len(questions)} open question(s)")
for q in questions:
    title = f"{q.pattern_key} | {q.row_count} row(s) | {q.rule_id} {q.reason}"
    with st.expander(title, expanded=bool(running(jobs, "advise", f"advise:{q.question_id}"))):
        st.markdown(f"**Missing:** {q.missing}")
        st.markdown(f"**Why the agent could not decide:** {q.why}")
        if q.llm_suggestion:
            s = q.llm_suggestion
            st.markdown(f"**LLM suggestion** ({q.llm_status}): {s.decision} `{s.value}` confidence "
                        f"{s.confidence:.2f} - {s.justification}")
        else:
            st.markdown(f"**LLM:** {q.llm_status}")
        st.dataframe([{"source_row_id": e.source_row_id, **e.values} for e in q.example_rows], width="stretch")

        slot = f"advise:{q.question_id}"
        job = running(jobs, "advise", slot)
        if job is not None:
            show_running(job)
        done = finished(slot)
        if done is not None and done.status == "failed":
            show_failure(done)
        if st.button("Demander l'avis de l'agent", key=f"advise_{q.question_id}", disabled=job is not None):
            launch(slot, lambda: api.advise_job(q.question_id, op))
        if q.question_id in advice:
            show_advice(advice[q.question_id])

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
auto_refresh(jobs)
