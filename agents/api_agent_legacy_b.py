"""API_Agent_Legacy_B: reads the Legacy_B_API and translates records to the Common_Vocabulary."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Literal

import httpx

from agents.schemas import FieldError
from legacy_b.schemas import LegacyBBulk, LegacyBRecord
from rules.config import RuleConfig
from rules.fields import dict_lookup, is_valid_vin, normalize_vin

CHUNK = 200
_DATE = re.compile(r"[0-9]{8}")


@dataclass(frozen=True)
class Translation:
    vin: str | None
    vehicle_model: str | None
    updated_on: str | None
    errors: tuple[FieldError, ...] = ()


@dataclass(frozen=True)
class ModelIndexResult:
    index: dict[str, str]
    status: Literal["ok", "failed"]
    errors: tuple[str, ...] = field(default=())


def translate(record: LegacyBRecord, cfg: RuleConfig) -> Translation:
    """Normalized VIN, canonical VehicleModel, ISO date; one FieldError per untranslatable field."""
    errors: list[FieldError] = []
    vin = normalize_vin(record.CHASSIS_NO)
    if not is_valid_vin(vin):
        errors.append(FieldError(field="CHASSIS_NO", raw=record.CHASSIS_NO))
        vin = None
    model = dict_lookup(record.MODELE, cfg.vehicle_models)
    if model is None:
        errors.append(FieldError(field="MODELE", raw=record.MODELE))
    updated = None
    if _DATE.fullmatch(record.DATE_MAJ):
        try:
            updated = datetime.strptime(record.DATE_MAJ, "%Y%m%d").date().isoformat()
        except ValueError:
            updated = None
    if updated is None:
        errors.append(FieldError(field="DATE_MAJ", raw=record.DATE_MAJ))
    return Translation(vin, model, updated, tuple(errors))


class LegacyBUnavailable(Exception):
    pass


class ApiAgentLegacyB:
    def __init__(self, client: httpx.Client, cfg: RuleConfig) -> None:
        self.http = client
        self.cfg = cfg

    @classmethod
    def from_url(cls, base_url: str, cfg: RuleConfig, timeout_s: float = 5.0) -> "ApiAgentLegacyB":
        return cls(httpx.Client(base_url=base_url, timeout=timeout_s), cfg)

    def model_reference(self, vin: str) -> Translation | None:
        """Translated record for an R1-normalized VIN; None when Legacy B has no record (404)."""
        try:
            r = self.http.get(f"/legacy-b/vehicles/{normalize_vin(vin)}")
        except httpx.HTTPError as exc:
            raise LegacyBUnavailable(type(exc).__name__) from None
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise LegacyBUnavailable(f"HTTP {r.status_code}")
        return translate(LegacyBRecord.model_validate(r.json()), self.cfg)

    def model_index(self, vins: Iterable[str]) -> ModelIndexResult:
        """Bulk reads in chunks of 200. A missing VIN has no entry; any transport error or 5xx fails the index."""
        vins = sorted({normalize_vin(v) for v in vins})
        index: dict[str, str] = {}
        for i in range(0, len(vins), CHUNK):
            chunk = vins[i:i + CHUNK]
            try:
                r = self.http.get("/legacy-b/vehicles", params=[("chassis_no", v) for v in chunk])
            except httpx.HTTPError as exc:
                return ModelIndexResult({}, "failed", (type(exc).__name__,))
            if r.status_code != 200:
                return ModelIndexResult({}, "failed", (f"HTTP {r.status_code}",))
            for rec in LegacyBBulk.model_validate(r.json()).records:
                t = translate(rec, self.cfg)
                if t.vin and t.vehicle_model:
                    index[t.vin] = t.vehicle_model
        return ModelIndexResult(index, "ok")
