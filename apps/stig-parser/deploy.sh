#!/usr/bin/env bash
#
# deploy.sh — one-command deploy for stig-parser into a GovCloud environment.
#
#   ./deploy.sh [env] [flags]        e.g.  ./deploy.sh   (auto-selects the one configured env)
#
# Phases (each can be trimmed with a flag):
#   0 guards    account + toolchain + filesystem safety checks
#   1 preflight the gitignored per-env files exist (backend.tf, terraform.tfvars)
#   2 layer     reuse the Lambda dependency layer (builds only if missing)
#   3 infra     terraform init/validate/plan -> y/N gate -> apply, then publish
#               the landing page (S3 only, no node build — runs even with
#               --skip-spa / --infra-only, since infra already points root at
#               this key the moment it applies)  [--plan-only]
#   4 spa       build the stig + rmf React bundles and sync them to the SPA
#               bucket (stig at its root, rmf under rmf/)  [--skip-spa]
#   5 smoke     check landing (/), the stig shell (/stig/), the rmf shell
#               (/rmf/), each shell's first script/stylesheet asset (200 —
#               catches a blank page even when the shell itself loads fine),
#               /config (200), and an authorized-only route on each app's
#               API (401)
#
# The dependency layer is REUSED by default (no Docker needed). It builds only
# when infra/build/deps-layer.zip is missing, or when you pass --rebuild-layer
# after bumping lxml/openpyxl. So a normal deploy is just: ./deploy.sh
#
# Flags:
#   --plan-only     stop after `terraform plan` (never applies; implies no spa/smoke)
#   --infra-only    run guards + preflight + layer + infra, skip spa + smoke.
#                   Warns loudly if the rmf bundle isn't published yet — the
#                   /rmf route this apply just wired up would 500 with
#                   nothing behind it until a later run publishes it.
#   --rebuild-layer force a fresh Docker build of the dependency layer
#   --skip-layer    never build; fail if the layer zip is missing (rarely needed)
#   --skip-spa      do not build or sync the frontend (same rmf-bundle warning
#                   as --infra-only, since this also skips the phase that
#                   publishes it)
#   --yes           skip the interactive apply prompt (also: AUTO_APPROVE=1)
#   -h, --help      show this help
#
# You always run this from the app root (apps/stig-parser): ./deploy.sh. With no env name it uses
# the single configured environment (the one with a real terraform.tfvars); pass
# a name only to pick among several. You never cd into the env folder — the
# script reaches into it for you.
#
# Per-env config is OPTIONAL. With no config, the account guard resolves the
# account and asks you to confirm it. To make the guard silent + strict, copy
# infra/envs/<env>/deploy.env.example to infra/envs/<env>/deploy.env (gitignored):
#   AWS_PROFILE=...  AWS_REGION=...  EXPECTED_ACCOUNT=...
# It sits beside backend.tf/terraform.tfvars because the account is a per-env
# fact (army-dev and a future prod may be different accounts). No account value
# is ever hard-coded here — this repo is public.

set -euo pipefail

# Git Bash (MSYS) rewrites leading-slash args into Windows paths before native
# exes see them — an SSM name like /cyber-toolbox/... reaches aws.exe as
# C:/Program Files/Git/... and "does not exist". Disable the conversion for
# this script; WSL/Linux ignore these variables entirely.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

# --- pretty logging --------------------------------------------------------
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
  YLW=$'\033[33m'; BLU=$'\033[34m'; RST=$'\033[0m'
else
  BOLD=; DIM=; RED=; GRN=; YLW=; BLU=; RST=
fi
step()  { printf '\n%s==> %s%s\n' "$BOLD$BLU" "$*" "$RST"; }
info()  { printf '    %s\n' "$*"; }
ok()    { printf '    %s✓ %s%s\n' "$GRN" "$*" "$RST"; }
warn()  { printf '    %s! %s%s\n' "$YLW" "$*" "$RST" >&2; }
die()   { printf '\n%s✗ %s%s\n' "$RED$BOLD" "$*" "$RST" >&2; exit 1; }

# --- locate repo root (script lives at the root) ---------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# --- parse args ------------------------------------------------------------
ENV=""
PLAN_ONLY=0; INFRA_ONLY=0; SKIP_LAYER=0; SKIP_SPA=0; AUTO_ENV=0; REBUILD_LAYER=0
AUTO_APPROVE="${AUTO_APPROVE:-0}"

usage() { sed -n '3,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for arg in "$@"; do
  case "$arg" in
    --plan-only)  PLAN_ONLY=1 ;;
    --infra-only) INFRA_ONLY=1 ;;
    --skip-layer)    SKIP_LAYER=1 ;;
    --rebuild-layer) REBUILD_LAYER=1 ;;
    --skip-spa)   SKIP_SPA=1 ;;
    --yes|-y)     AUTO_APPROVE=1 ;;
    -h|--help)    usage 0 ;;
    --skip-model-check) : ;; # rmf-only flag; tolerated so the root orchestrator can forward it to every app script (see rmf-migrator/deploy.sh's header comment)
    -*)           die "unknown flag: $arg  (try --help)" ;;
    *)            [[ -z "$ENV" ]] && ENV="$arg" || die "unexpected argument: $arg" ;;
  esac
done
# No env name given: auto-select the single configured environment — the one
# with a real (gitignored) terraform.tfvars. Bare `./deploy.sh` just works when
# one env is set up; it asks for a name only when several are.
if [[ -z "$ENV" ]]; then
  configured=()
  for _tfvars in infra/envs/*/terraform.tfvars; do
    [[ -f "$_tfvars" ]] || continue
    configured+=( "$(basename "$(dirname "$_tfvars")")" )
  done
  case ${#configured[@]} in
    1) ENV="${configured[0]}"; AUTO_ENV=1 ;;
    0) die "no configured environment found (no infra/envs/*/terraform.tfvars). Set one up, or pass a name: ./deploy.sh <env>" ;;
    *) die "multiple configured environments (${configured[*]}). Pass one: ./deploy.sh <env>" ;;
  esac
fi

ENV_DIR="infra/envs/${ENV}"
[[ -d "$ENV_DIR" ]] || die "no such environment: ${ENV_DIR}"

# Never leave a saved plan lying around, on any exit path (plan-only, abort, error).
trap 'rm -f "${ENV_DIR}/deploy.tfplan"' EXIT

# ===========================================================================
# Phase 0 — guards
# ===========================================================================
step "Phase 0 · Guards (${ENV})"
(( AUTO_ENV )) && info "auto-selected the only configured environment: ${ENV}"

# 0a. Never run from a Windows mount under WSL — npm/docker there corrupts
#     Lambda artifacts (a hazard both sibling AIE stacks learned the hard way).
if [[ "$REPO_ROOT" == /mnt/* && "${ALLOW_MNT:-0}" != "1" ]]; then
  die "refusing to run from a Windows mount ($REPO_ROOT).
    Clone into the native WSL filesystem (e.g. ~/stig-parser) and run there.
    Override at your own risk with ALLOW_MNT=1."
fi

# 0b. Optional per-env config (gitignored). Absent is fine — the account guard
#     below just confirms the resolved account instead of hard-checking it.
DEPLOY_ENV_FILE="${ENV_DIR}/deploy.env"
if [[ -f "$DEPLOY_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$DEPLOY_ENV_FILE"
  ok "loaded ${DEPLOY_ENV_FILE}"
fi
AWS_PROFILE="${AWS_PROFILE:-army-govcloud}"
AWS_REGION="${AWS_REGION:-us-gov-west-1}"
export AWS_PROFILE AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

# 0c. Force profile-based auth: stray static/assumed-role keys in the shell
#     (our whole terraform-role saga) silently target the wrong identity.
if [[ -n "${AWS_ACCESS_KEY_ID:-}${AWS_SECRET_ACCESS_KEY:-}${AWS_SESSION_TOKEN:-}" ]]; then
  warn "unsetting stray AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN so profile '${AWS_PROFILE}' is used"
  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
fi

# 0d. Toolchain.
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not on PATH${2:+ — $2}"; }
need aws
need terraform
tf_ver="$(terraform version -json 2>/dev/null | sed -n 's/.*"terraform_version": *"\([^"]*\)".*/\1/p' | head -1)"
[[ -n "$tf_ver" ]] || tf_ver="$(terraform version | sed -n '1s/Terraform v//p')"
tf_major="${tf_ver%%.*}"; tf_rest="${tf_ver#*.}"; tf_minor="${tf_rest%%.*}"
if (( tf_major < 1 || (tf_major == 1 && tf_minor < 10) )); then
  die "terraform ${tf_ver} is too old — the S3-native backend lock (use_lockfile) needs >= 1.10."
fi
ok "terraform ${tf_ver}"

# 0e. Account guard — the classic wrong-account tripwire.
info "resolving caller identity for profile '${AWS_PROFILE}'..."
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" \
  || die "could not authenticate with profile '${AWS_PROFILE}'. Check ~/.aws/config, or run 'aws sso login' / refresh MFA."
if [[ -n "${EXPECTED_ACCOUNT:-}" ]]; then
  [[ "$ACCOUNT" == "$EXPECTED_ACCOUNT" ]] \
    || die "WRONG ACCOUNT: profile resolved to ${ACCOUNT}, expected ${EXPECTED_ACCOUNT}."
  ok "account ${ACCOUNT} (matches EXPECTED_ACCOUNT)"
else
  warn "account is ${ACCOUNT} — no EXPECTED_ACCOUNT set to check it against"
  if [[ "$AUTO_APPROVE" == "1" ]]; then
    die "refusing to auto-approve into an unverified account. Set EXPECTED_ACCOUNT in ${DEPLOY_ENV_FILE}, or drop --yes to confirm the account interactively."
  fi
  read -r -p "    Deploy '${ENV}' into account ${ACCOUNT}? [y/N] " ans || true
  [[ "$ans" =~ ^[yY]([eE][sS])?$ ]] || die "aborted."
fi

# ===========================================================================
# Phase 1 — preflight (the gitignored per-env files that must exist)
# ===========================================================================
step "Phase 1 · Preflight"
missing=0
for f in backend.tf terraform.tfvars; do
  if [[ -f "${ENV_DIR}/${f}" ]]; then ok "${ENV_DIR}/${f}"; else
    warn "missing ${ENV_DIR}/${f} (gitignored — copy from ${f}.example and fill in)"; missing=1
  fi
done
(( missing == 0 )) || die "create the files above, then re-run."

# Read a value out of terraform.tfvars:  tfvar <key>
tfvar() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"\{0,1\}\([^\"]*\)\"\{0,1\}.*/\1/p" "${ENV_DIR}/terraform.tfvars" | head -1; }

# The env root reads the shared pool from SSM at plan time — the platform
# root must exist first. Fail here with a pointer, not mid-plan.
cognito_prefix="$(tfvar cognito_ssm_prefix)"; cognito_prefix="${cognito_prefix:-/cyber-toolbox/dev}"
if ! aws ssm get-parameter --name "${cognito_prefix}/cognito_user_pool_id" >/dev/null 2>&1; then
  die "shared pool not found at ${cognito_prefix}/* — apply the platform root first (platform/deploy.sh)"
fi

# ===========================================================================
# Phase 2 — dependency layer
# ===========================================================================
# The dependency layer changes only when lxml/openpyxl are bumped, so the
# default is to REUSE an existing zip — no Docker needed on a normal deploy.
# It builds automatically only when the zip is missing, or on --rebuild-layer.
LAYER_ZIP="infra/build/deps-layer.zip"
build_layer() {
  need docker "the layer must be built on Linux; run on a Docker host, or copy an existing ${LAYER_ZIP} onto this machine"
  local runtime; runtime="$(tfvar python_runtime)"; runtime="${runtime:-python3.12}"
  info "building ${LAYER_ZIP} for ${runtime} (Docker)..."
  ./infra/scripts/build-layer.sh "$runtime"
  ok "layer built"
}
if (( REBUILD_LAYER )); then
  step "Phase 2 · Dependency layer (rebuild requested)"
  build_layer
elif [[ -f "$LAYER_ZIP" ]]; then
  step "Phase 2 · Dependency layer"
  ok "reusing existing ${LAYER_ZIP} (pass --rebuild-layer after changing deps)"
elif (( SKIP_LAYER )); then
  die "--skip-layer but ${LAYER_ZIP} is missing — build it once (needs Docker) or copy it in."
else
  step "Phase 2 · Dependency layer (first build)"
  build_layer
fi

# ===========================================================================
# Phase 3 — infrastructure
# ===========================================================================
step "Phase 3 · Terraform (${ENV})"
tf() { terraform -chdir="$ENV_DIR" "$@"; }

info "init..."; tf init -input=false >/dev/null || die "terraform init failed."
ok "backend initialized"
info "validate..."; tf validate >/dev/null || die "terraform validate failed."
ok "configuration valid"

info "plan..."
set +e
tf plan -input=false -out=deploy.tfplan -detailed-exitcode
plan_rc=$?
set -e
case "$plan_rc" in
  0) ok "no changes — infrastructure already matches"; APPLIED_NOTHING=1 ;;
  2) info "changes pending (see plan above)"; APPLIED_NOTHING=0 ;;
  *) die "terraform plan failed (exit ${plan_rc})." ;;
esac

if (( PLAN_ONLY )); then
  step "Done (--plan-only)"; info "reviewed the plan; nothing applied."; exit 0
fi

if [[ "${APPLIED_NOTHING:-0}" == "0" ]]; then
  if [[ "$AUTO_APPROVE" != "1" ]]; then
    printf '\n'
    read -r -p "    ${BOLD}Apply this plan to ${ENV} (${ACCOUNT})? [y/N]${RST} " ans || true
    [[ "$ans" =~ ^[yY]([eE][sS])?$ ]] || { rm -f "${ENV_DIR}/deploy.tfplan"; die "aborted — nothing applied."; }
  fi
  info "applying..."
  tf apply -input=false deploy.tfplan || die "apply failed. Existing resources are unchanged; fix and re-run."
  ok "apply complete"
fi
rm -f "${ENV_DIR}/deploy.tfplan"

# --- AI killswitch parameter (created once; never overwritten) --------------
killswitch="$(tfvar ai_killswitch_param)"
if [[ -n "$killswitch" ]]; then
  if aws ssm get-parameter --name "$killswitch" >/dev/null 2>&1; then
    cur="$(aws ssm get-parameter --name "$killswitch" --query Parameter.Value --output text)"
    ok "killswitch ${killswitch} exists (value: ${cur})"
    [[ "$cur" == "enabled" ]] || warn "killswitch is not 'enabled' — AI stays OFF until it reads exactly 'enabled'"
  elif aws ssm put-parameter --name "$killswitch" --type String --value enabled \
    --description "stig-parser AI killswitch: exactly 'enabled' turns AI on; anything else fails closed" >/dev/null 2>&1; then
    ok "created killswitch ${killswitch} = enabled"
  else
    warn "could not read or create killswitch ${killswitch} — set it to 'enabled' manually, or AI stays OFF"
  fi
fi

# --- Landing page (S3 only, no node build needed) ---------------------------
# Runs whenever the module is in apigw_s3_proxy mode, regardless of
# --infra-only / --skip-spa: the root integration is repointed at this exact
# key the moment infra applies (main.tf), so if this step were skippable, `/`
# would start 500ing the instant someone used either flag.
spa_bucket="$(tf output -raw spa_bucket 2>/dev/null || true)"
if [[ -n "$spa_bucket" && "$spa_bucket" != "null" ]]; then
  step "Landing page"
  api_url="$(tf output -raw api_invoke_url)"
  # The tracked page ships an absolute-path placeholder (__STAGE__) instead of
  # a relative href: a relative href breaks whenever this page is reached
  # without a trailing slash (the bare invoke URL has none), because relative
  # resolution then drops the stage segment entirely. Substitute the real
  # stage path (scheme+host stripped, leading "/" kept, e.g. "/v1") into a
  # throwaway copy — never the tracked source — and publish that.
  stage_path="$(printf '%s' "$api_url" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+##')"
  [ -n "$stage_path" ] || die "could not derive a stage path from api_invoke_url ('${api_url}') — refusing to publish a landing page with a broken link."
  tmp_landing="$(mktemp)"
  # Extends the top-of-script tfplan trap rather than replacing it — both get
  # cleaned up on any exit path from here on, not just the success path below.
  trap 'rm -f "${ENV_DIR}/deploy.tfplan" "$tmp_landing"' EXIT
  sed "s#__STAGE__#${stage_path}#g" infra/modules/api/landing/index.html > "$tmp_landing"
  if grep -q '__STAGE__' "$tmp_landing"; then
    die "landing page still contains the __STAGE__ placeholder after substitution — refusing to publish an unsubstituted page."
  fi
  # Key must match the S3 key baked into spa_root's integration URI in
  # infra/modules/api/main.tf.
  #
  # --content-type is required here: aws s3 cp guesses Content-Type from the
  # SOURCE filename, not the destination key, and mktemp's output has no
  # extension — an unguessable name would upload as binary/octet-stream and
  # the browser would offer "/" as a download instead of rendering it. The
  # stig bundle sync (Phase 4, below) is not affected: it syncs real files
  # straight out of frontend/dist/, so its filenames (index.html, *.js, ...)
  # guess correctly on their own.
  info "publishing landing page -> s3://${spa_bucket}/landing/index.html (stage ${stage_path}) ..."
  aws s3 cp "$tmp_landing" "s3://${spa_bucket}/landing/index.html" --content-type "text/html; charset=utf-8" >/dev/null
  rm -f "$tmp_landing"
  ok "landing page published"
fi

# --- rmf bundle presence check (I2): only matters when we're about to skip
# the SPA phase. --infra-only exits before Phase 4 ever runs, and --skip-spa
# skips it outright: either way, if the rmf bundle has never been published (a
# from-scratch env, or the SPA phase failed on a previous run), the /rmf
# route this apply just wired up points at an S3 key that does not exist, and
# the landing page's rmf link 500s. --skip-spa at least gets caught by Phase
# 5's smoke test below; --infra-only exits 0 right after this block and would
# otherwise never reach Phase 5 at all — check explicitly, here, before either
# exit path, rather than let --infra-only fail silently.
if (( INFRA_ONLY || SKIP_SPA )) && [[ -n "$spa_bucket" && "$spa_bucket" != "null" ]]; then
  if ! aws s3api head-object --bucket "$spa_bucket" --key "rmf/index.html" >/dev/null 2>&1; then
    warn "################################################################"
    warn "  s3://${spa_bucket}/rmf/index.html does not exist."
    warn "  The /rmf route this apply just wired up has no bundle behind"
    warn "  it — visiting /rmf, or the landing page's rmf link, will 500"
    warn "  until a run without --infra-only/--skip-spa publishes it."
    warn "################################################################"
  fi
fi

if (( INFRA_ONLY )); then
  step "Done (--infra-only)"; exit 0
fi

# ===========================================================================
# Phase 4 — SPA build + sync
# ===========================================================================
if (( SKIP_SPA )); then
  step "Phase 4 · SPA (skipped)"
elif [[ -z "$spa_bucket" || "$spa_bucket" == "null" ]]; then
  step "Phase 4 · SPA (not applicable)"
  info "spa_bucket output is empty — spa_serving_mode is not apigw_s3_proxy; nothing to sync."
else
  step "Phase 4 · SPA bundle"
  need node; need npm
  api_url="$(tf output -raw api_invoke_url)"
  pool_id="$(tf output -raw cognito_user_pool_id)"
  client_id="$(tf output -raw cognito_client_id)"
  cognito_endpoint="https://cognito-idp-fips.${AWS_REGION}.amazonaws.com"
  info "building frontend (VITE_API_BASE=${api_url}, pool ${pool_id})..."
  ( cd frontend && npm ci --no-audit --no-fund >/dev/null 2>&1 \
    && VITE_API_BASE="$api_url" \
       VITE_COGNITO_USER_POOL_ID="$pool_id" \
       VITE_COGNITO_CLIENT_ID="$client_id" \
       VITE_AWS_REGION="$AWS_REGION" \
       VITE_COGNITO_ENDPOINT="$cognito_endpoint" \
       npm run build >/dev/null )
  [[ -d frontend/dist && -f frontend/dist/index.html ]] || die "frontend build produced no dist/index.html."
  ok "bundle built"
  [ -n "$pool_id" ] || die "cognito_user_pool_id output is empty — cannot verify the bundle bake"
  if ! grep -rqs "$pool_id" frontend/dist/assets/; then
    die "built bundle does not contain the Cognito pool id — VITE bake failed; refusing to ship an ungated SPA"
  fi
  # --exclude keeps this --delete sync from ever touching landing/ (published
  # above, same bucket) or rmf/ (published by the rmf SPA step below, same
  # bucket): without both carve-outs, every stig deploy would immediately
  # delete the landing page it just published, and would delete the live rmf
  # bundle outright — including the window before the rmf step below
  # re-uploads it, and permanently if that step then fails, since set -e
  # kills the script after this delete but before the re-upload.
  info "syncing dist/ -> s3://${spa_bucket}/ ..."
  aws s3 sync frontend/dist/ "s3://${spa_bucket}/" --delete --exclude "landing/*" --exclude "rmf/*" >/dev/null
  ok "SPA published to ${spa_bucket}"

  # --- rmf SPA (Phase R) -----------------------------------------------------
  # One shared Cognito client (D2) means one login covers both tools: reuse
  # the exact pool/client/region/endpoint values just baked into the stig
  # bundle above, unchanged. rmf's own vite.config.ts already supports
  # VITE_BASE_PATH for subpath serving — no frontend code change needed.
  #
  # VITE_BASE_PATH must include the API Gateway stage prefix, not just
  # "/rmf/": unlike stig's own `base: './'` (relative), rmf's vite.config.ts
  # uses this value as Vite's `base` directly, which emits ABSOLUTE asset
  # URLs. The document is served at .../v1/rmf/, so a bare "/rmf/" base would
  # make the browser request "/rmf/assets/..." off the origin root — API
  # Gateway reads that first path segment as a stage name, finds no stage
  # called "rmf" (the real stage is "v1"), and 403s every asset. rmf's
  # index.html un-hides the body before the bundle loads, so that renders as
  # a blank white page with no visible error. Same stage-prefix trap the
  # landing page burned three rounds on. Recompute the stage path locally
  # here (don't rely on the landing-page phase's stage_path still being in
  # scope) and die rather than build a bundle with a broken base.
  step "rmf SPA bundle"
  RMF_DIR="../rmf-migrator"
  [[ -d "$RMF_DIR/frontend" ]] || die "rmf-migrator frontend not found at ${RMF_DIR}/frontend (expected apps/stig-parser and apps/rmf-migrator as siblings)."
  rmf_stage_path="$(printf '%s' "$api_url" | sed -E 's#^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+##')"
  [ -n "$rmf_stage_path" ] || die "could not derive a stage path from api_invoke_url ('${api_url}') — refusing to build the rmf bundle with a broken asset base path."
  rmf_api_base="${api_url}/rmf/api"
  info "building rmf frontend (VITE_BASE_PATH=${rmf_stage_path}/rmf/, VITE_API_BASE_URL=${rmf_api_base}, pool ${pool_id})..."
  ( cd "$RMF_DIR/frontend" && npm ci --no-audit --no-fund >/dev/null 2>&1 \
    && VITE_BASE_PATH="${rmf_stage_path}/rmf/" \
       VITE_API_BASE_URL="$rmf_api_base" \
       VITE_COGNITO_USER_POOL_ID="$pool_id" \
       VITE_COGNITO_CLIENT_ID="$client_id" \
       VITE_AWS_REGION="$AWS_REGION" \
       VITE_COGNITO_ENDPOINT="$cognito_endpoint" \
       npm run build >/dev/null )
  [[ -d "$RMF_DIR/frontend/dist" && -f "$RMF_DIR/frontend/dist/index.html" ]] || die "rmf frontend build produced no dist/index.html."
  ok "rmf bundle built"
  if ! grep -rqs "$pool_id" "$RMF_DIR/frontend/dist/assets/"; then
    die "built rmf bundle does not contain the Cognito pool id — VITE bake failed; refusing to ship an ungated SPA"
  fi
  # No --exclude needed here (unlike the stig sync above): rmf/ is its own
  # disjoint prefix in the same bucket, so --delete cannot touch the stig
  # bundle at the bucket root or the landing page under landing/.
  info "syncing rmf dist/ -> s3://${spa_bucket}/rmf/ ..."
  aws s3 sync "$RMF_DIR/frontend/dist/" "s3://${spa_bucket}/rmf/" --delete >/dev/null
  ok "rmf SPA published to ${spa_bucket}/rmf/"
fi

# ===========================================================================
# Phase 5 — smoke test (public API: SPA shell, open config route, authorizer 401)
# ===========================================================================
step "Phase 5 · Smoke test"
api_url="$(tf output -raw api_invoke_url)"
origin="$(printf '%s' "$api_url" | sed -E 's#^([a-zA-Z][a-zA-Z0-9+.-]*://[^/]+).*#\1#')"
# A 200 on a shell's HTML is not enough: both tools' index.html un-hide the
# body before their bundle loads, so a shell that 200s while its assets 403
# still renders as a blank white page with no visible error — exactly the C1
# failure mode (a stage-prefix mismatch in VITE_BASE_PATH). Pull the first
# script/stylesheet URL out of the served HTML and fetch it directly, the way
# a browser would: an absolute path (leading "/") resolves against the origin
# (scheme+host, no stage); anything else resolves next to the page itself.
# Finding no asset URL at all is itself a failure, not something to skip.
check_first_asset() {
  local tool="$1" page_url="$2" html asset resolved code
  html="$(curl -s --max-time 30 "$page_url" || echo '')"
  asset="$(printf '%s' "$html" | grep -oE '(src|href)="[^"]+\.(js|css)[^"]*"' | head -1 | sed -E 's/^(src|href)="//; s/"$//' || true)"
  [ -n "$asset" ] || die "smoke: found no script/stylesheet asset URL in ${tool}'s served HTML (${page_url}) — cannot verify its bundle is reachable"
  case "$asset" in
    http://*|https://*) resolved="$asset" ;;
    /*)                  resolved="${origin}${asset}" ;;
    *)                   resolved="${page_url%/*}/${asset#./}" ;;
  esac
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$resolved" || echo 000)"
  [ "$code" = "200" ] || die "smoke: ${tool} asset ${resolved} returned ${code}, expected 200 (000 = could not reach it at all) — the shell HTML loaded but its bundle did not, which renders as a blank page in the browser"
}
# The root method now serves the toolbox landing page — probe exactly what users click.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/" || echo 000)"
[ "$code" = "200" ] || die "smoke: landing page returned ${code}, expected 200 (000 = could not reach the API at all)"
# A GET, not curl -I: this API defines no HEAD methods and returns 403 for
# one. Catches an extensionless-upload regression (mktemp has no suffix, so a
# client-side mimetype guess on the temp file's name would misfire) that a
# body-only check can't see — the browser would offer "/" as a download
# instead of rendering it, while every check above would still pass.
content_type="$(curl -s -o /dev/null -w '%{content_type}' --max-time 30 "${api_url}/" || echo 000)"
[[ "$content_type" == *"text/html"* ]] || die "smoke: landing page Content-Type is '${content_type}', expected text/html (000 = could not reach the API at all)"
# Prove the __STAGE__ placeholder was actually substituted at publish time —
# a silently unsubstituted page would still return 200 here and only break
# when a user clicks the (broken) link, exactly the failure mode this closes.
landing_body="$(curl -s --max-time 30 "${api_url}/" || echo '')"
[[ "$landing_body" == *"/stig/index.html"* ]] || die "smoke: landing page body does not contain /stig/index.html — check the __STAGE__ substitution in deploy.sh's landing-publish step"
[[ "$landing_body" == *"/rmf/index.html"* ]] || die "smoke: landing page body does not contain /rmf/index.html — check the __STAGE__ substitution in deploy.sh's landing-publish step"
[[ "$landing_body" != *"__STAGE__"* ]] || die "smoke: landing page body still contains the __STAGE__ placeholder — publish-time substitution failed silently"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/stig/" || echo 000)"
[ "$code" = "200" ] || die "smoke: stig SPA shell returned ${code}, expected 200 (000 = could not reach the API at all)"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/rmf/" || echo 000)"
[ "$code" = "200" ] || die "smoke: rmf SPA shell returned ${code}, expected 200 (000 = could not reach the API at all)"
rmf_content_type="$(curl -s -o /dev/null -w '%{content_type}' --max-time 30 "${api_url}/rmf/" || echo 000)"
[[ "$rmf_content_type" == *"text/html"* ]] || die "smoke: rmf SPA shell Content-Type is '${rmf_content_type}', expected text/html (000 = could not reach the API at all)"
check_first_asset "stig" "${api_url}/stig/"
check_first_asset "rmf" "${api_url}/rmf/"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/config" || echo 000)"
[ "$code" = "200" ] || die "smoke: /config returned ${code}, expected 200 (open route) (000 = could not reach the API at all)"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/jobs/00000000-0000-0000-0000-00000000dead" || echo 000)"
[ "$code" = "401" ] || die "smoke: bare /jobs/{id} returned ${code}, expected 401 (authorizer) (000 = could not reach the API at all)"
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${api_url}/rmf/api/projects" || echo 000)"
[ "$code" = "401" ] || die "smoke: bare /rmf/api/projects returned ${code}, expected 401 (rmf's own Cognito authorizer, reached through the proxy) (000 = could not reach the API at all)"
info "smoke: landing 200 (text/html, links substituted), stig shell 200, rmf shell 200 (text/html), stig+rmf first asset 200, config 200, bare data route 401, bare rmf api route 401"

step "${GRN}Deploy complete — ${ENV}${RST}"
# Trailing slash: this is the link people copy-paste. Without it, a relative
# href on the landing page (e.g. ./stig/index.html) resolves one level too
# high from the bare invoke URL and 404s/403s — the page itself still loads
# fine, so the break is silent until someone clicks through.
info "API:            ${api_url}/"
info "SPA bucket:     ${spa_bucket:-n/a}"
info "State machine:  $(tf output -raw state_machine_arn 2>/dev/null || echo n/a)"
