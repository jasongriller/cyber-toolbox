# rmf-rev5-migrator

Convert RMF **Rev 4** security policy documents to **Rev 5** with LLM assistance, self-hosted entirely inside your own AWS account.

`rmf-rev5-migrator` is an open-source tool for Assessment & Authorization (A&A) teams. Upload your existing Rev 4 policy documents (`.docx`), and the tool maps each section to its controls, applies NIST's official Rev 4 → Rev 5 control mapping, drafts updated Rev 5 language via **Amazon Bedrock**, surfaces coverage gaps, and exports a structure-preserving Rev 5 document plus a full per-control decision log.

> **Status:** Pre-release, in active development. Milestone M1 (ingest & parse pipeline) is landing first. See [Roadmap](#roadmap).

---

## Why this exists

Moving an A&A package from NIST SP 800-53 Rev 4 to Rev 5 is tedious and error-prone: controls were withdrawn, merged, renamed, and whole new families were added (e.g. `SR`, supply-chain risk management). Teams re-do this by hand, per document, per control, with no audit trail. This tool keeps a human in the loop at every decision while automating the mechanical work — and it runs where your Controlled Unclassified Information (CUI) already lives.

## Design principles

- **Runs in your boundary.** Everything deploys via Terraform into *your* AWS account. Documents never leave it. GovCloud (`us-gov-west-1`) is a supported target; commercial regions work for development.
- **Private by default.** No public endpoints. The app is served from a VPC-internal load balancer; access is via your existing network controls (VPN, portal, bastion). No application-level authentication to configure.
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
                   Private API Gateway
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

| Milestone | Scope |
|-----------|-------|
| **M1** | Terraform skeleton + document upload & DOCX parse pipeline |
| **M2** | Section → control mapping review UI (human checkpoint) |
| **M3** | Side-by-side editor, per-control chat assistant, Bedrock drafting |
| **M4** | Structure-preserving Rev 5 DOCX export + per-control decision log |
| **M5** | Coverage dashboard + conversion matrix exports (CSV/XLSX) |

Public v1 release follows M5.

## Repository layout

```
backend/     Python Lambda source + tests
frontend/    React + TypeScript SPA
terraform/   Reusable module + standalone example root
data/        Static NIST Rev4→Rev5 mapping + Rev5 catalog (public domain)
docs/        Architecture and deployment docs
```

## Development

Contributors do **not** need a GovCloud account. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for local setup (mocked Bedrock, LocalStack-free), and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the contribution workflow.

## Security

This tool handles CUI. If you find a security issue, please follow [`SECURITY.md`](SECURITY.md) rather than opening a public issue.

## License

[Apache-2.0](LICENSE).

NIST publications and the SP 800-53 control catalog / mappings redistributed under `data/` are U.S. Government works in the public domain.
