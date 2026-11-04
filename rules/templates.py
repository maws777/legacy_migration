"""Explanation templates, one per (rule_id, reason) pair. Zero tokens (tech.md principle 6)."""
from __future__ import annotations

from typing import Mapping

ANY = "*"   # templates valid for any rule_id (cases resolved outside the rules)

TEMPLATES: dict[tuple[str, str], str] = {
    ("INGEST", "MALFORMED_ROW"): "Row cell count differs from the header cell count; row rejected as malformed (A9).",
    ("R1", "OK"): "VIN '{before}' is valid (20 characters [0-9A-Z]).",
    ("R1", "NORMALIZED"): "VIN normalized from '{before}' to '{after}' (trim, uppercase, {removed} disallowed characters removed).",
    ("R1", "INVALID_VIN"): "Normalized VIN '{normalized}' has {length} characters; 20 characters [0-9A-Z] are required.",
    ("R2", "OK"): "Tool '{after}' is in ToolDico.",
    ("R2", "MAPPED"): "Tool '{before}' mapped to '{after}' (R2 mapping).",
    ("R2", "UNKNOWN_VARIANT"): "Tool '{before}' is not in ToolDico and has no R2 mapping. {llm}",
    ("R3", "FORMAT1"): "ecuCode {after} used as ValueDescription.{note}",
    ("R3", "DID_MAPPED"): "Format_2 ecuCode '{before}': label DID {did} mapped to {after}.",
    ("R3", "OUT_OF_SCOPE"): "DID {did} is not F187/F191/F188/F012: out of scope.",
    ("R3", "FORMAT2_NO_DID"): "Format_2 ecuCode '{before}' with label '{label}' that carries no DID: ValueDescription cannot be derived.",
    ("R3", "DEDUCED_DID"): "ecuCode '{before}' unreadable; ValueDescription {after} deduced from label DID {did}.",
    ("R3", "DEDUCED_VIN_LABEL"): "ecuCode '{before}' unreadable; label '{label}' names a VIN read: ValueDescription ID0001.",
    ("R3", "UNREADABLE"): "ecuCode '{before}' unreadable and label '{label}' gives no DID and is not a VIN label.",
    ("R3", "UNKNOWN_VARIANT"): "ecuCode '{before}' is neither Format_1 (ID0001-ID0005) nor Format_2 (Id###). {llm}",
    ("R4", "OK"): "ValueReference '{before}' matches ^[0-9A-Z]{{10}}$.",
    ("R4", "NORMALIZED"): "ValueReference normalized from '{before}' to '{after}' (trim, uppercase).",
    ("R4", "VIN_AS_VALUE"): "ValueDescription ID0001: ValueReference '{before}' replaced by the normalized VIN '{after}'.",
    ("R4", "EMPTY"): "ValueReference is empty.",
    ("R4", "NOM"): "ValueReference '{before}' is the Excel error #NOM?.",
    ("R4", "EXCEL_SCI"): "ValueReference '{before}' is Excel scientific notation; digits were lost.",
    ("R4", "FORBIDDEN_CHAR"): "ValueReference '{before}' contains characters outside 0-9 and A-Z: {chars}.",
    ("R4", "WRONG_LENGTH"): "ValueReference '{value}' has {length} characters; exactly 10 are required.",
    ("R5", "OK"): "ecuName '{after}' is in ECUDico.",
    ("R5", "DICT_NORMALIZED"): "ecuName '{before}' normalized to the ECUDico entry '{after}'.",
    ("R5", "LABEL_ECU"): "ecuName '{before}' not in ECUDico; ECU '{after}' taken from the label.",
    ("R5", "UNREADABLE"): "ecuName '{before}' unreadable and the label '{label}' names no single ECU.",
    ("R5", "UNKNOWN_VARIANT"): "ecuName '{before}' (key {normalized}) is not in ECUDico and the label names no single ECU. {llm}",
    ("R6", "OK"): "Vehmodel '{after}' is in VehicleModelsDico.",
    ("R6", "DICT_NORMALIZED"): "Vehmodel '{before}' normalized to the VehicleModelsDico entry '{after}'.",
    ("R6", "VIN_SIBLING"): "Vehmodel '{before}' not in VehicleModelsDico; model '{after}' taken from the other rows of VIN {vin}.",
    ("R6", "LEGACY_B"): "Vehmodel unreadable; model '{after}' taken from Legacy B for VIN {vin}.",
    ("R6", "UNREADABLE"): "Vehmodel '{before}' unreadable; no unique model on other rows of VIN {vin} and no Legacy B model.",
    ("R6", "UNKNOWN_VARIANT"): "Vehmodel '{before}' (key {normalized}) is not in VehicleModelsDico and VIN {vin} has no unique model. {llm}",
    ("R7", "VIN_MULTI_MODEL"): "VIN {vin} has {n} models in this Run ({models}); a vehicle has exactly one model.",
    ("R7", "SHARED_OP_ID"): "diagnosticFieId '{before}' is carried by operations with different identities in this Run.",
    ("R7", "LEGACY_B_CONFLICT"): "Vehmodel decided as '{decided}' but Legacy B holds '{legacy_b}' for VIN {vin}.",
    ("R8", "UNREADABLE"): "{field} '{before}' is empty or unreadable; a human decision is required (R8).",
    ("R8", "COPIED"): "{field} '{before}' copied unchanged.",
    ("R8", "DATE_PARSED"): "Creation_date '{before}' converted to {after}.",
    ("R8", "GENERATED_ID"): "diagnosticFieId empty; id {after} generated as UUID v5 over the Operation_Identity.",
    ("R8", "UNKNOWN_VARIANT"): "Creation_date '{before}' is not a valid dd/mm/yyyy hh:mm date-time. {llm}",
    ("FC1", "FAILED"): "Final check FC1 failed: VIN '{value}' does not match ^[0-9A-Z]{{20}}$.",
    ("FC2", "FAILED"): "Final check FC2 failed: DiagToolName '{value}' is not in ToolDico.",
    ("FC3", "FAILED"): "Final check FC3 failed: ValueDescription '{value}' is not in ID0001-ID0005.",
    ("FC4", "FAILED"): "Final check FC4 failed: ValueReference '{value}' does not match ^[0-9A-Z]{{10}}$.",
    ("LOAD", "CONSTRAINT"): "Target DB refused the row: {constraint}.",
    ("LOAD", "LOAD_FAILED"): "Target DB load failed for the row: {constraint}.",
    ("_row", "ROW_OUTCOME"): "Row outcome {outcome} ({rule_id} {reason}); {n} field decisions ({counts}).{checks}",
    (ANY, "HUMAN_VALUE"): "Human decision on {pattern_key}: apply_value '{after}'.",
    (ANY, "HUMAN_REJECT"): "Human decision on {pattern_key}: reject_group.",
    (ANY, "HUMAN_GENERATE"): "Human decision on {pattern_key}: generate_op_id '{after}'.",
    (ANY, "LLM_VALUE"): "{justification}",
    (ANY, "LLM_REJECT"): "{justification}",
    (ANY, "CACHE_VALUE"): "{justification}",
    (ANY, "CACHE_REJECT"): "{justification}",
}

REASONS: dict[str, tuple[str, ...]] = {
    "INGEST": ("MALFORMED_ROW",),
    "R1": ("OK", "NORMALIZED", "INVALID_VIN"),
    "R2": ("OK", "MAPPED", "UNKNOWN_VARIANT"),
    "R3": ("FORMAT1", "DID_MAPPED", "OUT_OF_SCOPE", "FORMAT2_NO_DID", "DEDUCED_DID", "DEDUCED_VIN_LABEL",
           "UNREADABLE", "UNKNOWN_VARIANT"),
    "R4": ("OK", "NORMALIZED", "VIN_AS_VALUE", "EMPTY", "NOM", "EXCEL_SCI", "FORBIDDEN_CHAR", "WRONG_LENGTH"),
    "R5": ("OK", "DICT_NORMALIZED", "LABEL_ECU", "UNREADABLE", "UNKNOWN_VARIANT"),
    "R6": ("OK", "DICT_NORMALIZED", "VIN_SIBLING", "LEGACY_B", "UNREADABLE", "UNKNOWN_VARIANT"),
    "R7": ("VIN_MULTI_MODEL", "SHARED_OP_ID", "LEGACY_B_CONFLICT"),
    "R8": ("UNREADABLE", "COPIED", "DATE_PARSED", "GENERATED_ID", "UNKNOWN_VARIANT"),
    "FC1": ("FAILED",), "FC2": ("FAILED",), "FC3": ("FAILED",), "FC4": ("FAILED",),
    "LOAD": ("CONSTRAINT", "LOAD_FAILED"),
    "_row": ("ROW_OUTCOME",),
    ANY: ("HUMAN_VALUE", "HUMAN_REJECT", "HUMAN_GENERATE", "LLM_VALUE", "LLM_REJECT", "CACHE_VALUE",
          "CACHE_REJECT"),
}


class _Params(dict):
    def __missing__(self, key: str) -> str:
        return ""


def render(rule_id: str, reason: str, /, **params: object) -> str:
    template = TEMPLATES.get((rule_id, reason)) or TEMPLATES[(ANY, reason)]
    text = template.format_map(_Params({k: "" if v is None else v for k, v in params.items()}))
    return " ".join(text.split())


def template_for(rule_id: str, reason: str) -> str:
    return TEMPLATES.get((rule_id, reason)) or TEMPLATES[(ANY, reason)]


def all_pairs() -> list[tuple[str, str]]:
    return [(r, reason) for r, reasons in REASONS.items() for reason in reasons]


def human_explanation(pattern_key: str, option: str, value: str | None = None) -> str:
    return f"Human decision on {pattern_key}: {option}" + (f" '{value}'" if value else "") + "."


def llm_note(params: Mapping[str, object]) -> str:  # pragma: no cover - convenience
    return str(params.get("llm", ""))
