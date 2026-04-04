@echo off
REM Launch Claude Code GUI (Windows)
cd /d "%~dp0"

if not exist ".venv" (
    echo First run: setting up virtual environment...
    python -m venv .venv
    .venv\Scripts\pip install -q pywebview
    echo Done!
)

.venv\Scripts\python main.py %*
