#!/usr/bin/env bash
# ============================================================
#  Wizard101 Companion -- Setup / Installer  (Linux / macOS)
#  Run this ONCE after cloning or downloading the project:
#      chmod +x install.sh && ./install.sh
#  This is the cross-platform counterpart to install.bat.
# ============================================================

# Resolve the directory this script lives in (handles symlinks & spaces)
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

echo
echo " ================================================================"
echo "  Wizard101 Companion  Setup"
echo " ================================================================"
echo

# ---- 1. Locate Python 3.10+ ----------------------------------
PYTHON=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
        maj="${ver%%.*}"; min="${ver##*.}"
        if [ "$maj" = "3" ] && [ "$min" -ge 10 ] 2>/dev/null; then
            PYTHON="$cand"; PY_VER="$ver"; break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo " [ERROR] Python 3.10 or newer was not found."
    echo " Install it from your package manager, e.g.:"
    echo "   Debian/Ubuntu : sudo apt install python3 python3-venv python3-pip"
    echo "   Fedora        : sudo dnf install python3 python3-pip"
    echo "   macOS (brew)  : brew install python@3.12"
    exit 1
fi
echo " [OK] Python $PY_VER found ($PYTHON)."
echo

# ---- 2. Create / reuse virtual environment -------------------
if [ -f "$APP_DIR/venv/bin/activate" ]; then
    echo " [OK] Virtual environment already exists."
else
    echo " Creating virtual environment..."
    if ! "$PYTHON" -m venv "$APP_DIR/venv"; then
        echo " [ERROR] Failed to create virtual environment."
        echo " On Debian/Ubuntu you may need: sudo apt install python3-venv"
        exit 1
    fi
    echo " [OK] Virtual environment created."
fi
echo

# shellcheck disable=SC1091
. "$APP_DIR/venv/bin/activate"
VPYTHON="$APP_DIR/venv/bin/python"
PIP="$VPYTHON -m pip"
echo " [OK] Virtual environment activated."
echo

# ---- 3. Upgrade pip ------------------------------------------
echo " Upgrading pip..."
$PIP install --upgrade pip --quiet
echo " [OK] pip upgraded."
echo

# small helper: install and report, non-fatal on failure
pip_soft() {   # $1 = friendly name, rest = pip args
    local name="$1"; shift
    echo " Installing $name..."
    if $PIP install "$@" --quiet; then
        echo " [OK] $name installed."
    else
        echo " [WARN] $name failed — continuing (feature may be disabled)."
    fi
    echo
}

pip_hard() {   # abort on failure
    local name="$1"; shift
    echo " Installing $name..."
    if ! $PIP install "$@" --quiet; then
        echo " [ERROR] $name installation failed."
        exit 1
    fi
    echo " [OK] $name installed."
    echo
}

# ---- 4. Core GUI + scraping ----------------------------------
pip_hard "core (PyQt5, requests, bs4, lxml, cloudscraper, nodriver)" \
    PyQt5 requests beautifulsoup4 lxml cloudscraper nodriver

# ---- 5. Global hotkeys ---------------------------------------
# NOTE: on Linux the 'keyboard' library reads /dev/input and needs root
# to register OS-wide hotkeys. Without root the app falls back to Qt
# shortcuts that fire only when the app window is focused.
pip_soft "keyboard (global hotkeys)" keyboard

# ---- 6. Screen capture + OCR ---------------------------------
pip_soft "Pillow (screenshot fallback)" Pillow
pip_soft "mss (cross-platform screen capture)" mss

echo " Installing PyTorch (CPU build)..."
if $PIP install torch torchvision --index-url https://download.pytorch.org/whl/cpu; then
    echo " [OK] PyTorch installed."
else
    echo " [WARN] PyTorch failed — trying default PyPI build..."
    $PIP install torch torchvision --quiet \
        && echo " [OK] PyTorch installed (PyPI)." \
        || echo " [WARN] PyTorch install failed. OCR will be disabled."
fi
echo

pip_soft "easyocr" easyocr
pip_soft "python-Levenshtein (optional, faster OCR matching)" python-Levenshtein
pip_soft "opencv-python-headless (icon matching)" opencv-python-headless

# ---- 7. git (for in-app updates) -----------------------------
echo " Checking for git..."
if command -v git >/dev/null 2>&1; then
    echo " [OK] git found on PATH."
else
    echo " [WARN] git not found. In-app updates need it. Install with:"
    echo "   Debian/Ubuntu : sudo apt install git"
    echo "   Fedora        : sudo dnf install git"
    echo "   macOS (brew)  : brew install git"
fi
echo

# ---- 8. Chrome / Chromium (for nodriver) ---------------------
echo " Checking for Chrome / Chromium..."
CHROME_FOUND=""
for c in google-chrome google-chrome-stable chromium chromium-browser chrome; do
    if command -v "$c" >/dev/null 2>&1; then CHROME_FOUND="$c"; break; fi
done
if [ -n "$CHROME_FOUND" ]; then
    echo " [OK] Browser found: $CHROME_FOUND"
else
    echo " [WARN] No Chrome/Chromium found. The wiki scrapers need one:"
    echo "   Debian/Ubuntu : sudo apt install chromium-browser"
    echo "   Fedora        : sudo dnf install chromium"
    echo "   macOS         : install Google Chrome from google.com/chrome"
fi
echo

# ---- 9. Verify -----------------------------------------------
echo " Verifying installation..."
echo
FAIL=0
check() {  # $1 label  $2 python-import-expr  $3 required(1)/optional(0)
    if "$VPYTHON" -c "$2" >/dev/null 2>&1; then
        printf "    %-16s [OK]\n" "$1"
    elif [ "$3" = "1" ]; then
        printf "    %-16s [FAIL]\n" "$1"; FAIL=1
    else
        printf "    %-16s [WARN - feature disabled]\n" "$1"
    fi
}
check "PyQt5"        "import PyQt5"                 1
check "bs4"          "import bs4"                   1
check "requests"     "import requests"             1
check "nodriver"     "import nodriver"             1
check "cloudscraper" "import cloudscraper"         0
check "keyboard"     "import keyboard"             0
check "Pillow"       "import PIL"                   0
check "mss"          "import mss"                   0
check "torch"        "import torch; torch.zeros(1)" 0
check "easyocr"      "import easyocr"              0
check "Levenshtein"  "import Levenshtein"          0
check "cv2"          "import cv2"                   0
echo

if [ "$FAIL" = "1" ]; then
    echo " [!] One or more required packages failed. See output above."
    exit 1
fi

echo " ================================================================"
echo "  Setup complete!"
echo
echo "   To launch the app:   ./start.sh"
echo "   Global hotkeys:      sudo ./start.sh   (Linux only, optional)"
echo " ================================================================"
echo
