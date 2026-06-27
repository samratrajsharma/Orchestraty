@echo off
TITLE Know Your Projects
cd /d "%~dp0"

REM Check for virtual environment python first
if exist .venv\Scripts\python.exe (
    echo [INFO] Running using virtual environment Python...
    .venv\Scripts\python.exe main.py
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        echo [INFO] Running using system Python...
        python main.py
    ) else (
        echo [ERROR] Python not found. Please run install_deps.bat first to set up the environment.
        echo.
        pause
        exit /b 1
    )
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] App failed to start. If dependencies are missing, run install_deps.bat.
    pause
    exit /b 1
)
