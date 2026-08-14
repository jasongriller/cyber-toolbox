"""Legacy .doc conversion seam."""

from .base import (
    ConversionError,
    ConversionFailed,
    ConversionUnavailable,
    DocConverter,
)
from .factory import build_converter
from .lambda_backend import LibreOfficeLambdaConverter
from .rejecting import CONVERSION_DISABLED_MESSAGE, RejectingConverter

__all__ = [
    "CONVERSION_DISABLED_MESSAGE",
    "ConversionError",
    "ConversionFailed",
    "ConversionUnavailable",
    "DocConverter",
    "LibreOfficeLambdaConverter",
    "RejectingConverter",
    "build_converter",
]
