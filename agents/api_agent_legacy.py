"""API_Agent_Legacy: Legacy A access (httpx) and schema discovery with human validation."""
from __future__ import annotations

import json
import re
import uuid
from typing import Callable, Protocol

import httpx
from sqlalchemy import Boolean, Column, DateTime, Engine, MetaData, String, Table, Text, delete, insert, select, update

from agents.schemas import ColumnMapping, FormatSpec, MappingConflict, MappingDifference, SchemaProposal
from legacy_a.schemas import Header, RowsPage, VinRows
from legacy_a.store import LegacyAStore
from rules.config import RuleConfig
from rules.timeutil import as_utc, naive_utc, utcnow

TARGET_ATTRIBUTES = (
    "Vehicle.VIN", "Vehicle.VehicleModel", "DiagnozedECU.ecuName", "DiagnosticOperation.diagnosticOpID",
    "DiagnosticOperation.creationDate", "DiagValueResult.ValueReference", "DiagValueResult.ValueDescription",
    "DiagToolsVersions.DiagToolName", "DiagToolsVersions.DiagToolVersion", "Label",
)
DEFAULT_FORMATS = [FormatSpec(name="Format_1", ecuCode_pattern="^ID000[1-5]$"),
                   FormatSpec(name="Format_2", ecuCode_pattern="^Id[0-9]{3}$")]
DEFAULT_CRITERION = ("Format_1 rows carry ecuCode ID0001-ID0005; Format_2 rows carry ecuCode Id### and a UDS DID "
                     "in the label.")
SYSTEM = "You map the columns of a CSV export to a target data model. Reply with JSON only."
_JSON = re.compile(r"\{.*\}", re.DOTALL)


class LegacyASource(Protocol):
    def header(self) -> list[str]: ...
    def rows_page(self, page: int, page_size: int) -> RowsPage: ...


class LegacyAClient:
    """httpx client over the Legacy_A_API (used by the Orchestrator and the Reconciler)."""

    def __init__(self, client: httpx.Client) -> None:
        self.http = client

    @classmethod
    def from_url(cls, base_url: str, timeout_s: float = 5.0) -> "LegacyAClient":
        return cls(httpx.Client(base_url=base_url, timeout=timeout_s))

    def header(self) -> list[str]:
        r = self.http.get("/legacy-a/header")
        r.raise_for_status()
        return Header.model_validate(r.json()).columns

    def rows_page(self, page: int, page_size: int) -> RowsPage:
        r = self.http.get("/legacy-a/rows", params={"page": page, "page_size": page_size})
        r.raise_for_status()
        return RowsPage.model_validate(r.json())

    def rows_by_vin(self, vin: str) -> VinRows:
        r = self.http.get(f"/legacy-a/vehicles/{vin}/rows")
        r.raise_for_status()
        return VinRows.model_validate(r.json())

    def is_reachable(self) -> bool:
        try:
            return self.http.get("/legacy-a/health").status_code == 200
        except httpx.HTTPError:
            return False


class LocalLegacyASource:
    """Reads the source file directly (pipeline-side discovery without the HTTP service)."""

    def __init__(self, store_factory: Callable[[], LegacyAStore]) -> None:
        self._factory = store_factory

    def header(self) -> list[str]:
        return list(self._factory().header)

    def rows_page(self, page: int, page_size: int) -> RowsPage:
        from legacy_a.schemas import RawRowOut
        s = self._factory()
        rows = [RawRowOut(source_row_id=r.source_row_id, values=dict(r.values), malformed=r.malformed)
                for r in s.page(page, page_size)]
        return RowsPage(page=page, page_size=page_size, total_rows=s.total_rows, rows=rows)


class MappingConflictError(Exception):
    def __init__(self, conflicts: list[MappingConflict]) -> None:
        super().__init__(f"{len(conflicts)} Column_Mapping conflict(s)")
        self.conflicts = conflicts


MAPPING_MD = MetaData()
column_mapping = Table(
    "column_mapping", MAPPING_MD,
    Column("id", String(36), primary_key=True),
    Column("status", String(10), nullable=False),            # proposed | validated
    Column("mapping", Text, nullable=False),
    Column("formats", Text, nullable=False),
    Column("format_criterion", Text, nullable=False),
    Column("conflicts", Text, nullable=False),
    Column("fallback", Boolean(create_constraint=False), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("validated_by", String(128)),
    Column("validated_at", DateTime),
)


def find_conflicts(mapping: dict[str, str | None], required: tuple[str, ...]) -> list[MappingConflict]:
    out = []
    for target in required:
        cols = sorted(c for c, t in mapping.items() if t == target)
        if len(cols) != 1:
            out.append(MappingConflict(
                target_attribute=target, source_columns=cols,
                message=f"{target} is mapped by {len(cols)} column(s){': ' + ', '.join(cols) if cols else ''}"))
    return out


def duplicate_conflicts(duplicates: dict[str, list]) -> list[MappingConflict]:
    """A column given several times in the LLM answer is a conflict, even with the same target."""
    out = []
    for column, targets in sorted(duplicates.items()):
        shown = " / ".join(str(t) for t in targets)
        out.append(MappingConflict(
            target_attribute=" / ".join(sorted({str(t) for t in targets})), source_columns=[column],
            kind="duplicate_column",
            message=f"column {column} appears {len(targets)} times in the LLM answer ({shown})"))
    return out


def mapping_differences(mapping: dict[str, str | None], reference: dict[str, str | None]) -> list[MappingDifference]:
    """Column by column, in header order then reference order, where the proposal differs from the reference."""
    columns = list(mapping) + [c for c in reference if c not in mapping]
    return [MappingDifference(column=c, proposed=mapping.get(c), reference=reference.get(c))
            for c in columns if mapping.get(c) != reference.get(c)]


def _pairs_keeping_duplicates(pairs):
    """``object_pairs_hook``: a dict whose ``__duplicates__`` entry lists repeated keys with all their values."""
    out, seen = {}, {}
    for k, v in pairs:
        seen.setdefault(k, []).append(v)
        out[k] = v
    dups = {k: vs for k, vs in seen.items() if len(vs) > 1}
    if dups:
        out["__duplicates__"] = dups
    return out


class SchemaDiscoveryAgent:
    def __init__(self, source: LegacyASource, engine: Engine, config: Callable[[], RuleConfig], llm=None) -> None:
        self.source = source
        self.engine = engine
        self._config = config
        self.llm = llm
        MAPPING_MD.create_all(engine)

    def _prompt(self, header: list[str], rows: list[dict], cfg: RuleConfig) -> str:
        lines = [";".join(r.get(h, "") for h in header) for r in rows]
        return (f"header: {';'.join(header)}\n"
                f"sample (first {len(rows)} rows, ';'-separated):\n" + "\n".join(lines) + "\n"
                "target attributes: " + ", ".join(TARGET_ATTRIBUTES) + "\n"
                "The ecuCode column mixes two record formats; describe the criterion.\n"
                'response: {"column_mapping": {"<header column>": "<target attribute>"|null, ...}, '
                '"formats": [{"name": "Format_1", "ecuCode_pattern": "..."}, {"name": "Format_2", '
                '"ecuCode_pattern": "..."}], "format_criterion": "<text>"}')

    def _reference(self, header: list[str], cfg: RuleConfig) -> dict[str, str | None]:
        ref = cfg.column_mapping.mapping
        return {h: ref.get(h) for h in header}

    def discover(self, run_id: str = "schema-discovery", use_llm: bool = True) -> SchemaProposal:
        """Propose a Column_Mapping (LLM, or the reference of Rule_Config when ``use_llm`` is False or the
        LLM is unavailable or fails). Conflicts and differences from the reference are part of the proposal."""
        cfg = self._config()
        header = self.source.header()
        mapping, formats, criterion, fallback, duplicates = None, DEFAULT_FORMATS, DEFAULT_CRITERION, True, {}
        if use_llm and self.llm is not None and self.llm.is_available():
            sample = self.source.rows_page(1, cfg.pipeline.discovery_sample_size).rows
            res = self.llm.complete(run_id=run_id, purpose="schema_discovery", system=SYSTEM,
                                    prompt=self._prompt(header, [r.values for r in sample], cfg), max_tokens=800)
            if res.ok:
                parsed = self._parse(res.text, header)
                if parsed is not None:
                    mapping, formats, criterion, duplicates = parsed
                    fallback = False
        if mapping is None:
            mapping = self._reference(header, cfg)
        conflicts = duplicate_conflicts(duplicates) + find_conflicts(mapping, cfg.column_mapping.required_targets)
        proposal = SchemaProposal(
            proposal_id=str(uuid.uuid4()), column_mapping=mapping, formats=formats, format_criterion=criterion,
            conflicts=conflicts, fallback=fallback, created_at=utcnow(),
            differences_from_reference=mapping_differences(mapping, dict(cfg.column_mapping.mapping)))
        with self.engine.begin() as c:
            c.execute(insert(column_mapping).values(
                id=proposal.proposal_id, status="proposed", mapping=json.dumps(mapping),
                formats=json.dumps([f.model_dump() for f in formats]), format_criterion=criterion,
                conflicts=json.dumps([x.model_dump() for x in proposal.conflicts]), fallback=fallback,
                created_at=naive_utc(proposal.created_at)))
        return proposal

    @staticmethod
    def _parse(text: str, header: list[str]):
        """(mapping, formats, criterion, duplicate columns) from the LLM answer, or None when unreadable."""
        m = _JSON.search(text or "")
        if not m:
            return None
        try:
            data = json.loads(m.group(0), object_pairs_hook=_pairs_keeping_duplicates)
            raw = data["column_mapping"]
            if not isinstance(raw, dict):
                return None
            duplicates = {k: v for k, v in (raw.pop("__duplicates__", None) or {}).items()}
            mapping = {h: (raw.get(h) if raw.get(h) in TARGET_ATTRIBUTES else None) for h in header}
            formats = [FormatSpec(**f) for f in data.get("formats", [])] or DEFAULT_FORMATS
            criterion = str(data.get("format_criterion") or DEFAULT_CRITERION)
        except (ValueError, KeyError, TypeError):
            return None
        return mapping, formats, criterion, duplicates

    def proposal(self, proposal_id: str) -> SchemaProposal | None:
        with self.engine.connect() as c:
            r = c.execute(select(column_mapping).where(column_mapping.c.id == proposal_id)).mappings().first()
        if r is None:
            return None
        mapping = json.loads(r["mapping"])
        return SchemaProposal(
            proposal_id=r["id"], column_mapping=mapping,
            formats=[FormatSpec(**f) for f in json.loads(r["formats"])], format_criterion=r["format_criterion"],
            conflicts=[MappingConflict(**x) for x in json.loads(r["conflicts"])], fallback=r["fallback"],
            created_at=as_utc(r["created_at"]),
            differences_from_reference=mapping_differences(mapping, dict(self._config().column_mapping.mapping)))

    def validate(self, proposal_id: str, operator: str) -> ColumnMapping:
        """Human validation; refused while conflicts remain. The answer repeats the differences from the
        reference so the operator sees exactly what is being accepted."""
        p = self.proposal(proposal_id)
        if p is None:
            raise KeyError(proposal_id)
        if p.conflicts:
            raise MappingConflictError(p.conflicts)
        now = utcnow()
        with self.engine.begin() as c:
            c.execute(delete(column_mapping).where(column_mapping.c.status == "validated"))
            c.execute(update(column_mapping).where(column_mapping.c.id == proposal_id).values(
                status="validated", validated_by=operator, validated_at=naive_utc(now)))
        return ColumnMapping(mapping=p.column_mapping, validated_by=operator, validated_at=now,
                             differences_from_reference=p.differences_from_reference)

    def validated_mapping(self) -> ColumnMapping | None:
        with self.engine.connect() as c:
            r = c.execute(select(column_mapping).where(column_mapping.c.status == "validated")).mappings().first()
        if r is None:
            return None
        return ColumnMapping(mapping=json.loads(r["mapping"]), validated_by=r["validated_by"],
                             validated_at=as_utc(r["validated_at"]))


def read_header(path) -> tuple[str, ...]:
    """Header line only (UTF-8, BOM removed) for the Column_Mapping check."""
    import csv
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return tuple(next(csv.reader(fh, delimiter=";"), []))


def mapping_provider_for(agent: SchemaDiscoveryAgent):
    """``check_mapping``: validated Column_Mapping vs file header (Requirements 23.6-23.8)."""
    def provider(cfg: RuleConfig, source_path) -> tuple[dict | None, str | None]:
        validated = agent.validated_mapping()
        if validated is None:
            return None, ("Column_Mapping validation pending: no row processed. Run schema discovery and validate "
                          "the proposal (POST /ops/schema/discover, POST /ops/schema/validate).")
        try:
            header = set(read_header(source_path))
        except (OSError, UnicodeDecodeError):
            return dict(validated.mapping), None          # the ingest node reports the read error
        expected = set(validated.mapping)
        added, missing = sorted(header - expected), sorted(expected - header)
        if added or missing:
            return None, (f"Header differs from the validated Column_Mapping: added columns {added}, missing "
                          f"columns {missing}. A new schema discovery and validation is required; no row processed.")
        return dict(validated.mapping), None
    return provider
