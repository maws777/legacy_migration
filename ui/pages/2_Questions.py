"""Questions en attente : validation en lot des suggestions de l'IA, ou une à la fois avec des choix expliqués."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import (  # noqa: E402
    act, active_jobs, auto_refresh, client, finished, hero, launch, link, load, running, setup, show_failure,
    show_running,
)
from ui.journey import QUESTION_OPTIONS  # noqa: E402

op = setup("Questions", "💬")
api = client()
hero("Vos décisions", "Quand l'outil ne peut pas décider seul, il regroupe les cas semblables en une question. "
                      "Votre réponse s'applique à tout le groupe.")

jobs = active_jobs()
advice = load("latest_advice", api.latest_advice) or {}
questions = sorted(load("open_questions", lambda: api.questions("open")) or [], key=lambda q: -q.row_count)
answered = st.session_state.get("_answered_count", 0)


def proposal(q) -> dict | None:
    """The best answer proposed by an AI for this question: the advisor first, else the Resolver_Agent.
    Both are checked by the rules before they get here."""
    a = advice.get(q.question_id)
    r = a.recommendation if a is not None and a.status == "recommended" else None
    if r is not None and r.option in q.options:
        value = r.value if r.option == "apply_value" else None
        what = "rejeter" if r.option == "reject_group" else (f"utiliser `{value}`" if value else r.option)
        return {"option": r.option, "value": value, "confidence": r.confidence, "why": r.justification,
                "what": what, "source": "Conseiller IA"}
    s = q.llm_suggestion
    if s is not None and q.suggestion_issue is None:
        reject = s.decision == "reject"
        return {"option": "reject_group" if reject else "apply_value", "value": None if reject else s.value,
                "confidence": s.confidence, "why": s.justification,
                "what": "rejeter" if reject else f"utiliser `{s.value}`", "source": "IA"}
    return None


def usable(q) -> bool:
    return proposal(q) is not None


def describe(q) -> str:
    p = proposal(q)
    if p:
        return p["what"]
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
        best = proposal(q)
        suggested_option = best["option"] if best else None
        slot = f"advise:{q.question_id}"
        job = running(jobs, "advise", slot)
        if job is not None:
            show_running(job)
        done = finished(slot)
        if done is not None and done.status == "failed":
            show_failure(done)
        if best:
            st.info(f"🤖 **{best['source']}** ({best['confidence']:.0%} de confiance) : {best['what']}. "
                    f"{best['why']}")
            a = advice.get(q.question_id)
            if a is not None and a.recommendation is not None and a.recommendation.evidence:
                with st.expander("Preuves du conseiller"):
                    st.markdown("\n".join(f"- {e}" for e in a.recommendation.evidence))
                    if a.recommendation.unknowns:
                        st.markdown("**Ce qu'il ne peut pas savoir :**\n"
                                    + "\n".join(f"- {u}" for u in a.recommendation.unknowns))
        elif q.question_id in advice and advice[q.question_id].status != "recommended":
            st.warning(f"Le conseiller IA n'a pas pu recommander : {advice[q.question_id].failure or 'statut ' + advice[q.question_id].status}.")
        if st.button("Demander conseil à l'IA", key=f"advise_{q.question_id}", disabled=job is not None):
            launch(slot, lambda: api.advise_job(q.question_id, op))
        if best:
            pass
        elif suggestion and q.suggestion_issue:
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
                default = best["value"] if best and best["option"] == "apply_value" else None
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
    todo = [q for q in items if q.question_id not in advice]
    if todo and st.button(f"Demander conseil à l'IA pour les {len(todo)} question(s) sans avis",
                          disabled=bool(running(jobs, "advise"))):
        for q in todo:
            if act(lambda: api.advise_job(q.question_id, op)) is None:
                break
        st.rerun()
    if running(jobs, "advise"):
        st.info("Le conseiller IA étudie les questions : les propositions apparaîtront ici au fil de l'eau.")
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
                p = proposal(q)
                cols = st.columns([1, 5, 3], vertical_alignment="center")
                picked[q.question_id] = cols[0].checkbox(
                    "Accepter", value=p["confidence"] * 100 >= threshold, key=f"pick_{q.question_id}_{threshold}")
                cols[1].markdown(f"**{q.field}** · {q.row_count:,} ligne(s)  \n→ {p['what']} _({p['source']})_")
                cols[1].caption(p["why"])
                cols[2].progress(min(max(p["confidence"], 0.0), 1.0), text=f"Confiance {p['confidence']:.0%}")
            if st.form_submit_button("Valider les suggestions cochées", type="primary"):
                done = 0
                for q in candidates:
                    if not picked[q.question_id]:
                        continue
                    p = proposal(q)
                    if act(lambda: api.answer(q.question_id, p["option"], op, p["value"])) is None:
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
auto_refresh(jobs)
