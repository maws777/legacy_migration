"""Bascule : choisir quel système les applications lisent, avec garde-fous et retour arrière."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from ui.common import act, client, hero, link, load, setup  # noqa: E402
from ui.journey import GATES, PHASES  # noqa: E402

op = setup("Bascule", "🔀")
api = client()
hero("Bascule", "La migration se fait par étapes. À chaque étape, les applications lisent soit l'ancien, "
                "soit le nouveau système. On ne passe au nouveau qu'après vérification et approbation.")
status = load("phase", api.phase)
now = status.current_phase if status else None

cols = st.columns(4)
for col, (n, (title, text)) in zip(cols, PHASES.items()):
    marker = " · vous êtes ici" if n == now else ""
    col.markdown(f'<div class="phase {"now" if n == now else ""}"><b>{n}. {title}{marker}</b><span>{text}</span></div>',
                 unsafe_allow_html=True)
st.write("")


def show(result) -> None:
    if result is None:
        return
    if result.status == "refused":
        st.error(f"Refusé : {result.reason}", icon="⛔")
    elif result.status == "pending_approval":
        st.warning(f"{result.reason}. Confirmez ci-dessous pour approuver.", icon="⏳")
    elif result.status == "cancelled":
        st.info("Bascule annulée.")
    else:
        st.success(f"C'est fait : phase {result.current_phase} · {PHASES[result.current_phase][0]}.", icon="✅")
    st.session_state.pop("phase", None)


if status:
    st.subheader("Prêt à passer à la phase suivante ?")
    for chk in status.gate:
        st.markdown(f"{'✅' if chk.passed else '⚠️'} **{GATES.get(chk.name, chk.name)}**")
        st.caption(chk.message)
    if status.summary:
        s = status.summary
        st.info(f"Dernier traitement : {s['loaded']:,} lignes sur {s['analysed']:,} chargées ({s['coverage_pct']} %). "
                f"Non chargées : {s['rejected_out_of_scope']:,} hors périmètre, {s['rejected_error']:,} en erreur, "
                f"{s['pending_human']:,} en attente d'une décision. En approuvant, vous acceptez ces exclusions.")

    if status.pending_target:
        with st.container(border=True):
            st.markdown(f"### Approbation requise : passer à la phase {status.pending_target} · "
                        f"{PHASES[status.pending_target][0]}")
            st.write("Cette étape change le système lu par les applications.")
            c1, c2 = st.columns(2)
            if c1.button("✅ J'approuve la bascule", type="primary"):
                show(act(lambda: api.approve(op)))
            if c2.button("Annuler"):
                show(act(lambda: api.reject(op)))
    else:
        options = [p for p in PHASES if p != now]
        target = st.selectbox("Aller vers", options, format_func=lambda p: f"{p}. {PHASES[p][0]}")
        st.caption(PHASES[target][1])
        if st.button("Demander ce changement", type="primary"):
            show(act(lambda: api.transition(target, op)))

    if now in (3, 4):
        with st.expander("Revenir en arrière"):
            st.write(f"Les applications retourneront sur la phase {now - 1} · {PHASES[now - 1][0]}. Aucune donnée n'est perdue.")
            if st.button(f"Revenir à la phase {now - 1}"):
                show(act(lambda: api.rollback(now - 1, op)))

st.subheader("Comparer les deux systèmes")
st.write("Compare un échantillon de dossiers entre l'ancien et le nouveau système.")
if st.button("🔍 Lancer la comparaison"):
    r = act(api.reconcile_control_sample)
    if r:
        if r.gaps == 0:
            st.success(f"Identiques sur {r.compared:,} dossiers comparés.")
        else:
            st.warning(f"{r.gaps:,} écart(s) sur {r.compared:,} dossiers : "
                       f"{r.missing_in_target} manquant(s) dans le nouveau, {r.value_mismatch} valeur(s) différente(s), "
                       f"{r.unexpected_in_target} en trop, {r.duplicate_in_target} en double.")
        st.session_state.pop("phase", None)

with st.expander("Historique des changements"):
    log = load("transitions", api.transitions) or []
    if log:
        st.dataframe([{"Date": e.at.strftime("%d/%m/%Y %H:%M:%S"), "De": e.from_phase, "Vers": e.to_phase,
                       "Type": "Retour arrière" if e.rollback else "Bascule", "Par": e.operator} for e in log],
                     hide_index=True, width="stretch")
    else:
        st.caption("Aucun changement pour l'instant.")
