"""LibreOffice .doc -> .docx converter, running in its own container image.

This function is the only place untrusted OLE2 bytes are parsed. It gets its
own Lambda and its own minimal role, so a LibreOffice vulnerability cannot
reach the API, the database, or Bedrock.

Isolation is not egress control, and the difference has been measured. A Lambda
outside a VPC has full internet access; LibreOffice uses it. A .doc carrying a
linked graphic made the built image issue OPTIONS/HEAD/GET for the linked URL
during a conversion that then succeeded — a callback from inside the CUI
boundary, an SSRF primitive, and (pointed at a host that never answers) a way
to hold a converter slot open. The image therefore refuses link updates and
remote graphic loading in soffice's own configuration; the evidence for both
the original probe and the refusal is in the plan's Gate 1 section. Denying
egress at the network as well — a VPC with no NAT or internet gateway, plus S3
and KMS endpoints — is Task 12's half, and until it lands the configuration is
the only thing holding.

Guards mirror common/limits.py: bounded input, bounded output, a decompression
ceiling on what soffice produced, and a format check on both ends. Macros never
execute during a headless convert; the vbaProject strip is defense in depth for
the artifact we hand downstream.

Denying LibreOffice the fetch is only half of the linked-graphic problem. The
URL the .doc named survives the conversion as an external OPC relationship, and
the package this function returns is what the pipeline stores at
`converted_s3_key`, parses, and re-exports as the Rev 5 document a reviewer
opens in Word on their own workstation — where the fetch happens on their
endpoint instead of ours, credential material included when the target is a UNC
path. So the same sanitization pass drops external resource relationships;
`_sanitize_output` says which ones and why.
"""

from __future__ import annotations

import copy
import io
import os
import re
import signal
import subprocess
import tempfile
import zipfile
from collections.abc import Callable

from rmf_migrator.common.limits import (
    CONVERT_SCRATCH_PREFIX,
    MAX_DOC_BYTES,
    MAX_DOCX_BYTES,
    DocxTooLarge,
    UnsupportedDocumentFormat,
    guard_docx_bytes,
    sniff_document_format,
)

# Below the function's own timeout, so a hung soffice is killed here and the
# failure is reported rather than truncated by a Lambda hard-kill.
_TIMEOUT_SECONDS = 60

# The only keys this function will read or write. The caller stages both
# objects under `{CONVERT_SCRATCH_PREFIX}{uuid}` (doc_convert/lambda_backend.py)
# and derives them from the same constant, so the two ends cannot drift apart.
# Without this check the event is a write primitive for anything holding
# lambda:InvokeFunction: a target of `projects/{id}/exports/...` would land a
# chosen document over the Rev 5 export a human downloads and signs, under the
# right CMK and content type. Task 12's role policy is the boundary; this is
# the assertion that the boundary was not widened by accident.
_SCRATCH_PREFIX = CONVERT_SCRATCH_PREFIX

# SIGKILL, not SIGTERM: a soffice that has stopped responding is the case this
# path exists for. Resolved through getattr because the developer hosts that
# run the unit suite are Windows, whose signal module has no SIGKILL — the
# image itself is Linux, where this is always signal.SIGKILL.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)

# Spelled out rather than imported from common.storage, which imports boto3 at
# module scope: the guards below stay importable without any AWS dependency,
# and this function's whole point is a small surface.
_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Absolute path, not a PATH lookup: the image (converter.Dockerfile) is the only
# place this runs, and resolving the interpreter of untrusted bytes through a
# mutable PATH is a needless degree of freedom.
_SOFFICE = "/usr/bin/soffice"

# The package part that declares a content type for every other part.
_CONTENT_TYPES = "[Content_Types].xml"

# The Override / Relationship elements naming the VBA part. Optional namespace
# prefixes and the long `<Tag ...></Tag>` form are both accepted, since which
# one appears is the producer's choice, not ours.
_VBA_REFERENCE = re.compile(
    rb"<([A-Za-z0-9_.-]+:)?(Override|Relationship)\b[^>]*vbaProject\.bin[^>]*"
    rb"(?:/>|>.*?</([A-Za-z0-9_.-]+:)?\2>)",
    re.DOTALL,
)

# Any single Relationship element. `\b` keeps it off the `<Relationships>` root.
_RELATIONSHIP = re.compile(
    rb"<([A-Za-z0-9_.-]+:)?Relationship\b[^>]*(?:/>|>.*?</([A-Za-z0-9_.-]+:)?Relationship\s*>)",
    re.DOTALL,
)
# Attribute quoting is the producer's choice, so both forms are accepted.
_EXTERNAL_TARGET_MODE = re.compile(rb"\bTargetMode\s*=\s*[\"']External[\"']")
_HYPERLINK_TYPE = re.compile(rb"\bType\s*=\s*[\"'][^\"']*/hyperlink[\"']")
_TARGET_ATTRIBUTE = re.compile(rb"\bTarget\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
_URL_SCHEME = re.compile(rb"\A([A-Za-z][A-Za-z0-9+.\-]*):")

# Schemes a hyperlink may keep. A hyperlink is the one external relationship a
# human has to choose to follow, and its URL is visible before they do, so
# dropping them all would cost a policy document its citations for no gain. A
# `file:` target is different: a UNC path resolves over SMB, which hands the
# reader's NTLM credentials to whoever named it, with no click beyond the one
# they already meant to make.
_CLICKABLE_SCHEMES = frozenset({b"http", b"https", b"mailto"})


class ConverterError(Exception):
    """Conversion could not produce a safe, in-bounds .docx."""


def _kill_process_group(process: subprocess.Popen, pgid: int) -> None:
    """SIGKILL the whole soffice tree, not just the process we spawned.

    /usr/bin/soffice execs oosplash, which forks soffice.bin beside it, so
    killing the direct child leaves the parser resident — holding the decoded
    document — while this function reports a clean, contained failure. Lambda
    then freezes that orphan with the sandbox and thaws it into the next
    invocation, inside the same memory cgroup.

    The group id is passed in rather than looked up: by the time this runs on a
    normal exit the leader has been reaped, and `os.getpgid` on a reaped pid
    raises — taking the group's surviving members with it into the next
    invocation, which is the case this exists for.
    """
    try:
        os.killpg(pgid, _KILL_SIGNAL)
    except (OSError, AttributeError):
        # OSError: nothing left in the group to signal. AttributeError: the
        # unit suite runs on Windows hosts, whose os module has no killpg —
        # the same reason _KILL_SIGNAL is resolved through getattr. Either way
        # the direct child still has to die.
        process.kill()
    process.wait()


def _run_soffice(data: bytes, workdir: str) -> bytes:
    source = os.path.join(workdir, "input.doc")
    with open(source, "wb") as handle:
        handle.write(data)
    process = subprocess.Popen(  # noqa: S603 — argv list, shell=False, paths are ours
        [
            _SOFFICE,
            "--headless",
            "--norestore",
            # Pin the import filter. Left to itself soffice picks one by
            # content, so an .xls carrying a forged `WordDocument` entry
            # would load through the BIFF parser. The format sniff cannot
            # prevent that — it reads the same attacker-supplied structure.
            # This flag is the containment boundary; the sniff is routing.
            "--infilter=MS Word 97",
            "--convert-to",
            "docx",
            "--outdir",
            workdir,
            source,
        ],
        # Piped, not inherited: soffice writes fragments of the document into
        # its own diagnostics, and inherited handles put those in CloudWatch.
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Its own session, so the reclaim below can kill the tree as a group.
        # subprocess.run's timeout cannot: it kills the direct child and waits
        # on it, leaving soffice.bin behind.
        start_new_session=True,
    )
    # start_new_session=True makes the child its own group leader, so the group
    # id is its pid. Captured now because every path below reclaims the group,
    # and after a normal exit the leader is gone and cannot be asked for it.
    pgid = process.pid
    try:
        try:
            process.communicate(timeout=_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise ConverterError("conversion timed out") from exc
        if process.returncode != 0:
            # stderr can echo document content; report failure, never the text.
            raise ConverterError("conversion process failed")

        produced = os.path.join(workdir, "input.docx")
        if not os.path.exists(produced):
            # How a refused input filter presents: soffice exits 0 and writes
            # nothing. Distinguishing that from a crash is not worth a branch —
            # both mean there is no document to hand on.
            raise ConverterError("conversion produced no output")
        # Measured before it is opened, not after. /tmp gives soffice 512 MB to
        # write into, so an input that drives a large conversion output would
        # otherwise be read into a 2 GB function in full and only then refused
        # — the ceiling enforced after the allocation it exists to prevent.
        if os.path.getsize(produced) > MAX_DOCX_BYTES:
            raise ConverterError(f"output exceeds {MAX_DOCX_BYTES} bytes")
        with open(produced, "rb") as handle:
            return handle.read()
    finally:
        # Every exit path, not just the timeout: a refused --infilter returns
        # exit 0 with no output, and that is the adversarial case — the
        # soffice.bin left behind is the one that just parsed a forged
        # container. Success reclaims too; the group is finished either way.
        _kill_process_group(process, pgid)


def _drop_vba_references(xml: bytes) -> bytes:
    """Remove the OPC elements that point at the VBA part.

    Deleting the part alone leaves a package that names it in two more places:
    an `<Override PartName="/word/vbaProject.bin">` in [Content_Types].xml and
    a `<Relationship Target="vbaProject.bin">` in word/_rels/document.xml.rels.
    python-docx raises KeyError on the dangling reference when it loads the
    package, and that surfaces in the parse worker — pointing the operator at
    the wrong stage for a document this function corrupted.

    Both are single, machine-written elements, so they are cut textually: the
    rest of the part stays byte-identical, where re-serializing through an XML
    writer would rewrite namespace prefixes across a file whose consumers are
    historically particular about them. `[^>]*` cannot cross a tag boundary,
    so the match stays inside the one element.
    """
    return _VBA_REFERENCE.sub(b"", xml)


def _drop_external_links(xml: bytes) -> bytes:
    """Remove relationships that point a consumer at somebody else's server.

    A linked graphic in the source .doc arrives here as
    `<Relationship Type=".../image" Target="http://..." TargetMode="External"/>`
    in word/_rels/document.xml.rels. The image half of the egress controls
    stops *this* process fetching it; the relationship itself rides on into the
    package the pipeline stores, parses, and re-exports, so the fetch simply
    moves to whichever workstation opens the Rev 5 document. Word resolves
    those targets on open, which makes them a beacon, an SSRF probe from the
    reviewer's network, and — for a `file:` UNC target — an NTLM capture.

    Hyperlinks are the deliberate exception; `_CLICKABLE_SCHEMES` says why.
    Internal relationships (Target="styles.xml", the VBA one above) carry no
    TargetMode and are left exactly as they were.

    Textual for the same reason `_drop_vba_references` is, and sound here for
    one more: these .rels are written by soffice from the .doc, not carried
    over from it, so the attribute values are canonical. Entity-escaping
    `TargetMode` to hide it from this pass is not something the input can
    reach; escaping the Target's scheme only makes the match fail, and a
    failed match drops the relationship.
    """

    def keep(match: re.Match[bytes]) -> bytes:
        element = match.group(0)
        if _EXTERNAL_TARGET_MODE.search(element) is None:
            return element
        if _HYPERLINK_TYPE.search(element) is None:
            return b""
        found = _TARGET_ATTRIBUTE.search(element)
        if found is None:
            return b""
        target = found.group(1) if found.group(1) is not None else found.group(2)
        scheme = _URL_SCHEME.match(target)
        if scheme is None or scheme.group(1).lower() not in _CLICKABLE_SCHEMES:
            return b""
        return element

    return _RELATIONSHIP.sub(keep, xml)


def _sanitize_part(xml: bytes) -> bytes:
    """Both textual passes, in the one place the parts they edit are read."""
    return _drop_external_links(_drop_vba_references(xml))


def _sanitize_output(data: bytes) -> bytes:
    """Rewrite the archive without the VBA project or any external resource link.

    A package needing neither edit is returned byte-identical rather than
    repacked: the manifest parts are small, so deciding that costs a few
    kilobytes of decompression, where rebuilding costs the whole archive.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as source:
            names = source.namelist()
            has_vba = any(name.endswith("vbaProject.bin") for name in names)
            edited = {}
            for name in names:
                if name != _CONTENT_TYPES and not name.endswith(".rels"):
                    continue
                payload = source.read(name)
                cleaned = _sanitize_part(payload)
                if cleaned != payload:
                    edited[name] = cleaned
            if not has_vba and not edited:
                return data
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
                for info in source.infolist():
                    if info.filename.endswith("vbaProject.bin"):
                        continue
                    payload = edited.get(info.filename)
                    if payload is None:
                        payload = source.read(info.filename)
                    target.writestr(info, payload)
            return out.getvalue()
    except zipfile.BadZipFile as exc:
        # convert_bytes runs guard_docx_bytes first, which opens the archive and
        # rejects a corrupt one, so this is unreachable through that path. It
        # stays because this function is also callable on its own, and the
        # alternative is a BadZipFile escaping as something other than the one
        # error type the caller handles.
        raise ConverterError("output is not a readable .docx archive") from exc


def convert_bytes(data: bytes, *, run: Callable[[bytes, str], bytes] = _run_soffice) -> bytes:
    if len(data) > MAX_DOC_BYTES:
        raise ConverterError(f"input exceeds {MAX_DOC_BYTES} bytes")
    if sniff_document_format(data) != "doc":
        raise ConverterError("input is not a legacy binary .doc")

    with tempfile.TemporaryDirectory() as workdir:
        converted = run(data, workdir)

    if len(converted) > MAX_DOCX_BYTES:
        raise ConverterError(f"output exceeds {MAX_DOCX_BYTES} bytes")
    if sniff_document_format(converted) != "docx":
        raise ConverterError("output is not a .docx")
    try:
        # The 25 MB ceiling above is compressed bytes; _sanitize_output then
        # decompresses every member into memory to rebuild the archive. This is
        # the ceiling on what that expands to, and it streams rather than
        # trusting the central directory's declared sizes. It has to run on
        # what soffice produced, not on the sanitized result — guarding the
        # output of the allocation this exists to prevent is no guard at all.
        # Translated into this module's one error type so the caller keeps a
        # single contract; the messages are static, so no document content
        # travels with them.
        guard_docx_bytes(converted)
    except (DocxTooLarge, UnsupportedDocumentFormat) as exc:
        raise ConverterError(str(exc)) from exc
    return _sanitize_output(converted)


def handler(event: dict, _context: object = None) -> dict:
    """Read source_key, convert, write target_key. Payload carries keys only.

    A 15 MB .doc exceeds Lambda's synchronous payload ceiling, so the bytes
    travel through the scratch prefix that the caller owns and purges
    (doc_convert/lambda_backend.py). Errors propagate: the caller reads
    FunctionError, and a written target key would be read as a success.
    """
    source_key = event["source_key"]
    target_key = event["target_key"]
    # Checked before anything is read or written, and without echoing the key:
    # it can carry a document name, and this exception reaches the caller.
    if not source_key.startswith(_SCRATCH_PREFIX) or not target_key.startswith(_SCRATCH_PREFIX):
        raise ConverterError("refusing a key outside the scratch prefix")
    if source_key == target_key:
        raise ConverterError("source and target must differ")
    # The bucket comes from configuration, not from the event. Pinning only the
    # keys leaves half the (bucket, key) pair caller-chosen, so whoever can
    # shape the payload picks which bucket is read and which one receives the
    # converted CUI — encrypted under this function's CMK and typed as a .docx.
    # Refused before any get_object, and without echoing the name.
    bucket = os.environ["DOCUMENTS_BUCKET"]
    if event.get("bucket", bucket) != bucket:
        raise ConverterError("refusing a bucket other than the configured one")

    # Imported here, not at module scope, so convert_bytes and its guards stay
    # importable — and testable — without boto3 or any AWS environment.
    import boto3

    from rmf_migrator.common.aws_clients import FAST_CONFIG

    # deepcopy, not the shared constant: botocore rewrites a Config's retries
    # dict in place, restating the pin for every later reader of the module.
    s3 = boto3.client("s3", config=copy.deepcopy(FAST_CONFIG))
    body = s3.get_object(Bucket=bucket, Key=source_key)["Body"]
    try:
        # Bounded read: the ceiling belongs here too, not only at upload time,
        # because this function reads whatever is at the key it is handed.
        data = body.read(MAX_DOC_BYTES + 1)
    finally:
        body.close()

    converted = convert_bytes(data)
    s3.put_object(
        Bucket=bucket,
        Key=target_key,
        Body=converted,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=os.environ["KMS_KEY_ID"],
        ContentType=_DOCX_CONTENT_TYPE,
    )
    return {"ok": True}
