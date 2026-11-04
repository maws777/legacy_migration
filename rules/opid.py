"""Deterministic diagnosticOpID: UUID v5 over the canonical JSON of the Operation_Identity."""
from __future__ import annotations

import json
import uuid

from rules.types import OperationIdentity

OP_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:legacy-migration:diagnostic-operation")


def canonical_identity(i: OperationIdentity) -> str:
    """JSON array (no concatenation collisions). Includes DiagToolName and DiagToolVersion (decisions.md)."""
    return json.dumps([i.vin, i.ecu_name, i.value_description, i.creation_date, i.value_reference,
                       i.diag_tool_name, i.diag_tool_version], separators=(",", ":"), ensure_ascii=True)


def generate_op_id(i: OperationIdentity) -> str:
    return str(uuid.uuid5(OP_ID_NAMESPACE, canonical_identity(i)))
