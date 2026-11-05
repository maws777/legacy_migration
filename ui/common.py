"""Shared Streamlit helpers: API client, error banner, last-good-data cache, background jobs, Operator."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

import streamlit as st
from streamlit.errors import StreamlitPageNotFoundError

from ui.api_client import ApiCallError, OrchestratorClient

DEFAULT_OPERATOR = "operator"

CSS = """
<style>
.block-container { padding-top: 2.2rem; max-width: 1100px; }
.hero { padding: 1.2rem 1.4rem; border-radius: 16px; margin-bottom: 1.2rem;
        background: linear-gradient(135deg, rgba(15,118,110,.16), rgba(37,99,235,.10));
        border: 1px solid rgba(15,118,110,.25); }
.hero h1 { margin: 0 0 .3rem 0; font-size: 1.9rem; }
.hero p { margin: 0; opacity: .85; font-size: 1.02rem; }
.steps { display: grid; grid-template-columns: repeat(5, 1fr); gap: .6rem; margin: .4rem 0 1.4rem 0; }
.step { border-radius: 12px; padding: .7rem .8rem; border: 1px solid rgba(128,128,128,.28);
        background: rgba(128,128,128,.06); min-height: 96px; }
.step .num { display: inline-flex; width: 1.6rem; height: 1.6rem; border-radius: 50%; align-items: center;
             justify-content: center; font-weight: 700; font-size: .85rem; background: rgba(128,128,128,.3); }
.step .t { font-weight: 650; margin-top: .35rem; line-height: 1.2; }
.step .h { font-size: .78rem; opacity: .7; margin-top: .2rem; }
.step.done { border-color: rgba(22,163,74,.55); background: rgba(22,163,74,.10); }
.step.done .num { background: #16a34a; color: white; }
.step.current { border: 2px solid #0f766e; background: rgba(15,118,110,.12); }
.step.current .num { background: #0f766e; color: white; }
.step.todo { opacity: .65; }
.pill { display: inline-block; padding: .1rem .6rem; border-radius: 999px; font-size: .8rem; font-weight: 600;
        background: rgba(15,118,110,.18); }
.phase { border-radius: 12px; padding: .8rem 1rem; border: 1px solid rgba(128,128,128,.28); height: 100%; }
.phase.now { border: 2px solid #0f766e; background: rgba(15,118,110,.12); }
.phase b { display: block; margin-bottom: .2rem; }
.phase span { font-size: .85rem; opacity: .8; }
@media (max-width: 800px) { .steps { grid-template-columns: 1fr; } }
</style>
"""


def frame() -> None:
    """Entrypoint frame, rendered once for every page: tab config, styling, sidebar (name and short help)."""
    st.set_page_config(page_title="Migration des diagnostics", page_icon="🧭", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    operator()
    with st.sidebar:
        with st.expander("Besoin d'aide ?"):
            st.markdown(
                "**Que fait cet outil ?** Il copie les données de l'ancien système vers le nouveau, sans rien "
                "casser.\n\n"
                "**Que dois-je faire ?** Suivez les 5 étapes de la page *Accueil*. L'outil vous indique "
                "toujours la prochaine action.\n\n"
                "**Je me trompe ?** Rien n'est définitif : toutes les décisions sont enregistrées dans le "
                "*Journal*, et la bascule peut être annulée.")


def setup(title: str, icon: str) -> str:
    """Operator name for a page (``title`` and ``icon`` are given to the navigation, see ui/app.py)."""
    return st.session_state.get("_operator") or DEFAULT_OPERATOR


def link(page: str, label: str, icon: str) -> None:
    """Navigation link; tolerated as absent when the page is not run from the multipage entrypoint."""
    try:
        st.page_link(page, label=label, icon=icon)
    except StreamlitPageNotFoundError:
        pass


def hero(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)



def refresh_seconds() -> float:
    """Auto-refresh period while a job runs (3 s; 0 disables it, e.g. in UI tests)."""
    return float(os.environ.get("UI_REFRESH_SECONDS", "3"))


def client() -> OrchestratorClient:
    if "api" not in st.session_state:
        st.session_state.api = OrchestratorClient(os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8000"))
    return st.session_state.api


def show_error(exc: ApiCallError) -> None:
    hint = " Vérifiez que les services tournent (scripts\\start_all.ps1)." if "unreachable" in exc.message else ""
    st.error(f"Impossible de terminer « {exc.operation} » : {exc.message}.{hint}", icon="⚠️")


def load(key: str, fn: Callable[[], Any]) -> Any:
    """Call the API; on failure show a banner naming the operation and keep the last loaded data."""
    try:
        st.session_state[key] = fn()
    except ApiCallError as exc:
        show_error(exc)
    return st.session_state.get(key)


def act(fn: Callable[[], Any]) -> Any | None:
    try:
        return fn()
    except ApiCallError as exc:
        show_error(exc)
        return None


def operator() -> str:
    """Operator name, kept across pages (session) and across a page reload (``?operator=`` in the URL)."""
    default = st.session_state.get("_operator") or st.query_params.get("operator") or DEFAULT_OPERATOR
    value = st.sidebar.text_input("Votre nom", value=default,
                                  help="Enregistré avec chacune de vos décisions, pour savoir qui a validé quoi.").strip() or default
    st.session_state["_operator"] = value
    if st.query_params.get("operator") != value:
        st.query_params["operator"] = value
    return value


# ---------------------------------------------------------------- background jobs
def active_jobs() -> list:
    """Jobs queued or running on the Orchestrator (the server is the source of truth, so a page reload
    or another browser tab still sees them)."""
    return load("_active_jobs", lambda: client().jobs(active=True)) or []


def running(jobs: list, kind: str, key: str | None = None):
    return next((j for j in jobs if j.kind == kind and (key is None or j.key == key)), None)


def launch(slot: str, start: Callable[[], Any]) -> None:
    """Start a job and remember it for this page; the page then shows it as running."""
    job = act(start)
    if job is not None:
        st.session_state.setdefault("_watched", {})[slot] = job.job_id
        st.session_state.get("_finished", {}).pop(slot, None)
        st.rerun()


def show_running(job) -> None:
    started = job.started_at or job.submitted_at
    secs = max(0, int(time.time() - started.timestamp()))
    period = refresh_seconds()
    refresh = f", rafraîchissement automatique toutes les {period:g} s" if period > 0 else ""
    st.info(f"{job.label} : en cours ({secs} s){refresh}. Les boutons sont désactivés jusqu'à la fin.")


def finished(slot: str):
    """The finished job of this slot, once (then kept for display until the next launch), else None."""
    watched = st.session_state.get("_watched", {})
    done = st.session_state.setdefault("_finished", {})
    job_id = watched.get(slot)
    if job_id is not None:
        try:
            job = client().job(job_id)
        except ApiCallError as exc:
            show_error(exc)
            watched.pop(slot, None)
            return done.get(slot)
        if not job.active:
            watched.pop(slot, None)
            done[slot] = job
    return done.get(slot)


def show_failure(job) -> None:
    err = job.error
    st.error(f"« {job.label} » a échoué après {job.seconds or 0:g} s : {err.message if err else 'erreur inconnue'}", icon="⚠️")


def auto_refresh(jobs: list) -> None:
    """Rerun the page every few seconds while a job is active (call it at the end of the page)."""
    period = refresh_seconds()
    if period > 0 and (jobs or st.session_state.get("_watched")):
        time.sleep(period)
        st.rerun()

