"""Mes décisions : les réponses déjà données, avec la possibilité de les annuler."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import act, client, hero, link, load, setup  # noqa: E402

op = setup("Décisions", "🗂️")
api = client()
hero("Mes décisions", "Les réponses que vous avez données. Une réponse annulée est oubliée : au prochain "
                      "traitement complet, la question sera posée à nouveau, et vous pourrez répondre autrement.")

KIND = {"value": "Valeur appliquée", "reject": "Groupe rejeté", "generate": "Identifiant généré"}

if st.session_state.pop("_revoked", False):
    st.success("Décision annulée. Relancez un traitement complet depuis l'accueil pour la reposer.", icon="↩️")

decisions = load("decisions_made", api.decisions_made) or []
if not decisions:
    st.info("Aucune décision enregistrée pour l'instant.")
    link("pages/2_Questions.py", label="Répondre aux questions", icon="💬")
    st.stop()

for i, d in enumerate(decisions):
    with st.container(border=True):
        left, right = st.columns([4, 1], vertical_alignment="center")
        kind = KIND.get(d.kind, d.kind) + (f" : `{d.value}`" if d.value else "")
        rows = f" · {d.row_count:,} ligne(s)" if d.row_count else ""
        left.markdown(f"**{d.field or 'Motif'}**{rows}  \n{kind}")
        left.caption(f"Par {d.operator} · {d.answered_at:%d/%m/%Y %H:%M} · motif `{d.pattern_key}`")
        if right.button("↩️ Annuler", key=f"revoke_{i}", help="Oublier cette réponse"):
            if act(lambda: api.revoke_decision(d.pattern_key, op)) is not None:
                st.session_state.pop("decisions_made", None)
                st.session_state["_revoked"] = True
                st.rerun()

link("pages/0_Accueil.py", label="Retour à l'accueil pour relancer un traitement", icon="🧭")
