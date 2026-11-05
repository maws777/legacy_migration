"""Journal : pourquoi chaque donnée a été copiée, corrigée, rejetée ou soumise à un humain."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import client, hero, load, setup  # noqa: E402
from ui.journey import ACTIONS, AUTHORS  # noqa: E402

setup("Journal", "📒")
api = client()
hero("Journal des décisions", "Rien n'est opaque : chaque décision est enregistrée avec sa raison. "
                              "Retrouvez ici ce qui est arrivé à une ligne précise.")

runs = sorted(load("runs", api.runs) or [], key=lambda r: r.started_at, reverse=True)
if not runs:
    st.info("Aucun traitement pour l'instant.")
    st.stop()
by_id = {r.run_id: r for r in runs}
run_id = st.selectbox("Traitement", list(by_id), format_func=lambda i: f"{by_id[i].started_at:%d/%m/%Y %H:%M}")

tab_row, tab_all, tab_ai = st.tabs(["🔎 Une ligne précise", "📋 Toutes les décisions", "🤖 Ce que l'IA a consulté"])

with tab_row:
    sel = st.number_input("Numéro de ligne du fichier source", min_value=1, step=1, value=1)
    detail = load("log_row", lambda: api.decisions(run_id, source_row=int(sel), limit=1000))
    if detail is not None:
        if not detail.items:
            st.info("Aucune décision pour cette ligne dans ce traitement.")
        for r in detail.items:
            with st.container(border=True):
                head = f"**{r.field}** · {ACTIONS.get(r.action, r.action)} · par {AUTHORS.get(r.decided_by, r.decided_by)}"
                if r.confidence is not None:
                    head += f" ({r.confidence:.0%} de confiance)"
                st.markdown(head)
                if r.before != r.after:
                    st.markdown(f"`{r.before}` → `{r.after}`")
                st.caption(r.explanation or r.reason)

with tab_all:
    c = st.columns(4)
    action = c[0].selectbox("Type de décision", [""] + list(ACTIONS), format_func=lambda a: ACTIONS.get(a, "Toutes"))
    decided_by = c[1].selectbox("Décidé par", [""] + list(AUTHORS), format_func=lambda a: AUTHORS.get(a, "Tous"))
    field = c[2].text_input("Champ", help="Par exemple : ecuName")
    rule_id = c[3].text_input("Règle", help="Par exemple : R5")
    filters = {"field": field.strip() or None, "action": action or None, "rule_id": rule_id.strip() or None,
               "decided_by": decided_by or None}
    page = load("log_page", lambda: api.decisions(run_id, limit=500, **filters))
    if page is not None:
        if page.total == 0:
            st.info("Aucune décision ne correspond à ces filtres.")
        else:
            st.caption(f"{page.total:,} décision(s) · les {len(page.items)} premières sont affichées")
            st.dataframe([{"Ligne": r.source_row, "Champ": r.field, "Décision": ACTIONS.get(r.action, r.action),
                           "Règle": r.rule_id, "Avant": r.before, "Après": r.after,
                           "Par": AUTHORS.get(r.decided_by, r.decided_by), "Explication": r.explanation}
                          for r in page.items], hide_index=True, width="stretch")

with tab_ai:
    calls = load("tool_calls", lambda: api.tool_calls(run_id))
    if calls is not None:
        if not calls:
            st.info("L'IA n'a rien consulté dans ce traitement (aucun cas inhabituel, ou IA indisponible).")
        else:
            st.caption("Pour chaque cas inhabituel, l'IA peut interroger des outils en lecture seule avant de proposer une réponse.")
            st.dataframe([c.model_dump(mode="json") for c in calls], hide_index=True, width="stretch")
