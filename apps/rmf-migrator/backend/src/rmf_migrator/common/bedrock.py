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
"""

from __future__ import annotations

import json
import re
from typing import Any

from .config import Config


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

            self._client = boto3.client("bedrock-runtime", region_name=region)

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
        temperature: float = 0.0,
    ) -> str:
        """One request/response turn; returns the assistant's text."""
        params: dict[str, Any] = {
            "modelId": self._model_id,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
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
            raise BedrockError(f"bedrock converse failed: {type(exc).__name__}") from exc

        return _first_text(response)

    def converse_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2048,
    ) -> Any:
        """Like ``converse`` but parses the response into a JSON object."""
        text = self.converse(system=system, user=user, max_tokens=max_tokens, temperature=0.0)
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
