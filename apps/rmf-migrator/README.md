# rmf-rev5-migrator

Convert RMF **Rev 4** security policy documents to **Rev 5** with LLM assistance, self-hosted entirely inside your own AWS account.

`rmf-rev5-migrator` is an open-source tool for Assessment & Authorization (A&A) teams. Upload your existing Rev 4 policy documents (`.docx`), and the tool maps each section to its controls, applies NIST's official Rev 4 → Rev 5 control mapping, drafts updated Rev 5 language via **Amazon Bedrock**, surfaces coverage gaps, and exports a structure-preserving Rev 5 document plus a full per-control decision log.

> **Status:** v1.1.0. Full pipeline works end to end — upload → map → draft → export → coverage. Operating instructions: [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

---

## Why this exists

Moving an A&A package from NIST SP 800-53 Rev 4 to Rev 5 is tedious and error-prone: controls were withdrawn, merged, renamed, and whole new families were added (e.g. `SR`, supply-chain risk management). Teams re-do this by hand, per document, per control, with no audit trail. This tool keeps a human in the loop at every decision while automating the mechanical work — and it runs where your Controlled Unclassified Information (CUI) already lives.

## Design principles

- **Runs in your boundary.** Everything deploys via Terraform into *your* AWS account. Documents never leave it. GovCloud (`us-gov-west-1`) is a supported target; commercial regions work for development.
- **Authenticated by default.** Production mode puts Lambdas in your VPC and requires AWS SigV4 on every API route. Serve the SPA through an internal signing proxy or portal so browser users inherit your existing IAM/identity controls.
- **CUI-aware.** Document content and LLM prompts/responses are never written to logs. All data at rest is encrypted with a customer-managed KMS key. Hard delete purges everything.
- **Human-verified.** The LLM proposes; a person confirms. The section→control mapping is reviewed and corrected *before* any drafting happens.
- **Toolbox-friendly.** Built to slot into a security team's internal tool portal — configurable base path, deep links, and a Terraform module that consumes your existing VPC/KMS/ALB.
- **Bring your own model.** The Bedrock model ID is pure configuration. Pick whatever your account has enabled.

## Architecture (target)

```
                    VPC-internal ALB
                          │
         ┌────────────────┴────────────────┐
         │  React SPA (static, S3 origin)   │
         └────────────────┬────────────────┘
                          │  /api/*
              IAM-protected API Gateway
                          │
                    Lambda (Python)
              ┌───────────┼───────────────┐
              │           │               │
          DynamoDB       SQS ──▶ Worker Lambda ──▶ Bedrock (VPC endpoint)
          (metadata)   (jobs)     (parse / draft)
              │
             S3 (documents)     ── all encrypted with a customer-managed KMS key ──
```

- **Frontend:** React + TypeScript, static build.
- **Backend:** Python Lambdas (`python-docx`, `boto3`).
- **Async work:** long LLM jobs run on worker Lambdas fed by SQS; the frontend polls job status from DynamoDB.
- **Infra:** Terraform, shipped as a reusable module plus a standalone example root.

## Roadmap

| Milestone | Scope | Status |
|-----------|-------|--------|
| **M1** | Terraform skeleton + document upload & DOCX parse pipeline | ✅ landed |
| **M2** | Section → control mapping (Bedrock) + review UI (human checkpoint) | ✅ landed |
| **M3** | Side-by-side editor, per-control chat assistant, Bedrock Rev 5 drafting | ✅ landed |
| **M4** | Structure-preserving Rev 5 DOCX export + per-control decision log | ✅ landed |
| **M5** | Package coverage dashboard + conversion matrix export (CSV) | ✅ landed |

All v1 milestones are implemented. The tool now runs the full pipeline end to end:
upload → parse → control mapping (human-reviewed) → Rev 5 drafting → structure-preserving
export → package coverage & gap analysis.

Beyond v1, the package can also be exported as a NIST **OSCAL component-definition**
(model v1.1.2) for import into a GRC platform: each approved Rev 5 draft becomes an
`implemented-requirement`, tagged with the originating Rev 4 control and its
disposition (`renamed` / `incorporated` / `split` / `moved`). A component-definition
is emitted rather than a full SSP because the tool holds control-implementation
narratives but not the system-characteristics (authorization boundary,
categorization, information types) an SSP requires.

## Control data

The NIST SP 800-53 catalogs (Rev 4 and Rev 5) and a derived Rev 4 → Rev 5
disposition map are **bundled** under `data/` (a U.S. Government public-domain
work), because private/GovCloud deployments have no internet egress to fetch them
at runtime. They are generated from the official [NIST OSCAL content](https://github.com/usnistgov/oscal-content)
by `scripts/fetch_catalogs.py`; a maintainer re-runs it to refresh the data. The
mapping's `same` / `renamed` / `withdrawn` / `new` relationships are computed from
the two catalogs; `merged` / `split` relationships from NIST's official comparison
workbook are layered on top over time.

### Baselines

Coverage and gap analysis measure a package against a baseline. Two families are
bundled, and they are **not** interchangeable:

| Baseline | Controls | Source |
| --- | --- | --- |
| NIST Low / Moderate / High | 149 / 287 / 370 | NIST OSCAL baseline profiles |
| FedRAMP Low / Moderate / High | 156 / 323 / 410 | FedRAMP Security Controls Baseline workbook |
| FedRAMP Tailored LI-SaaS | 156 (tailored Low) | same workbook, LI-SaaS sheet |

FedRAMP selects a strict superset of the NIST controls at the same impact level,
so scoring a FedRAMP package against a NIST baseline under-reports its gaps — a
FedRAMP Moderate project is measured against all 323 controls. LI-SaaS covers the
same controls as FedRAMP Low but records each control's tailoring action
(`Attest`, `Document and Assess`, `NSO`, …), which the reviewer needs to read a
gap correctly.

FedRAMP retired the `GSA/fedramp-automation` OSCAL repository and the
`automate.fedramp.gov` host during its 2026 consolidation. The surviving
machine-readable publication of the Rev 5 baselines is the FedRAMP Security
Controls Baseline workbook in FedRAMP's [`docs-legacy`](https://github.com/FedRAMP/docs-legacy)
repository (stamped with a legacy notice dated 2026-06-23), which
`scripts/fetch_fedramp_baselines.py` parses into `data/baselines/`. The script
validates every id against the bundled Rev 5 catalog and fails if FedRAMP
republishes the baselines in a shape it does not recognize.

## Repository layout

```
backend/     Python Lambda source + tests
frontend/    React + TypeScript SPA
terraform/   Reusable module + standalone example root
data/        Bundled NIST Rev4/Rev5 catalogs, Rev4→Rev5 mapping, NIST + FedRAMP baselines
scripts/     Maintainer tooling (fetch_catalogs.py, fetch_fedramp_baselines.py regenerate data/)
docs/        Architecture and deployment docs
```

## Development

Contributors do **not** need a GovCloud account. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for local setup (mocked Bedrock, LocalStack-free), and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow.

## Security

This tool handles CUI. If you find a security issue, please follow [`SECURITY.md`](SECURITY.md) rather than opening a public issue.

## License

[Apache-2.0](LICENSE).

NIST publications and the SP 800-53 control catalog / mappings redistributed under `data/` are U.S. Government works in the public domain.
