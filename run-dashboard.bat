@echo off
:: WorkGraph Unified Launcher – Windows
::
:: Runs both the collector and dashboard together
:: Dashboard: http://127.0.0.1:4000
:: Data is collected continuously in the background
::
:: To customize the dashboard port:
::   run-dashboard.bat --port 3000

setlocal
cd /d "%~dp0"

echo Starting WorkGraph Collector + Dashboard...
echo Dashboard URL: http://127.0.0.1:4000
echo Press Ctrl+C to stop everything
echo.

uv run python -m services.unified_launcher %*
