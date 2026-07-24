#!/usr/bin/env bash
# Guarded terraform entrypoint for the platform root (shared pool + SSM).
# Usage: ./deploy.sh [env] [--plan-only] [--yes]
set -euo pipefail

PLAN_ONLY=false
AUTO_YES=false
ENV=""
for arg in "$@"; do
  case "$arg" in
    --plan-only) PLAN_ONLY=true ;;
    --yes)       AUTO_YES=true ;;
    -*)          echo "unknown flag: $arg" >&2; exit 2 ;;
    *)           ENV="$arg" ;;
  esac
done

cd "$(dirname "$0")"
case "$(pwd -P)" in /mnt/*) echo "refusing to run from a Windows mount — use the native checkout" >&2; exit 1 ;; esac

# Env auto-select: exactly one envs/*/terraform.tfvars wins, like the app script.
if [ -z "$ENV" ]; then
  candidates=(infra/envs/*/terraform.tfvars)
  if [ "${#candidates[@]}" -ne 1 ] || [ ! -f "${candidates[0]}" ]; then
    echo "cannot auto-select env — pass one: ./deploy.sh <env>" >&2; exit 2
  fi
  ENV="$(basename "$(dirname "${candidates[0]}")")"
fi
ENV_DIR="infra/envs/${ENV}"
[ -f "${ENV_DIR}/terraform.tfvars" ] || { echo "missing ${ENV_DIR}/terraform.tfvars" >&2; exit 2; }
[ -f "${ENV_DIR}/backend.tf" ]       || { echo "missing ${ENV_DIR}/backend.tf" >&2; exit 2; }

# Profile wins over any stray env creds.
[ -f "${ENV_DIR}/deploy.env" ] && . "${ENV_DIR}/deploy.env"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_PROFILE="${AWS_PROFILE:-army-govcloud}"
export AWS_REGION="${AWS_REGION:-us-gov-west-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

tf_ver="$(terraform version -json | sed -n 's/.*"terraform_version":"\([^"]*\)".*/\1/p')"
case "$tf_ver" in
  0.*|1.[0-9].*) echo "terraform >= 1.10 required for use_lockfile (found $tf_ver)" >&2; exit 1 ;;
esac

actual="$(aws sts get-caller-identity --query Account --output text)"
if [ -n "${EXPECTED_ACCOUNT:-}" ]; then
  [ "$actual" = "$EXPECTED_ACCOUNT" ] || { echo "wrong account: $actual != $EXPECTED_ACCOUNT" >&2; exit 1; }
else
  echo "EXPECTED_ACCOUNT not set (deploy.env). Deploying to account ${actual}."
  if [ "$AUTO_YES" = true ]; then echo "--yes refused without EXPECTED_ACCOUNT" >&2; exit 1; fi
  read -r -p "continue? [y/N] " ok; [ "$ok" = y ] || exit 1
fi

tf() { terraform -chdir="$ENV_DIR" "$@"; }
trap 'rm -f "${ENV_DIR}/deploy.tfplan"' EXIT

tf init -input=false
tf validate
tf plan -input=false -out=deploy.tfplan
[ "$PLAN_ONLY" = true ] && exit 0

if [ "$AUTO_YES" != true ]; then
  read -r -p "apply this plan? [y/N] " ok; [ "$ok" = y ] || exit 1
fi
tf apply -input=false deploy.tfplan

pool_id="$(tf output -raw cognito_user_pool_id)"
echo
echo "pool: ${pool_id}"
echo "clients:"; tf output -json cognito_client_ids
cat <<SNIP

create a user:
  aws cognito-idp admin-create-user --user-pool-id ${pool_id} \\
    --username user@example.mil \\
    --user-attributes Name=email,Value=user@example.mil Name=email_verified,Value=true \\
    --profile ${AWS_PROFILE} --region ${AWS_REGION}
SNIP
