"""Questions en attente : une à la fois, avec le contexte, la suggestion de l'IA et des choix expliqués."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import act, client, hero, link, load, setup  # noqa: E402
from ui.journey import QUESTION_OPTIONS  # noqa: E402

op = setup("Questions", "💬")
api = client()
hero("Vos décisions", "Quand l'outil ne peut pas décider seul, il regroupe les cas semblables en une question. "
                      "Votre réponse s'applique à tout le groupe.")

questions = sorted(load("open_questions", lambda: api.questions("open")) or [], key=lambda q: -q.row_count)
answered = st.session_state.get("_answered_count", 0)

if not questions:
    st.success("Aucune question en attente. 🎉" if not answered else
               f"Terminé : {answered} question(s) traitée(s) pendant cette session.")
    link("pages/0_Accueil.py", label="Retour à l'accueil pour appliquer vos réponses", icon="🧭")
    st.stop()

total = len(questions) + answered
st.progress(answered / total, text=f"{answered} réponse(s) donnée(s) · {len(questions)} restante(s)")
q = questions[0]

with st.container(border=True):
    scope = f"la ligne {q.source_row_id}" if q.scope == "row" else f"{q.row_count:,} ligne(s) du même type"
    st.markdown(f"### Que faire pour {scope} ?")
    st.markdown(f"**Champ concerné :** `{q.field}`  \n**Problème :** {q.missing}")
    st.caption(f"Pourquoi l'outil ne décide pas seul : {q.why}")
    if q.example_rows:
        st.markdown("**Exemples de lignes concernées**")
        st.dataframe([{"Ligne": e.source_row_id, **e.values} for e in q.example_rows],
                     hide_index=True, width="stretch")

    suggestion = q.llm_suggestion
    suggested_option = None
    if suggestion:
        suggested_option = "reject_group" if suggestion.decision == "reject" else "apply_value"
        st.info(f"🤖 **Suggestion de l'IA** ({suggestion.confidence:.0%} de confiance) : "
                f"{'rejeter' if suggestion.decision == 'reject' else f'utiliser `{suggestion.value}`'}. "
                f"{suggestion.justification}")
    elif q.llm_status == "degraded":
        st.caption("🤖 L'IA n'était pas disponible : aucune suggestion.")

    with st.form(f"form_{q.question_id}"):
        options = list(q.options)
        index = options.index(suggested_option) if suggested_option in options else 0
        option = st.radio("Votre choix", options, index=index, key=f"opt_{q.question_id}",
                          format_func=lambda o: QUESTION_OPTIONS.get(o, (o, ""))[0],
                          captions=[QUESTION_OPTIONS.get(o, ("", ""))[1] for o in options])
        value = None
        if "apply_value" in options:
            default = suggestion.value if suggestion and suggestion.value else None
            if q.allowed_values:
                idx = q.allowed_values.index(default) if default in q.allowed_values else 0
                value = st.selectbox("Valeur à appliquer (si vous choisissez « Appliquer une valeur »)",
                                     q.allowed_values, index=idx, key=f"val_{q.question_id}")
            else:
                value = st.text_input("Valeur à appliquer (si vous choisissez « Appliquer une valeur »)",
                                      value=default or "", key=f"val_{q.question_id}")
        if st.form_submit_button("Valider ma réponse", type="primary"):
            if option == "apply_value" and not (value or "").strip():
                st.error("Indiquez la valeur à appliquer.")
            else:
                res = act(lambda: api.answer(q.question_id, option, op, value if option == "apply_value" else None))
                if res is not None:
                    st.session_state["_answered_count"] = answered + 1
                    st.session_state.pop("open_questions", None)
                    if res.created_questions:
                        st.toast(f"{len(res.created_questions)} nouvelle(s) question(s) par ligne créée(s).")
                    st.rerun()
