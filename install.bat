@echo off

:: ============================================================
::  Wizard101 Companion -- Setup / Installer
::  Run this ONCE after cloning or downloading the project.
:: ============================================================

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
cd /d "%APP_DIR%"

echo(
echo  ================================================================
echo   Wizard101 Companion  Setup
echo  ================================================================
echo(

:: ---- 1. Locate Python ----------------------------------------
set "PYTHON="

where python >nul 2>&1
if %errorlevel%==0 set "PYTHON=python" & goto :check_version

where python3 >nul 2>&1
if %errorlevel%==0 set "PYTHON=python3" & goto :check_version

echo  [ERROR] Python was not found.
echo  Install Python 3.12 from https://www.python.org
echo  Tick "Add Python to PATH" during installation, then rerun.
echo(
pause
exit /b 1

:check_version
for /f "tokens=2 delims= " %%V in ('"%PYTHON%" --version 2^>^&1') do set "PY_VER=%%V"

:: Extract major.minor without EnableDelayedExpansion
for /f "tokens=1,2 delims=." %%A in ("%PY_VER%") do (
    set "PY_MAJOR=%%A"
    set "PY_MINOR=%%B"
)

:: Use goto-based version check (no delayed expansion needed)
if "%PY_MAJOR%" LSS "3" goto :version_fail
if "%PY_MAJOR%"=="3" if "%PY_MINOR%" LSS "10" goto :version_fail
if "%PY_MAJOR%"=="3" if "%PY_MINOR%" GTR "12" goto :version_warn
goto :version_ok

:version_warn
echo  [WARN] Python %PY_VER% detected.
echo  PyTorch only supports Python 3.10 to 3.12. OCR may be disabled.
echo  Recommended: install Python 3.12 from https://www.python.org
echo(
timeout /t 5 >nul
goto :version_ok

:version_fail
echo  [ERROR] Python %PY_VER% detected. Python 3.10 or newer is required.
pause
exit /b 1

:version_ok
echo  [OK] Python %PY_VER% found.
echo(

:: ---- 2. Create / reuse virtual environment -------------------
if exist "%APP_DIR%\venv\Scripts\activate.bat" (
    echo  [OK] Virtual environment already exists.
    goto :venv_ready
)

echo  Creating virtual environment...
"%PYTHON%" -m venv "%APP_DIR%\venv"
if %errorlevel% neq 0 (
    echo  [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
echo  [OK] Virtual environment created.

:venv_ready
echo(
call "%APP_DIR%\venv\Scripts\activate.bat"

:: Use "python -m pip" instead of calling pip.exe directly.
:: This avoids cmd.exe choking on dots in the expanded pip.exe
:: path when the working directory contains spaces.
set "VPYTHON=%APP_DIR%\venv\Scripts\python.exe"
set "PIP="%VPYTHON%" -m pip"

echo  [OK] Virtual environment activated.
echo(

:: ---- 3. Upgrade pip ------------------------------------------
echo  Upgrading pip...
%PIP% install --upgrade pip --quiet
echo  [OK] pip upgraded.
echo(

:: ---- 4. Core GUI + scraping ----------------------------------
echo  Installing core dependencies...
%PIP% install PyQt5 requests beautifulsoup4 lxml cloudscraper --quiet
if %errorlevel% neq 0 goto :core_fail
echo  [OK] PyQt5, requests, beautifulsoup4, lxml, cloudscraper installed.
echo(
goto :install_keyboard

:core_fail
echo  [ERROR] Core dependency installation failed.
pause
exit /b 1

:: ---- 5. keyboard (global hotkeys) ---------------------------
:install_keyboard
echo  Installing keyboard...
%PIP% install keyboard --quiet
if %errorlevel% neq 0 goto :keyboard_warn
echo  [OK] keyboard installed.
echo(
goto :install_pillow

:keyboard_warn
echo  [WARN] keyboard failed. Hotkeys will only work when app is focused.
echo(

:: ---- 6. Pillow (screenshots for OCR) ------------------------
:install_pillow
echo  Installing Pillow...
%PIP% install Pillow --quiet
if %errorlevel% neq 0 goto :pillow_warn
echo  [OK] Pillow installed.
echo(
goto :install_torch

:pillow_warn
echo  [WARN] Pillow failed.
echo(

:: ---- 7. PyTorch 2.6.0 CPU -----------------------------------
:install_torch
echo  Installing PyTorch 2.6.0 CPU build...
echo  (Pinned to 2.6.0 - newer builds have a DLL init bug on Windows)
echo(
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"

%PIP% install "torch==2.6.0" "torchvision==0.21.0" --index-url "%TORCH_INDEX%"
if %errorlevel% neq 0 goto :torch_fallback
echo  [OK] PyTorch 2.6.0 installed.
goto :torch_done

:torch_fallback
echo  [WARN] torch 2.6.0 not found for this Python version.
echo  Trying latest CPU build...
%PIP% install torch torchvision --index-url "%TORCH_INDEX%"
if %errorlevel% neq 0 goto :torch_fail
echo  [OK] PyTorch installed (latest CPU build).
goto :torch_done

:torch_fail
echo  [WARN] PyTorch install failed. OCR will be disabled.

:torch_done
echo(

:: ---- 8. EasyOCR ---------------------------------------------
echo  Installing easyocr...
%PIP% install easyocr
if %errorlevel% neq 0 goto :easyocr_warn
echo  [OK] easyocr installed.
echo(
goto :install_levenshtein

:easyocr_warn
echo  [WARN] easyocr failed. OCR auto-detection will be disabled.
echo(

:: ---- 9. Levenshtein (faster OCR fuzzy matching) -------------
:install_levenshtein
echo  Installing python-Levenshtein (optional)...
%PIP% install python-Levenshtein --quiet
if %errorlevel% neq 0 goto :levenshtein_skip
echo  [OK] python-Levenshtein installed.
echo(
goto :install_git

:levenshtein_skip
echo  [OK] Levenshtein skipped (optional).
echo(

:: ---- 10. MinGit (portable git for updates) ------------------
:install_git
echo  Checking for git...

:: Check system git first
where git >nul 2>&1
if %errorlevel%==0 (
    echo  [OK] git already installed on system PATH.
    echo(
    goto :verify
)

:: Check if we already have local mingit
if exist "%APP_DIR%\mingit\cmd\git.exe" (
    echo  [OK] MinGit already installed locally.
    echo(
    goto :verify
)

echo  git not found — downloading MinGit (portable, ~30 MB)...
echo(

:: Use PowerShell to download MinGit
set "MINGIT_URL=https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/MinGit-2.47.1.2-64-bit.zip"
set "MINGIT_ZIP=%TEMP%\mingit.zip"
set "MINGIT_DIR=%APP_DIR%\mingit"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " ^
    "try { " ^
    "  $ProgressPreference = 'SilentlyContinue'; " ^
    "  Invoke-WebRequest -Uri '%MINGIT_URL%' -OutFile '%MINGIT_ZIP%' -UseBasicParsing; " ^
    "  Write-Host '  Download complete.'; " ^
    "} catch { " ^
    "  Write-Host '  Download failed:' $_.Exception.Message; " ^
    "  exit 1; " ^
    "}"
if %errorlevel% neq 0 goto :git_fail

echo  Extracting MinGit...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Expand-Archive -Path '%MINGIT_ZIP%' -DestinationPath '%MINGIT_DIR%' -Force"
if %errorlevel% neq 0 goto :git_fail

:: Clean up zip
del "%MINGIT_ZIP%" >nul 2>&1

:: Verify it works
"%MINGIT_DIR%\cmd\git.exe" --version >nul 2>&1
if %errorlevel%==0 (
    echo  [OK] MinGit installed to mingit\ folder.
    echo(
    goto :verify
)

:git_fail
echo  [WARN] MinGit download failed. In-app updates will require
echo         manual git installation from https://git-scm.com
echo(

:: ---- 11. Verify ---------------------------------------------
:verify
echo  Verifying installation...
echo(

set "FAIL=0"

"%VPYTHON%" -c "import PyQt5" >nul 2>&1
if %errorlevel%==0 ( echo    PyQt5           [OK] ) else ( echo    PyQt5           [FAIL] & set "FAIL=1" )

"%VPYTHON%" -c "import bs4" >nul 2>&1
if %errorlevel%==0 ( echo    bs4             [OK] ) else ( echo    bs4             [FAIL] & set "FAIL=1" )

"%VPYTHON%" -c "import requests" >nul 2>&1
if %errorlevel%==0 ( echo    requests        [OK] ) else ( echo    requests        [FAIL] & set "FAIL=1" )

"%VPYTHON%" -c "import cloudscraper" >nul 2>&1
if %errorlevel%==0 ( echo    cloudscraper    [OK] ) else ( echo    cloudscraper    [WARN] )

"%VPYTHON%" -c "import keyboard" >nul 2>&1
if %errorlevel%==0 ( echo    keyboard        [OK] ) else ( echo    keyboard        [WARN - hotkeys app-focused only] )

"%VPYTHON%" -c "import PIL" >nul 2>&1
if %errorlevel%==0 ( echo    Pillow          [OK] ) else ( echo    Pillow          [WARN] )

"%VPYTHON%" -c "import torch; torch.zeros(1)" >nul 2>&1
if %errorlevel%==0 ( echo    torch           [OK] ) else ( echo    torch           [WARN - OCR disabled] )

"%VPYTHON%" -c "import easyocr" >nul 2>&1
if %errorlevel%==0 ( echo    easyocr         [OK] ) else ( echo    easyocr         [WARN - OCR disabled] )

"%VPYTHON%" -c "import Levenshtein" >nul 2>&1
if %errorlevel%==0 ( echo    Levenshtein     [OK] ) else ( echo    Levenshtein     [optional, skipped] )

:: Check git (system or local)
set "GIT_STATUS=[NOT FOUND]"
where git >nul 2>&1
if %errorlevel%==0 ( set "GIT_STATUS=[OK]" ) else (
    if exist "%APP_DIR%\mingit\cmd\git.exe" ( set "GIT_STATUS=[OK - MinGit]" )
)
echo    git             %GIT_STATUS%

echo(

if "%FAIL%"=="1" (
    echo  [!] One or more required packages failed. See output above.
    echo(
    pause
    exit /b 1
)

echo  ================================================================
echo   Setup complete!
echo(
echo   To launch the app:    double-click start.bat
echo   For global hotkeys:   right-click start.bat, Run as administrator
echo  ================================================================
echo(
pause
