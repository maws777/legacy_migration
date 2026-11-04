"""Legacy_A_API response schemas (frozen contract)."""
from __future__ import annotations

from pydantic import BaseModel


class RawRowOut(BaseModel):
    source_row_id: int
    values: dict[str, str]
    malformed: bool = False


class RowsPage(BaseModel):
    page: int
    page_size: int
    total_rows: int
    rows: list[RawRowOut]


class VinRows(BaseModel):
    vin: str
    rows: list[RawRowOut]


class Header(BaseModel):
    columns: list[str]
