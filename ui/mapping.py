"""Step 1 of the home page: Column_Mapping proposal, operator correction and validation."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from agents.schemas import SchemaProposal
from ui.api_client import ApiCallError
from ui.common import act, finished, launch, load, running, show_failure, show_running

NONE = "(aucun)"
SAME = "background-color: #e3f4e3; color: #173b1b"
DIFF = "background-color: #fbe0dc; color: #5a1712; font-weight: bold"


def _proposal(api) -> SchemaProposal | None:
    """The proposal shown in the editor: the last one of this session, else the latest stored one (F5)."""
    if "_proposal" not in st.session_state:
        st.session_state["_proposal"] = load("_latest_proposal", api.latest_proposal)
    return st.session_state.get("_proposal")


def _table(p: SchemaProposal, reference: dict) -> None:
    st.dataframe(_styled_table(p, reference), width="stretch", hide_index=True)
    if p.differences_from_reference:
        st.markdown(f"**{len(p.differences_from_reference)} différence(s) avec le mapping de référence** "
                    "(lignes en rouge) :")
        for d in p.differences_from_reference:
            st.markdown(f"- `{d.column}` : proposé :red[{d.proposed or NONE}], référence :green[{d.reference or NONE}]")
    else:
        st.markdown(":green[Aucune différence avec le mapping de référence.]")


def _styled_table(p: SchemaProposal, reference: dict):
    rows = [{"Colonne source": c, "Attribut cible proposé": t or NONE,
             "Mapping de référence": reference.get(c) or NONE, "Écart": "oui" if t != reference.get(c) else ""}
            for c, t in p.column_mapping.items()]
    df = pd.DataFrame(rows)
    return df.style.apply(lambda r: [DIFF if r["Écart"] else SAME] * len(r), axis=1)


def mapping_step(api, op: str, jobs: list):
    """Render the section and return the validated Column_Mapping (None when nothing is validated)."""
    st.subheader("Étape 1 : mapping des colonnes")
    mapping = load("_validated_mapping", api.validated_mapping)
    if mapping is not None:
        when = mapping.validated_at.strftime("%d/%m/%Y à %H:%M:%S UTC")
        st.success(f"Mapping validé par {mapping.validated_by} le {when}"
                   + (f", avec {len(mapping.differences_from_reference)} différence(s) acceptée(s)"
                      if mapping.differences_from_reference else ""))
    else:
        st.warning("Aucun mapping validé : la migration ne traitera aucune ligne tant qu'il n'est pas validé.")

    job = running(jobs, "schema_discover")
    if job is not None:
        show_running(job)
    done = finished("schema")
    if done is not None and done.status == "succeeded" and st.session_state.get("_schema_shown") != done.job_id:
        st.session_state["_proposal"] = SchemaProposal(**done.result)
        st.session_state["_schema_shown"] = done.job_id
    elif done is not None and done.status == "failed":
        show_failure(done)

    c1, c2 = st.columns(2)
    if c1.button("Proposer le mapping avec l'IA", disabled=job is not None):
        launch("schema", lambda: api.schema_discover_job("llm"))
    if c2.button("Utiliser la correspondance de référence", disabled=job is not None):
        ref = act(lambda: api.schema_discover("reference"))
        if ref is not None:
            st.session_state["_proposal"] = ref
            st.rerun()

    p = _proposal(api)
    if p is None:
        return mapping
    info = load("_schema_reference", api.schema_reference)
    reference = info.mapping if info else {}
    origin = "Mapping de référence (Rule_Config)" if p.fallback else "Proposition de l'IA"
    st.markdown(f"**{origin}** ({p.created_at:%d/%m/%Y %H:%M:%S} UTC), formats : "
                + ", ".join(f"{f.name} `{f.ecuCode_pattern}`" for f in p.formats))
    _table(p, reference)
    if p.conflicts:
        st.error(f"Validation refusée tant qu'il reste {len(p.conflicts)} conflit(s) : corrigez les colonnes "
                 "ci-dessous ou utilisez le mapping de référence.\n\n"
                 + "\n".join(f"- {c.message or c.target_attribute}" for c in p.conflicts))
    targets = [NONE] + (info.targets if info else sorted({t for t in reference.values() if t}))
    with st.form("mapping_form"):
        st.markdown("**Correction colonne par colonne**")
        chosen = {}
        for column, target in p.column_mapping.items():
            current = target if target in targets else NONE
            chosen[column] = st.selectbox(column, targets, index=targets.index(current),
                                          key=f"map_{p.proposal_id}_{column}")
        if st.form_submit_button("Valider le mapping"):
            corrected = {c: (None if v == NONE else v) for c, v in chosen.items()}
            try:
                validated = api.validate_mapping(p.proposal_id, op, corrected)
            except ApiCallError as exc:
                st.error(f"Validation refusée : {exc.message}")
                if exc.status == 409:            # keep the corrected proposal and its conflicts on screen
                    st.session_state["_proposal"] = api.latest_proposal() or p
            else:
                st.session_state["_proposal"] = None          # nothing left to edit
                st.session_state["_validated_mapping"] = validated
                st.success(f"Mapping validé par {validated.validated_by}.")
                st.rerun()
    return mapping
