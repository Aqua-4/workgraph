@echo off
:: WorkGraph Unified Launcher – Windows
::
:: Runs both the collector and dashboard together
:: Dashboard: http://127.0.0.1:4000
:: Data is collected continuously in the background
:: Sync daemon auto-starts when sync_base_url and sync_token are set
:: in config/my-settings.yaml (or selected config).
::
:: To customize the dashboard port:
::   run-dashboard.bat --port 3000
:: To disable sync daemon startup:
::   run-dashboard.bat --no-sync

setlocal
cd /d "%~dp0"

echo Starting WorkGraph Collector + Dashboard...
echo Dashboard URL: http://127.0.0.1:4000
echo Sync daemon: auto (when sync config is present)
echo Press Ctrl+C to stop everything
echo.

uv run python -m services.unified_launcher %*
