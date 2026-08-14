"""Runtime configuration, sourced entirely from environment variables.

Every value the tool needs at runtime is injected by Terraform as a Lambda
environment variable. Nothing here is hardcoded to a region, model, or account
so the same artifact runs in commercial AWS and GovCloud unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"required environment variable {name} is not set")
    return value


# Every backend build_converter can construct, mapped to whether it actually
# converts. One table serves both jobs: membership is the typo check (so a bad
# deployment variable fails here as a ConfigError, not later as a ValueError out
# of Deps.build()), and the value is the .doc upload gate.
#
# A mapping rather than a derived set because the gate decides whether CUI bytes
# enter the documents bucket. Adding a backend forces an explicit answer here;
# subtracting a set would let a future non-converting mode — dry-run, audit, an
# aliased reject — inherit "converts" by omission and report the gate as open.
_BACKENDS: dict[str, bool] = {
    "reject": False,
    "lambda": True,
}


def _backend(name: str) -> str:
    value = (os.environ.get(name) or "reject").strip().lower()
    if value not in _BACKENDS:
        raise ConfigError(f"environment variable {name} has unknown value {value!r}")
    return value


@dataclass(frozen=True)
class Config:
    # Storage
    documents_bucket: str
    table_name: str
    kms_key_id: str

    # Async jobs
    parse_queue_url: str

    # Bedrock — model id is always configuration, never hardcoded
    bedrock_model_id: str
    bedrock_region: str

    # Optional trusted-header identity, injected by an upstream portal/proxy.
    # When unset, actions are attributed to "anonymous".
    identity_header: str | None

    # Guardrails are optional; absent in regions where unavailable.
    bedrock_guardrail_id: str | None
    bedrock_guardrail_version: str | None

    # Legacy .doc conversion. "reject" (default) keeps .doc uploads out; "lambda"
    # routes them through the LibreOffice converter function. Gated on the ISSO
    # decision recorded in the design spec.
    doc_conversion_backend: str = "reject"
    doc_converter_function_name: str | None = None

    @property
    def conversion_enabled(self) -> bool:
        """Whether .doc uploads are admitted. Defaults closed for a backend the
        table does not declare: from_env rejects unknown names, but a Config
        constructed directly bypasses that check."""
        return _BACKENDS.get(self.doc_conversion_backend, False)

    @staticmethod
    def from_env() -> Config:
        return Config(
            documents_bucket=_require("DOCUMENTS_BUCKET"),
            table_name=_require("TABLE_NAME"),
            kms_key_id=_require("KMS_KEY_ID"),
            parse_queue_url=_require("PARSE_QUEUE_URL"),
            bedrock_model_id=_require("BEDROCK_MODEL_ID"),
            bedrock_region=os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "")),
            identity_header=os.environ.get("IDENTITY_HEADER") or None,
            bedrock_guardrail_id=os.environ.get("BEDROCK_GUARDRAIL_ID") or None,
            bedrock_guardrail_version=os.environ.get("BEDROCK_GUARDRAIL_VERSION") or None,
            doc_conversion_backend=_backend("DOC_CONVERSION_BACKEND"),
            doc_converter_function_name=os.environ.get("DOC_CONVERTER_FUNCTION_NAME") or None,
        )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Cached per warm Lambda container."""
    return Config.from_env()
