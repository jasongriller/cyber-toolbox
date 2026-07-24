# cyber-toolbox merge — design spec (2026-07-24)

## Context

stig-parser (live in Army GovCloud, E2E-verified) and rmf-rev5-migrator (built, never deployed)
are becoming two apps of one "cyber toolbox." The toolbox research (PUBLIC-FLIP-HANDOFF.md §9,
verified 2026-07-24) settled the architecture: one monorepo, separate per-app deploys, a future
shared Cognito pool under `platform/`. This spec covers **the merge only** — building the
monorepo folder locally with both histories preserved. The public+Cognito flip, rmf standup, and
platform work are explicit follow-ups that land *inside* the new repo later.

Sequencing note: handoff §9.6 originally resolved "flip first, merge after." The owner has since
directed merge-first (this task). That is §9.6's Order 2, whose researched rationale stands:
the graft is mechanically verifiable with zero functional change, and Phase A then lands in its
final home.

## Locked decisions (owner-confirmed 2026-07-24)

| Decision | Value |
|---|---|
| Folder / future repo name | `C:\Users\jgril\github\cyber-toolbox` |
| Scope of this effort | Merge + fixups + verification only. No flip, no push, no deploy. |
| stig seed branch | `awsStandup` (34 ahead of `master`, clean ancestor, fully pushed, live-verified) |
| rmf seed branch | `main` (== `aws-standup` == `f37b54a` everywhere) |
| Monorepo default branch | `main` |
| Subdirectory names | `apps/stig-parser/`, `apps/rmf-migrator/` (rmf's internal naming is consistently `rmf-migrator`) |
| rmf tags | renamed `v1.0.1` → `rmf-migrator-v1.0.1`, `v1.1.0` → `rmf-migrator-v1.1.0` (stig has zero tags) |
| History scrub level | **Strip trailers**: remove `Co-Authored-By: Claude …` (and any "Generated with Claude" lines) from all commit messages; drop `.claude/launch.json` from rmf history. Author fields untouched — the 14 commits authored as "Claude" (2026-07-07/08, SR Jacoby's design era) stay as-is; flagged for the Jacoby conversation. |
| AWS naming (owner-decided 2026-07-24) | **No renames of live resources** (S3/DynamoDB can't rename; Lambda/IAM/SFN renames = replacements; IAM grant is scoped to `role/stig-parser-dev-*`). Toolbox identity lands as: a `cyber-toolbox` tag on every resource of both apps via the existing provider `default_tags` (one tfvars line + example update, applied with the NEXT apply — not this task), per-app resource prefixes kept, shared platform resources named `toolbox-<env>-*`. |

## Non-goals (hard)

- No pushes, no GitHub repo creation (both source repos live under github.com/Redirishman —
  SR Jacoby's account — so hosting is a pending human conversation).
- No modification of the two existing repos or their checkouts (fallback stays intact; the live
  Army deploy keeps running from the existing WSL checkout until push + re-clone).
- No `terraform apply`, no state migration (backend key stays `stig-parser/army-dev/terraform.tfstate`;
  per-root state keys are a later platform-phase task).
- No Cognito / platform implementation — `platform/` and `modules/` are stub READMEs only.

## Mechanics

### Graft (PR1-equivalent — pure, no content edits)

1. Fresh clones of each repo into scratchpad with `--no-local` (git-filter-repo requires a fresh
   clone; object-level ops make Windows/CRLF irrelevant — checkout attributes travel per-subtree).
2. Per repo, `git filter-repo`:
   - `--to-subdirectory-filter apps/<name>`
   - rmf only: `--tag-rename '':'rmf-migrator-'` and `--invert-paths --path .claude/`
     (separate filter passes are acceptable; filter-repo composes)
   - both: message callback stripping lines matching
     `(?im)^co-authored-by:\s*claude[^\n]*$` and `(?im)^.*generated with \[?claude[^\n]*$`,
     then collapsing any resulting trailing blank lines.
3. Assemble: `git init -b main cyber-toolbox` → seed commit (minimal root README) →
   `git merge --allow-unrelated-histories` the filtered stig branch, then the filtered rmf branch
   (merge commits, **never squash** — squash deletes the grafted history). Fetch rmf's renamed tags.
   Remove the temp remotes.

### Graft acceptance gates (all must pass before fixups)

- **Tree parity, stig**: `git rev-parse HEAD:apps/stig-parser` in cyber-toolbox equals
  `git rev-parse awsStandup^{tree}` in the original — cryptographic proof of byte-identical content
  (message edits don't touch trees).
- **Tree parity, rmf**: run inside the throwaway filtered clone (where both object sets exist):
  fetch the original repo's `main`, then `git diff FETCH_HEAD^{tree} HEAD:apps/rmf-migrator` must
  show exactly one change — `.claude/launch.json` deleted. Nothing else. (Cross-repo tree diffs
  are impossible; hash-equality alone can't be used here because one file is intentionally removed.)
- History: `git log --follow apps/stig-parser/deploy.sh` reaches pre-move commits; blame spot-check
  on one old file per app; `git shortlog -sn` totals match originals (63 Jacoby / 20+1 Griller /
  14 Claude / 8 Redirishman for stig side, 33+4 for rmf side); tags list shows exactly the two
  renamed rmf tags; zero `Co-Authored-By: Claude` matches in `git log --all`.

### Fixups (PR2-equivalent — separate commits on top)

- Delete the 5 inert per-app workflow files (`apps/*/.github/workflows/*`); build root
  `.github/workflows/` replacing them:
  - one CI workflow, `main`-pinned, with a `changes` job (dorny/paths-filter, job-level — workflow-level
    `paths:` deadlocks required checks) gating per-app jobs;
  - stig jobs get `working-directory: apps/stig-parser`; the secret-leak grep is repointed to
    `apps/stig-parser/infra/**` and must **fail closed** (empty-pathspec = failure, not skip);
    terraform fmt/test/tflint/checkov paths repointed likewise;
  - rmf jobs get `working-directory: apps/rmf-migrator`; release workflow retargeted from `v*`
    to `rmf-v*` tags (the renamed historical tags `rmf-migrator-v*` deliberately do not match).
- Root `README.md` (toolbox overview, app pointers, live-deployment note), `platform/README.md` and
  `modules/README.md` stubs, this spec committed as `docs/2026-07-24-monorepo-merge-design.md`
  (it contains no account values — verified).
- App-level touch-ups: stig README/deploy.sh header lines that say "repo root" → "apps/stig-parser";
  rmf: delete tracked `frontend/tsconfig.tsbuildinfo` + gitignore it.
- Recreate `.git/info/exclude` (not carried by clone): `.claude/`, `PUBLIC-FLIP-HANDOFF.md`,
  `GOVCLOUD-PLAYBOOK.md` (fixes the current gap where the playbook shows untracked).
- No root `.gitignore`/`.gitattributes` consolidation — per-subtree files are verified
  location-anchored and correct; unify later (if ever) with a dedicated renormalize commit.

### Local-only carry-over (file copies, never committed)

- `infra/envs/army-dev/`: `backend.tf`, `terraform.tfvars`, `deploy.env`, `.terraform.lock.hcl`
  → `apps/stig-parser/infra/envs/army-dev/`. NOT copied: `.terraform/` (re-init), stale
  `army-dev.tfplan` (one-shot artifact).
- `PUBLIC-FLIP-HANDOFF.md`, `GOVCLOUD-PLAYBOOK.md` → cyber-toolbox root, excluded per above.
- rmf's untracked `docs/superpowers/plans/2026-07-24-govcloud-standup.md` (plans the retired
  private/SigV4 posture) stays behind in the old checkout = archived by default, per handoff §9.7.5.

### App verification (Phase-4 gates)

- stig, from `apps/stig-parser/`: `pytest` suite green; `frontend` `npm ci && npm run build` green;
  `terraform -chdir=infra/envs/army-dev init` + `validate` green; the 4 module tftests green;
  `terraform plan -refresh=false` (quirk: this identity lacks `iam:GetRolePolicy`; refreshless
  avoids the known false error) — **expected: zero changes, or only the known-benign Lambda
  `source_code_hash` drift** (Windows vs WSL toolchain, playbook Wall 05). Any resource
  add/destroy/replace or non-hash change = full stop, investigate before proceeding.
- rmf, from `apps/rmf-migrator/`: backend tests green (`make test` / pytest per Makefile);
  `frontend` `npm ci && npm run build` green; `terraform init -backend=false` + `validate` on the
  module and `examples/standalone` (never deployed — no backend, no plan possible or needed).
- Both paths already verified self-relative pre-design: `deploy.sh` locates itself
  (`REPO_ROOT="$(cd "$(dirname …)")"`), Lambda `archive_file` source dir is env-relative
  (`backend_source_dir` default `../../..` → resolves to `apps/stig-parser/` post-move), and the
  zip excludes list is source-dir-relative (monorepo root sits above it, never zipped).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| filter-repo misuse corrupts something | Operates only on throwaway scratchpad clones; originals never touched. |
| Silent content drift during graft | Tree-hash parity gates (exact for stig; exactly-one-deletion for rmf). |
| CI protections lost in the move (leak grep fails open) | Root CI rebuild is an explicit fixup with fail-closed leak grep; per-app workflows deleted so nothing half-works. |
| Terraform plan shows surprises from new path | Refreshless plan gate with a precise allowed-diff definition (hash-only) and a hard stop otherwise. |
| Sensitive local files leak into the public-destined repo | They are copied only into gitignored/excluded locations; `git status` must read clean afterward; the spec's exclude-recreation step is mandatory. |
| Deploy continuity confusion | Live deploys explicitly remain on the old WSL checkout until the humans approve push + re-clone. |

## Open items for humans (not blockers for this work)

1. GitHub hosting: whose account, new repo vs rename; both sources live under Jacoby's account.
2. The Jacoby conversation (one talk covering: both public flips reverse his designs, monorepo
   hosting, old-repo archiving, the 14 Claude-authored design-era commits left as-is).
3. Archive old repos with pointer READMEs — only after the monorepo is pushed and blessed.
4. Branch protection / required checks on the new repo at push time.
5. Everything in handoff §9.7 that concerns the flip (DNS/ACM ticket, MFA, tenancy gap, aud pinning).
6. First apply after the merge (likely the flip's Phase A) carries the `cyber-toolbox` tag rollout:
   expect ~all resources to show benign in-place tag updates in that plan — this is intended.
