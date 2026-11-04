@echo off
REM Wrapper for Windows Task Scheduler / manual double-click. Launches the
REM config-editor UI directly from venv, independent of VS Code.
cd /d "%~dp0"

set PORT=8532

REM Free the port first, in case a previous crashed instance left it open.
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

if exist "venv\Scripts\streamlit.exe" (
    venv\Scripts\streamlit.exe run app.py --server.port %PORT% --server.headless true
) else (
    streamlit run app.py --server.port %PORT% --server.headless true
)
