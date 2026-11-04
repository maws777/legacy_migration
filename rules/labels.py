"""Label (DescriptionDiagECU) features: DID, Label_ECU, Label_Template."""
from __future__ import annotations

import re
from typing import Sequence

from rules.types import LabelFeatures

_DID_RE = re.compile(r"\(([0-9A-Fa-f]{4})\)\s*$")
_WORD_RE = re.compile(r"\w+")
ECU_TOKEN = "<ECU>"


def extract_did(label: str) -> str | None:
    m = _DID_RE.search(label)
    return m.group(1).upper() if m else None


def label_ecu(label: str, ecus: Sequence[str]) -> str | None:
    """The ECUDico entry named in the label, when exactly one entry appears as a whole word."""
    entries = set(ecus)
    found = {w for w in _WORD_RE.findall(label.upper()) if w in entries}
    return found.pop() if len(found) == 1 else None


def label_template(label: str, ecus: Sequence[str]) -> str:
    entries = set(ecus)
    return _WORD_RE.sub(lambda w: ECU_TOKEN if w.group().upper() in entries else w.group(), label).strip()


def label_features(label: str, ecus: Sequence[str]) -> LabelFeatures:
    return LabelFeatures(did=extract_did(label), ecu=label_ecu(label, ecus), template=label_template(label, ecus))
