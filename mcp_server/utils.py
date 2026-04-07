"""
Shared utilities for JSON serialization and common helpers.
"""

import json
import uuid
import datetime
import decimal
import logging
from typing import Any


logger = logging.getLogger(__name__)


class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles asyncpg/neo4j types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, datetime.timedelta):
            return str(obj)
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, set):
            return sorted(list(obj))
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


def to_json(obj: Any, pretty: bool = False) -> str:
    """Serialize object to JSON string with safe type handling."""
    return json.dumps(
        obj,
        cls=SafeJSONEncoder,
        indent=2 if pretty else None,
        ensure_ascii=False,
    )


def serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a database row (asyncpg Record as dict) to JSON-safe types."""
    result = {}
    for key, value in row.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif isinstance(value, (datetime.date, datetime.datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            result[key] = float(value)
        elif isinstance(value, list):
            result[key] = [
                str(v) if isinstance(v, uuid.UUID) else v for v in value
            ]
        else:
            result[key] = value
    return result


def make_tool_response(
    status: str,
    data: Any = None,
    metadata: dict | None = None,
    error: str | None = None,
    code: str | None = None,
) -> str:
    """Create a standardized tool response JSON string."""
    response: dict[str, Any] = {"status": status}
    if data is not None:
        response["data"] = data
    if metadata:
        response["metadata"] = metadata
    if error:
        response["error"] = error
    if code:
        response["code"] = code
    return to_json(response)


def success_response(data: Any, metadata: dict | None = None) -> str:
    return make_tool_response("success", data=data, metadata=metadata)


def error_response(message: str, code: str = "ERROR") -> str:
    return make_tool_response("error", error=message, code=code)