"""Tests for the chat service + handler."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.bedrock import BedrockError
from rmf_migrator.common.http import HttpError
from rmf_migrator.common.models import Document, DocumentStatus, Draft, Project, Section
from rmf_migrator.handlers.chat import _chat
from rmf_migrator.services.chat import ChatError, reply, validate_messages


class FakeChatBedrock:
    def __init__(self, text="Here is a suggestion.", *, error=False):
        self._text = text
        self._error = error
        self.last_system: str | None = None
        self.last_messages: list[dict[str, str]] | None = None

    def converse_messages(self, *, system, messages, max_tokens=2048, temperature=0.0):
        self.last_system = system
        self.last_messages = messages
        if self._error:
            raise BedrockError("down")
        return self._text


def _section():
    return Section(
        section_id="sec_1",
        document_id="doc_1",
        project_id="proj_1",
        order=0,
        level=1,
        heading="Account Management",
        text="Accounts are managed.",
    )


# ---- validate_messages -----------------------------------------------------


def test_validate_rejects_empty():
    with pytest.raises(ChatError):
        validate_messages([])


def test_validate_rejects_non_list():
    with pytest.raises(ChatError):
        validate_messages("hi")


def test_validate_rejects_bad_role():
    with pytest.raises(ChatError):
        validate_messages([{"role": "system", "content": "x"}])


def test_validate_requires_last_user():
    with pytest.raises(ChatError):
        validate_messages(
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        )


def test_validate_ok():
    msgs = validate_messages([{"role": "user", "content": "help me with AC-2"}])
    assert msgs == [{"role": "user", "content": "help me with AC-2"}]


def test_validate_truncates_history():
    many = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    # Force last to be user already (i even/odd irrelevant; all 'user').
    assert len(validate_messages(many)) == 20


# ---- reply -----------------------------------------------------------------


def test_reply_builds_context_and_returns_text():
    fake = FakeChatBedrock("Try this wording.")
    draft = Draft(
        project_id="proj_1",
        document_id="doc_1",
        section_id="sec_1",
        order=0,
        rev5_control_ids=["AC-2"],
        draft_text="Current draft.",
    )
    out = reply(
        [{"role": "user", "content": "improve this"}],
        section=_section(),
        draft=draft,
        bedrock=fake,
    )
    assert out == "Try this wording."
    assert "untrusted" in fake.last_system
    assert "AC-2" in fake.last_system


# ---- handler ---------------------------------------------------------------


def _seed(deps):
    project = Project(name="S")
    deps.repo.put_project(project)
    pid = project.project_id
    document = Document(project_id=pid, filename="a.docx", s3_key="k")
    document.status = DocumentStatus.DRAFTED
    deps.repo.put_document(document)
    did = document.document_id
    section = Section(
        section_id="sec_1",
        document_id=did,
        project_id=pid,
        order=0,
        level=1,
        heading="AC",
        text="x",
    )
    deps.repo.put_sections([section])
    return pid, did, section.section_id


def _event(body, path):
    return {"body": json.dumps(body), "pathParameters": path, "headers": {}}


def test_chat_handler_returns_reply(deps):
    deps.bedrock = FakeChatBedrock("Proposed text.")
    pid, did, sid = _seed(deps)
    resp = _chat(
        _event(
            {"messages": [{"role": "user", "content": "help"}]},
            {"project_id": pid, "document_id": did, "section_id": sid},
        ),
        deps,
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["reply"] == "Proposed text."


def test_chat_handler_404_missing_section(deps):
    deps.bedrock = FakeChatBedrock()
    pid, did, _ = _seed(deps)
    with pytest.raises(HttpError) as exc:
        _chat(
            _event(
                {"messages": [{"role": "user", "content": "hi"}]},
                {"project_id": pid, "document_id": did, "section_id": "sec_missing"},
            ),
            deps,
        )
    assert exc.value.status == 404


def test_chat_handler_400_bad_messages(deps):
    deps.bedrock = FakeChatBedrock()
    pid, did, sid = _seed(deps)
    with pytest.raises(HttpError) as exc:
        _chat(
            _event({"messages": []}, {"project_id": pid, "document_id": did, "section_id": sid}),
            deps,
        )
    assert exc.value.status == 400


def test_chat_handler_502_on_bedrock_error(deps):
    deps.bedrock = FakeChatBedrock(error=True)
    pid, did, sid = _seed(deps)
    with pytest.raises(HttpError) as exc:
        _chat(
            _event(
                {"messages": [{"role": "user", "content": "hi"}]},
                {"project_id": pid, "document_id": did, "section_id": sid},
            ),
            deps,
        )
    assert exc.value.status == 502
