@echo off
REM Wrapper for Windows Task Scheduler. Set up its own trigger for this one
REM -- once daily at 3:00pm -- separate from run_scheduler.bat's 40-minute
REM repeating trigger.
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set TIMESTAMP=%%i
set LOGFILE=logs\daily_summary_%TIMESTAMP%.log

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe run_daily_summary.py >> "%LOGFILE%" 2>&1
) else (
    python run_daily_summary.py >> "%LOGFILE%" 2>&1
)

set EXIT_CODE=%ERRORLEVEL%
exit /b %EXIT_CODE%
