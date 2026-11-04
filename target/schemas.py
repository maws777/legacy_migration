"""Target_API and client response schemas (frozen contract 1)."""
from __future__ import annotations

from pydantic import BaseModel


class DiagnosticItem(BaseModel):
    diagnostic_op_id: str | None
    creation_date: str | None            # ISO "YYYY-MM-DDTHH:MM"
    ecu_name: str | None
    value_description: str | None
    value_reference: str | None
    diag_tool_name: str | None
    diag_tool_version: str | None
    vehicle_model: str | None


class DiagnosticsResponse(BaseModel):
    vin: str
    operations: list[DiagnosticItem]


class TableStats(BaseModel):
    tables: dict[str, int]
