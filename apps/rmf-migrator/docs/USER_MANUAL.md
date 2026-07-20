# RMF Rev 5 Migrator — User Manual

**Version 1.1.0**

A guide for Assessment & Authorization (A&A) teams operating the tool day to day.
For standing it up in AWS, see [DEPLOYMENT.md](DEPLOYMENT.md); for working on the
code, see [DEVELOPMENT.md](DEVELOPMENT.md).

---

## 1. What this tool does

The RMF Rev 5 Migrator converts a system's NIST SP 800-53 **Rev 4** security
policy documents into **Rev 5**, keeping a human in the loop at every decision.

For each Rev 4 policy document (`.docx`) you upload, the tool:

1. Splits it into sections by heading.
2. Proposes the Rev 4 controls each section addresses (you review and correct this).
3. Applies NIST's official Rev 4 → Rev 5 crosswalk to find the Rev 5 successors.
4. Drafts updated Rev 5 language per section via Amazon Bedrock (you edit and approve).
5. Exports a **structure-preserving** Rev 5 `.docx` plus a full per-control decision log.
6. Reports package coverage against a baseline and flags gaps, including the
   Rev 5-new `SR` (supply-chain) family that no Rev 4 document carries.

Everything runs inside your own AWS account. Document content and model
prompts/responses are never written to logs.

---

## 2. Key concepts

| Term | Meaning |
|------|---------|
| **Project** | One system's A&A package. Holds one or more policy documents and a chosen baseline. |
| **Document** | A single uploaded Rev 4 `.docx` policy. |
| **Baseline** | The Rev 5 control set coverage is measured against (FedRAMP level, FIPS 199 level, DoD/CNSSI 1253, or generic 800-53). |
| **Section** | A heading-delimited block of a document. Mapping, drafting, and export all operate per section. |
| **Mapping** | The set of Rev 4 control IDs a section addresses. LLM-proposed, human-confirmed. |
| **Disposition** | What happened to a Rev 4 control in Rev 5 (see §6). |
| **Draft** | The proposed Rev 5 language for a section, edited and approved by a reviewer. |
| **Decision log** | Per-control audit trail: Rev 4 source, disposition, Rev 5 target, who approved, when. |
| **Coverage** | How much of the chosen baseline the approved drafts satisfy. |

---

## 3. Before you start

- **Access.** In a production (private) deployment the app is reached through your
  organization's internal portal or signing proxy; your existing network/identity
  controls gate it. There is no in-app login.
- **Input format.** Policy documents must be `.docx`. The parser reads paragraphs
  **and table cells** in reading order, so requirements stated inside tables are
  captured.
- **CUI handling.** Uploaded content is encrypted at rest with your customer-managed
  KMS key and is never logged. Deleting a project (§8) permanently purges every
  document, export, section, and audit record, including all prior S3 versions.

---

## 4. The workflow

The interface is a four-step pipeline. The stepper at the top of a document shows
where you are: **1 Mapping review → 2 Rev 5 editor → 3 Export → 4 Coverage.**

### 4.1 Create a project and upload a document

On the landing screen:

1. Under **Projects**, type a project name, pick the **baseline** the system is
   authorized at, and select **Create**.
2. With the project selected, use the file picker under **Documents** to upload a
   Rev 4 `.docx`.

Upload registers the document, sends the bytes straight to encrypted storage, and
automatically starts parsing. Parsing then chains into control mapping on its own.
The status pill advances `upload_pending → parsing → mapped`; the list refreshes
itself while work is in progress. When a document reaches **mapped**, select
**Open**.

### 4.2 Step 1 — Mapping review (the human checkpoint)

This is the gate: nothing is drafted until you approve the mapping.

Each row is one section, showing the section heading, the **proposed Rev 4
controls** (editable), the model's **confidence**, and the mapping **state**.

- Correct any row's control IDs (comma- or space-separated, e.g. `AC-2, AC-6(1)`)
  and select **Save**.
- When every row is right, select **Approve mapping & continue**.

Approval freezes the control set and automatically starts Rev 5 drafting. After
approval the rows become read-only.

### 4.3 Step 2 — Rev 5 editor

Each section is a card with the original Rev 4 text on the left and the **proposed
Rev 5 draft** on the right. The card header shows the Rev 5 target control(s) and
the **disposition** (see §6) for each mapped Rev 4 control.

Per section you can:

- **Edit** the Rev 5 text and select **Save**.
- Expand **Suggestions** for the model's improvement notes.
- Select **Ask assistant** to open a per-section chat — ask it to refine wording or
  explain a control. The conversation is scoped to that section and its draft.
- Select **Approve section** once the language is final.

The header counter (`n/total approved`) tracks progress. **Every generated draft
must be approved** before export unlocks. Editing an approved draft afterward
reverts it to unapproved and revokes any prior export.

### 4.4 Step 3 — Export

Available once every draft is approved (status **review_approved**).

- **Generate Rev 5 .docx** builds a new document that preserves the original's
  structure — headings, styles, tables, and unmapped boilerplate stay put; only the
  mapped section bodies are replaced with approved Rev 5 text. This runs in the
  background; the button shows progress.
- **Download Rev 5 .docx** downloads the generated file once ready.
- **Download decision log (CSV)** downloads the per-control audit trail.

If the document is not ready, a banner tells you to approve the mapping and drafts first.

### 4.5 Step 4 — Coverage dashboard

Project-level, spanning every document in the package.

- Pick a **baseline** (or use the project default) and **Refresh**.
- The metric shows coverage percentage and covered/total control counts.
- **Baseline gaps** lists required controls no approved draft addresses.
- **New in Rev 5, not covered** lists Rev 5-additions with no Rev 4 predecessor; the
  **`SR` supply-chain family is highlighted** because it is the most common blind
  spot in a Rev 4 → Rev 5 move.
- **Download conversion matrix (CSV)** — one row per Rev 4 control across the package.
- **Download OSCAL (JSON)** — a NIST OSCAL component-definition of the approved
  drafts, for import into a GRC tool.

---

## 5. Baselines

Coverage is measured against the baseline you pick. Available values:

| Baseline | Notes |
|----------|-------|
| `generic_800_53` | The full 800-53 Rev 5 catalog; no impact-level tailoring. |
| `fips199_low` / `fips199_moderate` / `fips199_high` | FIPS 199 categorization levels. |
| `fedramp_low` / `fedramp_moderate` / `fedramp_high` | Real FedRAMP baselines (156 / 323 / 410 controls). FedRAMP selects a strict superset of the NIST set at each level. |
| `fedramp_li_saas` | FedRAMP Tailored LI-SaaS. |
| `dod_cnssi_1253` | DoD / CNSSI 1253. |

You can override the baseline per view on the coverage dashboard without changing
the project's stored default.

---

## 6. Dispositions (Rev 4 → Rev 5)

Each mapped Rev 4 control carries a disposition from NIST's official crosswalk,
shown in the editor and the decision log:

| Disposition | Meaning |
|-------------|---------|
| **same** | Carried into Rev 5 unchanged (same ID). |
| **renamed** | Same control, new identifier/title. |
| **moved** | Requirement relocated to one other Rev 5 control. |
| **incorporated** | Folded into one existing Rev 5 control. |
| **split** | Requirement divided across more than one Rev 5 control (e.g. `AC-13 → AC-2, AU-6`). |
| **withdrawn** | Withdrawn with no Rev 5 successor (e.g. `SC-19`, `SA-12`). |

`new` controls (Rev 5 additions with no Rev 4 source, such as the `SR` family) do
not originate from a document section; they surface as coverage gaps in §4.5.

---

## 7. Document status reference

The status pill on a document reflects where it is in the pipeline:

| Status | Meaning |
|--------|---------|
| `upload_pending` | Registered; bytes not yet confirmed in storage. |
| `uploaded` | Bytes present; parsing not started. |
| `parsing` / `parsed` | Splitting into sections. |
| `mapping` / `mapped` | Proposing controls / **ready for mapping review**. |
| `mapping_approved` | Mapping confirmed; drafting queued. |
| `drafting` / `drafted` | Generating Rev 5 language / **ready for the editor**. |
| `review_approved` | Every draft approved; **ready to export**. |
| `exporting` / `exported` | Building the Rev 5 `.docx` / **ready to download**. |
| `failed` | A step failed. Re-trigger it (re-upload, re-open, or re-run export); the tool records which stage failed. |

---

## 8. Deleting a project (CUI hard delete)

On the landing screen, with a project selected, **Delete project** permanently
removes it. You are prompted to type the project's ID to confirm. This purges every
document, export, section, mapping, draft, and audit record, and deletes all stored
object versions — there is no recovery. Use it when the engagement is done or when a
project must be scrubbed.

---

## 9. Exports at a glance

| Artifact | Where | Contents |
|----------|-------|----------|
| Rev 5 `.docx` | Export | Structure-preserving Rev 5 document. |
| Decision log CSV | Export | Per-control audit trail for one document. |
| Conversion matrix CSV | Coverage | One row per Rev 4 control across the whole package. |
| OSCAL JSON | Coverage | NIST OSCAL component-definition of approved drafts. |

CSV exports are formula-injection safe.

---

## 10. Interface notes

- **Theme.** The toggle at the top right switches between the dark (default) and
  light treatments; your choice is remembered. Light is print-friendly.
- **Auto-refresh.** Lists and status views poll on their own while work is in
  progress; you do not need to reload.
- **Errors** appear as an inline banner on the affected screen, stating what went
  wrong.

---

## 11. Troubleshooting

| Symptom | What to do |
|---------|-----------|
| Document stuck at `failed` | Re-open it, or re-upload. The failed stage is recorded; re-triggering resumes from there. |
| A section maps to the wrong control | Correct it in Mapping review before approving; you cannot edit the mapping after approval. |
| Export button disabled | Every draft must be **approved** first. Check the `n/total approved` counter in the editor. |
| Coverage looks too high or too low | Confirm the baseline matches the level the system is authorized at (§5). |
| Draft text looks generic or empty | Edit it directly, or use **Ask assistant**. Empty drafts are skipped at export so the original text is preserved. |

---

## 12. Deployment & authentication (summary)

The tool self-hosts in your AWS account via Terraform. Two modes:

- **`private` (production, default).** Lambdas run in your VPC; **every API route
  requires AWS SigV4 (`AWS_IAM`)**. Browsers reach the API through your internal
  portal or signing proxy, inheriting your IAM/identity controls. There is no
  application-level login to manage.
- **`public` (dev/demo only).** An unauthenticated API for local testing. **Never
  put CUI through it.**

Full deployment steps, VPC-endpoint requirements, and the teardown checklist are in
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## 13. Version & support

- **Manual version:** 1.1.0
- **Changelog:** [CHANGELOG.md](../CHANGELOG.md)
- **Source & issues:** https://github.com/Redirishman/rmf-rev5-migrator

The tool assists; it does not certify. A qualified reviewer remains responsible for
every mapping, draft, and disposition it produces.
