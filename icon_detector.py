"""
icon_detector.py
═════════════════
Real icon recognition for Wizard101 spell cards via OpenCV template
matching — NOT text OCR. EasyOCR reads text; this module recognizes
the actual icon GRAPHICS printed on a spell card (Charm, Ward, Trap,
Damage Over Time, All Enemies, PvP Only, etc.) by comparing cropped
regions of the card image against a bundled reference library cropped
directly from the in-game Icon Dictionary.

Templates are the icon PRESETS' own images — the picture set with
"Choose Image…" in the preset editor (database_spells.list_icon_presets
supplies each preset's name + image_path). There is no separate
icon_templates/ folder to maintain; one image per preset is the single
source for both the legend thumbnail and visual matching.

Usage:
    from icon_detector import detect_icons
    presets = database_spells.list_icon_presets(conn)   # name + image_path
    results = detect_icons("spell_images/Krampus.png", presets)
    # -> [{"icon": "Damage Over Time", "confidence": 0.81, "box": (x,y,w,h)}, ...]

Approach:
    Multi-scale grayscale template matching (cv2.matchTemplate,
    TM_CCOEFF_NORMED) across 12 scale factors (0.20x-1.15x), with
    cross-icon non-max suppression so overlapping detections of
    different icons at the same image region keep only the
    highest-confidence one.

History — this was previously disabled by default after an early,
under-tested attempt produced consistent false positives. Root-caused
to two real bugs, both fixed:
    1. ~1/3 of the bundled templates were badly cropped (mostly blank
       parchment background with the icon barely visible at an edge),
       diluting the matching signal. Re-extracted all 60 templates
       using automated background-color-distance connected-component
       detection instead of manual pixel-coordinate guessing.
    2. The scale-search range (0.6x-1.3x) was tuned for the old,
       smaller/inconsistent templates. The corrected templates are
       properly large and tight, which means real small badge icons on
       actual cards need much smaller relative scale factors to match
       — the true best match for a held-out test case was at 0.4x,
       completely outside the old search range.

Validated on two independently-seeded synthetic benchmarks (known
icons pasted at varied scale/position onto varied background colors,
ground truth tracked) before re-enabling:
    benchmark 1 (8 cards, 24 icons):  precision=1.00  recall=0.50  F1=0.67
    benchmark 2 (20 cards, 62 icons): precision=0.93  recall=0.68  F1=0.79

UPDATE after real-world use: real spell card art (detailed background
illustration, gradients, overlapping graphics) produced more false
positives than the synthetic benchmark above predicted — synthetic
test cards have flat solid-color backgrounds, which is an easier
matching environment than actual card art. Threshold raised from the
benchmark-optimal 0.8 to 0.88 in response, trading some recall for
fewer wrong claims on real cards. This is a judgment call based on a
small real-world sample, not a re-run benchmark — if you have a
larger labeled set of real cards, re-tuning against that would be far
more trustworthy than this adjustment.

Limitations (still true, still documented honestly):
    This is template matching, not a trained classifier — it will
    miss some real icons (recall ~50-68% in testing) and very
    occasionally claim one that isn't there. It complements, not
    replaces, the text-keyword detection in spell_scraper.py's
    _structure_ocr_text. Results are always shown as a supplementary
    "visual match" signal with confidence, correctable via Edit mode.
"""

import os
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent

# Default match confidence for visual icon detection. 0.85 sits just
# below the older 0.88 "very few false positives" value: low enough to
# still surface clear icon graphics on a card, but high enough to avoid
# the spurious matches a lower bar produced (e.g. the Heal / Drain-Steal
# heart icons latching onto unrelated red/round card art). Every hit is
# still shown with its confidence and is removable. Lower this only if a
# card is clearly missing icons that are visibly present.
DEFAULT_CONFIDENCE = 0.85


_CV2_AVAILABLE = None
def _check_cv2():
    global _CV2_AVAILABLE
    if _CV2_AVAILABLE is None:
        try:
            import cv2  # noqa
            _CV2_AVAILABLE = True
        except ImportError:
            _CV2_AVAILABLE = False
    return _CV2_AVAILABLE


# Templates are now the icon PRESETS' own images (the "Choose Image…"
# picture set in the preset editor) — there is no separate icon_templates
# folder to keep in sync. The cache is keyed by a signature of the preset
# list (id, path, file mtime) so a bulk reparse loads each image once, but
# editing a preset's image is picked up on the next run.
_templates_cache: Optional[List[Dict]] = None
_templates_sig = None


def _presets_signature(presets):
    sig = []
    for p in presets:
        path = p.get("image_path", "") or ""
        try:
            mtime = os.path.getmtime(path) if path and os.path.exists(path) else 0
        except OSError:
            mtime = 0
        sig.append((p.get("id"), p.get("name", ""), path, mtime))
    return tuple(sig)


def _load_templates(presets):
    """
    Load icon-preset images as grayscale numpy arrays for matching.

    `presets` is the list returned by database_spells.list_icon_presets()
    — each entry needs a 'name' and an 'image_path'. Presets without a
    usable image file are skipped (they simply can't be visually matched;
    they still work as text/field-derived icons). Cached by preset
    signature so repeated calls in one reparse don't re-read every file.
    """
    global _templates_cache, _templates_sig
    sig = _presets_signature(presets)
    if _templates_cache is not None and _templates_sig == sig:
        return _templates_cache

    import cv2
    templates = []
    for p in presets:
        path = p.get("image_path", "") or ""
        if not path or not os.path.exists(path):
            continue
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        templates.append({
            "name": p["name"],
            "image": img,
            "h": img.shape[0],
            "w": img.shape[1],
        })
    _templates_cache = templates
    _templates_sig = sig
    logger.info(f"Loaded {len(templates)} preset icon images for detection")
    return templates


def detect_icons(
    card_image_path: str,
    presets: List[Dict],
    confidence_threshold: float = DEFAULT_CONFIDENCE,
    scales: tuple = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50,
                     0.60, 0.70, 0.85, 1.00, 1.15),
) -> List[Dict]:
    """
    Detect which icon-dictionary icons appear on a spell card image.

    Returns a list of dicts: {"icon": str, "confidence": float,
    "box": (x, y, w, h)}, deduplicated so each icon name appears at
    most once (its best/highest-confidence match), sorted by
    confidence descending.

    Returns an empty list (with a logged warning, not a raised
    exception) if OpenCV is unavailable or the image can't be read —
    callers should treat icon detection as optional enrichment, never
    a hard requirement.
    """
    if not _check_cv2():
        logger.debug("opencv-python not installed — skipping icon detection")
        return []

    import cv2
    import numpy as np

    if not Path(card_image_path).exists():
        return []

    card = cv2.imread(str(card_image_path), cv2.IMREAD_GRAYSCALE)
    if card is None:
        return []

    templates = _load_templates(presets or [])
    if not templates:
        return []

    card_h, card_w = card.shape[:2]
    best_per_icon: Dict[str, Dict] = {}

    for tmpl in templates:
        base_h, base_w = tmpl["h"], tmpl["w"]
        for scale in scales:
            tw = max(8, int(base_w * scale))
            th = max(8, int(base_h * scale))
            if tw >= card_w or th >= card_h:
                continue
            resized = cv2.resize(tmpl["image"], (tw, th), interpolation=cv2.INTER_AREA)
            try:
                result = cv2.matchTemplate(card, resized, cv2.TM_CCOEFF_NORMED)
            except cv2.error:
                continue
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val >= confidence_threshold:
                existing = best_per_icon.get(tmpl["name"])
                if existing is None or max_val > existing["confidence"]:
                    best_per_icon[tmpl["name"]] = {
                        "icon": tmpl["name"],
                        "confidence": round(float(max_val), 3),
                        "box": (max_loc[0], max_loc[1], tw, th),
                    }

    candidates = sorted(best_per_icon.values(), key=lambda r: -r["confidence"])

    # Cross-icon non-max suppression: a small card only has a handful of
    # real icons, so if two DIFFERENT icon labels both claim heavily
    # overlapping regions, that's a sign of a spurious secondary match —
    # keep only the higher-confidence one for that region.
    def _iou(box_a, box_b):
        ax, ay, aw, ah = box_a
        bx, by, bw, bh = box_b
        ix0, iy0 = max(ax, bx), max(ay, by)
        ix1, iy1 = min(ax+aw, bx+bw), min(ay+ah, by+bh)
        iw, ih = max(0, ix1-ix0), max(0, iy1-iy0)
        inter = iw * ih
        union = aw*ah + bw*bh - inter
        return inter / union if union > 0 else 0

    accepted: List[Dict] = []
    for cand in candidates:
        # Suppress a lower-confidence match when it substantially overlaps
        # a kept one (two different labels claiming the same spot is a sign
        # of a spurious secondary match).
        if any(_iou(cand["box"], acc["box"]) > 0.40 for acc in accepted):
            continue
        accepted.append(cand)

    results = accepted
    return results


def detect_icons_summary(card_image_path: str, presets: List[Dict],
                         confidence_threshold: float = DEFAULT_CONFIDENCE) -> str:
    """Convenience wrapper: returns a comma-joined string of detected icon names."""
    results = detect_icons(card_image_path, presets, confidence_threshold)
    return ", ".join(r["icon"] for r in results)


def is_available() -> bool:
    """
    Whether visual icon detection can run at all. Now that templates come
    from the icon presets' own images, this only checks that OpenCV is
    installed — whether any preset actually has a usable image is decided
    per-call in detect_icons (which returns [] if none do).
    """
    return _check_cv2()
