"""
OCR Module - Full-Screen Boss Detection
Scans the entire screen for boss names and fuzzy-matches against the local DB.
Forces CPU mode to avoid PyTorch DLL conflicts.

Matching strategy (boss-first, indexed):
  1. PRE-INDEX  — at set_known_names() time, build two structures (runs ONCE):
       • _all_boss_tokens   : flat set of every meaningful word across all boss names
       • _first_word_index  : dict mapping each boss name's first word → [boss names]
       • _name_token_index  : dict mapping boss name → frozenset of its tokens
  2. FAST GATE  — each scan, extract OCR word tokens and intersect with
       _all_boss_tokens.  If zero overlap, skip matching entirely (no boss on screen).
  3. CANDIDATE FILTER — only score boss names whose first word appears in OCR tokens,
       dramatically shrinking the match space.
  4. LEVENSHTEIN SCORING — uses Levenshtein.ratio() for each (ocr_candidate, boss_name)
       pair: battle-tested, handles OCR typos ("Malista1re" → "Malistaire") correctly.
  5. WORD-WINDOW SEARCH — still extracts contiguous sub-spans from long OCR lines
       ("Defeat Hanzo YellowEye in Ruined Alcazar" → "Hanzo YellowEye") but only
       runs on candidates that already passed the fast gate.
  6. Reset last_detected after several empty scans so re-entering same fight still fires.
"""

import re
import logging
from collections import defaultdict
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

OCR_AVAILABLE = False
_OCR_LOAD_ERROR: str = ""
try:
    import numpy as np
    from PIL import ImageGrab
    import easyocr
    OCR_AVAILABLE = True
except ImportError as _e:
    _OCR_LOAD_ERROR = f"Missing package: {_e}"
except OSError as _e:
    # PyTorch DLL initialisation failure on Python 3.13 / after a failed
    # PyInstaller build — treat as "not installed" so the app still starts.
    _OCR_LOAD_ERROR = f"DLL load error (torch/easyocr): {_e}"
    logger.warning("OCR disabled — DLL load failed: %s", _e)
except Exception as _e:
    _OCR_LOAD_ERROR = f"Unexpected import error: {_e}"
    logger.warning("OCR disabled — unexpected error during import: %s", _e)

# Levenshtein is optional — fall back to the legacy scorer if not installed
_LEV_AVAILABLE = False
try:
    from Levenshtein import ratio as _lev_ratio
    _LEV_AVAILABLE = True
except ImportError:
    pass


# ─── NOISE FILTERS ─────────────────────────────────────────────
# Applied to WHOLE OCR lines to skip obvious junk.
# Keep conservative — long UI sentences like "Defeat X in Y" must NOT be
# discarded here because the word-window step may still yield a boss name.
NOISE_PATTERNS = [
    re.compile(r'^.{0,2}$'),                                          # too short
    re.compile(r'^[^a-zA-Z]*$'),                                      # no letters
    re.compile(r'^\d[\d\s]+$'),                                       # mostly numbers
    re.compile(r'[{}\[\]<>|\\@#]'),                                   # markup chars
    re.compile(r'^(the|and|for|with|from|this|that|you|all)$', re.I),# stop words alone
    re.compile(r'(spellbook|backpack|friends|options|settings)', re.I),
    re.compile(r'\b(health|mana|energy|crowns|arena|badge)\b', re.I),
    re.compile(r'\b(flee|pvp|duel|pip\b)', re.I),
]

# Words excluded when building sub-window candidates from a long OCR sentence
_STRIP_WORDS = frozenset([
    "defeat", "find", "collect", "talk", "explore", "interact",
    "in", "at", "the", "a", "an", "of", "to", "and", "or",
    "from", "with", "near", "for", "on", "by", "ruined", "alcazar",
    "instance", "dungeon", "boss", "realm",
])

# Minimum token length considered meaningful for indexing / matching
_MIN_TOKEN_LEN = 3

MIN_CONFIDENCE   = 0.50
MIN_NAME_LENGTH  = 3
MATCH_THRESHOLD  = 0.60
RESET_AFTER_EMPTY = 3   # consecutive empty scans before resetting last_detected

# OCR matching modes
OCR_MODE_DYNAMIC = "dynamic"   # fuzzy / Levenshtein matching (original)
OCR_MODE_STRICT  = "strict"    # exact 1:1 case-insensitive match only


# ─── HELPERS ───────────────────────────────────────────────────

def is_noise(text: str) -> bool:
    return any(p.search(text) for p in NOISE_PATTERNS)


def _normalize_ocr(text: str) -> str:
    """
    Normalise common OCR character substitutions before matching.
    Digit-to-letter replacements only — keeps digits that appear at the
    start/end of a token (e.g. "5000" health values) unchanged because
    those won't survive the MIN_NAME_LENGTH gate anyway.

    Common EasyOCR confusions:
        0 → o,  1 → l (or i),  3 → e,  5 → s,  6 → g,  8 → b
    Applied at the word level: only words that are primarily alphabetic
    (contain at least one letter) get normalised.
    """
    _DIGIT_MAP = str.maketrans("013568", "oleseb")

    words = text.split()
    result = []
    for w in words:
        if any(c.isalpha() for c in w):
            result.append(w.translate(_DIGIT_MAP))
        else:
            result.append(w)
    return " ".join(result)


def _meaningful_tokens(text: str) -> Set[str]:
    """Return the set of lowercase words >= _MIN_TOKEN_LEN from `text`."""
    return {w.lower() for w in text.split() if len(w) >= _MIN_TOKEN_LEN}


def _word_windows(text: str) -> List[str]:
    """
    Generate all contiguous word sub-spans from `text`.
    e.g. "Defeat Hanzo YellowEye in" →
        ["Hanzo", "YellowEye", "Hanzo YellowEye", ...]
    Skips windows composed entirely of strip-words.
    """
    words = text.split()
    results = []
    n = len(words)
    for size in range(1, n + 1):
        for start in range(n - size + 1):
            window = words[start : start + size]
            if all(w.lower() in _STRIP_WORDS for w in window):
                continue
            results.append(" ".join(window))
    return results


# ─── SCORING ───────────────────────────────────────────────────

def _score_match(detected_clean: str, name_lower: str) -> float:
    """
    Return a 0..1 similarity score.

    If python-levenshtein is installed, uses Levenshtein.ratio() which is
    fast, handles OCR character substitutions well, and is properly normalised.

    Falls back to the original heuristic (exact > substring > word-overlap)
    when the library is absent so the module still works without it.
    """
    if not detected_clean or not name_lower:
        return 0.0

    # ── Fast exact check (no library needed) ────────────────────
    if detected_clean == name_lower:
        return 1.0

    if _LEV_AVAILABLE:
        return _lev_ratio(detected_clean, name_lower)

    # ── Legacy fallback ─────────────────────────────────────────
    # Substring
    if name_lower in detected_clean or detected_clean in name_lower:
        return min(len(detected_clean), len(name_lower)) / \
               max(len(detected_clean), len(name_lower))

    # Word overlap
    d_words = set(w for w in detected_clean.split() if len(w) > 2)
    n_words = set(w for w in name_lower.split() if len(w) > 2)
    if d_words and n_words:
        overlap = d_words & n_words
        if overlap:
            coverage = len(overlap) / len(n_words)
            balance  = len(overlap) / max(len(d_words), len(n_words))
            return max(coverage * 0.85, balance)

    return 0.0


# ─── BOSS-FIRST INDEX ──────────────────────────────────────────

class BossNameIndex:
    """
    Pre-computed index over all known boss names.  Built once at startup
    (or whenever the name list changes) so every scan is O(index lookups)
    instead of O(N × M × K).

    Attributes
    ----------
    _all_tokens      : flat set of every meaningful word across all boss names.
                       Used for the O(1) fast gate: if OCR words ∩ _all_tokens = ∅,
                       skip matching entirely.
    _first_word_idx  : {first_word_lower → [BossName, ...]}
                       Limits full fuzzy scoring to bosses whose first word appeared
                       in the OCR output.
    _name_tokens     : {BossName → frozenset of its meaningful tokens}
                       Available for future token-level pre-filtering.
    """

    def __init__(self, names: List[str]):
        self._all_tokens:    Set[str]                    = set()
        self._first_word_idx: Dict[str, List[str]]       = defaultdict(list)
        self._name_tokens:   Dict[str, FrozenSet[str]]   = {}
        self._all_names:     List[str]                   = list(names)

        for name in names:
            tokens = _meaningful_tokens(name)
            self._name_tokens[name] = frozenset(tokens)
            self._all_tokens.update(tokens)

            words = name.split()
            if words:
                first = words[0].lower()
                self._first_word_idx[first].append(name)

        logger.info(
            f"BossNameIndex built: {len(names)} names, "
            f"{len(self._all_tokens)} unique tokens, "
            f"{len(self._first_word_idx)} first-word buckets"
        )

    # ── Public API ─────────────────────────────────────────────

    def has_any_overlap(self, ocr_tokens: Set[str]) -> bool:
        """Fast gate: True if OCR output shares at least one token with any boss name."""
        return bool(ocr_tokens & self._all_tokens)

    def candidates_for(self, ocr_tokens: Set[str]) -> List[str]:
        """
        Return the subset of boss names worth full scoring.
        A boss qualifies if its first word appears in the OCR token set.
        Falls back to ALL names only when no first-word match is found
        (very rare — guards against multi-word first tokens or OCR splitting).
        """
        candidates: List[str] = []
        seen: Set[str] = set()
        for tok in ocr_tokens:
            for name in self._first_word_idx.get(tok, []):
                if name not in seen:
                    seen.add(name)
                    candidates.append(name)

        if not candidates:
            # Fallback: score all names (original behaviour)
            candidates = self._all_names

        return candidates

    @property
    def all_names(self) -> List[str]:
        return self._all_names


# ─── MATCHING ──────────────────────────────────────────────────

def fuzzy_match_boss(
    detected: str,
    index: BossNameIndex,
    threshold: float = MATCH_THRESHOLD,
) -> Optional[str]:
    """
    Boss-first fuzzy match.

    Steps
    -----
    1. Tokenise the OCR string and run the fast gate against the index.
    2. Build word-window candidates from the OCR string.
    3. Score only the boss names whose first word appeared in the OCR tokens.
    4. Return the best match above threshold, or None.
    """
    # Normalise OCR digit/letter confusions before anything else
    detected_norm  = _normalize_ocr(detected)
    detected_clean = detected_norm.lower().strip()
    if not detected_clean or len(detected_clean) < MIN_NAME_LENGTH:
        return None

    ocr_tokens = _meaningful_tokens(detected_clean)

    # ── Fast gate ───────────────────────────────────────────────
    if not index.has_any_overlap(ocr_tokens):
        return None

    # ── Build OCR candidates (full string + word windows) ───────
    ocr_candidates = [detected_clean] + [w.lower() for w in _word_windows(detected_norm)]

    # ── Restrict boss names to first-word matches ────────────────
    boss_candidates = index.candidates_for(ocr_tokens)

    best_match = None
    best_score = 0.0

    for name in boss_candidates:
        name_lower = name.lower()
        for cand in ocr_candidates:
            if len(cand) < MIN_NAME_LENGTH:
                continue
            score = _score_match(cand, name_lower)
            if score > best_score:
                best_score = score
                best_match = name
                if score == 1.0:
                    return name   # perfect — short-circuit

    return best_match if best_score >= threshold else None


def strict_match_boss(
    detected: str,
    index: BossNameIndex,
) -> Optional[str]:
    """
    Strict 1:1 case-insensitive match.
    Returns a boss name ONLY if the OCR text exactly matches a known boss name
    (after lowercasing both sides), or if a contiguous word-window from the OCR
    text exactly matches.  No fuzzy scoring at all.
    """
    detected_clean = detected.strip()
    if not detected_clean or len(detected_clean) < MIN_NAME_LENGTH:
        return None

    detected_lower = detected_clean.lower()

    # Build a lowercase → original-case lookup from the index
    for name in index.all_names:
        if detected_lower == name.lower():
            return name

    # Also try word-windows (e.g. "Defeat Malistaire" → "Malistaire")
    for window in _word_windows(detected_clean):
        window_lower = window.lower()
        if len(window_lower) < MIN_NAME_LENGTH:
            continue
        for name in index.all_names:
            if window_lower == name.lower():
                return name

    return None


# ═══════════════════════════════════════════════════════════════
# OCR SCANNER THREAD
# ═══════════════════════════════════════════════════════════════

class OCRScanner(QThread):
    """
    Background thread: grabs full screen every N seconds, runs EasyOCR,
    and fuzzy-matches all text regions against known boss names.

    Signals
    -------
    boss_detected(str)      first matched boss (legacy compat — also emitted)
    bosses_detected(list)   all matched bosses this scan
    status_update(str)      human-readable status line
    debug_text(str)         raw OCR output for the debug tab
    """

    boss_detected   = pyqtSignal(str)
    bosses_detected = pyqtSignal(list)
    status_update   = pyqtSignal(str)
    debug_text      = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running    = False
        self.reader     = None
        self._index:    Optional[BossNameIndex] = None
        self.last_detected_set: frozenset = frozenset()
        self._empty_scan_count: int = 0
        self.scan_interval = 3    # seconds between scans
        self.scan_region   = None # None = full screen
        self.ocr_mode      = OCR_MODE_DYNAMIC  # "dynamic" or "strict"

    def set_known_names(self, names: List[str]):
        """Build the boss-first index from the provided name list."""
        self._index = BossNameIndex(names)
        lev_status = "levenshtein" if _LEV_AVAILABLE else "legacy scorer"
        logger.info(f"Boss OCR: index ready ({len(names)} names, scorer={lev_status})")

    def set_scan_region(self, x: int, y: int, w: int, h: int):
        if x == 0 and y == 0 and w == 0 and h == 0:
            self.scan_region = None
        else:
            self.scan_region = (x, y, x + w, y + h)

    def set_mode(self, mode: str):
        """Switch between 'dynamic' (fuzzy) and 'strict' (exact) matching."""
        if mode in (OCR_MODE_DYNAMIC, OCR_MODE_STRICT):
            self.ocr_mode = mode
            logger.info(f"Boss OCR mode set to: {mode}")
        else:
            logger.warning(f"Unknown OCR mode '{mode}', keeping '{self.ocr_mode}'")

    def init_reader(self) -> bool:
        if not OCR_AVAILABLE:
            self.status_update.emit(
                "OCR libraries not installed (pip install easyocr pillow numpy)"
            )
            return False
        try:
            self.status_update.emit("Loading OCR model… (first run may take ~30 s)")
            self.reader = easyocr.Reader(['en'], gpu=False, verbose=False)
            self.status_update.emit("Boss OCR ready")
            return True
        except Exception as e:
            self.status_update.emit(f"OCR init failed: {e}")
            logger.error(f"OCR init error: {e}")
            return False

    def run(self):
        if not self.reader and not self.init_reader():
            return
        self.running = True
        self._empty_scan_count = 0
        self.status_update.emit("Boss OCR scanning…")
        while self.running:
            self._scan_once()
            self.msleep(int(self.scan_interval * 1000))

    def _scan_once(self):
        if self._index is None:
            logger.debug("Boss OCR: no index yet, skipping scan")
            return

        try:
            # ── Screen capture ───────────────────────────────────
            if self.scan_region:
                screenshot = ImageGrab.grab(bbox=self.scan_region)
            else:
                screenshot = ImageGrab.grab()

            img_array = np.array(screenshot)
            results   = self.reader.readtext(img_array, detail=1)

            debug_lines: List[str] = []
            raw_candidates: List[Tuple[str, float]] = []

            # ── Collect all OCR tokens for the fast gate ─────────
            all_ocr_tokens: Set[str] = set()

            for _box, text, confidence in results:
                text = text.strip()
                debug_lines.append(f"[{confidence:.2f}] {text}")
                if confidence < MIN_CONFIDENCE:
                    continue
                if len(text) < MIN_NAME_LENGTH:
                    continue
                if is_noise(text):
                    continue
                raw_candidates.append((text, confidence))
                all_ocr_tokens.update(_meaningful_tokens(_normalize_ocr(text)))

            self.debug_text.emit(
                '\n'.join(debug_lines) if debug_lines else '(no text detected)'
            )

            # ── Fast gate: any boss token on screen at all? ───────
            if not self._index.has_any_overlap(all_ocr_tokens):
                self._empty_scan_count += 1
                if self._empty_scan_count >= RESET_AFTER_EMPTY:
                    self.last_detected_set = frozenset()
                    self._empty_scan_count = 0
                return   # nothing to match — skip all fuzzy work

            # Sort highest confidence first
            raw_candidates.sort(key=lambda x: x[1], reverse=True)

            # ── Match against indexed boss names ──────────────────
            matched_bosses: List[str] = []
            matched_set:    set       = set()

            for ocr_text, _conf in raw_candidates:
                if self.ocr_mode == OCR_MODE_STRICT:
                    matched = strict_match_boss(ocr_text, self._index)
                else:
                    matched = fuzzy_match_boss(ocr_text, self._index)
                if matched and matched not in matched_set:
                    matched_set.add(matched)
                    matched_bosses.append(matched)
                    if len(matched_bosses) >= 3:   # cap at 3 to avoid noise
                        break

            # ── Emit / reset ─────────────────────────────────────
            if matched_bosses:
                self._empty_scan_count = 0
                frozen = frozenset(matched_bosses)
                if frozen != self.last_detected_set:
                    self.last_detected_set = frozen
                    self.bosses_detected.emit(matched_bosses)
                    self.boss_detected.emit(matched_bosses[0])
                    self.status_update.emit(
                        "Detected: " + " + ".join(matched_bosses)
                    )
            else:
                self._empty_scan_count += 1
                if self._empty_scan_count >= RESET_AFTER_EMPTY:
                    self.last_detected_set = frozenset()
                    self._empty_scan_count = 0

        except Exception as e:
            logger.debug(f"Boss OCR scan error: {e}")

    def stop(self):
        self.running = False
        self.status_update.emit("Boss OCR stopped")
