"""
Shared utilities for the Semantic MCP server.
"""

from __future__ import annotations

import json
import uuid
import datetime
import decimal
from typing import Any


class SafeJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, set):
            return sorted(list(obj))
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


def to_json(obj: Any) -> str:
    return json.dumps(obj, cls=SafeJSONEncoder, ensure_ascii=False)


def success_response(data: Any, metadata: dict | None = None) -> str:
    resp: dict[str, Any] = {"status": "success", "data": data}
    if metadata:
        resp["metadata"] = metadata
    return to_json(resp)


def error_response(message: str, code: str = "ERROR") -> str:
    return to_json({"status": "error", "error": message, "code": code})
