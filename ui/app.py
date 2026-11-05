"""Migration UI entrypoint. Run: ``streamlit run ui/app.py`` (Orchestrator on ``ORCHESTRATOR_URL``)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from ui.common import frame  # noqa: E402

frame()
st.navigation([
    st.Page("pages/0_Accueil.py", title="Accueil", icon="🧭", default=True),
    st.Page("pages/1_Resultats.py", title="Résultats", icon="📊"),
    st.Page("pages/2_Questions.py", title="Questions", icon="💬"),
    st.Page("pages/5_Decisions.py", title="Décisions", icon="🗂️"),
    st.Page("pages/3_Journal.py", title="Journal", icon="📒"),
    st.Page("pages/4_Bascule.py", title="Bascule", icon="🔀"),
]).run()
