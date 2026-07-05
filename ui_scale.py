"""
ui_scale.py  —  Wizard101 Companion global UI scaling
═════════════════════════════════════════════════════

Scales the whole app — main window, boxes, text and every HUD overlay —
using Qt's global ``QT_SCALE_FACTOR``.

IMPORTANT — scales UP only (100 % … 200 %)
------------------------------------------
Qt's ``QT_SCALE_FACTOR`` reliably scales the UI *up* (factor ≥ 1.0) but
renders BROKEN below 1.0 — labels disappear and cards blow up to giant
empty rectangles.  We therefore hard-floor every value at 1.0.  100 % is
the app's normal, designed size; the slider only goes bigger.

Auto-adjust (no double-counting)
--------------------------------
Auto mode reads the primary monitor's *effective* height via
``GetSystemMetrics`` — the number of logical pixels Qt actually renders
into.  Because that value already reflects the Windows display-scaling
setting, the computed factor can't compound with it:

    * 4K panel, Windows scale 100 %  → 2160 → 150 %  (fixes tiny text)
    * 4K panel, Windows scale 150 %  → 1440 → 100 %  (already comfy)
    * 2560/3440 × 1440   @100 %      → 1440 → 100 %  (baseline, unchanged)
    * 1920×1080          @100 %      → 1080 → 100 %  (floored, unchanged)
    * 1920×1080          @150 %      →  720 → 100 %  (floored, unchanged)

An earlier version read the *native* resolution instead — which does NOT
account for Windows scaling — so on a scaled 4K display it applied 1.5×
on top of the 1.5× Windows was already doing (2.25×), blowing the UI up.
Using the effective height fixes that.

Config is authoritative
-----------------------
``apply_to_environment()`` always sets (or clears) ``QT_SCALE_FACTOR``
from ``ui_scale.json``, so a value inherited from the in-app "Restart"
button can't override the saved configuration.  It MUST run from
``main()`` before ``QApplication`` is created; a change therefore only
takes effect after a restart.
"""

import os
import json

# ── Tunables ────────────────────────────────────────────────────────────
MIN_SCALE = 1.00          # hard floor — below 1.0 Qt renders broken
MAX_SCALE = 2.00          # largest allowed (double size)
STEP = 0.05               # slider / quantisation step (5%)
BASELINE_HEIGHT = 1440    # effective screen height that maps to 100%
DEFAULT_SCALE = 1.00      # "norm" — identical to the current app

_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ui_scale.json"
)

_APPLIED_SCALE = 1.0      # factor pushed to the environment this run
_DETECTED_HEIGHT = None   # cached effective screen height


# ── Clamping ────────────────────────────────────────────────────────────
def clamp_scale(value) -> float:
    """Clamp to [MIN_SCALE, MAX_SCALE] and round to the nearest STEP."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = DEFAULT_SCALE
    v = max(MIN_SCALE, min(MAX_SCALE, v))
    return round(round(v / STEP) * STEP, 2)


# ── Effective-height detection (accounts for Windows scaling) ───────────
def detect_screen_height():
    """Primary-monitor effective (logical) height, or None if unavailable.

    This is the height Qt renders into as a DPI-unaware app, which already
    reflects the Windows display-scaling setting — exactly what we want so
    the scale factor can't double-count.
    """
    global _DETECTED_HEIGHT
    try:
        import ctypes
        SM_CYSCREEN = 1
        h = int(ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN))
        if h > 0:
            _DETECTED_HEIGHT = h
            return h
    except Exception:
        pass
    _DETECTED_HEIGHT = None
    return None


def get_detected_height():
    """Cached effective height; runs detection once if needed."""
    if _DETECTED_HEIGHT is None:
        detect_screen_height()
    return _DETECTED_HEIGHT


# ── Auto scale (up-only) ────────────────────────────────────────────────
def compute_auto_scale(height=None) -> float:
    """Suggested scale for a screen of the given effective height.

    Only ever scales up (floored at 100%) so it can never shrink the UI
    into Qt's broken sub-1.0 range.  Unknown height → 100%.
    """
    if height is None:
        height = get_detected_height()
    if not height or height <= 0:
        return DEFAULT_SCALE
    return clamp_scale(height / float(BASELINE_HEIGHT))


# ── Config persistence ──────────────────────────────────────────────────
def load_config() -> dict:
    """Return {'scale': float, 'manual': bool}.

    manual=False → auto-adjust to the detected screen.
    manual=True  → use the stored scale (user override).
    Missing / corrupt file → auto mode.
    """
    data = {"scale": DEFAULT_SCALE, "manual": False}
    try:
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data["manual"] = bool(saved.get("manual", False))
                if "scale" in saved:
                    data["scale"] = clamp_scale(saved.get("scale"))
    except Exception:
        pass
    return data


def save_config(scale, manual) -> bool:
    """Persist the scale + manual flag. Returns True on success."""
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"scale": clamp_scale(scale), "manual": bool(manual)},
                      f, indent=2)
        return True
    except Exception:
        return False


def get_effective_scale() -> float:
    """The factor that should be active right now."""
    cfg = load_config()
    if cfg["manual"]:
        return clamp_scale(cfg["scale"])
    return compute_auto_scale()


def get_applied_scale() -> float:
    """The factor actually pushed to the environment this run."""
    return _APPLIED_SCALE


# ── The one call main() makes before QApplication ───────────────────────
def apply_to_environment() -> float:
    """Compute the effective scale and export it via ``QT_SCALE_FACTOR``.

    MUST run before ``QApplication`` is created.  ``ui_scale.json`` is
    authoritative — this always sets (>1.0) or clears (==1.0) the variable
    so nothing inherited can override it.
    """
    global _APPLIED_SCALE

    detect_screen_height()

    scale = get_effective_scale()
    _APPLIED_SCALE = scale

    if scale > 1.0 + 1e-3:
        os.environ["QT_SCALE_FACTOR"] = f"{scale:.2f}"
        os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    else:
        # Clean 100% — strip any inherited factor so behaviour is identical
        # to the pre-scaling app.
        os.environ.pop("QT_SCALE_FACTOR", None)
    return scale
