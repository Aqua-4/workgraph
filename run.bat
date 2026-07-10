@echo off
:: WorkGraph web dashboard – Windows launcher
::
:: Task Scheduler setup:
::   1. Open Task Scheduler > Create Task
::   2. Triggers: At log on (or At startup)
::   3. Actions: Start a program
::        Program : C:\path\to\workgraph\run.bat
::   4. Settings: Uncheck "Stop the task if it runs longer than"
::   5. (Optional) General: Run whether user is logged on or not
::
:: The dashboard will be accessible at: http://127.0.0.1:3000
::
:: To use a different port, pass --port argument:
::   run.bat --port 8080

setlocal
cd /d "%~dp0"

uv run python main.py --web --port 3000 %*
