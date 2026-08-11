"""The .doc -> .docx conversion port.

The pipeline depends only on this protocol, never on a concrete converter, so
the deployment decision (LibreOffice in the accreditation boundary, an
out-of-boundary service, or no conversion at all) is a configuration choice
rather than a code change. See docs/superpowers/specs/2026-08-07-doc-conversion-design.md.
"""

from __future__ import annotations

from typing import Protocol


class ConversionError(Exception):
    """Anything that went wrong at the conversion stage.

    Callers attribute the failing pipeline stage by catching this base, so a
    backend's transport and storage errors must not escape as their own types.
    """


class ConversionUnavailable(ConversionError):
    """No conversion backend is enabled in this environment.

    Distinct from ConversionFailed: the input may be perfectly valid. This is a
    deployment state, so it is reported to the user at upload time rather than
    as a failed parse job.
    """


class ConversionFailed(ConversionError):
    """The backend was reached but could not produce a usable .docx.

    Covers bad input, timeout, and output that breaches a size ceiling.
    """


class DocConverter(Protocol):
    def convert(self, data: bytes) -> bytes:
        """Convert legacy binary .doc bytes to .docx bytes.

        Raises ConversionUnavailable or ConversionFailed. A backend must wrap
        every internal error — transport, storage, size ceiling — in one of the
        two, so the caller can attribute the failure to the conversion stage.
        """
        ...
