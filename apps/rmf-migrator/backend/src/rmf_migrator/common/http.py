"""Helpers for API Gateway (HTTP API / proxy) Lambda handlers.

Keeps handlers free of boilerplate for parsing bodies, building responses, and
resolving identity. Identity prefers cryptographically verified sources (a
Cognito JWT authorizer's claims, then a SigV4 principal); a configured trusted
header injected by an upstream portal/proxy is only a last-resort fallback for
deployments with no authorizer in front of them.
"""

from __future__ import annotations

import base64
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
    # A gateway in front of this API may deliver the body base64-encoded with
    # isBase64Encoded set (the toolbox front door's REST API does this for all
    # bodies because its S3-serving mode needs binary_media_types = ["*/*"]).
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(400, "request body is not valid JSON") from exc
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
    """Return the caller's identity, trusting verified sources before headers.

    Checked in order:
      1. JWT claims from an HTTP API JWT (Cognito) authorizer — email, then
         cognito:username, then sub. Cryptographically verified.
      2. The SigV4 principal from an IAM authorizer. Also verified.
      3. A configured trusted header, injected by an upstream portal/proxy.
         Client-supplied and spoofable, so it is only a last resort kept for
         deployments with no authorizer in front of them.
      4. "anonymous".

    Header lookup is case-insensitive, matching API Gateway's behavior.
    """
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}

    claims = authorizer.get("jwt") or {}
    claims = claims.get("claims") or {}
    identity = claims.get("email") or claims.get("cognito:username") or claims.get("sub")
    if identity:
        return identity

    iam = authorizer.get("iam") or {}
    identity = iam.get("userArn") or iam.get("callerId")
    if identity:
        return identity

    if identity_header:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if identity := headers.get(identity_header.lower()):
            return identity

    return "anonymous"


def resolve_groups(event: dict[str, Any]) -> set[str]:
    """Group memberships from the verified JWT's cognito:groups claim.

    Only the JWT authorizer path is trusted — there is no header fallback,
    because group names drive authorization decisions and a client-supplied
    header could mint itself into any group. HTTP API JWT authorizers flatten
    the claim to a single string, historically in two shapes: a JSON array
    ('["admins","users"]') or bracket-space form ('[admins users]'); a plain
    list also appears in tests and future-proofs a fixed serialization.
    """
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") or {}
    raw = claims.get("cognito:groups")
    if raw is None:
        return set()
    if isinstance(raw, list):
        return {str(group) for group in raw}
    text = str(raw).strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return {str(group) for group in parsed}
        except ValueError:
            pass
        inner = text[1:-1].replace(",", " ")
        return {token.strip().strip('"') for token in inner.split() if token.strip()}
    return {text} if text else set()
