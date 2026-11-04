"""Common HTTP error body shared by every service (design "Frozen contracts" 1). Pure pydantic."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

ErrorCode = Literal["NOT_FOUND", "BACKEND_UNAVAILABLE", "VALIDATION_ERROR", "CONFLICT",
                    "UNKNOWN_RUN", "REFUSED", "INTERNAL"]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def error_payload(code: ErrorCode, message: str, details: Any = None) -> dict:
    return ErrorResponse(error=ErrorBody(code=code, message=message, details=details)).model_dump(mode="json")
