@echo off
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
echo Installing dependencies...
python -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo [ERROR] Installation failed.
    pause
    exit /b 1
)
echo Done! Run run.bat to start the program.
pause
