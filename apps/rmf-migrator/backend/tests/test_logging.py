"""The CUI-safe logger must refuse to emit anything that looks like content."""

from __future__ import annotations

import json

import pytest

from rmf_migrator.common.logging import (
    ContentInLogError,
    length_of,
    log_error,
    log_event,
)


def test_short_metadata_is_logged(capsys):
    log_event("t.event", project_id="proj_1", section_count=5)
    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["event"] == "t.event"
    assert payload["project_id"] == "proj_1"
    assert payload["section_count"] == 5


def test_long_string_is_rejected():
    with pytest.raises(ContentInLogError):
        log_event("t.event", body="x" * 500)


def test_long_string_nested_in_dict_is_rejected():
    with pytest.raises(ContentInLogError):
        log_event("t.event", meta={"draft": "y" * 400})


def test_long_string_in_list_is_rejected():
    with pytest.raises(ContentInLogError):
        log_event("t.event", items=["ok", "z" * 300])


def test_length_of_reports_size_not_text():
    assert length_of("hello") == 5
    assert length_of(None) == 0


def test_log_error_omits_message_by_default(capsys):
    log_error("t.failed", ValueError("secret policy text that should not leak"))
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["error_type"] == "ValueError"
    assert "error_message" not in payload


def test_log_error_includes_message_only_when_opted_in(capsys):
    log_error("t.failed", ValueError("safe"), include_message=True)
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["error_message"] == "safe"
