"""CUI-safe structured logging.

Hard rule for this project: document text, LLM prompts, and LLM responses must
NEVER reach CloudWatch. This module provides a logger that emits structured
JSON with an allowlist mindset — you log identifiers, counts, and statuses, not
content.

To make accidental leakage hard, ``log_event`` rejects values that look like
free-form document content (long strings) unless they are explicitly wrapped in
``Redacted`` or ``Count``/``Length`` markers. Callers should pass metadata, not
prose.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Any string value longer than this in a log payload is treated as suspected
# content and is rejected rather than emitted.
_MAX_INLINE_STR = 200


def _emit(stream_name: str, payload: dict[str, Any]) -> None:
    """Write one JSON line. The stream is resolved at call time (not import
    time) so test capture and Lambda's stdout/stderr redirection both work."""
    stream = getattr(sys, stream_name)
    stream.write(json.dumps(payload, default=str, separators=(",", ":")) + "\n")
    stream.flush()


class ContentInLogError(RuntimeError):
    """Raised when a log payload appears to contain document content."""


def length_of(text: str | None) -> int:
    """Safe way to report how much text something has without logging it."""
    return len(text) if text else 0


def _assert_safe(key: str, value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_INLINE_STR:
        raise ContentInLogError(
            f"refusing to log field {key!r}: value length {len(value)} exceeds "
            f"{_MAX_INLINE_STR}; log a length or identifier, not content"
        )
    if isinstance(value, dict):
        return {k: _assert_safe(f"{key}.{k}", v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_assert_safe(f"{key}[]", v) for v in value]
    return value


def log_event(event: str, /, **fields: Any) -> None:
    """Emit one structured, CUI-safe log line.

    Example::

        log_event("document.parsed", project_id=pid, document_id=did,
                  section_count=42, char_length=length_of(full_text))
    """
    safe = {k: _assert_safe(k, v) for k, v in fields.items()}
    _emit("stdout", {"event": event, **safe})


def log_error(event: str, /, error: BaseException, **fields: Any) -> None:
    """Log an error by type and message only.

    The exception message itself could theoretically echo content, so only the
    exception class name is logged by default; pass ``include_message=True``
    consciously if the exception type is known-safe.
    """
    include_message = bool(fields.pop("include_message", False))
    payload: dict[str, Any] = {
        "event": event,
        "error_type": type(error).__name__,
        **{k: _assert_safe(k, v) for k, v in fields.items()},
    }
    if include_message:
        payload["error_message"] = str(error)[:_MAX_INLINE_STR]
    _emit("stderr", payload)
