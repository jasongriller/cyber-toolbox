#!/usr/bin/env python3
"""Pre-flight a Bedrock model against this tool's real prompts.

The tool is model-agnostic by design — the Bedrock model id is pure
configuration. This script answers, for any candidate model, the only question
that matters before you deploy: *does it actually work with this codebase?*

It uses the real ``BedrockClient`` and the real mapping/drafting system prompts,
so a pass here means the pipeline will work, not that some toy prompt worked.

Checks, in order:
  1. Converse API      — the client is Converse-only; a model that doesn't serve
                         Converse cannot be used without a code change.
  2. System prompt     — the prompt-injection hardening lives in the system block.
                         Some models reject a separate system block.
  3. Mapping prompt    — must return JSON we can parse, with control ids that
                         actually exist in the bundled Rev 4 catalog.
  4. Drafting prompt   — must return JSON with draft_text + suggestions.

Usage:
    python scripts/check_bedrock_model.py                       # default candidate
    python scripts/check_bedrock_model.py openai.gpt-oss-120b-1:0 --region us-west-2

Credentials come from the usual boto3 chain (env vars, ~/.aws/credentials, SSO).
Costs a fraction of a cent: four short calls. Creates no infrastructure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from rmf_migrator.common.bedrock import (  # noqa: E402
    BedrockClient,
    BedrockError,
    ModelOutputError,
)
from rmf_migrator.common.catalog import rev4_catalog  # noqa: E402
from rmf_migrator.common.models import ControlMapping, Section  # noqa: E402
from rmf_migrator.services.drafting import build_draft  # noqa: E402
from rmf_migrator.services.mapping import map_section  # noqa: E402

DEFAULT_MODEL = "openai.gpt-oss-120b-1:0"

# A synthetic policy section. Never use real policy text or CUI here.
SAMPLE = Section(
    section_id="sec_preflight",
    document_id="doc_preflight",
    project_id="proj_preflight",
    order=0,
    level=2,
    heading="Account Management",
    text=(
        "The organization manages information system accounts, including "
        "establishing, activating, modifying, reviewing, disabling, and removing "
        "accounts. Account managers review accounts for compliance with account "
        "management requirements at least annually."
    ),
)

PASS = "PASS"
FAIL = "FAIL"


def _line(status: str, name: str, detail: str = "") -> None:
    mark = "+" if status == PASS else "!"
    print(f"  [{mark}] {status:4}  {name}" + (f" — {detail}" if detail else ""))


def check_converse(client: BedrockClient) -> bool:
    """1. Does the model serve the Converse API at all, with a system block?"""
    try:
        text = client.converse(
            system="You are a terse assistant. Reply with exactly the word: ready",
            user="Say the word.",
            max_tokens=64,
        )
    except BedrockError as exc:
        _line(FAIL, "Converse API + system block", str(exc))
        print(
            "\n      The client is Converse-only and puts prompt-injection hardening in\n"
            "      the system block. If this model does not serve Converse, or rejects a\n"
            "      system block, it needs a code change — not just a config change."
        )
        return False
    _line(PASS, "Converse API + system block", f"replied {text.strip()[:40]!r}")
    return True


def check_mapping(client: BedrockClient) -> bool:
    """3. Does the real mapping prompt come back as usable, validated JSON?"""
    mapping = map_section(SAMPLE, client)
    catalog = rev4_catalog()

    if mapping.confidence == 0.0 and not mapping.proposed_control_ids:
        _line(
            FAIL,
            "Mapping prompt -> JSON",
            "no controls proposed (model errored, or returned unparseable output)",
        )
        return False

    unknown = [c for c in mapping.proposed_control_ids if c not in catalog]
    if unknown:
        # Shouldn't happen — the engine validates against the catalog — but be explicit.
        _line(FAIL, "Mapping prompt -> JSON", f"invented control ids: {unknown}")
        return False

    _line(
        PASS,
        "Mapping prompt -> JSON",
        f"proposed {mapping.proposed_control_ids or '[]'} @ confidence {mapping.confidence}",
    )
    if not mapping.proposed_control_ids:
        print(
            "      NOTE: parsed fine but proposed no controls. The sample text is clearly\n"
            "      AC-2 (Account Management); a good model should find it. Usable, but this\n"
            "      model may map weakly."
        )
    return True


def check_drafting(client: BedrockClient) -> bool:
    """4. Does the real drafting prompt come back as usable JSON?"""
    mapping = ControlMapping(
        project_id=SAMPLE.project_id,
        document_id=SAMPLE.document_id,
        section_id=SAMPLE.section_id,
        order=0,
        final_control_ids=["AC-2"],
    )
    draft = build_draft(SAMPLE, mapping, client)

    if not draft.draft_text:
        _line(FAIL, "Drafting prompt -> JSON", "returned no draft_text")
        return False

    _line(
        PASS,
        "Drafting prompt -> JSON",
        f"{len(draft.draft_text)} chars, {len(draft.suggestions)} suggestion(s)",
    )
    print(f"      Rev 5 targets resolved: {draft.rev5_control_ids}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("model_id", nargs="?", default=DEFAULT_MODEL)
    parser.add_argument(
        "--region", default=None, help="defaults to your boto3/AWS region"
    )
    args = parser.parse_args()

    print(f"\nPre-flighting Bedrock model: {args.model_id}")
    print(f"Region: {args.region or '(from environment)'}\n")

    try:
        client = BedrockClient(args.model_id, region=args.region)
    except Exception as exc:  # noqa: BLE001
        print(f"could not build a Bedrock client: {type(exc).__name__}: {exc}")
        return 2

    results: list[bool] = []

    results.append(check_converse(client))
    if not results[0]:
        print("\nStopping: without Converse, the remaining checks cannot run.\n")
        return 1

    try:
        results.append(check_mapping(client))
        results.append(check_drafting(client))
    except ModelOutputError as exc:
        _line(FAIL, "structured output", str(exc))
        results.append(False)

    ok = all(results)
    print()
    if ok:
        print(f"VERDICT: {args.model_id} works with this tool.")
        print("Set it as bedrock_model_id in terraform.tfvars.\n")
    else:
        print(
            f"VERDICT: {args.model_id} is NOT usable as-is. See the failures above.\n"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
