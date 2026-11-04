"""Legacy_B_API schemas: the native Legacy B format."""
from __future__ import annotations

from pydantic import BaseModel


class LegacyBRecord(BaseModel):
    CHASSIS_NO: str
    MODELE: str
    DATE_MAJ: str


class LegacyBBulk(BaseModel):
    records: list[LegacyBRecord]
    missing: list[str]
