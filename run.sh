#!/usr/bin/env bash
# WorkGraph collector – Linux/macOS launcher
#
# Cron example (run at system boot):
#   @reboot /path/to/workgraph/run.sh >> /path/to/workgraph/logs/cron.log 2>&1
#
# Cron example (restart every hour if not already running):
#   0 * * * * pgrep -f "workgraph/main.py" > /dev/null || /path/to/workgraph/run.sh >> /path/to/workgraph/logs/cron.log 2>&1

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Self-healing mode is default for continuous collection.
# Pass through one-shot and web modes directly to main.py.
for arg in "$@"; do
	if [[ "$arg" == "--once" || "$arg" == "--web" ]]; then
		exec uv run python main.py "$@"
	fi
done

exec uv run python -m services.collector_supervisor "$@"
