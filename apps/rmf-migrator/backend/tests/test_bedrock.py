"""Bedrock wrapper tests with a fake underlying client (no AWS)."""

from __future__ import annotations

import pytest

from rmf_migrator.common.bedrock import BedrockClient, ModelOutputError


class FakeConverseClient:
    """Records the last converse() params and returns a canned text block."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_params: dict | None = None

    def converse(self, **params):
        self.last_params = params
        return {"output": {"message": {"content": [{"text": self._text}]}}}


def test_converse_returns_text_and_passes_model_id():
    fake = FakeConverseClient("hello")
    client = BedrockClient("my.model.id", client=fake)
    assert client.converse(system="s", user="u") == "hello"
    assert fake.last_params["modelId"] == "my.model.id"
    assert fake.last_params["messages"][0]["content"][0]["text"] == "u"
    assert "guardrailConfig" not in fake.last_params


def test_guardrail_config_included_when_set():
    fake = FakeConverseClient("ok")
    client = BedrockClient("m", client=fake, guardrail_id="gr-123", guardrail_version="2")
    client.converse(system="s", user="u")
    gc = fake.last_params["guardrailConfig"]
    assert gc["guardrailIdentifier"] == "gr-123"
    assert gc["guardrailVersion"] == "2"


def test_converse_json_parses_raw_json():
    fake = FakeConverseClient('{"a": 1, "b": ["x"]}')
    client = BedrockClient("m", client=fake)
    assert client.converse_json(system="s", user="u") == {"a": 1, "b": ["x"]}


def test_converse_json_strips_code_fence():
    fake = FakeConverseClient('```json\n{"a": 1}\n```')
    client = BedrockClient("m", client=fake)
    assert client.converse_json(system="s", user="u") == {"a": 1}


def test_converse_json_extracts_from_surrounding_prose():
    fake = FakeConverseClient('Here is the mapping:\n{"a": 2}\nHope that helps!')
    client = BedrockClient("m", client=fake)
    assert client.converse_json(system="s", user="u") == {"a": 2}


def test_converse_json_raises_on_non_json():
    fake = FakeConverseClient("I could not comply.")
    client = BedrockClient("m", client=fake)
    with pytest.raises(ModelOutputError):
        client.converse_json(system="s", user="u")


def test_zero_temperature_default():
    fake = FakeConverseClient("ok")
    BedrockClient("m", client=fake).converse(system="s", user="u")
    assert fake.last_params["inferenceConfig"]["temperature"] == 0.0
