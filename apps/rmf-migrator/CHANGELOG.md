# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-07-20

Feature release: OSCAL and richer crosswalk exports, project purge, authenticated
private-mode API, pipeline hardening, and a redesigned interface. See
[docs/USER_MANUAL.md](docs/USER_MANUAL.md) for end-to-end operating instructions.

### Added

- **Real FedRAMP Rev 5 baselines.** `data/baselines/` now carries FedRAMP's own
  Low (156), Moderate (323), High (410), and Tailored LI-SaaS baselines. The
  LI-SaaS baseline also records each control's tailoring action (`Attest`,
  `Document and Assess`, `NSO`, …).
- **Crosswalk successor relationships.** Withdrawn Rev 4 controls are reclassified
  from the OSCAL Rev 5 catalog's own successor links into `moved`, `incorporated`,
  `split`, or `withdrawn`, and those successors flow forward as drafting targets,
  matrix dispositions, and decision-log entries.
- **OSCAL export.** A project's approved drafts export as a NIST OSCAL
  component-definition (model v1.1.2, deterministic output) from the coverage
  dashboard, for import into a GRC tool.
- **Project hard delete.** A project can be purged from the interface (typed-ID
  confirmation), removing every document, export, section, mapping, draft, and
  audit record, including all prior stored object versions.
- **Table-aware parsing.** The `.docx` parser now reads table cells in reading
  order, so requirements stated inside tables are captured for mapping and export.
- **Redesigned interface.** New visual system (self-hosted type, dark/light
  themes, status pills, monospace data tables, per-step navigation). No change to
  the API or data flow.

### Changed

- **Breaking (baselines):** the `fedramp` baseline value is replaced by
  `fedramp_low`, `fedramp_moderate`, `fedramp_high`, and `fedramp_li_saas`, so a
  project records the impact level it is actually authorized at. Projects created
  with `fedramp` are not migrated; re-create them at the correct level.
- **Breaking (deployment):** `private` mode now requires AWS SigV4 (`AWS_IAM`) on
  every API route and requires at least one trusted browser origin in
  `frame_ancestors` (used for the API/S3 CORS allowlist). Grant trusted callers
  `execute-api:Invoke`; browsers reach the API through an internal signing proxy.
- **Export gate.** Export requires every generated draft to be approved
  (`review_approved`). Editing an approved draft reverts it and revokes any prior
  export. Coverage and the conversion matrix count only approved work.

### Fixed

- **FedRAMP projects are no longer scored against a NIST baseline.** They now
  measure against the FedRAMP baseline (a strict superset at each impact level),
  so coverage is no longer over-reported and previously invisible required
  controls appear as gaps.
- **Empty drafts no longer blank a section on export.** A section with no drafted
  text keeps its original content instead of being emptied.
- **Pipeline jobs are idempotent and self-recovering.** Deterministic job IDs,
  conditional writes, and stale-lease reclaim make redelivered or interrupted
  parse/map/draft/export jobs safe to retry; unknown queue messages are dead-
  lettered instead of dropped.

### Security

- **`.docx` decompression is bounded** against zip-bomb inputs, measuring actual
  decompressed size rather than trusting the archive's declared sizes.
- **Input size ceilings** on parsed policy text, draft edits, and chat history.
- **Oversized section bodies are externalized** to encrypted storage rather than
  inlined, keeping items within limits.

## [1.0.1] - 2026-07-14

First working release. v1.0.0 was withdrawn: its Terraform failed `terraform init`
(a cross-variable guard used `condition = false`, which Terraform rejects), so it
could not be deployed.

### Fixed

- **Terraform now initializes, validates, and lints cleanly.** Rewrote the
  cross-variable preconditions (guardrail id/version, private-mode VPC inputs) to
  reference the variables they guard. Removed two dead declarations.
- **Lambda environment variables are encrypted with the project CMK**, closing a
  gap against the project's "customer-managed key everywhere" posture.
- **Lambda security-group egress narrowed to TCP 443**, instead of all protocols
  and ports.
- **S3 lifecycle configuration added.** Versioning was enabled, so superseded CUI
  object versions would have accumulated indefinitely; they now expire (default
  90 days) and aborted multipart uploads are cleaned up.
- **Secret scanning actually scans.** The gitleaks CI step was diffing a push
  range that starts at the root commit's nonexistent parent, so it errored after
  scanning zero bytes. It now scans the full history.

Remaining IaC-scanner findings are deliberate design decisions and carry inline
justifications (notably: no API-level authorizer, because access is gated by the
adopter's network — see `terraform/modules/rmf-migrator/apigateway.tf`).

## [1.0.0] - 2026-07-13 [WITHDRAWN]

First release. Full RMF Rev 4 → Rev 5 conversion pipeline, self-hosted in the
adopter's own AWS account (GovCloud-ready), all LLM work via Amazon Bedrock.

### Added

- **Ingest & parse (M1):** document upload via presigned S3 PUT and a DOCX
  section parser that extracts an ordered, nested section tree. Async job
  pipeline (SQS worker + DynamoDB status). Reusable Terraform module plus a
  standalone example root; private/public `network_mode` toggle; customer-managed
  KMS encryption throughout; CUI-safe logging that refuses document content.
- **Control mapping + human checkpoint (M2):** per-section Rev 4 control proposals
  from Bedrock, validated against the bundled NIST catalog (the model cannot
  invent control ids), reviewed and corrected by a human before any drafting.
  Bundled the official NIST SP 800-53 Rev 4/Rev 5 catalogs and a derived
  Rev 4 → Rev 5 crosswalk.
- **Rev 5 drafting + chat (M3):** structure-aware Rev 5 policy drafting with
  improvement suggestions, plus a per-section chat assistant. Side-by-side editor.
- **Structure-preserving export + decision log (M4):** generates a Rev 5 `.docx`
  that keeps the original document's headings, styles, and boilerplate, replacing
  only mapped section bodies. Per-control decision log (CSV) audit trail.
- **Package coverage dashboard + conversion matrix (M5):** project-level coverage
  and gap analysis against the NIST LOW/MODERATE/HIGH baselines, flagging Rev 5-new
  controls (e.g. the SR supply-chain family) no source document carried forward.
  Conversion summary matrix (CSV) export.

### Security

- No public endpoints by default; access is gated by the adopter's network.
- Document content, prompts, and model responses are never written to logs.
- Prompt-injection defenses: untrusted document text is marked as data and model
  output is validated against the bundled catalog.
- Least-privilege IAM; the Bedrock model id is always configuration.

### Notes

- FedRAMP and CNSSI 1253 baselines are approximated to NIST MODERATE/HIGH and are
  overridable; bundling authoritative FedRAMP baselines is future work.
- Crosswalk `merged`/`split` relationships (which require NIST's official
  comparison workbook) are not yet represented; `same`/`renamed`/`withdrawn`/`new`
  are derived from the two catalogs.

[1.0.1]: https://github.com/Redirishman/rmf-rev5-migrator/releases/tag/v1.0.1
[1.0.0]: https://github.com/Redirishman/rmf-rev5-migrator/releases/tag/v1.0.0
