#!/usr/bin/env bash
# WorkGraph web dashboard – Linux/macOS launcher
#
# Cron example (run at system boot):
#   @reboot /path/to/workgraph/run.sh >> /path/to/workgraph/logs/cron.log 2>&1
#
# Cron example (restart every hour if not already running):
#   0 * * * * pgrep -f "workgraph/run.sh" > /dev/null || /path/to/workgraph/run.sh >> /path/to/workgraph/logs/cron.log 2>&1
#
# The dashboard will be accessible at: http://127.0.0.1:3000
#
# To use a different port, edit the --port value below:
#   exec uv run python main.py --web --port 8080 "$@"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec uv run python main.py --web --port 3000 "$@"
