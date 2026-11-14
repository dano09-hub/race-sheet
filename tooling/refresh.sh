#!/usr/bin/env bash
# Refresh every region, rebuild the page, stage it for publish.
# Cron example (07:00 UTC daily, log in the repo):
#   0 7 * * * cd /path/to/race-sheet && ./tooling/refresh.sh >> .refresh.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== $(date -u +%FT%TZ) refresh ==="
python3 tooling/refresh.py "$@"
echo "=== staged dist/public/index.html ($(stat -c%s dist/public/index.html) bytes) ==="
echo "publish dist/ to go live"
