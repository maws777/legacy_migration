"""Field-level rule functions (pure). Design "Normalization definitions"."""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Sequence

UNREADABLE_MARKER = "#NOM?"
_NON_VIN_CHARS = re.compile(r"[^0-9A-Z]")


def normalize_vin(raw: str) -> str:
    """R1 normalization: trim, uppercase, remove every character outside [0-9A-Z]."""
    return _NON_VIN_CHARS.sub("", raw.strip().upper())


def _drop_separators(s: str) -> str:
    return "".join(c for c in s if not (c.isspace() or c == "-"))


def normalized_key(raw: str) -> str:
    """Normalized_Key: remove whitespace and dashes, then uppercase (R5, R6)."""
    # The second pass only matters for exotic characters whose uppercase form is a separator.
    return _drop_separators(_drop_separators(raw).upper())


@lru_cache(maxsize=64)
def _dict_index(dico: tuple[str, ...]) -> dict[str, str]:
    return {normalized_key(e): e for e in dico}


def dict_lookup(raw: str, dico: Sequence[str]) -> str | None:
    """Canonical dictionary entry whose Normalized_Key equals the one of ``raw``, else None."""
    return _dict_index(tuple(dico)).get(normalized_key(raw))


def is_unreadable(raw: str) -> bool:
    """Unreadable_Value: empty after trimming or equal to ``#NOM?``."""
    s = raw.strip()
    return s == "" or s == UNREADABLE_MARKER


# ---------------------------------------------------------------- field rules (rest of rules/fields.py)
from datetime import datetime  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

from rules.templates import render  # noqa: E402
from rules.types import FieldDecision, R2Result, R4Result  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover
    from rules.config import RuleConfig

_VIN_FULL = re.compile(r"[0-9A-Z]{20}")
_REF_FULL = re.compile(r"[0-9A-Z]{10}")
_EXCEL_SCI = re.compile(r"[0-9]+([.,][0-9]+)?E[+-]?[0-9]+")
_DATE_RE = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{2}):([0-9]{2})")


def is_valid_vin(v: str) -> bool:
    return _VIN_FULL.fullmatch(v) is not None


def is_valid_value_ref(v: str) -> bool:
    return _REF_FULL.fullmatch(v) is not None


def r1_vin(raw: str, normalized: str | None = None) -> FieldDecision:
    """R1: REJECT INVALID_VIN, MIGRATE OK (unchanged) or CORRECT NORMALIZED."""
    v = normalize_vin(raw) if normalized is None else normalized
    if not is_valid_vin(v):
        return FieldDecision("VIN", "REJECT", "R1", "INVALID_VIN", raw, "",
                             explanation=render("R1", "INVALID_VIN", normalized=v, length=len(v)))
    if v == raw:
        return FieldDecision("VIN", "MIGRATE", "R1", "OK", raw, raw, explanation=render("R1", "OK", before=raw))
    return FieldDecision("VIN", "CORRECT", "R1", "NORMALIZED", raw, v,
                         explanation=render("R1", "NORMALIZED", before=raw, after=v,
                                            removed=len(raw.strip()) - len(v)))


def r2_tool(raw: str, cfg: "RuleConfig") -> R2Result:
    if is_unreadable(raw):
        return R2Result("unreadable", raw.strip(), None)
    t = raw.strip()
    if t in cfg.mappings.tool_map:
        return R2Result("mapped", t, cfg.mappings.tool_map[t])
    if t in cfg.tools:
        return R2Result("migrate", t, t)
    return R2Result("unknown", t, None)


def r4_classify(raw: str) -> R4Result:
    """R4 for ID0002-ID0005. First reason wins: EMPTY > NOM > EXCEL_SCI > FORBIDDEN_CHAR > WRONG_LENGTH."""
    v = raw.strip().upper()
    if len(v) == 0:
        reason: str | None = "EMPTY"
    elif v == UNREADABLE_MARKER:
        reason = "NOM"
    elif _EXCEL_SCI.fullmatch(v):
        reason = "EXCEL_SCI"
    elif any(not ("0" <= c <= "9" or "A" <= c <= "Z") for c in v):
        reason = "FORBIDDEN_CHAR"
    elif len(v) != 10:
        reason = "WRONG_LENGTH"
    else:
        reason = None
    return R4Result(reason, v)


def parse_creation_date(raw: str) -> datetime | None:
    m = _DATE_RE.fullmatch(raw.strip())
    if not m:
        return None
    d, mo, y, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def format_creation_date(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M")


def iso_minute(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")
