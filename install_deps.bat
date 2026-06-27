@echo off
TITLE Know Your Projects — Setup
echo ============================================================
echo   Know Your Projects - Dependency Installer
echo ============================================================
echo.

REM 1. Check if we already have a virtual environment with Python
if exist .venv\Scripts\python.exe (
    echo [OK] Existing virtual environment found.
    goto install_deps
)

REM 2. Check if python is on system PATH
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] System Python found. Creating virtual environment...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [WARNING] Failed to create venv using system Python. Trying uv...
        goto try_uv
    )
    goto install_deps
)

:try_uv
REM 3. Check if uv is installed in user profile or PATH
where uv >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] uv found on PATH. Creating virtual environment...
    uv venv --python 3.11
    goto install_deps
)

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    echo [OK] uv found in user local bin. Creating virtual environment...
    "%USERPROFILE%\.local\bin\uv.exe" venv --python 3.11
    goto install_deps
)

REM 4. If nothing is found, install uv to bootstrap Python
echo [INFO] Python/uv not found on PATH. Downloading uv package manager...
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install uv. Please install Python manually from:
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo [OK] uv installed successfully. Creating virtual environment...
"%USERPROFILE%\.local\bin\uv.exe" venv --python 3.11

:install_deps
echo.
echo ============================================================
echo   Installing required packages into virtual environment...
echo ============================================================
echo.

if exist "%USERPROFILE%\.local\bin\uv.exe" (
    "%USERPROFILE%\.local\bin\uv.exe" pip install -r requirements.txt
) else if exist .venv\Scripts\pip.exe (
    .venv\Scripts\pip.exe install -r requirements.txt
) else (
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Setup complete! Run the app with:
echo   run_app.bat
echo ============================================================
pause
