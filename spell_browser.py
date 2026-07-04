"""
spell_browser.py  v3
════════════════════
Spell Browser — full-feature UI widget for the Wizard101 Companion.

v3 additions:
  • +/- zoom buttons (main grid card size)
  • Right-click on tile → delete single / delete school / delete all
  • Delete All button in header
  • Tier variants excluded from main grid; shown in detail popup in order
  • Click image in detail → full-screen enlarge dialog
  • Edit mode in detail popup (fix OCR errors)
  • OCR section replaced with structured Spell Summary + interpretation
  • 🖼 Get Images button (triggers --images scraper pass)
"""

import os
import sys
import re
from pathlib import Path
from typing import Optional, List, Dict

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QGridLayout, QDialog,
    QTextBrowser, QComboBox, QProgressBar, QTextEdit,
    QSizePolicy, QApplication, QMessageBox, QMenu, QSplitter,
    QSpinBox, QCheckBox, QCompleter, QFileDialog
)
from PyQt5.QtCore import Qt, QProcess, QTimer, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QPixmap, QColor, QPainter, QIcon, QPixmapCache

import database_spells as ds
import logging

# Cloudflare-challenge alert (shared marker + GUI sound player)
try:
    import cf_alert
except Exception:
    cf_alert = None


def _play_cf_alert():
    """Play the Cloudflare-challenge alert sound (best-effort, never raises)."""
    try:
        from hud_overlays import cf_alert_player
        cf_alert_player.play()
    except Exception:
        pass

logger = logging.getLogger(__name__)

APP_DIR      = Path(__file__).parent
IMG_DIR      = APP_DIR / "spell_images"
CACHE_DIR    = APP_DIR / "spell_cache"
# Fallback card images for fusion reagents/results not fetched as their own
# spell (downloaded by spell_scraper into this folder).
FUSION_IMG_DIR = APP_DIR / "spell_images_fusion"
SPELL_SCRAPER = str(APP_DIR / "spell_scraper.py")

SCHOOL_COLORS = ds.SCHOOL_COLORS
SCHOOL_ORDER  = ["All"] + ds.SPELL_SCHOOLS

# Base tile dimensions (multiplied by zoom)
_BASE_CARD_W  = 100
_BASE_CARD_H  = 120
_BASE_IMG_W   = 90
_BASE_IMG_H   = 90
GRID_COLS     = 6

ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.5, 2.5, 0.25

# ── Filter-panel widget styling ──
_FILTER_COMBO_SS = (
    "QComboBox{background:#0f1830;color:#d8d8d8;border:1px solid #2a3a5a;"
    "border-radius:5px;padding:3px 6px;font-size:11px;}"
    "QComboBox::drop-down{border:none;width:16px;}"
    "QComboBox QAbstractItemView{background:#0f1830;color:#d8d8d8;"
    "selection-background-color:#1f3460;}"
)
_FILTER_CB_SS = (
    "QCheckBox{color:#c0c0c0;font-size:11px;spacing:6px;}"
    "QCheckBox::indicator{width:14px;height:14px;}"
)
_FILTER_SPIN_SS = (
    "QSpinBox{background:#0f1830;color:#d8d8d8;border:1px solid #2a3a5a;"
    "border-radius:5px;padding:2px 4px;font-size:11px;}"
)


def _full_hex(c: str) -> str:
    """Expand '#RGB' → '#RRGGBB' so alpha suffixes stay valid."""
    if c.startswith("#") and len(c) == 4:
        return "#" + "".join(x * 2 for x in c[1:])
    return c


def _sc(school: str) -> str:
    return _full_hex(SCHOOL_COLORS.get(school, "#888888"))


def _rgba(hex_color: str, alpha_hex: str) -> str:
    """
    Convert #RRGGBB + 2-char hex alpha string to rgba(r,g,b,a).
    Qt's CSS parser does NOT support #RRGGBBAA; use rgba() instead.
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{int(alpha_hex, 16)})"


def _placeholder(school: str, pip: str, w: int, h: int) -> QPixmap:
    pix = QPixmap(w, h)
    col = QColor(_sc(school))
    col.setAlpha(130)
    pix.fill(col)
    p = QPainter(pix)
    p.setPen(QColor("#e0e0e0"))
    p.setFont(QFont("Segoe UI", max(8, w // 10), QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, pip or "?")
    p.end()
    return pix


# ── Icon / effect interpretation (from in-game icon dictionary) ────────
def interpret_spell(spell: dict) -> str:
    """
    Build a human-readable effect summary from spell data + structured OCR.

    OCR cannot recognize icon graphics directly (it reads text, not
    shapes) — but the readable text printed alongside icons on the card
    ("Gambit", "and ... (N)", "then", "All Enemies", etc.) is what this
    function and the upstream OCR structuring (spell_scraper.py's
    _structure_ocr_text) key on. Icon names below reference the in-game
    Icon Dictionary (Combat / Loot pages).
    """
    school     = spell.get("school", "")
    spell_type = (spell.get("spell_type") or "").lower()
    pip        = spell.get("pip_cost", "0")
    spip       = spell.get("school_pip_cost", 0)
    acc        = spell.get("accuracy", 0)
    pvp        = spell.get("pvp")
    desc       = spell.get("description", "")
    ocr_raw    = spell.get("ocr_raw", "")
    ocr_dmg    = spell.get("ocr_damage", "")
    ocr_fx     = spell.get("ocr_effect", "")
    ocr_dot    = spell.get("ocr_dot_damage", "")
    ocr_rounds = spell.get("ocr_dot_rounds", "")
    ocr_gambit = spell.get("ocr_gambit", "")
    ocr_kw_str = spell.get("ocr_keywords", "")
    ocr_heal        = spell.get("ocr_heal", "")
    ocr_heal_rounds = spell.get("ocr_heal_rounds", "")
    ocr_divided     = spell.get("ocr_divided", "")
    ocr_conditional = spell.get("ocr_conditional", "")
    ocr_clear       = spell.get("ocr_clear_effect", "")
    ocr_uncertain   = bool(spell.get("ocr_uncertain"))

    lines = []
    # ── Header line ──────────────────────────────────────────────────
    pip_str = str(pip)
    if spip:
        pip_str += f" + {spip} {school}"
    pvp_level = spell.get("pvp_level", "") or ""
    pvp_str = f"⚔ PvP Lvl {pvp_level}" if pvp_level else "⚔ PvP Lvl —"
    lines.append(
        f"⚡ {pip_str} pip  •  🎯 {acc}%  •  🏫 {school}  •  "
        f"📌 {spell_type.title() or '—'}  •  {pvp_str}"
    )

    # ── Uncertainty warning — shown first, impossible to miss ───────────
    if ocr_uncertain:
        lines.append("")
        lines.append("⚠️ OCR TEXT LOOKS GARBLED FOR THIS CARD — values below")
        lines.append("   may be incomplete or wrong. Check the raw text at the")
        lines.append("   bottom and correct via ✎ Edit if needed.")

    # ── Description ──────────────────────────────────────────────────
    if desc:
        lines.append("")
        lines.append("📖 Description:")
        lines.append(f"   {desc}")

    # ── Structured heal / heal-over-time ───────────────────────────────
    if ocr_heal:
        lines.append("")
        if ocr_heal_rounds:
            lines.append("💚 Heal Over Time:")
            lines.append(f"   {ocr_heal} over {ocr_heal_rounds} round(s)")
        else:
            lines.append("❤️ Heal:")
            lines.append(f"   {ocr_heal}")

    # ── Structured damage (initial hit) ────────────────────────────────
    if ocr_dmg:
        lines.append("")
        lines.append("💥 Initial Hit:")
        lines.append(f"   {ocr_dmg}")

    # ── Structured DoT ──────────────────────────────────────────────────
    if ocr_dot:
        lines.append("")
        rounds_txt = f" over {ocr_rounds} round(s)" if ocr_rounds else ""
        lines.append("🔥 Damage Over Time:")
        lines.append(f"   {ocr_dot}{rounds_txt}")

    # ── Divided / AOE ───────────────────────────────────────────────────
    if ocr_divided:
        lines.append("")
        lines.append("🎯 Divided Damage:")
        lines.append(f"   {ocr_divided}")

    # ── Clear effect (e.g. Ashes to Ashes: DoT + clear ward for bonus) ──
    if ocr_clear:
        lines.append("")
        lines.append("🧹 Clear Effect:")
        lines.append(f"   {ocr_clear}")

    # ── Conditional effects ─────────────────────────────────────────────
    if ocr_conditional:
        lines.append("")
        lines.append("⚙ Conditional Effect:")
        lines.append(f"   {ocr_conditional}")

    # ── Gambit / Detonate ─────────────────────────────────────────────
    if ocr_gambit:
        lines.append("")
        lines.append("🍀 Gambit / Detonate:")
        lines.append(f"   {ocr_gambit}")

    # ── Buffs / accuracy mods ────────────────────────────────────────
    if ocr_fx:
        lines.append("")
        lines.append("✨ Buff / Modifier:")
        lines.append(f"   {ocr_fx}")

    # ── Detected icon-backed keywords ───────────────────────────────────
    ocr_keywords = [k.strip() for k in ocr_kw_str.split(",") if k.strip()]

    # ── Auto-interpretation from combined text (description + OCR raw) ──
    corpus = (desc + " " + ocr_raw).lower()
    interp = []

    # Plain damage (only if not already captured structurally as a DoT/gambit)
    if not ocr_dmg and not ocr_dot and not ocr_gambit:
        dm = re.search(
            r"(\d[\d,]+(?:\s*[-–]\s*\d[\d,]+)?)\s*(?:" + re.escape(school.lower()) +
            r"\s+)?damage(?!\s+over)",
            corpus,
        )
        if dm:
            interp.append(f"💥 Deals {dm.group(1)} {school} Damage  [icon: 👊 Damage]")

    # Heal
    hm = re.search(r"(?:heals?|restores?)\s*(?:by\s*)?([\d,]+)\s+health", corpus)
    if hm:
        interp.append(f"❤️ Heals {hm.group(1)} Health  [icon: ❤️ Heal]")

    # Targeting — All Enemies / All Friends / Friend / Enemy
    if "All Enemies" in ocr_keywords or re.search(r"all enem", corpus):
        interp.append("😈 Targets All Enemies  [icon: 😈 All Enemies]")
    elif re.search(r"\benemy\b", corpus):
        interp.append("👹 Targets a single Enemy  [icon: 👹 Enemy]")
    if "All Friends" in ocr_keywords or re.search(r"all friend", corpus):
        interp.append("🙂 Targets All Friends  [icon: 🙂 All Friends]")

    # Blade / Charm
    for bm in re.finditer(r"([+-]?\d+)\s*%\s*(?:\w+\s+)?(?:damage\s+)?(?:blade|charm)", corpus):
        interp.append(f"🍀 Applies {bm.group(1)}% Blade/Charm  [icon: 🍀 Charm]")

    # Ward / Shield
    for wm in re.finditer(r"([+-]?\d+)\s*%\s*(?:\w+\s+)?(?:damage\s+)?(?:ward|shield|resist)", corpus):
        interp.append(f"🛡 Applies {wm.group(1)}% Ward  [icon: 🛡 Ward]")

    # Trap / Curse
    for tm in re.finditer(r"([+-]?\d+)\s*%\s*(?:\w+\s+)?(?:damage\s+)?(?:trap|curse)", corpus):
        interp.append(f"🪤 Sets {tm.group(1)}% Trap  [icon: 🪤 Trap]")

    # Jinx
    if "Jinx" in ocr_keywords or re.search(r"\bjinx\b", corpus):
        interp.append("🃏 Applies a Jinx  [icon: 🃏 Jinx]")

    # Absorb
    am = re.search(r"absorb(?:s|ing)?\s*([\d,]+)", corpus)
    if am:
        interp.append(f"🛡 Absorbs {am.group(1)} Damage  [icon: 🛡 Absorb]")

    # Stun / Stun Resistance
    if "Stun" in ocr_keywords or re.search(r"\bstun\b", corpus):
        interp.append("⭐ Stuns target  [icon: ⭐ Stun]")

    # Block / Critical
    if "Block" in ocr_keywords or re.search(r"\bblock\b", corpus):
        interp.append("🎯 Affects Block chance  [icon: 🎯 Block]")
    if "Critical" in ocr_keywords or re.search(r"\bcritical\b", corpus):
        interp.append("💫 Affects Critical chance  [icon: 💫 Critical]")

    # Armor Piercing
    if "Armor Piercing" in ocr_keywords or re.search(r"\bpierc", corpus):
        interp.append("🔻 Armor Piercing  [icon: 🔻 Armor Piercing]")

    # Minion / Threat
    if "Minion" in ocr_keywords or re.search(r"\bminion\b|\bsummon\b", corpus):
        interp.append("👤 Summons a Minion  [icon: 👤 Minion]")
    if "Threat" in ocr_keywords or re.search(r"\bthreat\b", corpus):
        interp.append("☠️ Generates Threat  [icon: ☠️ Threat]")

    # Polymorph
    if "Polymorph" in ocr_keywords or re.search(r"\bpolymorph\b", corpus):
        interp.append("🔄 Polymorphs caster  [icon: 🔄 Polymorph]")

    # Dispel
    if "Dispel" in ocr_keywords or re.search(r"\bdispel\b", corpus):
        interp.append("❌ Dispels a charm/ward/aura  [icon: ❌ Dispel]")

    # Aura / Harmful Aura
    if "Aura" in ocr_keywords or re.search(r"\baura\b", corpus):
        if re.search(r"harm", corpus):
            interp.append("🟣 Applies a Harmful Aura  [icon: 🟣 Harmful Aura]")
        else:
            interp.append("🟡 Applies an Aura  [icon: 🟡 Aura]")

    # Enchantment
    if "Enchantment" in ocr_keywords or re.search(r"\benchant", corpus):
        interp.append("✨ Enchants a spell  [icon: ✨ Enchantment]")

    # Afterlife
    if "Afterlife" in ocr_keywords or re.search(r"\bafterlife\b", corpus):
        interp.append("💗 Afterlife effect  [icon: 💗 Afterlife]")

    if interp:
        lines.append("")
        lines.append("🎴 Spell Interpretation:")
        lines.extend(f"   {i}" for i in interp)

    # ── Visually-detected icons (real OpenCV template matching) ────────
    # Tagged with "(visual, NN%)" by spell_scraper.py's icon_detector
    # integration — real OpenCV template matching against the in-game
    # icon dictionary (precision 0.93-1.00, recall 0.50-0.68 on
    # validated benchmarks — see icon_detector.py). Kept in its own
    # section since it's a different signal than text-keyword matches:
    # it can MISS real icons (recall isn't 100%), but a detection it
    # does report is usually correct.
    visual_icons = [k for k in ocr_keywords if "(visual," in k]
    if visual_icons:
        lines.append("")
        lines.append("🔍 Detected Icons (visual match):")
        for v in visual_icons:
            lines.append(f"   {v}")

    # ── Any remaining text-keyword matches not already surfaced above ──
    surfaced_terms = {
        "gambit", "detonate", "damage over time", "heal over time",
        "all enemies", "all friends", "stun", "minion", "polymorph",
        "dispel", "absorb", "block", "critical", "armor piercing",
        "aura", "charm", "ward", "trap", "jinx", "enchantment",
        "afterlife", "threat", "no discard", "divided",
    }
    leftover = [
        k for k in ocr_keywords
        if "(visual," not in k and k.lower() not in surfaced_terms
    ]
    if leftover:
        lines.append("")
        lines.append("🔎 Other detected text terms:")
        lines.append(f"   {', '.join(leftover)}")

    # ── Raw OCR text — always shown for transparency ────────────────────
    # Structured parsing above is best-effort; the actual extracted text
    # is shown here so it can be checked against the card directly and
    # corrected via Edit mode if the structured interpretation is wrong.
    if ocr_raw:
        lines.append("")
        lines.append("📋 Raw OCR Text (for verification):")
        for raw_line in ocr_raw.split("\n"):
            if raw_line.strip():
                lines.append(f"   {raw_line.strip()}")

    return "\n".join(lines) if lines else "(No effect data available)"


# ═══════════════════════════════════════════════════════════════════════
# IMAGE ZOOM DIALOG
# ═══════════════════════════════════════════════════════════════════════

def _build_ocr_description_fallback(spell: dict) -> str:
    """
    Build a best-effort description sentence from OCR-derived fields,
    used ONLY when the wiki's own `descrip` field came back empty (so
    there's still something to start editing from instead of a blank
    box). Never auto-saved — the caller shows this with a visible
    warning and only writes it to the database if the user clicks Save.
    """
    parts = []
    if spell.get("ocr_damage"):
        parts.append(f"Deals {spell['ocr_damage']} damage.")
    if spell.get("ocr_dot_damage"):
        rounds = spell.get("ocr_dot_rounds", "")
        rtxt = f" over {rounds} rounds" if rounds else ""
        parts.append(f"Deals {spell['ocr_dot_damage']} damage{rtxt}.")
    if spell.get("ocr_heal"):
        rounds = spell.get("ocr_heal_rounds", "")
        rtxt = f" over {rounds} rounds" if rounds else ""
        parts.append(f"Heals {spell['ocr_heal']}{rtxt}.")
    if spell.get("ocr_divided"):
        parts.append(spell["ocr_divided"] + ".")
    if spell.get("ocr_clear_effect"):
        parts.append(spell["ocr_clear_effect"] + ".")
    if spell.get("ocr_conditional"):
        parts.append(spell["ocr_conditional"] + ".")
    if spell.get("ocr_gambit"):
        parts.append(spell["ocr_gambit"] + ".")
    if spell.get("ocr_effect"):
        parts.append(f"Applies {spell['ocr_effect']}.")
    return " ".join(parts)



class ClickableImageLabel(QLabel):
    """
    QLabel that emits a 'clicked' signal on left-click.

    IMPORTANT: never assign a lambda directly to mousePressEvent
    (e.g. `widget.mousePressEvent = lambda e: dialog.exec_()`). PyQt's
    sip layer requires virtual-method overrides to return None; a
    lambda whose body evaluates to dialog.exec_() returns an int,
    which raises "TypeError: invalid argument to sipBadCatcherResult()"
    and crashes the app. A proper signal/slot avoids this entirely.
    """
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageZoomDialog(QDialog):
    def __init__(self, image_path: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("QDialog{background:#0d1b2a;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        pix = QPixmap(image_path)
        if not pix.isNull():
            screen = QApplication.primaryScreen().availableGeometry()
            max_w  = int(screen.width()  * 0.6)
            max_h  = int(screen.height() * 0.75)
            scaled = pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl.setPixmap(scaled)
            self.resize(scaled.width() + 20, scaled.height() + 50)
        else:
            lbl.setText("Image not found")
            lbl.setStyleSheet("color:#888;font-size:14px;")
            self.resize(320, 200)
        v.addWidget(lbl)
        close = QPushButton("Close")
        close.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 18px;}"
            "QPushButton:hover{background:#e94560;}"
        )
        close.clicked.connect(self.accept)
        v.addWidget(close, 0, Qt.AlignCenter)


# ═══════════════════════════════════════════════════════════════════════
# SPELL CARD TILE
# ═══════════════════════════════════════════════════════════════════════

class SpellTile(QFrame):
    clicked    = pyqtSignal(str)          # spell name
    delete_req = pyqtSignal(str, str)     # (name_or_school, mode)

    def __init__(self, spell: dict, card_w: int, card_h: int,
                 img_w: int, img_h: int, parent=None):
        super().__init__(parent)
        self._name   = spell["name"]
        self._school = spell.get("school", "Unknown")
        self._pip    = str(spell.get("pip_cost", "?"))
        self._img    = spell.get("image_path", "")
        self._card_w = card_w
        self._card_h = card_h
        self._img_w  = img_w
        self._img_h  = img_h

        self.setFixedSize(card_w, card_h)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("spellTile")
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        accent = _sc(self._school)
        self.setStyleSheet(
            f"QFrame#spellTile{{background:#16213e;border:1px solid {_rgba(accent,'55')};border-radius:6px;}}"
            f"QFrame#spellTile:hover{{border:1px solid {accent};background:#1f2d50;}}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(3, 3, 3, 2)
        v.setSpacing(2)

        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(img_w, img_h)
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setStyleSheet("background:transparent;border:none;")
        self._load_image()
        v.addWidget(self._img_lbl, 0, Qt.AlignHCenter)

        nm = QLabel(self._name + ("  ⚠️" if spell.get("ocr_uncertain") else ""))
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(True)
        if spell.get("ocr_uncertain"):
            nm.setToolTip("OCR text looks garbled for this card — verify in detail view")
            nm.setStyleSheet("color:#ffb84d;font-size:9px;background:transparent;border:none;")
        else:
            nm.setStyleSheet("color:#d0d0d0;font-size:9px;background:transparent;border:none;")
        nm.setFixedHeight(max(16, card_h - img_h - 6))
        v.addWidget(nm)

    def _load_image(self):
        pix = None
        if self._img and Path(self._img).exists():
            raw = QPixmap(self._img)
            if not raw.isNull():
                pix = raw.scaled(self._img_w, self._img_h,
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if pix is None:
            pix = _placeholder(self._school, self._pip, self._img_w, self._img_h)
        self._img_lbl.setPixmap(pix)

    def refresh_image(self):
        self._load_image()

    def mousePressEvent(self, event):
        # Call the base implementation FIRST, while this tile's C++ object
        # is guaranteed alive. The click is emitted through a queued
        # connection (see refresh()), so emit() only *posts* the slot and
        # returns immediately — the detail dialog opens on a later event
        # loop turn, after this handler has fully unwound. That is what
        # prevents "RuntimeError: wrapped C/C++ object of type SpellTile
        # has been deleted": a synchronous (direct) connection would open
        # the modal dialog here, and a save inside it could delete this
        # very tile via refresh() before super().mousePressEvent() runs.
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._name)

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;"
            "border-radius:4px;padding:4px;}"
            "QMenu::item{padding:6px 16px;}"
            "QMenu::item:selected{background:#e94560;}"
        )
        act_single = menu.addAction(f"🗑 Delete  '{self._name}'")
        menu.addSeparator()
        act_school = menu.addAction(f"🗑 Delete all  {self._school}  spells")
        menu.addSeparator()
        act_all = menu.addAction("🗑 Delete ALL spells")
        chosen = menu.exec_(self.mapToGlobal(pos))
        if chosen == act_single:
            self.delete_req.emit(self._name, "single")
        elif chosen == act_school:
            self.delete_req.emit(self._school, "school")
        elif chosen == act_all:
            self.delete_req.emit("", "all")


# ═══════════════════════════════════════════════════════════════════════
# SPELL DETAIL DIALOG
# ═══════════════════════════════════════════════════════════════════════

class SpellDetailDialog(QDialog):
    spell_updated = pyqtSignal(str)  # spell name, when saved in edit mode

    def __init__(self, spell: dict, conn, parent=None):
        super().__init__(parent)
        self._spell = spell
        self._conn  = conn
        self._nav_stack = []  # list of spell dicts, for "← Back" navigation
        self._refetch_process = None
        self.setWindowTitle(spell["name"])
        self.setMinimumSize(600, 700)
        self.resize(660, 740)
        self.setStyleSheet("""
            QDialog,QWidget{background:#1a1a2e;color:#e0e0e0;
                            font-family:'Segoe UI',Tahoma,sans-serif;}
            QTextEdit,QTextBrowser{background:#0d1b2a;color:#e0e0e0;border:none;
                                   font-size:12px;padding:8px;}
            QLineEdit{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;
                      border-radius:4px;padding:6px 8px;font-size:12px;}
            QLineEdit:focus{border-color:#c39bd3;}
            QPushButton{background:#0f3460;color:#e0e0e0;border:none;
                        border-radius:5px;padding:6px 16px;font-size:12px;}
            QPushButton:hover{background:#e94560;}
            QGroupBox{border:1px solid #1f3460;border-radius:6px;
                      margin-top:10px;padding-top:14px;
                      font-weight:bold;color:#e94560;}
            QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}
        """)
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top action bar ────────────────────────────────────────────
        bar = QWidget()
        bar.setStyleSheet("background:#16213e;border-bottom:1px solid #0f3460;")
        bar.setFixedHeight(44)
        brow = QHBoxLayout(bar)
        brow.setContentsMargins(12, 6, 12, 6)
        brow.setSpacing(8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setToolTip("Return to the spell you came from")
        self._back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 12px;font-size:11px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setVisible(False)
        brow.addWidget(self._back_btn)

        self._name_lbl = QLabel(self._spell["name"])
        self._name_lbl.setFont(QFont("Segoe UI", 14, QFont.Bold))
        accent = _sc(self._spell.get("school", ""))
        self._name_lbl.setStyleSheet(f"color:{accent};background:transparent;")
        brow.addWidget(self._name_lbl)
        brow.addStretch()

        # Replace this spell's image with a file of your choosing (the wiki
        # occasionally links the wrong card art).
        self._img_btn = QPushButton("🖼 Change Image")
        self._img_btn.setToolTip("Pick a local image file to use as this spell's card image")
        self._img_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#c39bd3;border:1px solid #3a2a4a;"
            "border-radius:5px;padding:5px 12px;font-size:11px;}"
            "QPushButton:hover{background:#2a1f3a;}"
        )
        self._img_btn.clicked.connect(self._change_image)
        brow.addWidget(self._img_btn)

        # Re-scrape this one spell from the wiki, overwriting image + text.
        self._refetch_btn = QPushButton("🔄 Re-fetch")
        self._refetch_btn.setToolTip("Re-download this spell from the wiki, overwriting its image, text and data")
        self._refetch_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4dd07a;border:1px solid #24503a;"
            "border-radius:5px;padding:5px 12px;font-size:11px;}"
            "QPushButton:hover{background:#173a28;}"
        )
        self._refetch_btn.clicked.connect(self._refetch_spell)
        brow.addWidget(self._refetch_btn)

        close_btn = QPushButton("✕ Close")
        close_btn.clicked.connect(self.accept)
        brow.addWidget(close_btn)
        root.addWidget(bar)

        # ── Scrollable body ───────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")
        body = QWidget()
        self._body_v = QVBoxLayout(body)
        self._body_v.setContentsMargins(16, 12, 16, 16)
        self._body_v.setSpacing(10)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)
        self._scroll = scroll

        self._populate_body()

    def _navigate_to_spell(self, new_spell: dict):
        """
        Replace the dialog's content with a different spell's detail
        view (used when clicking a spellement tier), pushing the
        current spell onto a back-stack so '← Back' can return to it.
        This REPLACES the open dialog's content rather than stacking a
        new modal popup on top, per the requested navigation model.
        """
        self._nav_stack.append(self._spell)
        self._spell = new_spell
        self._back_btn.setVisible(True)
        self.setWindowTitle(new_spell.get("name", ""))
        self._populate_body()
        self._scroll.verticalScrollBar().setValue(0)

    def _go_back(self):
        if not self._nav_stack:
            return
        self._spell = self._nav_stack.pop()
        self._back_btn.setVisible(bool(self._nav_stack))
        self.setWindowTitle(self._spell.get("name", ""))
        self._populate_body()
        self._scroll.verticalScrollBar().setValue(0)

    # ── Re-fetch this spell (overwrite image + text) ──────────────────

    def _reload_current_spell(self, name: str = None):
        """Reload the shown spell fresh from the DB and repopulate in place
        (no back-stack push). Clears the pixmap cache so an overwritten image
        file at the same path is re-read from disk rather than served stale."""
        name = name or self._spell.get("name", "")
        QPixmapCache.clear()
        fresh = ds.get_spell(self._conn, name)
        if fresh:
            self._spell = fresh
            self._populate_body()
            self._scroll.verticalScrollBar().setValue(0)
            self.spell_updated.emit(name)

    def _refetch_spell(self):
        if self._refetch_process and self._refetch_process.state() != QProcess.NotRunning:
            return
        name = self._spell.get("name", "")
        if not name:
            return
        if QMessageBox.question(
            self, "Re-fetch Spell",
            f"Re-download <b>{name}</b> from the wiki?<br><br>"
            "This overwrites its image, description and other fetched data with "
            "the current wiki version. Your manual icon picks are preserved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        self._refetch_btn.setEnabled(False)
        self._img_btn.setEnabled(False)
        self._refetch_btn.setText("⏳ Re-fetching…")

        self._refetch_buf = ""
        self._refetch_process = QProcess(self)
        self._refetch_process.setProcessChannelMode(QProcess.MergedChannels)
        self._refetch_process.readyReadStandardOutput.connect(self._on_refetch_out)
        self._refetch_process.finished.connect(self._on_refetch_done)
        self._refetch_process.start(sys.executable, [SPELL_SCRAPER, "--spell", name, "--force"])

    def _on_refetch_out(self):
        """Stream refetch stdout to catch the Cloudflare-challenge marker live
        and accumulate it so _on_refetch_done still has the full output."""
        if not self._refetch_process:
            return
        data = bytes(self._refetch_process.readAllStandardOutput()).decode("utf-8", "replace")
        self._refetch_buf += data
        if cf_alert is not None and cf_alert.MARKER in data:
            _play_cf_alert()

    def _on_refetch_done(self, code, _status=None):
        out = getattr(self, "_refetch_buf", "")
        if self._refetch_process:
            # Drain any trailing bytes not yet delivered via the stream hook.
            out += bytes(self._refetch_process.readAllStandardOutput()).decode("utf-8", "replace")
        out = cf_alert.strip_marker(out) if cf_alert is not None else out
        self._refetch_btn.setEnabled(True)
        self._img_btn.setEnabled(True)
        self._refetch_btn.setText("🔄 Re-fetch")
        self._refetch_process = None
        if code == 0:
            self._reload_current_spell()
        else:
            QMessageBox.warning(
                self, "Re-fetch Failed",
                "The re-fetch did not complete successfully.\n\n"
                + (out[-800:] if out.strip() else f"Exit code {code}.")
            )

    def _change_image(self):
        """Let the user replace this spell's card image with a local file —
        for the cases where the wiki links the wrong art."""
        name = self._spell.get("name", "")
        if not name:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose image for {name}", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)"
        )
        if not path:
            return
        pix = QPixmap(path)
        if pix.isNull():
            QMessageBox.warning(self, "Invalid Image",
                                "That file couldn't be loaded as an image.")
            return
        IMG_DIR.mkdir(exist_ok=True)
        safe = re.sub(r'[<>:"/\\|?*]', "_", name)
        dest = IMG_DIR / f"{safe}.png"
        if not pix.save(str(dest), "PNG"):
            QMessageBox.warning(self, "Save Failed",
                                f"Could not write the image to:\n{dest}")
            return
        sid = self._spell.get("id")
        if sid is not None:
            self._conn.execute("UPDATE spells SET image_path=? WHERE id=?",
                               (str(dest), sid))
            self._conn.commit()
        self._reload_current_spell()

    def _populate_body(self):
        v = self._body_v
        # Clear previous widgets
        while v.count():
            it = v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        sp = self._spell
        school = sp.get("school", "Unknown")
        accent = _sc(school)

        # Keep top-bar name label in sync with whichever spell is shown
        # (base spell or a navigated-to spellement tier). Flag garbled
        # OCR right in the title so it's visible without scrolling.
        title_text = sp.get("name", "")
        if sp.get("ocr_uncertain"):
            title_text += "  ⚠️ verify OCR"
        self._name_lbl.setText(title_text)
        self._name_lbl.setStyleSheet(f"color:{accent};background:transparent;")

        # ── Header row: image + stats ─────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(16)

        # Spell card image (clickable to enlarge)
        img_path = sp.get("image_path", "")
        self._img_container = ClickableImageLabel()
        self._img_container.setFixedSize(140, 180)
        self._img_container.setAlignment(Qt.AlignCenter)
        self._img_container.setCursor(Qt.PointingHandCursor)
        self._img_container.setToolTip("Click to enlarge")
        if img_path and Path(img_path).exists():
            pix = QPixmap(img_path).scaled(138, 178, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._img_container.setPixmap(pix)
            self._img_container.setStyleSheet(
                f"background:{_rgba(accent,'11')};border:2px solid {_rgba(accent,'44')};border-radius:6px;"
            )
        else:
            self._img_container.setText("No image\n(click to try)")
            self._img_container.setStyleSheet(
                f"background:{_rgba(accent,'22')};border:2px dashed {_rgba(accent,'55')};"
                "border-radius:6px;color:#666;font-size:11px;"
            )
        self._img_container.clicked.connect(self._enlarge_image)
        hdr.addWidget(self._img_container)

        # ── Stats (right side) — ALL fetch-only fields, directly editable ──
        # Everything here comes from the wiki's own data (not OCR) —
        # School, Pip Cost, School Pip Cost, Shadow Pip Cost, Accuracy,
        # Type, PvP, Description. If a field is empty because the wiki
        # page didn't have it, the Description field falls back to a
        # best-effort OCR-derived suggestion (clearly marked, never
        # auto-saved) so there's still something to start editing from.
        stats_v = QVBoxLayout()
        stats_v.setSpacing(6)

        def _core_field(icon, label, widget, width=120):
            rw = QHBoxLayout()
            i = QLabel(icon); i.setFixedWidth(20); i.setStyleSheet("background:transparent;")
            lb = QLabel(f"<b>{label}:</b>"); lb.setFixedWidth(width)
            lb.setStyleSheet("color:#888;background:transparent;font-size:11px;")
            rw.addWidget(i); rw.addWidget(lb); rw.addWidget(widget, stretch=1)
            stats_v.addLayout(rw)
            return widget

        _FIELD_SS = (
            "QLineEdit,QComboBox,QSpinBox{background:#0d1b2a;color:#e0e0e0;"
            "border:1px solid #1f3460;border-radius:4px;padding:3px 6px;font-size:11px;}"
            "QLineEdit:focus,QComboBox:focus,QSpinBox:focus{border-color:#c39bd3;}"
        )

        self._school_fld = QComboBox()
        self._school_fld.setEditable(True)
        self._school_fld.addItems(["Fire","Ice","Storm","Myth","Life","Death",
                                   "Balance","Star","Moon","Sun","Shadow","Unknown"])
        self._school_fld.setCurrentText(school)
        self._school_fld.setStyleSheet(_FIELD_SS)
        _core_field("🏫", "School", self._school_fld)

        self._pip_fld = QLineEdit(str(sp.get("pip_cost", "0")))
        self._pip_fld.setStyleSheet(_FIELD_SS)
        _core_field("⚡", "Pip Cost", self._pip_fld)

        self._spip_fld = QSpinBox()
        self._spip_fld.setRange(0, 9)
        self._spip_fld.setValue(int(sp.get("school_pip_cost", 0) or 0))
        self._spip_fld.setStyleSheet(_FIELD_SS)
        _core_field("🏫⚡", "School Pip", self._spip_fld)

        self._shadow_pip_fld = QSpinBox()
        self._shadow_pip_fld.setRange(0, 9)
        self._shadow_pip_fld.setValue(int(sp.get("shadow_pip_cost", 0) or 0))
        self._shadow_pip_fld.setStyleSheet(_FIELD_SS)
        _core_field("🌑⚡", "Shadow Pip", self._shadow_pip_fld)

        self._acc_fld = QSpinBox()
        self._acc_fld.setRange(0, 100)
        self._acc_fld.setSuffix("%")
        self._acc_fld.setValue(int(sp.get("accuracy", 0) or 0))
        self._acc_fld.setStyleSheet(_FIELD_SS)
        _core_field("🎯", "Accuracy", self._acc_fld)

        self._type_fld = QLineEdit(sp.get("spell_type", "") or "")
        self._type_fld.setStyleSheet(_FIELD_SS)
        _core_field("📌", "Type", self._type_fld)

        # PvP level (e.g. "170+"), shown/edited below Type. Replaces the old
        # "PvP Legal" checkbox — PvP status is now driven by the detected
        # "No PvP" / "PvP Only" icons instead of a boolean flag.
        self._pvplevel_fld = QLineEdit(sp.get("pvp_level", "") or "")
        self._pvplevel_fld.setPlaceholderText("e.g. 170+")
        self._pvplevel_fld.setStyleSheet(_FIELD_SS)
        _core_field("⚔", "PvP Level", self._pvplevel_fld)

        stats_v.addStretch()
        hdr.addLayout(stats_v, stretch=1)
        v.addLayout(hdr)

        # ── Description — full width below image+stats, multi-line ────
        # ── Description — flat, no double-border (no QGroupBox heading
        # border + inner QTextEdit border stacking). Just a label row
        # and the text area with a single clean outer line.
        desc_outer = QFrame()
        desc_outer.setStyleSheet(
            "QFrame{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;}"
        )
        # Hug the content vertically. Without this the body layout stretches
        # this frame to fill leftover space, and that slack pools inside the
        # frame (the header label balloons, leaving a big gap under short or
        # empty descriptions). Fixed vertical policy makes the frame take
        # exactly its sizeHint; the trailing addStretch() in the body then
        # soaks up the extra space instead.
        desc_outer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._desc_outer = desc_outer
        desc_outer_v = QVBoxLayout(desc_outer)
        desc_outer_v.setContentsMargins(10, 8, 10, 8)
        desc_outer_v.setSpacing(4)
        desc_hdr = QLabel("📖 Description")
        desc_hdr.setStyleSheet(
            "color:#4d96ff;font-size:11px;font-weight:bold;background:transparent;border:none;"
        )
        desc_hdr.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        desc_outer_v.addWidget(desc_hdr)
        self._desc_fld = QTextEdit()
        self._desc_fld.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._desc_fld.setStyleSheet(
            "QTextEdit{background:transparent;color:#e0e0e0;border:none;"
            "padding:0px;font-size:12px;}"
        )
        # Trim the built-in document margin so text hugs the top-left and
        # the box can shrink to fit — the old fixed 62px height left a big
        # empty gap under short one-line descriptions.
        self._desc_fld.document().setDocumentMargin(2)
        self._desc_fld.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        real_desc = sp.get("description", "")
        self._desc_is_ocr_fallback = False
        if real_desc:
            self._desc_fld.setPlainText(real_desc)
        else:
            fallback = _build_ocr_description_fallback(sp)
            if fallback:
                self._desc_fld.setPlainText(fallback)
                self._desc_is_ocr_fallback = True
        desc_outer_v.addWidget(self._desc_fld)
        # Size the box to its text (snug for short descriptions, growing up
        # to a sensible cap for long ones). textChanged keeps it right while
        # the user edits; the deferred call runs once the real widget width
        # is known so wrapped-line height is measured correctly.
        self._desc_fld.textChanged.connect(self._autosize_desc_field)
        QTimer.singleShot(0, self._autosize_desc_field)
        self._desc_fallback_hint = None
        if self._desc_is_ocr_fallback:
            self._desc_fallback_hint = QLabel(
                "⚠️ No wiki description — OCR guess shown. Edit and Save to confirm."
            )
            self._desc_fallback_hint.setWordWrap(True)
            self._desc_fallback_hint.setStyleSheet(
                "color:#ffb84d;font-size:10px;background:transparent;border:none;"
            )
            desc_outer_v.addWidget(self._desc_fallback_hint)
        v.addWidget(desc_outer)

        self._core_save_btn = QPushButton("💾 Save Fields")
        self._core_save_btn.setStyleSheet(
            "QPushButton{background:#0f4d2e;color:#9fffcf;border:1px solid #1f6a44;"
            "border-radius:5px;padding:6px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#156b40;}"
        )
        self._core_save_btn.clicked.connect(self._save_core_fields)
        v.addWidget(self._core_save_btn, 0, Qt.AlignRight)

        # ── Wiki page link ─────────────────────────────────────────────
        wiki_path = sp.get("wiki_path", "")
        if wiki_path:
            wiki_url = f"https://wiki.wizard101central.com/wiki/{wiki_path}"
            wiki_lnk = QLabel(f'<a href="{wiki_url}" style="color:#4d96ff;font-size:11px;">📖 View on Wiki</a>')
            wiki_lnk.setOpenExternalLinks(True)
            wiki_lnk.setStyleSheet("background:transparent;")
            v.addWidget(wiki_lnk)

        # ── Icon Legend — icons detected via OCR/visual matching, with
        # their descriptions, manageable per-spell (add/remove) ───────
        # Auto-link OCR-detected keywords that match known presets the
        # first time this spell is opened, so the legend isn't empty.
        self._maybe_autolink_ocr_icons()

        icon_box = QFrame()
        icon_box.setStyleSheet(
            "QFrame{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;}"
        )
        icon_outer_v = QVBoxLayout(icon_box)
        icon_outer_v.setContentsMargins(10, 8, 10, 8)
        icon_outer_v.setSpacing(6)
        icon_hdr = QLabel("🔍 Detected Icons")
        icon_hdr.setStyleSheet(
            "color:#c39bd3;font-size:11px;font-weight:bold;background:transparent;border:none;"
        )
        icon_outer_v.addWidget(icon_hdr)
        icon_layout = icon_outer_v  # alias so the rest of the code below works unchanged

        self._icon_rows_container = QWidget()
        self._icon_rows_container.setStyleSheet("background:transparent;border:none;")
        self._icon_rows_v = QVBoxLayout(self._icon_rows_container)
        self._icon_rows_v.setContentsMargins(0, 0, 0, 0)
        self._icon_rows_v.setSpacing(4)
        icon_layout.addWidget(self._icon_rows_container)
        self._refresh_icon_legend()

        add_row = QHBoxLayout()
        self._icon_add_combo = QComboBox()
        self._icon_add_combo.setStyleSheet(_FIELD_SS)
        # Make it a searchable picker: type to filter by any substring of
        # the preset name, pick from the popup. NoInsert so typing never
        # creates a bogus entry.
        self._icon_add_combo.setEditable(True)
        self._icon_add_combo.setInsertPolicy(QComboBox.NoInsert)
        _icon_completer = self._icon_add_combo.completer()
        _icon_completer.setCompletionMode(QCompleter.PopupCompletion)
        _icon_completer.setFilterMode(Qt.MatchContains)
        _icon_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._icon_add_combo.lineEdit().setPlaceholderText("🔍 Search icon to add…")
        self._refresh_icon_add_combo()
        add_row.addWidget(self._icon_add_combo, stretch=1)
        add_icon_btn = QPushButton("＋ Add")
        add_icon_btn.setStyleSheet(
            "QPushButton{background:#3a1f60;color:#c39bd3;border:none;"
            "border-radius:4px;padding:4px 12px;font-size:11px;}"
            "QPushButton:hover{background:#6a3fa0;}"
        )
        add_icon_btn.clicked.connect(self._on_add_icon)
        add_row.addWidget(add_icon_btn)
        icon_layout.addLayout(add_row)
        v.addWidget(icon_box)

        # ── Training Status (from rendered-HTML acquisition data) ──────
        self._build_training_section(v, sp)

        # ── Fusion Formula (rendered above Spellement Paths) ──────────
        self._build_fusion_section(v, sp)

        # ── Tier / Spellement paths ───────────────────────────────────
        # Collect tier variants from DB first
        tier_variants = ds.get_tier_variants(self._conn, sp["name"])
        paths = sp.get("spellement_paths", [])

        if tier_variants or paths:
            sp_box = self._group("✨ Spellement Paths (Tiers)", "#ffd93d")
            spl = QVBoxLayout()
            spl.setSpacing(6)

            # Tier variants from DB (sorted by name which sorts tiers in order)
            if tier_variants:
                for tv in tier_variants:
                    base_n, tier_lbl = ds.is_tier_variant(tv["name"])
                    img_p = tv.get("image_path", "")
                    has_img = bool(img_p and Path(img_p).exists())

                    fw = QWidget()
                    fw.setStyleSheet(
                        "background:#0d1b2a;border:1px solid #ffd93d22;border-radius:5px;"
                    )
                    fwl = QHBoxLayout(fw)
                    fwl.setContentsMargins(8, 6, 8, 6)
                    fwl.setSpacing(10)

                    badge = QLabel(f"Tier {tier_lbl}")
                    badge.setFixedWidth(56)
                    badge.setAlignment(Qt.AlignCenter)
                    badge.setStyleSheet(
                        "background:#2a1a00;color:#ffd93d;border:1px solid #ffd93d55;"
                        "border-radius:4px;font-size:10px;font-weight:bold;"
                    )
                    fwl.addWidget(badge)

                    # Always create a clickable thumbnail — falls back to a
                    # placeholder when the tier variant's own image hasn't
                    # been downloaded yet (each tier is a separate DB row
                    # with its own image_path; without this fallback the
                    # row had nothing clickable at all and appeared dead).
                    thumb = ClickableImageLabel()
                    thumb.setFixedSize(48, 64)
                    if has_img:
                        thumb.setPixmap(
                            QPixmap(img_p).scaled(46, 62, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        )
                        thumb.setStyleSheet("background:transparent;")
                    else:
                        thumb.setText("No\nimage")
                        thumb.setAlignment(Qt.AlignCenter)
                        thumb.setStyleSheet(
                            "background:#1a1a2e;color:#555;font-size:9px;"
                            "border:1px dashed #444;border-radius:4px;"
                        )
                    thumb.setCursor(Qt.PointingHandCursor)
                    thumb.setToolTip(f"View {tv['name']} details" if has_img else
                                     "No image downloaded yet for this tier — "
                                     "use 🖼 Get Images in the Spell Browser")
                    _tv_name = tv["name"]
                    thumb.clicked.connect(
                        lambda n=_tv_name: self._open_tier_detail(n)
                    )
                    fwl.addWidget(thumb)

                    # Explicit, always-visible button as a second, larger
                    # click target — guarantees discoverability even if the
                    # small thumbnail is easy to miss.
                    view_btn = QPushButton("🔍 View")
                    view_btn.setFixedWidth(64)
                    view_btn.setStyleSheet(
                        "QPushButton{background:#2a1a00;color:#ffd93d;border:1px solid #ffd93d55;"
                        "border-radius:4px;padding:4px 6px;font-size:10px;}"
                        "QPushButton:hover{background:#3a2500;}"
                    )
                    view_btn.clicked.connect(
                        lambda _, n=_tv_name: self._open_tier_detail(n)
                    )
                    fwl.addWidget(view_btn)

                    info_v = QVBoxLayout()
                    name_lbl = QLabel(tv["name"])
                    name_lbl.setStyleSheet(
                        "color:#d0d0d0;font-size:11px;font-weight:bold;background:transparent;"
                    )
                    info_v.addWidget(name_lbl)
                    detail_txt = tv.get("ocr_damage") or tv.get("description") or "—"
                    detail_lbl = QLabel(detail_txt)
                    detail_lbl.setWordWrap(True)
                    detail_lbl.setStyleSheet("color:#a0a0a0;font-size:10px;background:transparent;")
                    info_v.addWidget(detail_lbl)
                    fwl.addLayout(info_v, stretch=1)

                    spl.addWidget(fw)

            # Wikitext-parsed spellement paths (if no DB tier variants)
            elif paths:
                for path in paths:
                    if isinstance(path, dict):
                        tier = path.get("tier", "?")
                        desc = (path.get("description") or path.get("damage") or
                                path.get("effect") or "—")
                        row = QHBoxLayout()
                        t = QLabel(f"Tier {tier}")
                        t.setFixedWidth(56)
                        t.setAlignment(Qt.AlignCenter)
                        t.setStyleSheet(
                            "background:#2a1a00;color:#ffd93d;border:1px solid #ffd93d55;"
                            "border-radius:4px;font-size:10px;font-weight:bold;"
                        )
                        row.addWidget(t)
                        dl = QLabel(desc)
                        dl.setWordWrap(True)
                        dl.setStyleSheet("color:#d0d0d0;font-size:11px;background:transparent;")
                        row.addWidget(dl, stretch=1)
                        fw = QFrame()
                        fw.setStyleSheet(
                            "QFrame{background:#0d1b2a;border:1px solid #ffd93d22;border-radius:5px;}"
                        )
                        fw.setLayout(row)
                        fw.layout().setContentsMargins(8, 6, 8, 6)
                        spl.addWidget(fw)

            sp_box.layout().addLayout(spl)
            v.addWidget(sp_box)

        # ── Training sources (legacy wikitext fallback) ──────────────
        # Only shown when the richer rendered-HTML Training Status section
        # (built above) has nothing — avoids duplicating the same info.
        sources = sp.get("training_sources", [])
        where   = sp.get("where_to_train", "")
        items   = sources or ([s.strip() for s in where.split(";") if s.strip()] if where else [])
        has_rich_training = bool((sp.get("training_info") or {}).get("sections"))
        if items and not has_rich_training:
            src_box = self._group("🎓 How to Get", "#27ae60")
            for src in items:
                lbl = QLabel(f"  •  {src}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#c0c0c0;font-size:11px;background:transparent;")
                src_box.layout().addWidget(lbl)
            v.addWidget(src_box)

        v.addStretch()

    def _group(self, title: str, color: str) -> "QGroupBox":
        from PyQt5.QtWidgets import QGroupBox
        box = QGroupBox(title)
        box.setStyleSheet(
            f"QGroupBox{{border:1px solid {_rgba(color,'55')};border-radius:6px;"
            "margin-top:10px;padding-top:14px;font-weight:bold;"
            f"color:{color};}}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}"
        )
        QVBoxLayout(box)
        return box

    def _enlarge_image(self):
        img_path = self._spell.get("image_path", "")
        if img_path and Path(img_path).exists():
            dlg = ImageZoomDialog(img_path, self._spell["name"], self)
            dlg.exec_()
        else:
            QMessageBox.information(self, "No Image",
                                    "No image has been downloaded for this spell yet.\n"
                                    "Use '🖼 Get Images' in the Spell Browser to fetch it.")

    def _open_tier_detail(self, tier_spell_name: str):
        """
        Open a spellement tier's own full detail view, replacing the
        current dialog content (not stacking a new modal popup), with
        '← Back' available to return to the spell that led here.
        """
        tier_spell = ds.get_spell(self._conn, tier_spell_name)
        if not tier_spell:
            QMessageBox.warning(
                self, "Not Found",
                f"Could not load details for '{tier_spell_name}'."
            )
            return
        self._navigate_to_spell(tier_spell)

    # ── Training Status section (requirement 2) ───────────────────────

    def _build_training_section(self, v: QVBoxLayout, sp: dict):
        """
        Render the rendered-HTML acquisition data (Training Points can/cannot,
        Trainer + level, Spellements-to-learn, Requirements to Train,
        Prerequisite to Train, "cannot be trained" notes, Other Acquisition
        Sources) as a section below the Detected Icons. Faithful to whatever
        the wiki page shows — categories/lines are rendered generically.
        """
        info = sp.get("training_info") or {}
        sections = info.get("sections") or []
        if not sections:
            return
        box = self._group("🎓 Training Status", "#27ae60")
        outer = QVBoxLayout()
        outer.setSpacing(8)
        for section in sections:
            title = section.get("title", "")
            if title:
                sec_lbl = QLabel(title)
                sec_lbl.setStyleSheet(
                    "color:#4dd07a;font-size:11px;font-weight:bold;"
                    "background:transparent;border:none;"
                )
                outer.addWidget(sec_lbl)
            for cat in section.get("categories", []):
                heading = cat.get("heading", "")
                lines = cat.get("lines", [])
                if heading:
                    h = QLabel(heading)
                    h.setWordWrap(True)
                    h.setStyleSheet(
                        "color:#c8c8c8;font-size:11px;font-weight:bold;"
                        "background:transparent;border:none;margin-left:4px;"
                    )
                    outer.addWidget(h)
                for ln in lines:
                    bl = QLabel(f"•  {ln}")
                    bl.setWordWrap(True)
                    bl.setStyleSheet(
                        "color:#a8a8a8;font-size:11px;background:transparent;"
                        "border:none;margin-left:14px;"
                    )
                    outer.addWidget(bl)
        box.layout().addLayout(outer)
        v.addWidget(box)

    # ── Fusion Formula section (requirement 3) ────────────────────────

    def _resolve_spell_image(self, name: str) -> str:
        """
        Path to a card image for `name`: prefer the fetched spell's own image,
        then the fusion image cache downloaded by the scraper, else "".
        """
        safe = re.sub(r'[<>:"/\\|?*]', "_", name)
        for p in (IMG_DIR / f"{safe}.png", FUSION_IMG_DIR / f"{safe}.png"):
            if p.exists():
                return str(p)
        return ""

    def _fusion_card(self, spell_info: dict) -> QWidget:
        """One clickable fusion card (image + name) that navigates on click."""
        name = spell_info.get("name", "")
        img_path = self._resolve_spell_image(name)

        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(3)

        thumb = ClickableImageLabel()
        thumb.setFixedSize(84, 129)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setCursor(Qt.PointingHandCursor)
        if img_path:
            thumb.setPixmap(
                QPixmap(img_path).scaled(82, 127, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            thumb.setStyleSheet("background:transparent;border:none;")
        else:
            thumb.setText("No\nimage")
            thumb.setStyleSheet(
                "background:#1a1a2e;color:#555;font-size:9px;"
                "border:1px dashed #444;border-radius:4px;"
            )
        thumb.setToolTip(f"Open {name}" if name else "")
        # Skip navigation when the card is the current spell (the fusion
        # result on its own page) — nothing to navigate to.
        if name and name.lower() != (self._spell.get("name", "") or "").lower():
            thumb.clicked.connect(lambda n=name: self._open_tier_detail(n))
        hv.addWidget(thumb, 0, Qt.AlignHCenter)

        nm = QLabel(name)
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(True)
        nm.setFixedWidth(90)
        nm.setStyleSheet("color:#d0d0d0;font-size:9px;background:transparent;border:none;")
        hv.addWidget(nm, 0, Qt.AlignHCenter)
        return holder

    def _build_fusion_section(self, v: QVBoxLayout, sp: dict):
        """
        Render fusion recipes as a visual  image A + image B = image C  row,
        above the Spellement Paths. Each card is clickable and opens that
        spell in this same dialog (like a spellement tier). The reagent
        spells stay visible in the global grid — they're ordinary spells,
        not (Tier …) variants, so nothing is excluded.
        """
        recipes = sp.get("fusion_formulae") or []
        if not recipes:
            return

        def _sep(symbol: str) -> QLabel:
            s = QLabel(symbol)
            s.setAlignment(Qt.AlignCenter)
            s.setStyleSheet(
                "color:#ff9c40;font-size:22px;font-weight:bold;"
                "background:transparent;border:none;"
            )
            return s

        box = self._group("⚗ Fusion Formula", "#ff9c40")
        fv = QVBoxLayout()
        fv.setSpacing(8)
        for recipe in recipes:
            components = recipe.get("components", [])
            result = recipe.get("result")

            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row = QHBoxLayout(row_w)
            row.setContentsMargins(4, 4, 4, 4)
            row.setSpacing(6)
            row.addStretch()
            for i, comp in enumerate(components):
                if i > 0:
                    row.addWidget(_sep("+"))
                row.addWidget(self._fusion_card(comp))
            if result:
                row.addWidget(_sep("="))
                row.addWidget(self._fusion_card(result))
            row.addStretch()
            fv.addWidget(row_w)
        box.layout().addLayout(fv)
        v.addWidget(box)

    def _maybe_autolink_ocr_icons(self):
        """
        Auto-link icon presets based on the spell's OCR keyword list,
        but only if no icons have been manually managed yet for this spell.
        This fills the Detected Icons legend automatically after a scrape
        without overwriting manual edits.

        Two sources are combined:
        1. Text-based keyword tags (e.g. "Gambit", "All Enemies") from
           _structure_ocr_text and the description parser.
        2. Visual template-matching hits (e.g. "Damage Over Time (visual, 85%)")
           from icon_detector — these already include the icon name, just
           with a "(visual, NN%)" suffix to strip.
        """
        sid = self._spell.get("id")
        if sid is None:
            return
        # Only auto-link once — if there are already manually-managed links,
        # don't touch them.
        existing = ds.get_icons_for_spell(self._conn, sid)
        if existing:
            return

        ocr_kw_str = self._spell.get("ocr_keywords", "")

        all_presets = {p["name"]: p["id"] for p in ds.list_icon_presets(self._conn)}
        if not all_presets:
            return

        kw_raw = [k.strip() for k in ocr_kw_str.split(",") if k.strip()]

        # The raw keyword string uses commas as separators, but visual
        # detection entries look like "Damage Over Time (visual, 82%)"
        # which also contains a comma — a naive split fragments them into
        # ["Damage Over Time (visual", "82%)"].  Re-join any fragment that
        # looks like a dangling percentage back onto its preceding entry.
        kw_merged = []
        i = 0
        while i < len(kw_raw):
            token = kw_raw[i]
            # A dangling fragment ends with "%)" and has no space-icon word
            if re.match(r'^\d+\.?\d*%\)$', token.strip()) and kw_merged:
                kw_merged[-1] = kw_merged[-1] + ", " + token
            else:
                kw_merged.append(token)
            i += 1
        kw_raw = kw_merged

        # Add the full structured-field derivation (name / Type / school /
        # pip costs / PvP flag) plus a direct scan of the clean description
        # and raw OCR text — derive_all_icon_labels already folds
        # extract_icon_keyword_labels(description + ocr_raw) in. Together
        # these are what make a single spell resolve to SEVERAL icons
        # instead of one/none.
        kw_raw += ds.derive_all_icon_labels(self._spell)

        if not kw_raw:
            return

        # Lowercased name→id map for the shared resolver (exact → alias →
        # conservative fuzzy), so the dialog links exactly what a Reparse
        # would.
        presets_lc = {name.lower(): pid for name, pid in all_presets.items()}
        for kw in kw_raw:
            name_candidate = kw
            if "(visual," in kw:
                name_candidate = kw.split("(visual,")[0].strip()
                name_candidate = name_candidate.replace("_", " ")
            pid = ds._resolve_preset_name(name_candidate, presets_lc)
            if pid is not None:
                ds.link_icon_to_spell(self._conn, sid, pid, auto=True)

    def _autosize_desc_field(self):
        """Resize the description box to fit its text, within [30, 108]px."""
        try:
            fld = self._desc_fld
            doc = fld.document()
            doc.setTextWidth(fld.viewport().width())
            h = int(doc.size().height()) + 2 * int(fld.frameWidth()) + 8
            fld.setFixedHeight(max(30, min(108, h)))
            # The enclosing frame has a Fixed vertical policy, so nudge it to
            # recompute its sizeHint now that the inner field height changed —
            # otherwise it can lag one layout pass behind.
            outer = getattr(self, "_desc_outer", None)
            if outer is not None:
                outer.updateGeometry()
        except (AttributeError, RuntimeError):
            # Field was rebuilt (navigation) or deleted before the deferred
            # call fired — nothing to size.
            pass

    def _save_core_fields(self):
        """Save the right-side fetch-only fields (School, Pip Cost,
        School Pip, Shadow Pip, Accuracy, Type, PvP, Description)."""
        sid = self._spell.get("id")
        if sid is None:
            return
        ds.update_spell_core_fields(
            self._conn, sid,
            school=self._school_fld.currentText().strip(),
            pip_cost=self._pip_fld.text().strip(),
            school_pip_cost=self._spip_fld.value(),
            shadow_pip_cost=self._shadow_pip_fld.value(),
            accuracy=self._acc_fld.value(),
            spell_type=self._type_fld.text().strip(),
            pvp_level=self._pvplevel_fld.text().strip(),
            description=self._desc_fld.toPlainText().strip(),
        )
        updated = ds.get_spell(self._conn, self._spell["name"])
        if updated:
            self._spell = updated
        self.spell_updated.emit(self._spell["name"])
        # Lightweight, non-blocking confirmation instead of a modal
        # popup — editing should feel immediate, not interrupt the flow
        # with a dialog to dismiss after every single save.
        self._core_save_btn.setText("✓ Saved")
        self._core_save_btn.setStyleSheet(
            "QPushButton{background:#156b40;color:#9fffcf;border:1px solid #1f6a44;"
            "border-radius:5px;padding:6px 16px;font-size:12px;font-weight:bold;}"
        )
        QTimer.singleShot(1400, self._reset_save_button_label)
        # Note: deliberately NOT calling _populate_body() here — the
        # on-screen fields already show the just-saved values (they're
        # the source of truth for the save), and rebuilding would
        # delete this very button out from under the timer callback
        # above. Only the OCR-fallback warning needs to disappear once
        # a real description has been saved, handled directly below.
        if self._desc_is_ocr_fallback and self._desc_fld.toPlainText().strip():
            self._desc_is_ocr_fallback = False
            if self._desc_fallback_hint is not None:
                self._desc_fallback_hint.setVisible(False)

    def _reset_save_button_label(self):
        try:
            self._core_save_btn.setText("💾 Save Fields")
            self._core_save_btn.setStyleSheet(
                "QPushButton{background:#0f4d2e;color:#9fffcf;border:1px solid #1f6a44;"
                "border-radius:5px;padding:6px 16px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#156b40;}"
            )
        except RuntimeError:
            pass  # dialog/button was closed/deleted before the timer fired

    def _refresh_icon_legend(self):
        """Rebuild the list of icon-description rows attached to this spell."""
        while self._icon_rows_v.count():
            it = self._icon_rows_v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        sid = self._spell.get("id")
        linked = ds.get_icons_for_spell(self._conn, sid) if sid is not None else []

        if not linked:
            empty = QLabel("No icons detected or added yet.")
            empty.setStyleSheet(
                "color:#666;font-size:12px;background:transparent;border:none;"
            )
            self._icon_rows_v.addWidget(empty)
            return

        for icon in linked:
            row_w = QWidget()
            # Scope the border to the row frame ONLY. An unscoped
            # "border:1px solid ..." on a QWidget cascades to every child
            # QLabel (QLabel derives from QFrame), which is what drew the
            # stray outline around each icon's name and description text.
            # Using an objectName selector confines it to the container.
            row_w.setObjectName("iconRow")
            row_w.setStyleSheet(
                "QWidget#iconRow{background:#0d1b2a;border:1px solid #c39bd322;"
                "border-radius:6px;}"
            )
            row = QHBoxLayout(row_w)
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(12)

            thumb = QLabel()
            thumb.setFixedSize(44, 44)
            thumb.setAlignment(Qt.AlignCenter)
            img_p = icon.get("image_path", "")
            if img_p and Path(img_p).exists():
                pix = QPixmap(img_p).scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                thumb.setPixmap(pix)
            thumb.setStyleSheet("background:transparent;border:none;")
            row.addWidget(thumb)

            text_v = QVBoxLayout()
            text_v.setSpacing(2)
            name_lbl = QLabel(f"<b>{icon['name']}</b>")
            name_lbl.setStyleSheet(
                "color:#c39bd3;font-size:14px;background:transparent;border:none;"
            )
            text_v.addWidget(name_lbl)
            desc_lbl = QLabel(icon.get("description", ""))
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(
                "color:#aaa;font-size:12px;background:transparent;border:none;"
            )
            text_v.addWidget(desc_lbl)
            row.addLayout(text_v, stretch=1)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(24, 24)
            del_btn.setStyleSheet(
                "QPushButton{background:transparent;color:#666;border:none;font-size:14px;}"
                "QPushButton:hover{color:#e94560;}"
            )
            _icon_id = icon["id"]
            del_btn.clicked.connect(lambda _, iid=_icon_id: self._on_remove_icon(iid))
            row.addWidget(del_btn, 0, Qt.AlignTop)

            self._icon_rows_v.addWidget(row_w)

    def _refresh_icon_add_combo(self):
        self._icon_add_combo.clear()
        # Show the preset's own image (the same one assigned in Icon Presets)
        # to the left of each name, in both the dropdown and the search popup.
        self._icon_add_combo.setIconSize(QSize(22, 22))
        all_presets = ds.list_icon_presets(self._conn)
        sid = self._spell.get("id")
        linked_ids = {i["id"] for i in ds.get_icons_for_spell(self._conn, sid)} if sid is not None else set()
        for p in all_presets:
            if p["id"] in linked_ids:
                continue
            img_p = p.get("image_path", "")
            icon = QIcon(img_p) if img_p and Path(img_p).exists() else QIcon()
            self._icon_add_combo.addItem(icon, p["name"], p["id"])
        # Start blank so the placeholder shows and the user searches from
        # scratch rather than the combo defaulting to the first item.
        if self._icon_add_combo.isEditable():
            self._icon_add_combo.setCurrentIndex(-1)
            self._icon_add_combo.lineEdit().clear()

    def _on_add_icon(self):
        sid = self._spell.get("id")
        if sid is None or self._icon_add_combo.count() == 0:
            return
        # Resolve the chosen preset: prefer an exact (case-insensitive)
        # name match against the current items — the searchable line edit
        # means currentData() can lag behind the typed text.
        text = self._icon_add_combo.currentText().strip()
        preset_id = None
        if text:
            for i in range(self._icon_add_combo.count()):
                if self._icon_add_combo.itemText(i).strip().lower() == text.lower():
                    preset_id = self._icon_add_combo.itemData(i)
                    break
        if preset_id is None:
            preset_id = self._icon_add_combo.currentData()
        if preset_id is None:
            return
        ds.link_icon_to_spell(self._conn, sid, preset_id)
        self._refresh_icon_legend()
        self._refresh_icon_add_combo()

    def _on_remove_icon(self, icon_preset_id: int):
        sid = self._spell.get("id")
        if sid is None:
            return
        ds.unlink_icon_from_spell(self._conn, sid, icon_preset_id)
        self._refresh_icon_legend()
        self._refresh_icon_add_combo()


# ═══════════════════════════════════════════════════════════════════════
# MAIN SPELL BROWSER WIDGET
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# FILTER / SORT HELPERS
# ═══════════════════════════════════════════════════════════════════════

_LEVEL_RE = re.compile(r"Level\s*(\d+)", re.I)
_NUM_RE   = re.compile(r"\d+")

# Training-filter options: (internal key, display label). Derived from the
# rendered-HTML training/fusion data we now store per spell.
_TRAINING_OPTIONS = [
    ("trainable",    "Can be trained"),
    ("nontrainable", "Non-trainable"),
    ("spellement",   "Spellement"),
    ("fusion",       "Fusion (reagent/result)"),
    ("craftable",    "Craftable"),
    ("quest",        "Quest reward"),
]


def _flt_int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except (ValueError, TypeError):
        m = _NUM_RE.search(str(v or ""))
        return int(m.group()) if m else 0


def _derive_spell_level(sp: dict) -> int:
    """
    Trainer-required level parsed from the training info
    ("… (Level 22+ Required)"), matching the number shown on the wiki.
    Falls back to 0 when no trainer level is present.
    """
    ti = sp.get("training_info") or {}
    levels = []
    for sec in ti.get("sections", []):
        for cat in sec.get("categories", []):
            if "trainer" in (cat.get("heading", "") or "").lower():
                for ln in cat.get("lines", []):
                    m = _LEVEL_RE.search(ln or "")
                    if m:
                        levels.append(int(m.group(1)))
    return min(levels) if levels else 0


def _derive_training_flags(sp: dict, tier_bases: set) -> set:
    """Set of training-type keys for a spell (see _TRAINING_OPTIONS)."""
    flags = set()
    ti = sp.get("training_info") or {}
    for sec in ti.get("sections", []):
        for cat in sec.get("categories", []):
            h = (cat.get("heading", "") or "").lower()
            has_lines = bool(cat.get("lines"))
            if "cannot be trained" in h:
                flags.add("nontrainable")
            if "trainer" in h and has_lines:
                flags.add("trainable")
            if "can purchase this spell" in h:
                flags.add("trainable")
            if "crafted" in h:
                flags.add("craftable")
            if "rewarded" in h or "quest" in h:
                flags.add("quest")
            if "spellement" in h and "can be used to learn" in h and "cannot" not in h:
                flags.add("spellement")
    if sp.get("spellement_paths"):
        flags.add("spellement")
    if sp.get("name") in tier_bases:          # has tier variants → spellement
        flags.add("spellement")
    if sp.get("fusion_formulae"):
        flags.add("fusion")
    return flags


def _derive_damage_value(sp: dict) -> int:
    """First numeric damage value for sorting (0 if none)."""
    for field in ("ocr_damage", "ocr_dot_damage", "ocr_heal", "description"):
        m = _NUM_RE.search(str(sp.get(field) or ""))
        if m:
            return int(m.group())
    return 0


class SpellBrowserWidget(QWidget):
    nav_hub = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        ds.init_spell_tables(conn)
        try:
            from icon_preset_seed import get_seed_presets
            ds.seed_icon_presets_if_empty(conn, get_seed_presets())
        except Exception as e:
            logger.debug(f"Icon preset seeding skipped: {e}")

        self._current_school = "All"
        self._search_text    = ""
        self._zoom           = 1.75  # equivalent to pressing ⊕ three times from 1.0
        self._tiles: List[SpellTile] = []
        self._fetch_process  = None
        self._show_tiers     = False   # tier variants excluded by default

        # ── Filter + sort state ──
        self._sort_key   = "name"      # name|pip|level|damage|type
        self._sort_desc  = False
        self._flt_pvp    = "all"       # all|pvp|nopvp
        self._flt_shadow = "all"       # all|yes|no
        self._flt_types    = set()     # selected spell_type values (OR)
        self._flt_icons    = set()     # selected detected-icon names (OR)
        self._flt_training = set()     # selected training keys (OR)
        self._flt_level_min = 0
        self._flt_level_max = 0        # 0 = no upper bound
        self._flt_pvplevel_min = 0
        self._flt_pvplevel_max = 0
        self._known_types: list = []   # currently-shown dynamic type options
        self._known_icons: list = []   # currently-shown dynamic icon options
        self._type_checks: Dict[str, QCheckBox] = {}
        self._icon_checks: Dict[str, QCheckBox] = {}
        self._training_checks: Dict[str, QCheckBox] = {}

        self._build()

    # ── Computed tile dimensions ─────────────────────────────────────
    @property
    def _cw(self): return max(60, int(_BASE_CARD_W * self._zoom))
    @property
    def _ch(self): return max(70, int(_BASE_CARD_H * self._zoom))
    @property
    def _iw(self): return max(50, int(_BASE_IMG_W  * self._zoom))
    @property
    def _ih(self): return max(50, int(_BASE_IMG_H  * self._zoom))

    # ── UI BUILD ─────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background:#16213e;border-bottom:1px solid #0f3460;")
        header.setFixedHeight(54)
        hr = QHBoxLayout(header)
        hr.setContentsMargins(10, 8, 10, 8)
        hr.setSpacing(6)

        back = QPushButton("← Hub")
        back.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back.clicked.connect(self.nav_hub)
        hr.addWidget(back)

        title = QLabel("✨ Spell Browser")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setStyleSheet("color:#c39bd3;background:transparent;")
        hr.addWidget(title)
        hr.addStretch()

        # Search
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍 Search spells…")
        self._search_edit.setFixedWidth(200)
        self._search_edit.textChanged.connect(self._on_search)
        hr.addWidget(self._search_edit)

        # Zoom controls
        zoom_lbl = QLabel("Card size:")
        zoom_lbl.setStyleSheet("color:#666;font-size:11px;background:transparent;")
        hr.addWidget(zoom_lbl)
        for sym, delta, tip in [
            ("−", -ZOOM_STEP, "Smaller cards"),
            ("+", ZOOM_STEP, "Larger cards"),
        ]:
            z = QPushButton(sym)
            z.setToolTip(tip)
            z.setFixedSize(28, 28)
            z.setStyleSheet(
                "QPushButton{background:#0f3460;color:#ffffff;border:none;"
                "border-radius:5px;font-size:16px;font-weight:bold;"
                "font-family:Arial,Segoe UI,sans-serif;}"
                "QPushButton:hover{background:#4d96ff;}"
            )
            z.clicked.connect(lambda _, d=delta: self._change_zoom(d))
            hr.addWidget(z)

        hr.addSpacing(4)

        # Fetch buttons
        for label, tip, slot, bg, hbg in [
            ("⬇ Fetch",        "Fetch a single spell (from search box)", self._fetch_single,  "#1b5c38", "#27ae60"),
            ("⬇ School",       "Fetch all spells in current school",     self._fetch_school,  "#1b5c38", "#27ae60"),
            ("🖼 Images",       "Download missing images",                self._download_images, "#3a1f60", "#6a3fa0"),
            ("♻ Reparse",      "Reparse cached data, no network",        self._reparse_all,   "#0f3460", "#4d96ff"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:#e0e0e0;border:none;"
                "border-radius:5px;padding:5px 10px;font-size:11px;font-weight:bold;}"
                f"QPushButton:hover{{background:{hbg};}}"
            )
            b.clicked.connect(slot)
            hr.addWidget(b)

        # Resume: when ticked, School / All fetches skip spells already in the
        # DB, so an interrupted fetch continues from where it left off (and a
        # routine re-fetch grabs only newly-added spells) instead of grinding
        # back through everything.
        self._resume_chk = QCheckBox("Resume")
        self._resume_chk.setToolTip(
            "Skip spells already fetched — resume an interrupted fetch, or grab "
            "only new spells, without re-processing the ones you already have.")
        self._resume_chk.setStyleSheet(
            "QCheckBox{color:#9fd0b0;font-size:11px;font-weight:bold;spacing:5px;}"
            "QCheckBox::indicator{width:14px;height:14px;}"
        )
        hr.addWidget(self._resume_chk)

        # Stop the running fetch / reparse subprocess.
        self._stop_btn = QPushButton("⏹ Stop")
        self._stop_btn.setToolTip("Stop the current fetch / reparse operation")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setStyleSheet(
            "QPushButton{background:#5c1b1b;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:5px 10px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;}"
            "QPushButton:disabled{background:#2a2130;color:#6a5a6a;}"
        )
        self._stop_btn.clicked.connect(self._stop_fetch)
        hr.addWidget(self._stop_btn)

        # Delete All button
        del_all = QPushButton("🗑 All")
        del_all.setToolTip("Delete ALL spells from the database")
        del_all.setStyleSheet(
            "QPushButton{background:#5c1b1b;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:5px 10px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;}"
        )
        del_all.clicked.connect(lambda: self._on_delete_request("", "all"))
        hr.addWidget(del_all)

        root.addWidget(header)

        # ── Progress output (hidden) ───────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(3)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(False)
        self._progress_bar.setStyleSheet(
            "QProgressBar{background:#0d1b2a;border:none;}"
            "QProgressBar::chunk{background:#c39bd3;}"
        )
        root.addWidget(self._progress_bar)

        self._progress_out = QTextEdit()
        self._progress_out.setReadOnly(True)
        self._progress_out.setFixedHeight(72)
        self._progress_out.setVisible(False)
        self._progress_out.setStyleSheet(
            "background:#0a0a15;color:#a0a0a0;font-family:Consolas;font-size:10px;border:none;"
        )
        root.addWidget(self._progress_out)

        # ── Body: sidebar + grid ──────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)

        # Left panel: schools + sort + filters (scrollable so the dynamic
        # filter lists can grow without clipping).
        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFixedWidth(200)
        sidebar_scroll.setStyleSheet(
            "QScrollArea{background:#16213e;border:none;border-right:1px solid #0f3460;}"
            "QScrollBar:vertical{background:#16213e;width:8px;}"
            "QScrollBar::handle:vertical{background:#0f3460;border-radius:4px;}"
        )
        sidebar = QWidget()
        sidebar.setStyleSheet("background:#16213e;")
        sv = QVBoxLayout(sidebar)
        sv.setContentsMargins(8, 8, 8, 8)
        sv.setSpacing(3)

        # ── SCHOOL ──
        sch_title = QLabel("SCHOOL")
        sch_title.setStyleSheet("color:#555;font-size:9px;font-weight:bold;letter-spacing:1px;")
        sch_title.setAlignment(Qt.AlignCenter)
        sv.addWidget(sch_title)
        sv.addSpacing(4)

        self._school_btns: Dict[str, QPushButton] = {}
        for school in SCHOOL_ORDER:
            btn = QPushButton(school)
            btn.setCheckable(True)
            btn.setChecked(school == "All")
            color = _sc(school)
            btn.setStyleSheet(
                f"QPushButton{{background:#0f1830;color:{color};"
                f"border:1px solid transparent;border-radius:5px;"
                f"padding:5px 8px;font-size:11px;font-weight:bold;text-align:left;}}"
                f"QPushButton:checked{{background:{_rgba(color,'22')};border:1px solid {_rgba(color,'88')};}}"
                f"QPushButton:hover{{background:{_rgba(color,'11')};border:1px solid {_rgba(color,'44')};}}"
            )
            btn.clicked.connect(lambda _, s=school: self._set_school(s))
            sv.addWidget(btn)
            self._school_btns[school] = btn

        self._count_lbl = QLabel("0 shown")
        self._count_lbl.setStyleSheet("color:#777;font-size:10px;padding-top:4px;")
        self._count_lbl.setAlignment(Qt.AlignCenter)
        sv.addWidget(self._count_lbl)

        # ── SORT (detached) ──
        sv.addSpacing(10)
        sort_title = QLabel("SORT BY")
        sort_title.setStyleSheet("color:#555;font-size:9px;font-weight:bold;letter-spacing:1px;")
        sort_title.setAlignment(Qt.AlignCenter)
        sv.addWidget(sort_title)
        sort_row = QHBoxLayout()
        sort_row.setSpacing(4)
        self._sort_combo = QComboBox()
        for lbl, key in [("Name", "name"), ("Pip cost", "pip"), ("Level", "level"),
                         ("Damage", "damage"), ("Type", "type")]:
            self._sort_combo.addItem(lbl, key)
        self._sort_combo.setStyleSheet(_FILTER_COMBO_SS)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        sort_row.addWidget(self._sort_combo, 1)
        self._sort_dir_btn = QPushButton("↑")
        self._sort_dir_btn.setToolTip("Ascending / descending")
        self._sort_dir_btn.setFixedWidth(30)
        self._sort_dir_btn.setStyleSheet(
            "QPushButton{background:#0f1830;color:#c8c8c8;border:1px solid #2a3a5a;"
            "border-radius:5px;padding:4px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        self._sort_dir_btn.clicked.connect(self._toggle_sort_dir)
        sort_row.addWidget(self._sort_dir_btn)
        sv.addLayout(sort_row)

        # ── FILTERS (visually detached panel) ──
        sv.addSpacing(10)
        filt = QFrame()
        filt.setObjectName("filterPanel")
        filt.setStyleSheet(
            "QFrame#filterPanel{background:#121c38;border:1px solid #23345c;border-radius:8px;}"
            "QLabel{background:transparent;}"
        )
        fv = QVBoxLayout(filt)
        fv.setContentsMargins(8, 8, 8, 10)
        fv.setSpacing(6)

        head = QHBoxLayout()
        ftitle = QLabel("FILTERS")
        ftitle.setStyleSheet("color:#c39bd3;font-size:10px;font-weight:bold;letter-spacing:1px;")
        head.addWidget(ftitle)
        head.addStretch()
        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#888;border:none;font-size:10px;"
            "text-decoration:underline;}QPushButton:hover{color:#e94560;}"
        )
        reset_btn.clicked.connect(self._reset_filters)
        head.addWidget(reset_btn)
        fv.addLayout(head)

        # PvP — driven by the detected "No PvP" / "PvP Only" icons, not a flag.
        fv.addWidget(self._filter_heading("PvP"))
        self._pvp_combo = QComboBox()
        for lbl, key in [("All", "all"), ("PvP", "pvp"),
                         ("No PvP", "nopvp"), ("PvP Only", "pvponly")]:
            self._pvp_combo.addItem(lbl, key)
        self._pvp_combo.setStyleSheet(_FILTER_COMBO_SS)
        self._pvp_combo.currentIndexChanged.connect(
            lambda: self._set_and_refresh("_flt_pvp", self._pvp_combo.currentData()))
        fv.addWidget(self._pvp_combo)

        # Shadow pips
        fv.addWidget(self._filter_heading("Shadow Pips"))
        self._shadow_combo = QComboBox()
        for lbl, key in [("All", "all"), ("Yes", "yes"), ("No", "no")]:
            self._shadow_combo.addItem(lbl, key)
        self._shadow_combo.setStyleSheet(_FILTER_COMBO_SS)
        self._shadow_combo.currentIndexChanged.connect(
            lambda: self._set_and_refresh("_flt_shadow", self._shadow_combo.currentData()))
        fv.addWidget(self._shadow_combo)

        # Level (from / to)
        fv.addWidget(self._filter_heading("Level (trainer)"))
        lvl_row = QHBoxLayout()
        lvl_row.setSpacing(4)
        from_lbl = QLabel("From"); from_lbl.setStyleSheet("color:#888;font-size:10px;")
        to_lbl = QLabel("To");   to_lbl.setStyleSheet("color:#888;font-size:10px;")
        self._lvl_min = QSpinBox(); self._lvl_max = QSpinBox()
        for sb in (self._lvl_min, self._lvl_max):
            sb.setRange(0, 170)
            sb.setStyleSheet(_FILTER_SPIN_SS)
            sb.valueChanged.connect(self._on_level_changed)
        lvl_row.addWidget(from_lbl); lvl_row.addWidget(self._lvl_min, 1)
        lvl_row.addWidget(to_lbl);   lvl_row.addWidget(self._lvl_max, 1)
        fv.addLayout(lvl_row)
        hint = QLabel("(To = 0 means no max)")
        hint.setStyleSheet("color:#666;font-size:9px;")
        fv.addWidget(hint)

        # PvP Level (from / to) — second level filter, on the wiki "PvP Level".
        fv.addWidget(self._filter_heading("PvP Level"))
        pvpl_row = QHBoxLayout()
        pvpl_row.setSpacing(4)
        pf = QLabel("From"); pf.setStyleSheet("color:#888;font-size:10px;")
        pt = QLabel("To");   pt.setStyleSheet("color:#888;font-size:10px;")
        self._pvplvl_min = QSpinBox(); self._pvplvl_max = QSpinBox()
        for sb in (self._pvplvl_min, self._pvplvl_max):
            sb.setRange(0, 170)
            sb.setStyleSheet(_FILTER_SPIN_SS)
            sb.valueChanged.connect(self._on_level_changed)
        pvpl_row.addWidget(pf); pvpl_row.addWidget(self._pvplvl_min, 1)
        pvpl_row.addWidget(pt); pvpl_row.addWidget(self._pvplvl_max, 1)
        fv.addLayout(pvpl_row)

        # Training
        fv.addWidget(self._filter_heading("Training"))
        for key, label in _TRAINING_OPTIONS:
            cb = QCheckBox(label)
            cb.setStyleSheet(_FILTER_CB_SS)
            cb.toggled.connect(self._on_training_toggled)
            self._training_checks[key] = cb
            fv.addWidget(cb)

        # Spell type (dynamic)
        fv.addWidget(self._filter_heading("Spell Type"))
        self._type_box = QVBoxLayout()
        self._type_box.setSpacing(2)
        fv.addLayout(self._type_box)

        # Detected icons (dynamic)
        fv.addWidget(self._filter_heading("Detected Icons"))
        self._icon_box = QVBoxLayout()
        self._icon_box.setSpacing(2)
        fv.addLayout(self._icon_box)

        sv.addWidget(filt)
        sv.addStretch()

        sidebar_scroll.setWidget(sidebar)
        body_row.addWidget(sidebar_scroll)

        # Grid area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#1a1a2e;}"
            "QScrollBar:vertical{background:#16213e;width:8px;}"
            "QScrollBar::handle:vertical{background:#0f3460;border-radius:4px;}"
        )
        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background:#1a1a2e;")
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(6)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        scroll.setWidget(self._grid_container)
        body_row.addWidget(scroll, stretch=1)

        root.addWidget(body, stretch=1)

        QTimer.singleShot(50, self.refresh)

    # ── SCHOOL / SEARCH / ZOOM ───────────────────────────────────────

    def _set_school(self, school: str):
        self._current_school = school
        for n, b in self._school_btns.items():
            b.setChecked(n == school)
        self.refresh()

    def _on_search(self, text: str):
        self._search_text = text.strip()
        QTimer.singleShot(280, self.refresh)

    def _change_zoom(self, delta: float):
        self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom + delta))
        self.refresh()

    # ── FILTER / SORT CONTROLS ───────────────────────────────────────

    def _filter_heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#7a88a8;font-size:10px;font-weight:bold;padding-top:2px;")
        return lbl

    def _set_and_refresh(self, attr: str, value):
        setattr(self, attr, value)
        self.refresh()

    def _on_sort_changed(self):
        self._sort_key = self._sort_combo.currentData()
        self.refresh()

    def _toggle_sort_dir(self):
        self._sort_desc = not self._sort_desc
        self._sort_dir_btn.setText("↓" if self._sort_desc else "↑")
        self.refresh()

    def _on_level_changed(self):
        self._flt_level_min = self._lvl_min.value()
        self._flt_level_max = self._lvl_max.value()
        self._flt_pvplevel_min = self._pvplvl_min.value()
        self._flt_pvplevel_max = self._pvplvl_max.value()
        self.refresh()

    def _on_training_toggled(self):
        self._flt_training = {k for k, cb in self._training_checks.items() if cb.isChecked()}
        self.refresh()

    def _on_type_toggled(self, opt: str, checked: bool):
        (self._flt_types.add if checked else self._flt_types.discard)(opt)
        self.refresh()

    def _on_icon_toggled(self, opt: str, checked: bool):
        (self._flt_icons.add if checked else self._flt_icons.discard)(opt)
        self.refresh()

    def _reset_filters(self):
        self._flt_pvp = "all"; self._flt_shadow = "all"
        self._flt_types = set(); self._flt_icons = set(); self._flt_training = set()
        self._flt_level_min = 0; self._flt_level_max = 0
        self._flt_pvplevel_min = 0; self._flt_pvplevel_max = 0
        widgets = [self._pvp_combo, self._shadow_combo, self._lvl_min, self._lvl_max,
                   self._pvplvl_min, self._pvplvl_max,
                   *self._training_checks.values(),
                   *self._type_checks.values(), *self._icon_checks.values()]
        for w in widgets:
            w.blockSignals(True)
        self._pvp_combo.setCurrentIndex(0)
        self._shadow_combo.setCurrentIndex(0)
        self._lvl_min.setValue(0); self._lvl_max.setValue(0)
        self._pvplvl_min.setValue(0); self._pvplvl_max.setValue(0)
        for cb in (*self._training_checks.values(),
                   *self._type_checks.values(), *self._icon_checks.values()):
            cb.setChecked(False)
        for w in widgets:
            w.blockSignals(False)
        self.refresh()

    def _sync_dynamic_filters(self, avail_types, avail_icons, icon_img_map=None):
        """Rebuild the dynamic Type / Icon checkbox lists when the available
        options change, preserving current selections."""
        if avail_types != self._known_types:
            self._known_types = avail_types
            self._flt_types &= set(avail_types)
            self._rebuild_check_group(self._type_box, self._type_checks,
                                      avail_types, self._flt_types, self._on_type_toggled)
        if avail_icons != self._known_icons:
            self._known_icons = avail_icons
            self._flt_icons &= set(avail_icons)
            self._rebuild_check_group(self._icon_box, self._icon_checks,
                                      avail_icons, self._flt_icons, self._on_icon_toggled,
                                      icon_map=icon_img_map)

    def _rebuild_check_group(self, box, store, options, selected, handler, icon_map=None):
        store.clear()
        while box.count():
            it = box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not options:
            empty = QLabel("—")
            empty.setStyleSheet("color:#555;font-size:10px;")
            box.addWidget(empty)
            return
        for opt in options:
            cb = QCheckBox(opt)
            cb.setStyleSheet(_FILTER_CB_SS)
            if icon_map:
                p = icon_map.get(opt, "")
                if p and Path(p).exists():
                    cb.setIcon(QIcon(p))
                    cb.setIconSize(QSize(22, 22))   # same size as the "＋ Add" dropdown
            cb.setChecked(opt in selected)
            cb.toggled.connect(lambda ch, o=opt: handler(o, ch))
            store[opt] = cb
            box.addWidget(cb)

    def _stop_fetch(self):
        if self._fetch_process and self._fetch_process.state() != QProcess.NotRunning:
            self._progress_out.append("\n⏹ Stopping — finishing the current spell, then halting…")
            self._stop_btn.setEnabled(False)
            self._fetch_process.kill()

    # ── FILTER / SORT APPLICATION ────────────────────────────────────

    def _passes_filters(self, sp: dict) -> bool:
        icons = sp.get("_icons", set())
        # PvP status comes from the detected icons now, not the pvp flag.
        if self._flt_pvp == "nopvp" and "No PvP" not in icons:
            return False
        if self._flt_pvp == "pvp" and "No PvP" in icons:
            return False
        if self._flt_pvp == "pvponly" and "PvP Only" not in icons:
            return False
        shadow = _flt_int(sp.get("shadow_pip_cost")) > 0
        if self._flt_shadow == "yes" and not shadow:
            return False
        if self._flt_shadow == "no" and shadow:
            return False
        lvl = sp.get("_level", 0)
        if self._flt_level_min and lvl < self._flt_level_min:
            return False
        if self._flt_level_max and lvl > self._flt_level_max:
            return False
        pvpl = sp.get("_pvplevel", 0)
        if self._flt_pvplevel_min and pvpl < self._flt_pvplevel_min:
            return False
        if self._flt_pvplevel_max and pvpl > self._flt_pvplevel_max:
            return False
        if self._flt_types and (sp.get("spell_type") or "") not in self._flt_types:
            return False
        if self._flt_icons and not (self._flt_icons & icons):
            return False
        if self._flt_training and not (self._flt_training & sp.get("_training", set())):
            return False
        return True

    def _sort_spells(self, spells: list) -> list:
        key = self._sort_key
        rev = self._sort_desc
        if key == "pip":
            spells.sort(key=lambda s: (ds.pip_sort_key(s.get("pip_cost", "0")), s["name"].lower()), reverse=rev)
        elif key == "level":
            spells.sort(key=lambda s: (s.get("_level", 0), s["name"].lower()), reverse=rev)
        elif key == "damage":
            spells.sort(key=lambda s: (_derive_damage_value(s), s["name"].lower()), reverse=rev)
        elif key == "type":
            spells.sort(key=lambda s: ((s.get("spell_type") or "").lower(), s["name"].lower()), reverse=rev)
        else:  # name
            spells.sort(key=lambda s: s["name"].lower(), reverse=rev)
        return spells

    # ── GRID REFRESH ─────────────────────────────────────────────────

    def refresh(self):
        self._tiles.clear()
        while self._grid_layout.count():
            it = self._grid_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        # One query for everything: all base spells (across schools), so the
        # per-school counts and the dynamic filter option lists are stable.
        all_base = ds.list_spells_base_only(self.conn)
        icon_links = ds.get_all_spell_icon_links(self.conn)
        tier_bases = ds.get_tier_base_names(self.conn)
        for sp in all_base:
            sp["_icons"]    = icon_links.get(sp.get("id"), set())
            sp["_level"]    = _derive_spell_level(sp)
            sp["_pvplevel"] = _flt_int(sp.get("pvp_level", ""))
            sp["_training"] = _derive_training_flags(sp, tier_bases)

        # Per-school counts on the school buttons.
        counts = ds.count_spells_by_school(self.conn)
        for school, btn in self._school_btns.items():
            btn.setText(f"{school}  ({counts.get(school, 0)})")

        # Dynamic filter options: spell types from the data; detected-icon
        # options from ALL icon presets (so a newly-added preset shows up
        # here immediately, even before it's linked to any spell), each with
        # its preset image.
        avail_types = sorted({(sp.get("spell_type") or "").strip()
                              for sp in all_base if (sp.get("spell_type") or "").strip()})
        presets = ds.list_icon_presets(self.conn)
        avail_icons = [p["name"] for p in presets]
        icon_img_map = {p["name"]: p.get("image_path", "") for p in presets}
        self._sync_dynamic_filters(avail_types, avail_icons, icon_img_map)

        # Apply school + search + filters.
        srch = (self._search_text or "").lower()
        spells = [
            sp for sp in all_base
            if (self._current_school == "All" or sp.get("school") == self._current_school)
            and (not srch or srch in sp["name"].lower())
            and self._passes_filters(sp)
        ]
        spells = self._sort_spells(spells)

        self._count_lbl.setText(f"{len(spells)} shown")

        if not spells:
            msg = ("No spells found.\nUse ⬇ Fetch to download from the wiki."
                   if not (self._search_text or self._any_filter_active())
                   else "No spells match the current search / filters.")
            lbl = QLabel(msg)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color:#555;font-size:13px;")
            self._grid_layout.addWidget(lbl, 0, 0, 1, GRID_COLS)
            return

        for idx, spell in enumerate(spells):
            tile = SpellTile(spell, self._cw, self._ch, self._iw, self._ih)
            # Queued connection: the click handler (which opens a modal
            # dialog) must run AFTER SpellTile.mousePressEvent has fully
            # returned. Otherwise a Save inside the dialog can trigger a
            # refresh() that deletes this tile while its mousePressEvent
            # is still on the call stack → "wrapped C/C++ object ...
            # has been deleted" crash.
            tile.clicked.connect(self._on_spell_clicked, Qt.QueuedConnection)
            tile.delete_req.connect(self._on_delete_request)
            row, col = divmod(idx, GRID_COLS)
            self._grid_layout.addWidget(tile, row, col)
            self._tiles.append(tile)

        last_row = (len(spells) - 1) // GRID_COLS + 1
        self._grid_layout.setRowStretch(last_row, 1)

    def _any_filter_active(self) -> bool:
        return bool(
            self._flt_pvp != "all" or self._flt_shadow != "all"
            or self._flt_types or self._flt_icons or self._flt_training
            or self._flt_level_min or self._flt_level_max
            or self._flt_pvplevel_min or self._flt_pvplevel_max
        )

    # ── SPELL DETAIL ─────────────────────────────────────────────────

    def _on_spell_clicked(self, name: str):
        # Runs as a QUEUED slot (see refresh()), i.e. on a clean event-loop
        # turn *after* the originating SpellTile.mousePressEvent has fully
        # returned. That means it is safe to delete/recreate tiles here (or
        # from a Save inside the dialog) without invalidating an event that
        # is still being processed. A single refresh once the dialog closes
        # is therefore all that's needed — no fragile singleShot juggling.
        spell = ds.get_spell(self.conn, name)
        if not spell:
            return
        dlg = SpellDetailDialog(spell, self.conn, parent=self)
        dlg.exec_()
        self.refresh()

    # ── DELETE ───────────────────────────────────────────────────────

    def _on_delete_request(self, name: str, mode: str):
        if mode == "single":
            if QMessageBox.question(
                self, "Delete Spell", f"Delete '{name}' from the database?",
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            ds.delete_spell(self.conn, name)

        elif mode == "school":
            count = ds.get_spell_count(self.conn, school=name)
            if QMessageBox.question(
                self, "Delete School",
                f"Delete all {count} {name} spells?",
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            ds.delete_spells_by_school(self.conn, name)

        elif mode == "all":
            count = ds.get_spell_count(self.conn)
            if count == 0:
                QMessageBox.information(self, "Empty", "No spells to delete.")
                return
            if QMessageBox.question(
                self, "Delete ALL Spells",
                f"Permanently delete ALL {count} spells?\nThis cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            ds.delete_all_spells(self.conn)

        self.refresh()

    # ── SUBPROCESS ───────────────────────────────────────────────────

    def _fetch_single(self):
        name = self._search_edit.text().strip()
        if not name:
            from PyQt5.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "Fetch Spell", "Enter spell name (e.g. Vengeance):"
            )
            if not ok or not name.strip():
                return
        self._run_scraper(["--spell", name.strip()], f"Fetching {name}…")

    def _fetch_school(self):
        resume = ["--resume"] if self._resume_chk.isChecked() else []
        rlabel = " (resume — skipping already-fetched)" if resume else ""
        school = self._current_school
        if school == "All":
            if QMessageBox.question(
                self, "Fetch ALL Schools",
                "Fetch all spells from all 11 schools? This may take a long time."
                + ("\n\nResume is on: spells already in your database will be skipped."
                   if resume else ""),
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            self._run_scraper(["--all"] + resume, f"Fetching all spells{rlabel}…")
        else:
            self._run_scraper(["--school", school] + resume, f"Fetching {school} spells{rlabel}…")

    def _reparse_all(self):
        self._run_scraper(["--reparse"], "Reparsing cached spells…")

    def _download_images(self):
        self._run_scraper(["--images"], "Downloading missing images…")

    def _run_scraper(self, args: list, label: str):
        if self._fetch_process and self._fetch_process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Busy", "A fetch is already running.")
            return
        self._progress_bar.setVisible(True)
        self._progress_out.setVisible(True)
        self._progress_out.clear()
        self._progress_out.append(f"▶ {label}")

        self._fetch_process = QProcess(self)
        self._fetch_process.setProcessChannelMode(QProcess.MergedChannels)
        self._fetch_process.readyReadStandardOutput.connect(self._on_fetch_out)
        self._fetch_process.finished.connect(self._on_fetch_done)
        self._fetch_process.start(sys.executable, [SPELL_SCRAPER] + args)
        self._stop_btn.setEnabled(True)

    def _on_fetch_out(self):
        data = self._fetch_process.readAllStandardOutput().data().decode(
            "utf-8", errors="replace"
        )
        # Cloudflare "verify you are human" checkbox appeared in the scraper's
        # Chrome window → play the alert sound so the user knows to click it.
        if cf_alert is not None and cf_alert.MARKER in data:
            _play_cf_alert()
            data = cf_alert.strip_marker(data)
        self._progress_out.append(data.rstrip())
        sb = self._progress_out.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_fetch_done(self, code, status):
        self._stop_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        stopped = status == QProcess.CrashExit  # killed by the Stop button
        if stopped:
            self._progress_out.append(
                "\n⏹ Stopped. Spells finished before stopping are saved. "
                "Tick “Resume” and Fetch again to continue where you left off.")
        else:
            self._progress_out.append(f"\n{'[OK]' if code == 0 else '[FAIL]'} Done (exit {code}).")
        QTimer.singleShot(6000, lambda: self._progress_out.setVisible(False))
        self.refresh()
