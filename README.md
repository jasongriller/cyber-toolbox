# cyber-toolbox

One repo, multiple cyber tools, separate deploys.

| App | Path | Status |
|---|---|---|
| STIG Condenser | `apps/stig-parser/` | Live (dev) — deploys via `apps/stig-parser/deploy.sh` |
| RMF Rev5 Migrator | `apps/rmf-migrator/` | Built, not yet deployed |

- `./deploy.sh` — one-command deploy: shared platform first, then the stig-parser app.
- `platform/` — shared toolbox infrastructure (its own Terraform root/state): the shared Cognito user pool + per-app clients, published to SSM; custom domain later.
- `modules/` — shared Terraform modules, extracted only once two consumers exist.
- `docs/` — cross-cutting records, including the monorepo merge design.

Each app keeps its own README, tooling, Terraform state, and path-gated CI jobs
(`.github/workflows/ci.yml`). Work on one app never touches the other's deploy.

Histories of both apps were grafted here intact (see `docs/2026-07-24-monorepo-merge-design.md`);
`git log -- apps/<name>` reaches back to each tool's first commit.
