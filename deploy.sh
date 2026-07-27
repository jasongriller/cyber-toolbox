#!/usr/bin/env bash
# One-command deploy for the whole toolbox: shared platform first (skips
# itself when nothing changed), then each app. Flags pass through to all of
# them (--plan-only, --yes, plus each app script's own flags). Note
# stig-parser's script dies on a flag it doesn't recognize (platform's and
# rmf-migrator's both ignore one) — a flag meant for only one future app
# needs every sibling script updated to tolerate it, not just the new one.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> platform (shared login pool)"
./platform/deploy.sh "$@"

echo "==> stig-parser (app)"
./apps/stig-parser/deploy.sh "$@"

echo "==> rmf-migrator (app)"
./apps/rmf-migrator/deploy.sh "$@"

echo "==> toolbox deploy complete"
