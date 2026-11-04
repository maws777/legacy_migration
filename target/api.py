"""Target_API (port 8003): read-only GET routes over the Target_DB.

Run: ``uvicorn target.api:app --port 8003``. The database comes from ``TARGET_DB`` or Rule_Config paths.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from rules.errors import error_payload
from rules.fields import normalize_vin
from target.repository import TargetRepository
from target.schema import make_engine, sqlite_url
from target.schemas import DiagnosticsResponse, TableStats


def _default_repo() -> TargetRepository:
    path = os.environ.get("TARGET_DB")
    if not path:
        from rules.config import load_rule_config
        path = load_rule_config().pipeline.paths.target_db
    return TargetRepository(make_engine(sqlite_url(path), read_only=True))


def create_app(repo: TargetRepository | Callable[[], TargetRepository] | None = None) -> FastAPI:
    app = FastAPI(title="Target_API")
    lock = threading.Lock()
    holder: dict[str, TargetRepository] = {}
    factory = repo if callable(repo) else (lambda: repo) if repo is not None else _default_repo

    def get_repo() -> TargetRepository:
        with lock:
            if "r" not in holder:
                holder["r"] = factory()
            return holder["r"]

    @app.get("/target/vehicles/{vin}/diagnostics", response_model=DiagnosticsResponse)
    def diagnostics(vin: str):
        v = normalize_vin(vin)
        items = get_repo().diagnostics_by_vin(v)
        if not items:
            return JSONResponse(status_code=404, content=error_payload("NOT_FOUND", f"no diagnostics for VIN {v}"))
        return DiagnosticsResponse(vin=v, operations=items)

    @app.get("/target/stats", response_model=TableStats)
    def stats() -> TableStats:
        return TableStats(tables=get_repo().table_counts())

    @app.get("/target/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
