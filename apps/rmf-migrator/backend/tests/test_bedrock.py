"""Bedrock wrapper tests with a fake underlying client (no AWS)."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from rmf_migrator.common.bedrock import (
    MIN_TEMPERATURE,
    BedrockClient,
    BedrockError,
    ModelOutputError,
)
from rmf_migrator.common.logging import _MAX_INLINE_STR


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


def test_min_temperature_is_within_the_narrowest_documented_range():
    """Amazon Nova accepts 0.00001-1.0 inclusive and rejects 0.0; Anthropic and
    the OpenAI OSS models accept 0.0-1.0. MIN_TEMPERATURE has to sit in the
    intersection or the wrapper stops being model-agnostic."""
    assert 0.00001 <= MIN_TEMPERATURE <= 1.0


def test_default_temperature_is_floored():
    fake = FakeConverseClient("ok")
    BedrockClient("m", client=fake).converse(system="s", user="u")
    assert fake.last_params["inferenceConfig"]["temperature"] == MIN_TEMPERATURE


def test_explicit_zero_temperature_is_floored():
    """0.0 is the obvious spelling of "be deterministic" and several callers use
    it. Floor it rather than let Bedrock reject the request."""
    fake = FakeConverseClient("ok")
    BedrockClient("m", client=fake).converse(system="s", user="u", temperature=0.0)
    assert fake.last_params["inferenceConfig"]["temperature"] == MIN_TEMPERATURE


def test_converse_messages_floors_temperature():
    fake = FakeConverseClient("ok")
    BedrockClient("m", client=fake).converse_messages(
        system="s", messages=[{"role": "user", "content": "u"}], temperature=0.0
    )
    assert fake.last_params["inferenceConfig"]["temperature"] == MIN_TEMPERATURE


def test_converse_json_floors_temperature():
    fake = FakeConverseClient('{"a": 1}')
    BedrockClient("m", client=fake).converse_json(system="s", user="u")
    assert fake.last_params["inferenceConfig"]["temperature"] == MIN_TEMPERATURE


def test_temperature_above_the_floor_is_passed_through():
    fake = FakeConverseClient("ok")
    BedrockClient("m", client=fake).converse(system="s", user="u", temperature=0.7)
    assert fake.last_params["inferenceConfig"]["temperature"] == 0.7


# ---- error legibility ------------------------------------------------------
#
# Nobody who can reproduce a Bedrock failure has access to this repo: these
# tools run in someone else's AWS account. A BedrockError string in CloudWatch
# is the entire diagnostic surface, so it has to name the cause.

# What Bedrock actually returns when a model needs an inference profile and is
# invoked by its bare foundation-model id — the exact wall a Nova Pro rollout
# hits outside us-east-1.
NOVA_PROFILE_ERROR = (
    "Invocation of model ID amazon.nova-pro-v1:0 with on-demand throughput isn't "
    "supported. Retry your request with the ID or ARN of an inference profile that "
    "contains this model."
)


def _client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "Converse")


class RaisingConverseClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def converse(self, **params):
        raise self._exc


def _converse_failure(exc: Exception) -> str:
    client = BedrockClient("m", client=RaisingConverseClient(exc))
    with pytest.raises(BedrockError) as caught:
        client.converse(system="s", user="u")
    return str(caught.value)


def test_error_names_the_aws_error_code():
    detail = _converse_failure(_client_error("AccessDeniedException", "no"))
    assert "AccessDeniedException" in detail


def test_error_carries_the_aws_remediation_text():
    """AWS's own message says how to fix it; dropping it strands the deployer."""
    detail = _converse_failure(_client_error("ValidationException", NOVA_PROFILE_ERROR))
    assert "inference profile" in detail


def test_iam_and_parameter_failures_are_distinguishable():
    """Both are botocore ClientError. Collapsing to the class name made the two
    most likely Nova blockers produce byte-identical errors."""
    denied = _converse_failure(_client_error("AccessDeniedException", "no"))
    invalid = _converse_failure(_client_error("ValidationException", "bad temperature"))
    assert denied != invalid


def test_error_stays_short_enough_to_log_intact():
    """log_error truncates at _MAX_INLINE_STR; stay under it so the cause is not
    the half that gets cut."""
    detail = _converse_failure(_client_error("ValidationException", "x" * 2000))
    assert len(detail) < _MAX_INLINE_STR


def test_error_never_echoes_the_prompt():
    """The wrapper's CUI promise: prompts and responses stay out of the message."""
    client = BedrockClient("m", client=RaisingConverseClient(_client_error("X", "y")))
    with pytest.raises(BedrockError) as caught:
        client.converse(system="SYSTEM-CUI-MARKER", user="USER-CUI-MARKER")
    assert "CUI-MARKER" not in str(caught.value)


def test_error_falls_back_to_the_exception_type():
    """Non-AWS exceptions keep the old behaviour rather than losing all detail."""
    assert "ValueError" in _converse_failure(ValueError("boom"))
