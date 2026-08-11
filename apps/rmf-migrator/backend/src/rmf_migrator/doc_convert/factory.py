"""Backend selection. One config value decides; nothing else in the pipeline
knows which converter is in play."""

from __future__ import annotations

from typing import Any

from .base import DocConverter
from .lambda_backend import LibreOfficeLambdaConverter
from .rejecting import RejectingConverter


def build_converter(config: Any, *, store: Any) -> DocConverter:
    backend = config.doc_conversion_backend
    if backend == "reject":
        return RejectingConverter()
    if backend == "lambda":
        if not config.doc_converter_function_name:
            raise ValueError("DOC_CONVERSION_BACKEND=lambda requires DOC_CONVERTER_FUNCTION_NAME")
        return LibreOfficeLambdaConverter(
            function_name=config.doc_converter_function_name,
            bucket=config.documents_bucket,
            store=store,
        )
    raise ValueError(f"unknown DOC_CONVERSION_BACKEND: {backend!r}")
