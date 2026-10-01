"""Thin wrapper over Amazon Bedrock's Converse API.

Design goals:
* **No hardcoded model.** The model id is passed in (ultimately from config).
* **Testable.** The underlying boto3 client is injectable; tests pass a fake and
  never touch AWS.
* **CUI-safe.** This wrapper never logs prompts or responses. Callers must not
  either (see common/logging.py).
* **Guardrails optional.** Applied when configured; absent where unavailable
  (e.g. some GovCloud regions), where prompt hardening is the fallback.
* **Structured output.** ``converse_json`` extracts a JSON object from the model
  response, tolerating code fences and surrounding prose.
* **Portable inference params.** Temperature is floored at ``MIN_TEMPERATURE``
  so a deterministic request stays valid on every candidate model.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import Config

# The lowest temperature every candidate model accepts. Amazon Nova's valid
# range is 0.00001-1.0 inclusive and it rejects a literal 0.0; Anthropic and the
# OpenAI OSS models on Bedrock accept 0.0-1.0. Picking the floor of the stricter
# range keeps one value valid everywhere, which is the point — the model id is
# pure configuration, so a temperature that only some models accept would make
# swapping models a code change.
#
# Not meaningfully different from 0.0: at 1e-5 the sampler is greedy for any
# realistic logit spread, so mapping and drafting stay reproducible.
MIN_TEMPERATURE = 0.00001


class BedrockError(RuntimeError):
    pass


class ModelOutputError(BedrockError):
    """The model returned something that could not be parsed as expected JSON."""


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model response.

    Handles: raw JSON, ```json fenced blocks, and JSON with leading/trailing prose.
    """
    stripped = text.strip()
    # Strip a leading ```json / ``` fence and trailing fence if present.
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK_RE.search(stripped)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ModelOutputError("model response was not valid JSON") from exc
    raise ModelOutputError("no JSON object found in model response")


# Cap on a BedrockError message. common/logging.py treats any logged string over
# 200 chars as suspected content and truncates it, so staying just under that
# keeps the cause from being the half that gets cut.
_MAX_ERROR_CHARS = 199


def _describe_failure(exc: Exception) -> str:
    """Name a Bedrock failure precisely enough for someone else to fix it.

    These tools run in other people's AWS accounts, so this string is usually
    the only diagnostic anyone gets. Reporting ``type(exc).__name__`` alone made
    the two most common misconfigurations — an IAM policy that doesn't cover the
    model, and a model id that needs an inference profile — both surface as the
    single word "ClientError".

    Uses the AWS error code and service message, never the prompt or response:
    Bedrock's messages describe the request's authorization and shape, so they
    carry no document content, which is what keeps this wrapper's CUI promise.
    """
    code = type(exc).__name__
    message = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = str(error.get("Code") or "").strip() or code
            message = str(error.get("Message") or "").strip()
    detail = f"{code}: {message}" if message else code
    if len(detail) > _MAX_ERROR_CHARS:
        detail = detail[: _MAX_ERROR_CHARS - 3].rstrip() + "..."
    return detail


class BedrockClient:
    def __init__(
        self,
        model_id: str,
        *,
        region: str | None = None,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        client: Any = None,
    ) -> None:
        self._model_id = model_id
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version
        if client is not None:
            self._client = client
        else:
            import boto3

            from rmf_migrator.common.aws_clients import BEDROCK_CONFIG

            self._client = boto3.client(
                "bedrock-runtime", region_name=region, config=BEDROCK_CONFIG
            )

    @classmethod
    def from_config(cls, config: Config, *, client: Any = None) -> BedrockClient:
        return cls(
            config.bedrock_model_id,
            region=config.bedrock_region or None,
            guardrail_id=config.bedrock_guardrail_id,
            guardrail_version=config.bedrock_guardrail_version,
            client=client,
        )

    def converse(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = MIN_TEMPERATURE,
    ) -> str:
        """One request/response turn; returns the assistant's text."""
        return self.converse_messages(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def converse_messages(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = MIN_TEMPERATURE,
    ) -> str:
        """Multi-turn conversation; ``messages`` is [{role, content}].

        Floors ``temperature`` at ``MIN_TEMPERATURE``. Every path into Bedrock
        funnels through here, so callers spelling determinism as 0.0 are made
        valid in one place rather than each having to know the model's range.
        """
        params: dict[str, Any] = {
            "modelId": self._model_id,
            "system": [{"text": system}],
            "messages": [
                {"role": m["role"], "content": [{"text": m["content"]}]} for m in messages
            ],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": max(temperature, MIN_TEMPERATURE),
            },
        }
        if self._guardrail_id:
            params["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
                "trace": "disabled",
            }

        try:
            response = self._client.converse(**params)
        except Exception as exc:  # noqa: BLE001 — normalize client errors
            raise BedrockError(_describe_failure(exc)) from exc

        return _first_text(response)

    def converse_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
    ) -> Any:
        """Like ``converse`` but parses the response into a JSON object."""
        text = self.converse(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=MIN_TEMPERATURE,
        )
        return _extract_json(text)


def _first_text(response: dict[str, Any]) -> str:
    try:
        blocks = response["output"]["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise ModelOutputError("unexpected Bedrock response shape") from exc
    for block in blocks:
        if "text" in block:
            return block["text"]
    raise ModelOutputError("Bedrock response contained no text block")
