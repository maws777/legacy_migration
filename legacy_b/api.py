"""Legacy_B_API (port 8002): GET-only, native Legacy B format, independent of the phase.

Run: ``uvicorn legacy_b.api:app --port 8002`` after ``python -m legacy_b.simulator``.
The records path comes from ``LEGACY_B_CSV`` or ``legacy_b/data/legacy_b_records.csv``.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from legacy_b.schemas import LegacyBBulk, LegacyBRecord
from legacy_b.simulator import DEFAULT_OUT, RECORDS_FILE
from legacy_b.store import LegacyBStore
from rules.errors import error_payload


def _default_store() -> LegacyBStore:
    return LegacyBStore.from_csv(os.environ.get("LEGACY_B_CSV") or DEFAULT_OUT / RECORDS_FILE)


def create_app(store: LegacyBStore | Callable[[], LegacyBStore] | None = None) -> FastAPI:
    app = FastAPI(title="Legacy_B_API")
    lock = threading.Lock()
    holder: dict[str, LegacyBStore] = {}
    factory = store if callable(store) else (lambda: store) if store is not None else _default_store

    def get_store() -> LegacyBStore:
        with lock:
            if "s" not in holder:
                holder["s"] = factory()
            return holder["s"]

    @app.get("/legacy-b/vehicles/{chassis_no}", response_model=LegacyBRecord)
    def one(chassis_no: str):
        rec = get_store().get(chassis_no)
        if rec is None:
            return JSONResponse(status_code=404, content=error_payload("NOT_FOUND", "no Legacy B record"))
        return rec

    @app.get("/legacy-b/vehicles", response_model=LegacyBBulk)
    def bulk(chassis_no: list[str] = Query(..., min_length=1, max_length=200)) -> LegacyBBulk:
        s = get_store()
        records, missing = [], []
        for c in chassis_no:
            rec = s.get(c)
            (records.append(rec) if rec is not None else missing.append(c))
        return LegacyBBulk(records=records, missing=missing)

    @app.get("/legacy-b/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
