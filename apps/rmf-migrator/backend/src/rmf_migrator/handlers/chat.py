"""POST .../documents/{document_id}/sections/{section_id}/chat — per-section chat.

Request body: {"messages": [{"role": "user"|"assistant", "content": "..."}]}.
Returns {"reply": "..."}. Stateless: the client sends the running history each
turn; nothing is persisted here.
"""

from __future__ import annotations

from typing import Any

from rmf_migrator.common.bedrock import BedrockError
from rmf_migrator.common.http import (
    HttpError,
    error_response,
    json_response,
    parse_body,
    path_param,
)
from rmf_migrator.common.logging import log_event
from rmf_migrator.handlers.deps import Deps
from rmf_migrator.services.chat import ChatError, reply, validate_messages


def _chat(event: dict[str, Any], deps: Deps) -> dict[str, Any]:
    project_id = path_param(event, "project_id")
    document_id = path_param(event, "document_id")
    section_id = path_param(event, "section_id")

    section = None
    for s in deps.repo.list_sections(document_id):
        if s.section_id == section_id:
            section = s
            break
    if section is None:
        raise HttpError(404, "section not found")

    body = parse_body(event)
    try:
        messages = validate_messages(body.get("messages"))
    except ChatError as exc:
        raise HttpError(400, str(exc)) from exc

    draft = deps.repo.get_draft(document_id, section_id)

    try:
        answer = reply(messages, section=section, draft=draft, bedrock=deps.bedrock)
    except BedrockError as exc:
        raise HttpError(502, "assistant is unavailable") from exc

    log_event(
        "chat.turn",
        project_id=project_id,
        document_id=document_id,
        section_id=section_id,
        turn_count=len(messages),
    )
    return json_response(200, {"reply": answer})


def handler(event: dict[str, Any], _context: Any = None) -> dict[str, Any]:
    try:
        return _chat(event, Deps.build())
    except HttpError as err:
        return error_response(err)
