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

if [[ -x "$SCRIPT_DIR/.venv/bin/uv" ]]; then
	UV_BIN="$SCRIPT_DIR/.venv/bin/uv"
elif [[ -x "$HOME/.local/bin/uv" ]]; then
	UV_BIN="$HOME/.local/bin/uv"
else
	UV_BIN="$(command -v uv)"
fi

# Self-healing mode is default for continuous collection.
# Pass through one-shot and web modes directly to main.py.
for arg in "$@"; do
	if [[ "$arg" == "--once" || "$arg" == "--web" ]]; then
		exec "$UV_BIN" run python main.py "$@"
	fi
done

exec "$UV_BIN" run python -m services.collector_supervisor "$@"
