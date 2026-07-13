"""Helpers for API Gateway (HTTP API / proxy) Lambda handlers.

Keeps handlers free of boilerplate for parsing bodies, building responses, and
resolving identity. Identity is optional and comes only from a configured
trusted header injected by an upstream portal/proxy — there is no app-level auth.
"""

from __future__ import annotations

import json
from typing import Any

_JSON_HEADERS = {"Content-Type": "application/json"}


class HttpError(Exception):
    """Raise inside a handler to return a specific status/message to the client."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def json_response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": _JSON_HEADERS,
        "body": json.dumps(body, default=str),
    }


def error_response(err: HttpError) -> dict[str, Any]:
    return json_response(err.status, {"error": err.message})


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HttpError(400, "request body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HttpError(400, "request body must be a JSON object")
    return parsed


def path_param(event: dict[str, Any], name: str) -> str:
    value = (event.get("pathParameters") or {}).get(name)
    if not value:
        raise HttpError(400, f"missing path parameter: {name}")
    return value


def resolve_identity(event: dict[str, Any], identity_header: str | None) -> str:
    """Return the caller identity from the configured trusted header, or
    'anonymous' when no header is configured or present.

    Header lookup is case-insensitive, matching API Gateway's behavior.
    """
    if not identity_header:
        return "anonymous"
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get(identity_header.lower()) or "anonymous"
