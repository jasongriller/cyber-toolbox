"""Default backend: no conversion available.

Deployed behavior matches the pre-conversion tool exactly, except the message
distinguishes "this environment has conversion switched off" from "your file is
broken" — the user's next action differs.
"""

from __future__ import annotations

from .base import ConversionUnavailable

# The permanent user-facing answer. Upload registration rejects .doc with this
# same text, so it lives here once rather than in each place that says it.
#
# It must not prescribe opening *this* file locally. request_upload never sees
# bytes, so the same sentence answers a file nobody has examined and a file the
# upload screen already classified as a Word 97-2003 container — the shape a
# hostile document takes. Assume the worse case: name a remedy that works on the
# author's copy instead.
CONVERSION_DISABLED_MESSAGE = (
    "legacy .doc conversion is not enabled in this environment — ask the "
    "document's author to re-save a .docx from their own copy, or ask your "
    "operator whether conversion should be enabled here"
)


class RejectingConverter:
    def convert(self, data: bytes) -> bytes:
        raise ConversionUnavailable(CONVERSION_DISABLED_MESSAGE)
