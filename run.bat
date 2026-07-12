@echo off
:: WorkGraph collector – Windows launcher
::
:: Task Scheduler setup:
::   1. Open Task Scheduler > Create Task
::   2. Triggers: At log on (or At startup)
::   3. Actions: Start a program
::        Program : C:\path\to\workgraph\run.bat
::   4. Settings: Uncheck "Stop the task if it runs longer than"
::   5. (Optional) General: Run whether user is logged on or not
::
:: To run once and exit (e.g. for a scheduled snapshot):
::   run.bat --once

setlocal
cd /d "%~dp0"

set "USE_MAIN=0"
for %%A in (%*) do (
	if /I "%%~A"=="--once" set "USE_MAIN=1"
	if /I "%%~A"=="--web" set "USE_MAIN=1"
)

if "%USE_MAIN%"=="1" (
	uv run python main.py %*
) else (
	uv run python -m services.collector_supervisor %*
)
