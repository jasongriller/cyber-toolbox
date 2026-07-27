#!/usr/bin/env bash
#
# deploy.sh — one-command deploy for rmf-migrator into a GovCloud environment.
#
#   ./deploy.sh [env] [flags]        e.g.  ./deploy.sh   (auto-selects the one configured env)
#
# Phases:
#   0 guards    account + toolchain + filesystem safety checks
#   1 preflight the gitignored per-env files exist (backend.tf, terraform.tfvars);
#               the shared Cognito pool exists in SSM
#   2 build     package the Lambda zip (py -3.13 scripts/build_lambda.py) —
#               always rebuilds; unlike stig-parser's dependency layer there
#               is no slow Docker build here worth caching
#   3 infra     terraform init/validate/plan -> y/N gate -> apply  [--plan-only]
#
# No SPA phase yet. This script stops at a working HTTP API whose url is
# published to SSM (rmf_api_url) — wiring the rmf SPA and its /rmf route onto
# the shared toolbox front door is a later phase.
#
# Flags:
#   --plan-only     stop after `terraform plan` (never applies)
#   --yes           skip the interactive apply prompt (also: AUTO_APPROVE=1)
#   -h, --help      show this help
#   Unknown flags are ignored, not rejected: the root orchestrator forwards
#   every app script's flags to every app script (e.g. stig-parser's
#   --skip-spa reaching this script when you run the toolbox's ./deploy.sh).
#
# You always run this from the app root (apps/rmf-migrator): ./deploy.sh. With
# no env name it uses the single configured environment (the one with a real
# terraform.tfvars); pass a name only to pick among several. You never cd into
# the env folder — the script reaches into it for you.
#
# Per-env config is OPTIONAL. With no config, the account guard resolves the
# account and asks you to confirm it. To make the guard silent + strict, copy
# terraform/envs/<env>/deploy.env.example to terraform/envs/<env>/deploy.env
# (gitignored):
#   AWS_PROFILE=...  AWS_REGION=...  EXPECTED_ACCOUNT=...
# No account value is ever hard-coded here — this repo is public.

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

# --- locate app root (script lives at the app root) -------------------------
APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_ROOT"

# --- parse args --------------------------------------------------------------
ENV=""
PLAN_ONLY=0; AUTO_ENV=0
AUTO_APPROVE="${AUTO_APPROVE:-0}"

usage() { sed -n '3,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

for arg in "$@"; do
  case "$arg" in
    --plan-only)  PLAN_ONLY=1 ;;
    --yes|-y)     AUTO_APPROVE=1 ;;
    -h|--help)    usage 0 ;;
    -*)           : ;; # unknown flag — ignored, see header comment
    *)            [[ -z "$ENV" ]] && ENV="$arg" || die "unexpected argument: $arg" ;;
  esac
done
# No env name given: auto-select the single configured environment — the one
# with a real (gitignored) terraform.tfvars. Bare `./deploy.sh` just works when
# one env is set up; it asks for a name only when several are.
if [[ -z "$ENV" ]]; then
  configured=()
  for _tfvars in terraform/envs/*/terraform.tfvars; do
    [[ -f "$_tfvars" ]] || continue
    configured+=( "$(basename "$(dirname "$_tfvars")")" )
  done
  case ${#configured[@]} in
    1) ENV="${configured[0]}"; AUTO_ENV=1 ;;
    0) die "no configured environment found (no terraform/envs/*/terraform.tfvars). Set one up, or pass a name: ./deploy.sh <env>" ;;
    *) die "multiple configured environments (${configured[*]}). Pass one: ./deploy.sh <env>" ;;
  esac
fi

ENV_DIR="terraform/envs/${ENV}"
[[ -d "$ENV_DIR" ]] || die "no such environment: ${ENV_DIR}"

# Never leave a saved plan lying around, on any exit path (plan-only, abort, error).
trap 'rm -f "${ENV_DIR}/deploy.tfplan"' EXIT

# ===========================================================================
# Phase 0 — guards
# ===========================================================================
step "Phase 0 · Guards (${ENV})"
(( AUTO_ENV )) && info "auto-selected the only configured environment: ${ENV}"

# 0a. Never run from a Windows mount under WSL — the sibling stig-parser stack
#     learned this the hard way (corrupted build artifacts).
if [[ "$APP_ROOT" == /mnt/* && "${ALLOW_MNT:-0}" != "1" ]]; then
  die "refusing to run from a Windows mount ($APP_ROOT).
    Clone into the native WSL filesystem (e.g. ~/cyber-toolbox) and run there.
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
#     silently target the wrong identity.
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
ok "shared pool present at ${cognito_prefix}"

# ===========================================================================
# Phase 2 — Lambda package
# ===========================================================================
step "Phase 2 · Lambda package"
need py "install Python 3.13 with the 'py' launcher, or build the zip manually per apps/rmf-migrator/docs/DEPLOYMENT.md"
info "building the Lambda zip (py -3.13 scripts/build_lambda.py)..."
py -3.13 scripts/build_lambda.py
ok "Lambda zip built"

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

step "${GRN}Deploy complete — ${ENV}${RST}"
info "API:               $(tf output -raw rmf_api_url 2>/dev/null || echo n/a)"
info "Published to SSM:  $(tf output -raw rmf_api_url_ssm_parameter 2>/dev/null || echo n/a)"
info "SPA + shared front-door routing not wired yet — a later phase."
