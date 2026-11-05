"""Questions en attente : validation en lot des suggestions de l'IA, ou une à la fois avec des choix expliqués."""
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


def usable(q) -> bool:
    """An AI proposal the rules accept, for a question that allows that kind of answer."""
    return q.llm_suggestion is not None and q.suggestion_issue is None


def proposed_answer(q) -> tuple[str, str | None]:
    s = q.llm_suggestion
    return ("reject_group", None) if s.decision == "reject" else ("apply_value", s.value)


def describe(q) -> str:
    s = q.llm_suggestion
    return "rejeter" if s.decision == "reject" else f"utiliser `{s.value}`"


def record(count: int) -> None:
    st.session_state["_answered_count"] = st.session_state.get("_answered_count", 0) + count
    st.session_state.pop("open_questions", None)


def render_single(q) -> None:
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
        if usable(q):
            suggested_option = proposed_answer(q)[0]
            st.info(f"🤖 **Suggestion de l'IA** ({suggestion.confidence:.0%} de confiance) : {describe(q)}. "
                    f"{suggestion.justification}")
        elif suggestion:
            st.warning(f"🤖 L'IA propose de {describe(q)}, mais les règles refusent : {q.suggestion_issue}.")
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
                default = suggestion.value if usable(q) and suggestion.value else None
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
                    res = act(lambda: api.answer(q.question_id, option, op,
                                                 value if option == "apply_value" else None))
                    if res is not None:
                        record(1)
                        if res.created_questions:
                            st.toast(f"{len(res.created_questions)} nouvelle(s) question(s) par ligne créée(s).")
                        st.rerun()


def render_batch(items: list) -> None:
    candidates = [q for q in items if usable(q)]
    refused = [q for q in items if q.llm_suggestion is not None and not usable(q)]
    if not candidates:
        st.info("Aucune suggestion de l'IA n'est exploitable : répondez question par question.")
    else:
        st.write("L'IA propose une réponse par question et **les règles de l'outil l'ont déjà vérifiée**. "
                 "Cochez celles que vous validez : vous restez le décideur.")
        threshold = st.slider("Cocher d'office les suggestions dont la confiance est au moins de", 0, 100, 70,
                              step=5, format="%d %%", key="batch_threshold")
        picked = {}
        with st.form("batch_form"):
            for q in candidates:
                s = q.llm_suggestion
                cols = st.columns([1, 5, 3], vertical_alignment="center")
                picked[q.question_id] = cols[0].checkbox(
                    "Accepter", value=s.confidence * 100 >= threshold, key=f"pick_{q.question_id}_{threshold}")
                cols[1].markdown(f"**{q.field}** · {q.row_count:,} ligne(s)  \n→ {describe(q)}")
                cols[1].caption(s.justification)
                cols[2].progress(min(max(s.confidence, 0.0), 1.0), text=f"Confiance {s.confidence:.0%}")
            if st.form_submit_button("Valider les suggestions cochées", type="primary"):
                done = 0
                for q in candidates:
                    if not picked[q.question_id]:
                        continue
                    option, value = proposed_answer(q)
                    if act(lambda: api.answer(q.question_id, option, op, value)) is None:
                        break
                    done += 1
                if done:
                    record(done)
                    st.session_state["_batch_done"] = done
                    st.rerun()
    if refused:
        with st.expander(f"{len(refused)} suggestion(s) écartée(s) par les règles"):
            for q in refused:
                st.markdown(f"- **{q.field}** ({q.row_count:,} ligne(s)) : l'IA propose de {describe(q)}, "
                            f"refusé : {q.suggestion_issue}.")
    without = len(items) - len(candidates) - len(refused)
    if without:
        st.caption(f"{without} question(s) sans suggestion : à traiter dans l'onglet « Une par une ».")


if "_batch_done" in st.session_state:
    st.success(f"{st.session_state.pop('_batch_done')} réponse(s) enregistrée(s).", icon="✅")

if not questions:
    st.success("Aucune question en attente. 🎉" if not answered else
               f"Terminé : {answered} question(s) traitée(s) pendant cette session.")
    link("pages/0_Accueil.py", label="Retour à l'accueil pour appliquer vos réponses", icon="🧭")
    st.stop()

total = len(questions) + answered
st.progress(answered / total, text=f"{answered} réponse(s) donnée(s) · {len(questions)} restante(s)")

single_tab, batch_tab = st.tabs(["Une par une", f"Validation en lot ({sum(usable(q) for q in questions)})"])
with single_tab:
    render_single(questions[0])
with batch_tab:
    render_batch(questions)
