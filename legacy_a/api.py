"""Legacy_A_API (port 8001): GET-only access to the raw rows.

Run: ``uvicorn legacy_a.api:app --port 8001``. The source path comes from ``LEGACY_A_CSV`` or
the Rule_Config ``paths.source_csv``; it is loaded on the first request.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from fastapi import FastAPI, Query

from legacy_a.schemas import Header, RawRowOut, RowsPage, VinRows
from legacy_a.store import LegacyAStore
from rules.fields import normalize_vin
from rules.types import RawRow


def _out(row: RawRow) -> RawRowOut:
    return RawRowOut(source_row_id=row.source_row_id, values=dict(row.values), malformed=row.malformed)


def _default_store() -> LegacyAStore:
    path = os.environ.get("LEGACY_A_CSV")
    if not path:
        from rules.config import load_rule_config
        path = load_rule_config().pipeline.paths.source_csv
    return LegacyAStore.from_path(path)


def create_app(store: LegacyAStore | Callable[[], LegacyAStore] | None = None) -> FastAPI:
    app = FastAPI(title="Legacy_A_API")
    lock = threading.Lock()
    holder: dict[str, LegacyAStore] = {}
    factory = store if callable(store) else (lambda: store) if store is not None else _default_store

    def get_store() -> LegacyAStore:
        with lock:
            if "s" not in holder:
                holder["s"] = factory()
            return holder["s"]

    @app.get("/legacy-a/rows", response_model=RowsPage)
    def rows(page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)) -> RowsPage:
        s = get_store()
        return RowsPage(page=page, page_size=page_size, total_rows=s.total_rows,
                        rows=[_out(r) for r in s.page(page, page_size)])

    @app.get("/legacy-a/vehicles/{vin}/rows", response_model=VinRows)
    def vehicle_rows(vin: str) -> VinRows:
        s = get_store()
        return VinRows(vin=normalize_vin(vin), rows=[_out(r) for r in s.rows_by_vin(vin)])

    @app.get("/legacy-a/header", response_model=Header)
    def header() -> Header:
        return Header(columns=list(get_store().header))

    @app.get("/legacy-a/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
