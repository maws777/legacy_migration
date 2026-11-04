"""Shared Streamlit helpers: API client, error banner, last-good-data cache."""
from __future__ import annotations

import os
from typing import Any, Callable

import streamlit as st

from ui.api_client import ApiCallError, OrchestratorClient


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
    return st.sidebar.text_input("Operator", value=st.session_state.get("operator", "operator"), key="operator")
