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

uv run python main.py %*
