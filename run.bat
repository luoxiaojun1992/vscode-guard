@echo off
cd /d "%~dp0"
python vscode_guard.py
if %errorlevel% neq 0 (
    echo [ERROR] Failed to start. Please run install.bat first.
    pause
)
