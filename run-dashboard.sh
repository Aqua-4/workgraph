#!/usr/bin/env bash
# WorkGraph Unified Launcher – Linux/macOS
#
# Runs both the collector and dashboard together
# Dashboard: http://127.0.0.1:4000
# Data is collected continuously in the background
#
# To customize the dashboard port:
#   ./run-dashboard.sh --port 3000

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting WorkGraph Collector + Dashboard..."
echo "Dashboard URL: http://127.0.0.1:4000"
echo "Press Ctrl+C to stop everything"
echo ""

exec uv run python -m services.unified_launcher "$@"
