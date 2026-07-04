#!/usr/bin/env bash
# ============================================================
#  Wizard101 Companion — Launcher  (Linux / macOS)
#  Cross-platform counterpart to start.bat.
#      ./start.sh
#  For OS-wide hotkeys on Linux (optional):  sudo ./start.sh
# ============================================================

# Resolve the folder this script lives in and cd into it so all
# relative paths (databases, caches, assets) resolve correctly.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || exit 1

# ── Pick the interpreter ─────────────────────────────────────
# Prefer the project venv; fall back to system python3.
if [ -x "$APP_DIR/venv/bin/python" ]; then
    # shellcheck disable=SC1091
    . "$APP_DIR/venv/bin/activate"
    PYTHON="$APP_DIR/venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    echo
    echo " [ERROR] Python was not found on this system."
    echo " Install Python 3.10+ and run ./install.sh first."
    echo
    exit 1
fi

# ── Verify minimum Python version (3.10+) ───────────────────
PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
maj="${PY_VER%%.*}"; min="${PY_VER##*.}"
if [ "$maj" != "3" ] || [ "$min" -lt 10 ] 2>/dev/null; then
    echo " [WARNING] Python $PY_VER detected — 3.10 or newer is recommended."
    sleep 2
fi

# ── Launch ───────────────────────────────────────────────────
echo
echo " Starting Wizard101 Companion…"
echo " App directory : $APP_DIR"
echo " Python        : $PYTHON ($PY_VER)"
echo

"$PYTHON" "$APP_DIR/boss_wiki.py"
rc=$?

if [ "$rc" -ne 0 ]; then
    echo
    echo " [!] The app exited with an error (code $rc)."
    echo "     Check boss_wiki.log in the app folder for details."
    echo
    # Keep terminal open only when launched by double-click (no parent TTY)
    if [ ! -t 1 ]; then read -r -p " Press Enter to close..." _; fi
fi

exit "$rc"
