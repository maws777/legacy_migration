"""Accueil : un parcours guidé en 5 étapes. Lancer : ``streamlit run ui/app.py``."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from agents.schemas import SchemaProposal  # noqa: E402
from ui.common import (  # noqa: E402
    act, active_jobs, auto_refresh, client, finished, hero, launch, link, load, running, setup, show_failure, show_running,
)
from ui.journey import PHASES, RUN_STATUS, build_steps, phase_label  # noqa: E402

op = setup("Accueil", "🧭")
api = client()
jobs = active_jobs()

mapping = load("mapping", api.schema_mapping)
runs = sorted(load("runs", api.runs) or [], key=lambda r: r.started_at, reverse=True)
phase = load("phase_home", api.phase)
open_questions = load("q_home", api.questions) or []
recon = load("recon_home", api.latest_reconciliation)
paused = sorted({r.campaign_id for r in runs if r.campaign_status == "paused"})
aligned = bool(recon and recon.compared > 0 and recon.gaps == 0)
steps = build_steps(mapping_ok=mapping is not None, runs=runs, open_questions=len(open_questions),
                    paused=bool(paused), aligned=aligned, phase=phase.current_phase if phase else 1)
current = next((s for s in steps if s.status == "current"), None)

hero("Migration des diagnostics véhicule",
     "Cet outil copie les données de l'ancien système vers le nouveau. Il traite seul les cas évidents, "
     "fait appel à l'IA pour les cas inhabituels, et ne vous sollicite que lorsqu'il ne peut vraiment pas décider.")

ICON = {"done": "✓", "current": "→", "todo": ""}
cards = "".join(
    f'<div class="step {s.status}"><span class="num">{ICON[s.status] or s.number}</span>'
    f'<div class="t">{s.title}</div><div class="h">{s.hint}</div></div>' for s in steps)
st.markdown(f'<div class="steps">{cards}</div>', unsafe_allow_html=True)

pipeline_job = running(jobs, "campaign") or running(jobs, "resume")
schema_job = running(jobs, "schema_discover")


# ---------------------------------------------------------------- step panels
def mapping_table(columns: dict) -> list[dict]:
    return [{"Colonne du fichier": c, "Champ de destination": t or "— ignorée —"} for c, t in columns.items()]


def panel_schema() -> None:
    st.markdown("### Étape 1 · Comprendre le fichier")
    st.write("Avant de migrer, l'outil doit savoir à quoi correspond chaque colonne du fichier source. "
             "Il propose une correspondance, **vous la relisez et la validez**.")
    if schema_job is not None:
        show_running(schema_job)
    c1, c2 = st.columns(2)
    if c1.button("✨ Analyser le fichier avec l'IA", type="primary", disabled=schema_job is not None,
                 help="L'IA lit l'en-tête et quelques lignes. Peut prendre une minute."):
        launch("schema", lambda: api.schema_discover_job("llm"))
    if c2.button("Utiliser la correspondance de référence", disabled=schema_job is not None,
                 help="Correspondance prédéfinie, sans IA. Instantané."):
        launch("schema", lambda: api.schema_discover_job("reference"))
    done = finished("schema")
    if done is None:
        return
    if done.status != "succeeded":
        show_failure(done)
        return
    proposal = SchemaProposal(**done.result)
    st.markdown("**Proposition à relire**")
    if proposal.fallback:
        st.info("L'IA n'était pas disponible : voici la correspondance de référence.")
    st.dataframe(mapping_table(proposal.column_mapping), hide_index=True, width="stretch")
    for d in proposal.differences_from_reference:
        st.warning(f"Différence avec la référence pour « {d.column} » : proposé {d.proposed or 'aucun'}, "
                   f"attendu {d.reference or 'aucun'}.")
    for conflict in proposal.conflicts:
        st.error(f"Conflit sur « {conflict.target_attribute} » : {', '.join(conflict.source_columns)}. "
                 f"{conflict.message}")
    if st.button("✅ Je valide cette correspondance", type="primary", disabled=bool(proposal.conflicts)):
        if act(lambda: api.validate_schema(proposal.proposal_id, op)) is not None:
            st.session_state.get("_finished", {}).pop("schema", None)
            st.rerun()


def panel_migrate() -> None:
    st.markdown("### Étape 2 · Lancer la migration")
    st.write("L'outil lit chaque ligne de l'ancien système et applique ses règles. Les lignes sûres sont copiées, "
             "les erreurs évidentes corrigées ; les cas ambigus seront regroupés en **quelques questions** pour vous.")
    if pipeline_job is not None:
        show_running(pipeline_job)
    if st.button("🚀 Lancer la migration", type="primary", disabled=pipeline_job is not None):
        launch("campaign", lambda: api.start_campaign_job(op))


def panel_questions() -> None:
    st.markdown("### Étape 3 · Répondre aux questions")
    if open_questions:
        rows = sum(q.row_count for q in open_questions)
        st.write(f"**{len(open_questions)} question(s)** concernent **{rows:,} ligne(s)**. "
                 "Une réponse s'applique à tout un groupe de lignes semblables.")
        link("pages/2_Questions.py", label="Répondre aux questions", icon="💬")
        return
    st.write("Toutes les questions ont une réponse. Relancez le traitement pour que vos réponses soient appliquées.")
    if pipeline_job is not None:
        show_running(pipeline_job)
    cid = st.selectbox("Migration en pause", paused) if len(paused) > 1 else (paused[0] if paused else None)
    if cid and st.button("▶️ Appliquer mes réponses", type="primary", disabled=pipeline_job is not None):
        launch("resume", lambda: api.resume_job(cid, op))


def panel_verify() -> None:
    st.markdown("### Étape 4 · Vérifier")
    st.write("L'outil compare un échantillon de dossiers entre l'ancien et le nouveau système.")
    if st.button("🔍 Comparer les deux systèmes", type="primary"):
        if act(api.reconcile_control_sample):
            st.session_state.pop("recon_home", None)
            st.rerun()
    if recon:
        if recon.gaps == 0:
            st.success(f"Identiques sur {recon.compared:,} dossiers comparés.")
        else:
            st.warning(f"{recon.gaps:,} écart(s) sur {recon.compared:,} dossiers comparés.")


def panel_switch() -> None:
    st.markdown("### Étape 5 · Basculer")
    n = phase.current_phase if phase else 1
    st.write("Tout est prêt. La bascule fait lire le nouveau système par les applications ; elle demande une "
             "approbation explicite et reste réversible.")
    link("pages/4_Bascule.py", label=f"Ouvrir la page Bascule (actuellement : {phase_label(n)})", icon="🔀")


PANELS = {"schema": panel_schema, "migrate": panel_migrate, "questions": panel_questions,
          "verify": panel_verify, "switch": panel_switch}

# ---------------------------------------------------------------- job results
for slot in ("campaign", "resume"):
    done = finished(slot)
    if done is None:
        continue
    if done.status == "succeeded":
        r = done.result
        if r["open_questions"]:
            st.success(f"Traitement terminé en {done.seconds:g} s. {r['open_questions']} question(s) vous attendent.",
                       icon="✅")
        else:
            st.success(f"Traitement terminé en {done.seconds:g} s. Aucune question en attente "
                       f"(Run {r['run_id']} {r['status']}, campagne {r['campaign_status']}).", icon="✅")
    else:
        show_failure(done)

# ---------------------------------------------------------------- what to do now
if current is not None:
    with st.container(border=True):
        PANELS[current.key]()
else:
    st.success("Migration terminée : les applications lisent le nouveau système. 🎉")
    link("pages/4_Bascule.py", label="Voir la page Bascule", icon="🔀")

# ---------------------------------------------------------------- new full run, available at any time
if runs:
    with st.expander("🔁 Relancer un traitement complet"):
        st.write("Retraite tout le fichier avec les règles actuelles et vos réponses enregistrées. "
                 "Utile après avoir modifié les règles ou annulé une décision (page **Décisions**). "
                 "Ensuite, répondez aux nouvelles questions éventuelles puis **refaites la comparaison** "
                 "avant de basculer.")
        if st.button("🔁 Relancer un traitement complet", disabled=pipeline_job is not None, key="rerun"):
            st.session_state.pop("recon_home", None)
            launch("campaign", lambda: api.start_campaign_job(op))

if mapping is not None and current is not None and current.key != "schema":
    with st.expander("Correspondance des colonnes validée"):
        st.caption(f"Validée par {mapping.validated_by} le {mapping.validated_at:%d/%m/%Y à %H:%M}.")
        st.dataframe(mapping_table(mapping.mapping), hide_index=True, width="stretch")

# ---------------------------------------------------------------- latest result
latest = next((r for r in runs if r.status == "completed"), None)
if latest is not None:
    st.subheader("Dernier traitement")
    report = load(f"home_report_{latest.run_id}", lambda: api.report(latest.run_id))
    metrics = load(f"home_metrics_{latest.run_id}", lambda: api.metrics(latest.run_id))
    if report:
        cols = st.columns(5)
        cols[0].metric("Lignes analysées", f"{report.analysed:,}")
        cols[1].metric("Migrées telles quelles", f"{report.migrate:,}",
                       help="Données déjà conformes, copiées sans modification.")
        cols[2].metric("Corrigées", f"{report.correct:,}", help="Corrigées automatiquement par une règle.")
        cols[3].metric("Rejetées", f"{report.reject:,}",
                       help=f"{report.rejected_out_of_scope:,} hors périmètre, {report.rejected_error:,} en erreur.")
        cols[4].metric("À décider par vous", f"{report.pending_human:,}")
        st.progress(min(report.coverage_pct, 100) / 100,
                    text=f"{report.loaded:,} lignes sur {report.analysed:,} chargées dans le nouveau système "
                         f"({report.coverage_pct:g} %)")
        if metrics and metrics.degraded_mode:
            st.warning("L'IA n'était pas disponible pendant ce traitement : les cas inhabituels vous ont été "
                       "envoyés. Démarrez le modèle (Ollama) puis lancez un nouveau traitement.", icon="🤖")
        elif report.tokens:
            st.caption(f"🤖 L'IA a été utilisée : {report.tokens:,} tokens (≈ {report.cost_eur:.4f} € théoriques).")
        st.caption(f"Durée : {report.seconds:.1f} s · {report.traced_pct:g} % des décisions sont tracées.")

if phase:
    reader = "le nouveau système" if phase.current_phase == 4 else "l'ancien système"
    st.caption(f"Les applications lisent actuellement **{reader}** · {phase_label(phase.current_phase)}")

with st.expander("Historique des traitements"):
    if runs:
        st.dataframe([{"Début": r.started_at.strftime("%d/%m/%Y %H:%M:%S"),
                       "Statut": RUN_STATUS.get(r.status, r.status),
                       "Type": "Complet" if r.kind == "full" else "Rechargement",
                       "Questions ouvertes": r.open_questions} for r in runs], hide_index=True, width="stretch")
    else:
        st.caption("Aucun traitement pour l'instant.")
    st.caption(" · ".join(f"{n}. {t}" for n, (t, _) in PHASES.items()))

auto_refresh(jobs)
