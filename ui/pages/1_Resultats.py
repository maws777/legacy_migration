"""Résultats d'un traitement : un verdict, un tableau, quelques chiffres. Détails repliés."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import client, hero, link, load, setup  # noqa: E402
from ui.journey import RUN_STATUS  # noqa: E402

setup("Résultats", "📊")
api = client()
hero("Résultats", "Ce qu'il s'est passé pendant un traitement.")

runs = sorted(load("runs", api.runs) or [], key=lambda r: r.started_at, reverse=True)
if not runs:
    st.info("Aucun traitement pour l'instant. Lancez la migration depuis l'accueil.")
    link("pages/0_Accueil.py", label="Aller à l'accueil", icon="🧭")
    st.stop()

by_id = {r.run_id: r for r in runs}
run_id = st.selectbox("Traitement", list(by_id), format_func=lambda i: (
    f"{by_id[i].started_at:%d/%m/%Y %H:%M} · {RUN_STATUS.get(by_id[i].status, by_id[i].status)}"
    f" · {'complet' if by_id[i].kind == 'full' else 'rechargement'}"))
report = load(f"report_{run_id}", lambda: api.report(run_id))
metrics = load(f"metrics_{run_id}", lambda: api.metrics(run_id))
recon = load("recon_stats", lambda: api.latest_reconciliation("control_sample"))
if not report:
    st.stop()

# 1. Verdict
pending = report.pending_human
if pending:
    st.warning(f"**{pending:,} ligne(s) attendent votre décision.** Les autres sont traitées.", icon="⏳")
    link("pages/2_Questions.py", label="Répondre aux questions", icon="💬")
else:
    st.success(f"**Terminé : {report.loaded:,} lignes sur {report.analysed:,} sont dans le nouveau système "
               f"({report.coverage_pct:g} %).**", icon="✅")
st.progress(min(report.coverage_pct, 100) / 100,
            text=f"{report.loaded:,} / {report.analysed:,} lignes chargées ({report.coverage_pct:g} %)")

# 2. Un seul tableau : ce qu'on a fait des lignes
total = max(report.analysed, 1)
table = [
    ("✅ Copiées telles quelles", report.migrate, "Déjà conformes."),
    ("🔧 Corrigées puis copiées", report.correct, "Une règle a corrigé la donnée."),
    ("⛔ Rejetées", report.reject,
     f"{report.rejected_out_of_scope:,} hors périmètre, {report.rejected_error:,} en erreur."),
    ("⏳ En attente de vous", report.ask_human, "Réponses à donner dans la page Questions."),
]
st.dataframe(
    [{"Résultat": label, "Lignes": n, "Part": n / total, "Détail": note} for label, n, note in table],
    hide_index=True, width="stretch",
    column_config={"Lignes": st.column_config.NumberColumn(format="%d"),
                   "Part": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})

# 3. Trois chiffres utiles
c = st.columns(3)
c[0].metric("Durée", f"{report.seconds:.1f} s")
c[1].metric("Décisions expliquées", f"{report.traced_pct:g} %", help="Consultables ligne par ligne dans le Journal.")
c[2].metric("Identique à l'ancien système",
            f"{recon.aligned_pct:g} %" if recon and recon.aligned_pct is not None else "pas mesuré",
            help="Mesuré sur l'échantillon de contrôle (page Bascule).")

# 4. IA, en une phrase
if metrics and metrics.degraded_mode:
    st.warning("L'IA n'était pas disponible : les cas inhabituels ont été envoyés aux humains.", icon="🤖")
elif report.tokens:
    st.caption(f"🤖 L'IA a étudié {metrics.distinct_pattern_keys if metrics else '?'} motif(s) inhabituel(s) : "
               f"{report.tokens:,} tokens (≈ {report.cost_eur:.4f} € théoriques).")
else:
    st.caption("🤖 L'IA n'a pas été nécessaire : tout a été traité par des règles.")

with st.expander("Détails techniques"):
    for n in report.notes or []:
        st.markdown(f"- {n}")
    st.code(report.text, language="text")
    if metrics:
        st.json(metrics.model_dump(mode="json"))
