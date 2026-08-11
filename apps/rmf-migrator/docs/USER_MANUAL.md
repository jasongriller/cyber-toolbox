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

- **Access.** How you sign in depends on how this deployment is configured (see
  DEPLOYMENT.md's Network modes). In a **private** deployment the app is reached
  through your organization's internal portal or signing proxy; your existing
  network/identity controls gate it, and there is no in-app login. In a
  **public + Cognito** deployment — e.g. this tool served through the cyber
  toolbox's shared front door — you sign in with the toolbox's shared login,
  and every API route requires that login's token.
- **Input format.** Policy documents must be `.docx`. The parser reads paragraphs
  **and table cells** in reading order, so requirements stated inside tables are
  captured. Legacy Word 97-2003 `.doc` files, uploaded honestly as `.doc`, are
  accepted **only if your deployment enables conversion** (§4.1); otherwise the
  upload is refused with a message pointing you at the two safe remedies — ask
  the document's author to re-save a `.docx` from their own copy, or ask your
  operator whether conversion should be enabled here. A legacy binary `.doc`
  **renamed** to `.docx` is a different case and is always rejected, regardless
  of the conversion setting: the file picker detects it by content, not
  extension, before upload. The refusal never asks you to open the file
  yourself (§4.1 explains why) — rename it back to `.doc` and re-upload if
  conversion is enabled, or use Save As from the author's own copy otherwise.
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
   Rev 4 `.docx` (or a legacy `.doc`, where conversion is enabled — see below).

Upload registers the document, sends the bytes straight to encrypted storage, and
automatically starts parsing. Parsing then chains into control mapping on its own.
The status pill advances `upload_pending → parsing → mapped`; the list refreshes
itself while work is in progress. When a document reaches **mapped**, select
**Open**. If a document instead shows **failed**, the reason appears directly
under its filename in the document list, along with what to do about it.

**Legacy `.doc` uploads.** If your deployment enables conversion (the
`enable_doc_conversion` Terraform flag — off by default, because it puts
LibreOffice inside the accreditation boundary), a Word 97-2003 `.doc` is
converted to `.docx` server-side before parsing, and the document list shows a
**converted from .doc** marker next to the filename. Two things follow from this,
both of which matter for an audit trail:

- The **converted copy** is what gets parsed, mapped, drafted from, and exported.
  Section text and headings you review are read out of it, not out of the original.
- The **original `.doc` is retained** unchanged in encrypted storage alongside the
  converted copy, so the file you uploaded remains the provenance record. Deleting
  the project (§8) purges both.

Conversion preserves heading structure, which is what the mapping review depends
on. It is not a full-fidelity round trip — treat the converted copy the way you
would treat any Save As, and check the Mapping review screen (§4.2) reflects the
sections you expect.

**If conversion fails, do not convert the file by hand.** The document lands at
`failed` and the status badge names the stage and the error type. The two error
types you can see there do not mean the same thing:

- **`failed (convert: ConversionUnavailable)`** is a statement about the
  *deployment*, not about the file. Conversion is switched off here (it is off
  by default), so the sandbox never saw the document and passed no judgement on
  it; the usual case is an ordinary legacy document someone renamed `.docx`
  years ago. Ask your operator whether conversion is meant to be enabled in this
  environment. Once they enable it, select **Retry** on the document you already
  uploaded — the pipeline re-runs from the conversion step, so you do not need to
  upload it again. For a deployment that will not enable conversion, the author
  of a known-good document can re-save a `.docx` from *their* copy.
- **`failed (convert: ConversionFailed)`** covers a refusal the sandbox issued on
  the document's content *and* ordinary operational failures that present
  identically: a timeout, output that breached the size ceiling, a throttled or
  erroring backend. Select **Retry** on the failed document once — re-uploading
  makes a second document record and a second retained original, a duplicate CUI
  copy for no gain. If it fails the same way again, ask your
  operator to confirm from the converter's own CloudWatch logs which of the two
  it was — a transport error is not a security event, and reporting every one of
  them trains the response channel to ignore the real thing.

**When the converter did refuse the document itself**, retain it, do not forward
it, and report it to your security team. Do not open it in Word to Save As a
`.docx`: conversion runs in a network-denied, macro-disabled sandbox precisely so
that a file built to look like a `.doc` is never opened on a workstation, and
doing it by hand gives that file the macro surface, network access, and
credentials the sandbox exists to deny it. The same rule covers a file the upload
screen refuses **on its bytes** — that check reads the file's magic number, not
its name, so it has already classified the content. Where this deployment
converts `.doc`, re-upload those bytes under their true `.doc` name and let the
sandbox handle them; otherwise treat the file as above rather than opening it.

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
| `failed` | A step failed. Select **Retry** to re-run from the failed stage without re-uploading — the tool records which stage failed. Re-uploading a document already in encrypted storage creates a second document record and a second retained original, so only fall back to a fresh upload if the document record itself needs replacing. For a `.doc` that failed at the conversion step specifically, see §4.1 and the troubleshooting rows in §11. |

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
| Document stuck at `failed` | Select **Retry** to re-run from the failed stage without re-uploading; the failed stage is recorded. Re-uploading a document already in encrypted storage creates a second document record and a second retained original, so only fall back to a fresh upload if the document record itself needs replacing. If the failed stage was conversion of a `.doc`, see §4.1 and the two rows below. |
| A section maps to the wrong control | Correct it in Mapping review before approving; you cannot edit the mapping after approval. |
| Export button disabled | Every draft must be **approved** first. Check the `n/total approved` counter in the editor. |
| Coverage looks too high or too low | Confirm the baseline matches the level the system is authorized at (§5). |
| Draft text looks generic or empty | Edit it directly, or use **Ask assistant**. Empty drafts are skipped at export so the original text is preserved. |
| A `.doc` upload is refused | Three possible causes. (1) Conversion is not enabled in this deployment (§4.1) — the refusal is about the deployment and says nothing about the file, so ask the document's author to re-save a `.docx` from their own copy, or ask your operator whether conversion should be enabled here. If the upload screen refused these bytes earlier, cause (2) applies as well and opening the file yourself is **not** safe. (2) The upload screen read the file's bytes and they are not the format the name claims — do **not** open it locally; if it reports legacy `.doc` bytes under a `.docx` name, rename it to `.doc` and upload it again so the sandbox converts it. See §4.1. (3) The file is over the size cap (15 MB for `.doc`, 25 MB for `.docx`). |
| A `.doc` document reached `failed` at the conversion step | Read the error type on the badge. `ConversionUnavailable` means conversion is switched off in this deployment (§4.1) and says nothing about the file; once your operator enables it, select **Retry** on the same document rather than uploading it again. `ConversionFailed` covers a refusal on the document's content but also timeouts, the 15 MB ceiling, and backend errors — select **Retry** once, and have your operator check the converter's CloudWatch logs before anyone escalates it. Either way, do not convert it by hand in Word (§4.1). |
| A converted `.doc` produced one long section | The original's headings were direct formatting rather than heading styles, so nothing marked the sections. Apply heading styles in Word, save as `.docx`, and re-upload. |

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
