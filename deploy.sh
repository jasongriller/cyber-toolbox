#!/usr/bin/env bash
# One-command deploy for the whole toolbox: shared platform first (skips
# itself when nothing changed), then the app. Flags pass through to both
# (--plan-only, --yes, plus the app script's own flags).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> platform (shared login pool)"
./platform/deploy.sh "$@"

echo "==> stig-parser (app)"
./apps/stig-parser/deploy.sh "$@"

echo "==> toolbox deploy complete"
