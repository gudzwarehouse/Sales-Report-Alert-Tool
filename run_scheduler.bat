@echo off
REM Wrapper for Windows Task Scheduler. Runs run_sales_alerts.py once and
REM exits -- Task Scheduler's own trigger (every 40 minutes, matching the
REM VBA's timerInterval) handles repetition, not this script.
cd /d "%~dp0"

if not exist "logs" mkdir "logs"

REM %date%/%time% substring parsing is locale-dependent (breaks on non-US
REM date formats) -- use PowerShell's Get-Date for a reliable timestamp.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set TIMESTAMP=%%i
set LOGFILE=logs\scheduler_%TIMESTAMP%.log

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe run_sales_alerts.py >> "%LOGFILE%" 2>&1
) else (
    python run_sales_alerts.py >> "%LOGFILE%" 2>&1
)

set EXIT_CODE=%ERRORLEVEL%
exit /b %EXIT_CODE%
