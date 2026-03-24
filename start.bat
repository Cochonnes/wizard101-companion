@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  Wizard101 Companion — Launcher
::  Works from any directory the user places the app in.
::  Double-click this file OR run it from a shortcut / terminal.
:: ============================================================

:: Resolve the folder this .bat lives in (handles spaces and
:: UNC paths; strips trailing backslash added by %~dp0).
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

:: Change into the app folder so all relative paths in the
:: Python scripts (database files, logs, assets) resolve correctly.
cd /d "%APP_DIR%"

:: ── Locate Python ────────────────────────────────────────────
:: Prefer the Python that's on PATH; fall back to the embedded
:: python.exe or python3.exe if present inside the app folder.
set "PYTHON="

where python >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON=python"
    goto :found_python
)

where python3 >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON=python3"
    goto :found_python
)

:: Local / bundled python inside the app directory
if exist "%APP_DIR%\python\python.exe" (
    set "PYTHON=%APP_DIR%\python\python.exe"
    goto :found_python
)

echo.
echo  [ERROR] Python was not found on this system.
echo.
echo  Please install Python 3.10 or newer from https://www.python.org
echo  and make sure to tick "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:found_python
:: ── Verify minimum Python version (3.10+) ───────────────────
for /f "tokens=2 delims= " %%V in ('"%PYTHON%" --version 2^>^&1') do set "PY_VER=%%V"
for /f "tokens=1,2 delims=." %%A in ("%PY_VER%") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)
if !PY_MAJOR! LSS 3 goto :version_fail
if !PY_MAJOR!==3 if !PY_MINOR! LSS 10 goto :version_fail
goto :version_ok

:version_fail
echo.
echo  [WARNING] Python %PY_VER% detected — Python 3.10 or newer is recommended.
echo  Some features may not work correctly.
echo.
timeout /t 3 >nul

:version_ok
:: ── Optional: activate a virtual environment if present ──────
if exist "%APP_DIR%\venv\Scripts\activate.bat" (
    call "%APP_DIR%\venv\Scripts\activate.bat"
)

:: ── Launch the app ───────────────────────────────────────────
echo.
echo  Starting Wizard101 Companion…
echo  App directory : %APP_DIR%
echo  Python        : %PYTHON% (%PY_VER%)
echo.

"%PYTHON%" "%APP_DIR%\boss_wiki.py"

:: If the app exits with a non-zero code keep the window open
:: so the user can read any error messages.
if %errorlevel% neq 0 (
    echo.
    echo  [!] The app exited with an error (code %errorlevel%).
    echo      Check boss_wiki.log in the app folder for details.
    echo.
    pause
)

endlocal
exit
