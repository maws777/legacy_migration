"""Shared Streamlit helpers: API client, error banner, last-good-data cache, background jobs, Operator."""
from __future__ import annotations

import os
import time
from typing import Any, Callable

import streamlit as st

from ui.api_client import ApiCallError, OrchestratorClient

DEFAULT_OPERATOR = "operator"


def refresh_seconds() -> float:
    """Auto-refresh period while a job runs (3 s; 0 disables it, e.g. in UI tests)."""
    return float(os.environ.get("UI_REFRESH_SECONDS", "3"))


def client() -> OrchestratorClient:
    if "api" not in st.session_state:
        st.session_state.api = OrchestratorClient(os.environ.get("ORCHESTRATOR_URL", "http://127.0.0.1:8000"))
    return st.session_state.api


def load(key: str, fn: Callable[[], Any]) -> Any:
    """Call the API; on failure show a banner naming the operation and keep the last loaded data."""
    try:
        st.session_state[key] = fn()
    except ApiCallError as exc:
        st.error(f"{exc.operation} failed: {exc.message}")
    return st.session_state.get(key)


def act(fn: Callable[[], Any]) -> Any | None:
    try:
        return fn()
    except ApiCallError as exc:
        st.error(f"{exc.operation} failed: {exc.message}")
        return None


def operator() -> str:
    """Operator name, kept across pages (session) and across a page reload (``?operator=`` in the URL)."""
    default = st.session_state.get("_operator") or st.query_params.get("operator") or DEFAULT_OPERATOR
    value = st.sidebar.text_input("Operator", value=default).strip() or default
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
            st.error(f"{exc.operation} failed: {exc.message}")
            watched.pop(slot, None)
            return done.get(slot)
        if not job.active:
            watched.pop(slot, None)
            done[slot] = job
    return done.get(slot)


def show_failure(job) -> None:
    err = job.error
    st.error(f"{job.label} failed after {job.seconds or 0:g} s: {err.message if err else 'unknown error'}")


def auto_refresh(jobs: list) -> None:
    """Rerun the page every few seconds while a job is active (call it at the end of the page)."""
    period = refresh_seconds()
    if period > 0 and jobs:
        time.sleep(period)
        st.rerun()
