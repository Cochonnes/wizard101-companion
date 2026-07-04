"""
deck_builder.py  v2
════════════════════
Deck Builder — create, edit and share Wizard101 deck presets.

Changes from v1:
  • CSS fix: default color "#888" → "#888888" everywhere (avoids 5-char invalid hex)
  • Center panel and spell picker now share space equally via splitter stretch
  • Saved-deck list: search field + school filter + sort (Name / School / Tag / Cards)
  • Left panel width: 220 (slightly wider to fit filter widgets)
"""

import re
import sys
from pathlib import Path
from typing import Optional, List, Dict

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QFrame, QDialog, QComboBox,
    QListWidget, QListWidgetItem, QSplitter, QTextEdit, QGridLayout,
    QSizePolicy, QMessageBox, QInputDialog, QApplication, QMenu
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QBrush, QPixmap

import database_spells as ds

# ── Constants ────────────────────────────────────────────────────────
MAX_MAIN_DECK   = 64
MAX_SIDE_DECK   = 14
MAX_COPIES      = 4
MAX_SIDE_COPIES = 7

SCHOOL_COLORS = ds.SCHOOL_COLORS
SCHOOLS       = ["Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance",
                 "Star", "Moon", "Sun", "Shadow"]
DECK_TAGS     = ds.DECK_TAGS


def _full_color(color: str) -> str:
    """Expand 3-char hex (#RGB) to 6-char (#RRGGBB) so alpha suffixes stay valid."""
    if color.startswith("#") and len(color) == 4:
        return "#" + "".join(c * 2 for c in color[1:])
    return color


def _sc(school: str) -> str:
    """Return a guaranteed 6-char hex color for a school (safe for alpha suffixes)."""
    return _full_color(SCHOOL_COLORS.get(school, "#888888"))


class ClickableImageLabel(QLabel):
    """
    QLabel that emits a 'clicked' signal on left-click.

    IMPORTANT: never assign a lambda directly to mousePressEvent — PyQt's
    sip layer requires virtual-method overrides to return None, and a
    lambda whose body evaluates to dialog.exec_() returns an int, which
    raises "TypeError: invalid argument to sipBadCatcherResult()" and
    crashes the app. A proper signal/slot avoids this entirely.
    """
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageZoomDialog(QDialog):
    """Enlarged spell card image popup, used by both the spell picker and
    the main/side deck card slots."""

    def __init__(self, image_path: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("QDialog{background:#0d1b2a;}")
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignCenter)
        pix = QPixmap(image_path) if image_path else QPixmap()
        if not pix.isNull():
            screen = QApplication.primaryScreen().availableGeometry()
            max_w  = int(screen.width()  * 0.5)
            max_h  = int(screen.height() * 0.7)
            scaled = pix.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            lbl.setPixmap(scaled)
            self.resize(scaled.width() + 20, scaled.height() + 50)
        else:
            lbl.setText("No image downloaded for this spell yet.")
            lbl.setStyleSheet("color:#888;font-size:13px;padding:30px;")
            self.resize(320, 160)
        v.addWidget(lbl)
        close = QPushButton("Close")
        close.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 18px;}"
            "QPushButton:hover{background:#e94560;}"
        )
        close.clicked.connect(self.accept)
        v.addWidget(close, 0, Qt.AlignCenter)


def _rgba(hex_color: str, alpha_hex: str) -> str:
    """
    Convert #RRGGBB + 2-char hex alpha to rgba(r,g,b,a).
    Qt's CSS parser does NOT support #RRGGBBAA format; use rgba() instead.
    """
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{int(alpha_hex, 16)})"


# ═══════════════════════════════════════════════════════════════════════
# CARD SLOT WIDGET
# ═══════════════════════════════════════════════════════════════════════

class CardSlot(QFrame):
    changed    = pyqtSignal()
    remove_req = pyqtSignal(str, int)

    def __init__(self, card: dict, conn, is_side: bool = False, parent=None):
        super().__init__(parent)
        self._card = card
        self._conn = conn
        self._side = is_side
        self.setFixedHeight(58)
        self.setObjectName("cardSlot")
        color = _sc(card.get("spell_school", ""))
        self.setStyleSheet(
            f"QFrame#cardSlot{{background:#0d1b2a;border:1px solid {_rgba(color,'33')};"
            f"border-radius:4px;margin:1px;}}"
            f"QFrame#cardSlot:hover{{border:1px solid {_rgba(color,'88')};}}"
        )
        self._build()

    def _build(self):
        h = QHBoxLayout(self)
        h.setContentsMargins(4, 4, 6, 4)
        h.setSpacing(8)

        school = self._card.get("spell_school", "")
        color  = _sc(school)
        name   = self._card.get("spell_name", "?")

        # Clickable card-image thumbnail
        thumb = ClickableImageLabel()
        thumb.setFixedSize(38, 50)
        thumb.setAlignment(Qt.AlignCenter)
        sp_data = ds.get_spell(self._conn, name) if name else None
        img_path = sp_data.get("image_path") if sp_data else None
        if img_path and Path(img_path).exists():
            pix = QPixmap(img_path).scaled(36, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            thumb.setPixmap(pix)
            thumb.setStyleSheet("background:transparent;border:none;")
        else:
            pix = QPixmap(36, 48)
            c = QColor(color)
            c.setAlpha(110)
            pix.fill(c)
            thumb.setPixmap(pix)
            thumb.setStyleSheet("background:transparent;border:none;")
        thumb.setCursor(Qt.PointingHandCursor)
        thumb.setToolTip(f"{name}\nClick to enlarge")
        thumb.clicked.connect(
            lambda p=img_path, n=name: ImageZoomDialog(p, n, self).exec_()
        )
        h.addWidget(thumb)

        if school:
            pip_lbl = QLabel(school[:3])
            pip_lbl.setFixedWidth(28)
            pip_lbl.setAlignment(Qt.AlignCenter)
            pip_lbl.setStyleSheet(
                f"background:{_rgba(color,'22')};color:{color};"
                "border-radius:3px;font-size:9px;font-weight:bold;"
            )
            h.addWidget(pip_lbl)

        name_lbl = QLabel(name)
        name_lbl.setStyleSheet("color:#d0d0d0;font-size:11px;background:transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_lbl.setWordWrap(True)
        h.addWidget(name_lbl, stretch=1)

        for sym, fn in [("−", self._on_minus), ("＋", self._on_plus)]:
            btn = QPushButton(sym)
            btn.setFixedSize(20, 20)
            btn.setStyleSheet(
                "QPushButton{background:#1a1a2e;color:#888;border:none;"
                "border-radius:3px;font-size:13px;font-weight:bold;}"
                "QPushButton:hover{background:#e94560;color:#fff;}"
            )
            btn.clicked.connect(fn)
            h.addWidget(btn)
            if sym == "−":
                self._qty_lbl = QLabel(f"×{self._card.get('quantity', 1)}")
                self._qty_lbl.setFixedWidth(24)
                self._qty_lbl.setAlignment(Qt.AlignCenter)
                self._qty_lbl.setStyleSheet(
                    "color:#ffd93d;font-size:12px;font-weight:bold;background:transparent;"
                )
                h.addWidget(self._qty_lbl)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(20, 20)
        del_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#444;border:none;font-size:11px;}"
            "QPushButton:hover{color:#e94560;}"
        )
        del_btn.clicked.connect(self._on_remove)
        h.addWidget(del_btn)

    def _on_plus(self):
        limit = MAX_SIDE_COPIES if self._side else MAX_COPIES
        qty = int(self._card.get("quantity", 1))
        if qty < limit:
            self._card["quantity"] = qty + 1
            self._qty_lbl.setText(f"×{self._card['quantity']}")
            self.changed.emit()

    def _on_minus(self):
        qty = int(self._card.get("quantity", 1))
        if qty > 1:
            self._card["quantity"] = qty - 1
            self._qty_lbl.setText(f"×{self._card['quantity']}")
            self.changed.emit()
        else:
            self._on_remove()

    def _on_remove(self):
        self.remove_req.emit(self._card.get("spell_name", ""), 1 if self._side else 0)

    def get_card(self) -> dict:
        return dict(self._card)


# ═══════════════════════════════════════════════════════════════════════
# DECK PANEL
# ═══════════════════════════════════════════════════════════════════════

class DeckPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._main_cards: List[dict] = []
        self._side_cards: List[dict] = []
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#1a1a2e;}"
            "QScrollBar:vertical{background:#16213e;width:7px;}"
            "QScrollBar::handle:vertical{background:#0f3460;border-radius:3px;}"
        )
        inner = QWidget()
        inner.setStyleSheet("background:#1a1a2e;")
        self._deck_v = QVBoxLayout(inner)
        self._deck_v.setContentsMargins(8, 6, 8, 8)
        self._deck_v.setSpacing(2)

        self._main_header = QLabel("MAIN DECK  (0 / 64)")
        self._main_header.setStyleSheet(
            "color:#c39bd3;font-size:10px;font-weight:bold;letter-spacing:1px;"
            "background:transparent;padding:4px 0 2px 0;"
        )
        self._deck_v.addWidget(self._main_header)

        self._main_frame = QWidget()
        self._main_frame.setStyleSheet("background:transparent;")
        self._main_v = QVBoxLayout(self._main_frame)
        self._main_v.setContentsMargins(0, 0, 0, 0)
        self._main_v.setSpacing(2)
        self._deck_v.addWidget(self._main_frame)

        self._deck_v.addSpacing(8)
        self._side_header = QLabel("SIDE DECK  (0 / 14)")
        self._side_header.setStyleSheet(
            "color:#4d96ff;font-size:10px;font-weight:bold;letter-spacing:1px;"
            "background:transparent;padding:4px 0 2px 0;"
        )
        self._deck_v.addWidget(self._side_header)

        self._side_frame = QWidget()
        self._side_frame.setStyleSheet("background:transparent;")
        self._side_v = QVBoxLayout(self._side_frame)
        self._side_v.setContentsMargins(0, 0, 0, 0)
        self._side_v.setSpacing(2)
        self._deck_v.addWidget(self._side_frame)
        self._deck_v.addStretch()

        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

    def load_cards(self, main: List[dict], side: List[dict]):
        self._main_cards = [dict(c) for c in main]
        self._side_cards = [dict(c) for c in side]
        self._rebuild()

    def _rebuild(self):
        for layout in (self._main_v, self._side_v):
            while layout.count():
                it = layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()

        total_main = sum(c.get("quantity", 1) for c in self._main_cards)
        total_side = sum(c.get("quantity", 1) for c in self._side_cards)
        self._main_header.setText(f"MAIN DECK  ({total_main} / {MAX_MAIN_DECK})")
        self._side_header.setText(f"SIDE DECK  ({total_side} / {MAX_SIDE_DECK})")

        for card in self._main_cards:
            slot = CardSlot(card, self.conn, is_side=False)
            slot.changed.connect(self._on_slot_changed)
            slot.remove_req.connect(self._remove_card)
            self._main_v.addWidget(slot)

        for card in self._side_cards:
            slot = CardSlot(card, self.conn, is_side=True)
            slot.changed.connect(self._on_slot_changed)
            slot.remove_req.connect(self._remove_card)
            self._side_v.addWidget(slot)

    def _on_slot_changed(self):
        for i in range(self._main_v.count()):
            w = self._main_v.itemAt(i).widget()
            if isinstance(w, CardSlot):
                self._main_cards[i] = w.get_card()
        for i in range(self._side_v.count()):
            w = self._side_v.itemAt(i).widget()
            if isinstance(w, CardSlot):
                self._side_cards[i] = w.get_card()
        self._update_headers()
        self.changed.emit()

    def _update_headers(self):
        total_main = sum(c.get("quantity", 1) for c in self._main_cards)
        total_side = sum(c.get("quantity", 1) for c in self._side_cards)
        self._main_header.setText(f"MAIN DECK  ({total_main} / {MAX_MAIN_DECK})")
        self._side_header.setText(f"SIDE DECK  ({total_side} / {MAX_SIDE_DECK})")

    def _remove_card(self, spell_name: str, is_side: int):
        if is_side:
            self._side_cards = [c for c in self._side_cards if c.get("spell_name") != spell_name]
        else:
            self._main_cards = [c for c in self._main_cards if c.get("spell_name") != spell_name]
        self._rebuild()
        self.changed.emit()

    def add_spell(self, spell_name: str, spell_school: str, to_side: bool = False):
        cards   = self._side_cards if to_side else self._main_cards
        limit   = MAX_SIDE_COPIES  if to_side else MAX_COPIES
        max_tot = MAX_SIDE_DECK    if to_side else MAX_MAIN_DECK
        total   = sum(c.get("quantity", 1) for c in cards)

        for card in cards:
            if card.get("spell_name", "").lower() == spell_name.lower():
                if card.get("quantity", 1) < limit:
                    card["quantity"] = card.get("quantity", 1) + 1
                    self._rebuild()
                    self.changed.emit()
                return

        if total >= max_tot:
            which = "side deck" if to_side else "main deck"
            QMessageBox.warning(self, "Deck Full", f"The {which} is full ({max_tot} cards max).")
            return

        cards.append({
            "spell_name":   spell_name,
            "spell_school": spell_school,
            "quantity":     1,
            "is_side_deck": 1 if to_side else 0,
        })
        self._rebuild()
        self.changed.emit()

    def get_cards(self) -> dict:
        return {
            "main": [dict(c) for c in self._main_cards],
            "side": [dict(c) for c in self._side_cards],
        }

    def clear(self):
        self._main_cards.clear()
        self._side_cards.clear()
        self._rebuild()


# ═══════════════════════════════════════════════════════════════════════
# SPELL PICKER PANEL
# ═══════════════════════════════════════════════════════════════════════

class SpellPickerTile(QFrame):
    """
    Clickable spell card tile for the deck-builder picker. Shows ONLY
    the card image — the spell name is never rendered as a visible
    label (kept purely as tooltip + internal data): images visible,
    names hidden but still searchable via the search box (which
    filters against the database, independent of what's shown).
    """
    picked = pyqtSignal(str, str, bool)  # name, school, to_side

    TILE_W, TILE_H = 118, 152
    IMG_W,  IMG_H  = 110, 142

    def __init__(self, spell: dict, parent=None):
        super().__init__(parent)
        self._name   = spell["name"]
        self._school = spell.get("school", "")
        self._img    = spell.get("image_path", "")
        self.setFixedSize(self.TILE_W, self.TILE_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{self._name}\n({self._school}, {spell.get('pip_cost','0')} pip)\n"
                        "Double-click → main deck\nRight-click → side deck")
        color = _sc(self._school)
        self.setStyleSheet(
            f"QFrame{{background:#0d1b2a;border:1px solid {_rgba(color,'44')};border-radius:6px;}}"
            f"QFrame:hover{{border:2px solid {color};background:#16213e;}}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(3, 3, 3, 3)
        img_lbl = QLabel()
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setStyleSheet("background:transparent;border:none;")
        if self._img and Path(self._img).exists():
            pix = QPixmap(self._img).scaled(
                self.IMG_W, self.IMG_H, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            img_lbl.setPixmap(pix)
        else:
            pix = QPixmap(self.IMG_W, self.IMG_H)
            c = QColor(color)
            c.setAlpha(110)
            pix.fill(c)
            img_lbl.setPixmap(pix)
        v.addWidget(img_lbl)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pass  # single click does nothing; double-click adds (see below)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(self._name, self._school, False)
        super().mouseDoubleClickEvent(event)

    def _on_context(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        act_main = menu.addAction(f"＋ {self._name} → Main Deck")
        act_side = menu.addAction(f"＋ {self._name} → Side Deck")
        chosen = menu.exec_(self.mapToGlobal(pos))
        if chosen == act_main:
            self.picked.emit(self._name, self._school, False)
        elif chosen == act_side:
            self.picked.emit(self._name, self._school, True)


class SpellPickerPanel(QWidget):
    spell_selected = pyqtSignal(str, str, bool)

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        hdr = QLabel("✨ SPELL PICKER")
        hdr.setStyleSheet(
            "color:#555;font-size:9px;font-weight:bold;letter-spacing:1px;background:transparent;"
        )
        v.addWidget(hdr)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search spells…")
        self._search.textChanged.connect(self._on_search)
        v.addWidget(self._search)

        self._school_combo = QComboBox()
        self._school_combo.addItem("All Schools")
        for s in SCHOOLS:
            self._school_combo.addItem(s)
        self._school_combo.currentTextChanged.connect(lambda _: self._on_search(self._search.text()))
        v.addWidget(self._school_combo)

        # Image-tile grid (replaces the old text-only list). Spell names
        # are intentionally NOT shown as visible labels — only as
        # tooltips — while remaining fully searchable via the box above,
        # which filters against the database directly.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:#0d1b2a;border:1px solid #1f3460;border-radius:5px;}"
            "QScrollBar:vertical{background:#16213e;width:7px;}"
            "QScrollBar::handle:vertical{background:#0f3460;border-radius:3px;}"
        )
        self._grid_container = QWidget()
        self._grid_container.setStyleSheet("background:#0d1b2a;")
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(4)
        self._grid.setContentsMargins(6, 6, 6, 6)
        self._scroll.setWidget(self._grid_container)
        v.addWidget(self._scroll, stretch=1)

        manual_lbl = QLabel("Add by name (not in DB):")
        manual_lbl.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        v.addWidget(manual_lbl)

        manual_row = QHBoxLayout()
        self._manual = QLineEdit()
        self._manual.setPlaceholderText("Spell name…")
        manual_row.addWidget(self._manual, stretch=1)
        add_btn = QPushButton("Add")
        add_btn.setFixedWidth(44)
        add_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:4px;padding:4px 8px;font-size:11px;}"
            "QPushButton:hover{background:#4d96ff;}"
        )
        add_btn.clicked.connect(self._on_manual_add)
        manual_row.addWidget(add_btn)
        v.addLayout(manual_row)

        hint = QLabel("Double-click → main deck\nRight-click → side deck\n"
                      "Hover a card to see its name")
        hint.setStyleSheet("color:#444;font-size:9px;background:transparent;")
        v.addWidget(hint)

        self._on_search("")

    def _on_search(self, text: str):
        while self._grid.count():
            it = self._grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

        school_filter = self._school_combo.currentText()
        if school_filter == "All Schools":
            school_filter = None
        spells = ds.list_spells(self.conn, school=school_filter, search=text or None)

        cols = 2
        for idx, sp in enumerate(spells):
            tile = SpellPickerTile(sp)
            tile.picked.connect(self.spell_selected)
            row, col = divmod(idx, cols)
            self._grid.addWidget(tile, row, col)
        if not spells:
            empty = QLabel("No spells found.")
            empty.setStyleSheet("color:#555;font-size:11px;")
            empty.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(empty, 0, 0, 1, cols)

    def _on_manual_add(self):
        name = self._manual.text().strip()
        if name:
            self.spell_selected.emit(name, "", False)
            self._manual.clear()

    def refresh(self):
        self._on_search(self._search.text())


# ═══════════════════════════════════════════════════════════════════════
# DECK METADATA PANEL
# ═══════════════════════════════════════════════════════════════════════

class DeckMetaPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)
        self._build()

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        name_lbl = QLabel("Name:")
        name_lbl.setFixedWidth(38)
        name_lbl.setStyleSheet("color:#999;font-size:11px;background:transparent;")
        row1.addWidget(name_lbl)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My Deck")
        row1.addWidget(self.name_input, stretch=2)
        sch_lbl = QLabel("School:")
        sch_lbl.setFixedWidth(46)
        sch_lbl.setStyleSheet("color:#999;font-size:11px;background:transparent;")
        row1.addWidget(sch_lbl)
        self.school_combo = QComboBox()
        for s in SCHOOLS:
            self.school_combo.addItem(s)
        row1.addWidget(self.school_combo)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        tag_lbl = QLabel("Tag:")
        tag_lbl.setFixedWidth(38)
        tag_lbl.setStyleSheet("color:#999;font-size:11px;background:transparent;")
        row2.addWidget(tag_lbl)
        self.tag_combo = QComboBox()
        self.tag_combo.setEditable(True)
        for t in DECK_TAGS:
            self.tag_combo.addItem(t)
        row2.addWidget(self.tag_combo, stretch=1)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        notes_lbl = QLabel("Notes:")
        notes_lbl.setFixedWidth(38)
        notes_lbl.setStyleSheet("color:#999;font-size:11px;background:transparent;")
        row3.addWidget(notes_lbl)
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Optional description…")
        row3.addWidget(self.desc_input, stretch=1)
        v.addLayout(row3)

    def load(self, deck: dict):
        self.name_input.setText(deck.get("name", ""))
        sc = deck.get("school", "Fire")
        idx = self.school_combo.findText(sc)
        if idx >= 0:
            self.school_combo.setCurrentIndex(idx)
        tag = deck.get("tag", "")
        tidx = self.tag_combo.findText(tag)
        if tidx >= 0:
            self.tag_combo.setCurrentIndex(tidx)
        else:
            self.tag_combo.setCurrentText(tag)
        self.desc_input.setText(deck.get("description", ""))

    def get_meta(self) -> dict:
        return {
            "name":        self.name_input.text().strip() or "Unnamed Deck",
            "school":      self.school_combo.currentText(),
            "tag":         self.tag_combo.currentText().strip(),
            "description": self.desc_input.text().strip(),
        }


# ═══════════════════════════════════════════════════════════════════════
# MAIN DECK BUILDER WIDGET
# ═══════════════════════════════════════════════════════════════════════

class DeckBuilderWidget(QWidget):
    nav_hub = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        ds.init_spell_tables(conn)
        self._current_deck_id: Optional[int] = None
        self._unsaved = False
        self._build()

    # ── UI BUILD ─────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet("background:#16213e; border-bottom:1px solid #0f3460;")
        header.setFixedHeight(54)
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(12, 8, 12, 8)
        hdr.setSpacing(10)

        back_btn = QPushButton("← Hub")
        back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back_btn.clicked.connect(self.nav_hub)
        hdr.addWidget(back_btn)

        title = QLabel("🃏 Deck Builder")
        title.setFont(QFont("Segoe UI", 17, QFont.Bold))
        title.setStyleSheet("color:#4d96ff; background:transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        for label, tip, slot in [
            ("📤 Export Code", "Copy a base64 share code for the current deck", self._export_code),
            ("📥 Import Code", "Import a deck from a base64 share code",         self._import_code),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
                "border-radius:5px;padding:5px 12px;font-size:11px;}"
                "QPushButton:hover{background:#4d96ff;}"
            )
            btn.clicked.connect(slot)
            hdr.addWidget(btn)

        root.addWidget(header)

        # ── Body splitter ─────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(
            "QSplitter{background:#1a1a2e;}"
            "QSplitter::handle{background:#0f3460;width:2px;}"
        )

        # ── LEFT: saved deck list (220px) ─────────────────────────
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet("background:#16213e; border-right:1px solid #0f3460;")
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)
        lv.setSpacing(5)

        lhdr = QLabel("SAVED DECKS")
        lhdr.setStyleSheet(
            "color:#555;font-size:9px;font-weight:bold;letter-spacing:1px;background:transparent;"
        )
        lv.addWidget(lhdr)

        # Search
        self._deck_search = QLineEdit()
        self._deck_search.setPlaceholderText("🔍 Search decks…")
        self._deck_search.setStyleSheet(
            "QLineEdit{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;"
            "border-radius:4px;padding:4px 8px;font-size:11px;}"
            "QLineEdit:focus{border-color:#4d96ff;}"
        )
        self._deck_search.textChanged.connect(lambda _: self._refresh_deck_list())
        lv.addWidget(self._deck_search)

        # School filter
        self._deck_school_filter = QComboBox()
        self._deck_school_filter.addItem("All Schools")
        for s in SCHOOLS:
            self._deck_school_filter.addItem(s)
        self._deck_school_filter.setStyleSheet(
            "QComboBox{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;"
            "border-radius:4px;padding:4px 8px;font-size:11px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#0d1b2a;color:#e0e0e0;"
            "selection-background-color:#1f4a80;}"
        )
        self._deck_school_filter.currentTextChanged.connect(lambda _: self._refresh_deck_list())
        lv.addWidget(self._deck_school_filter)

        # Sort
        sort_row = QHBoxLayout()
        sort_lbl = QLabel("Sort:")
        sort_lbl.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        sort_row.addWidget(sort_lbl)
        self._deck_sort = QComboBox()
        self._deck_sort.addItems(["Name A→Z", "Name Z→A", "School", "Tag", "Cards"])
        self._deck_sort.setStyleSheet(
            "QComboBox{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;"
            "border-radius:4px;padding:3px 6px;font-size:10px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#0d1b2a;color:#e0e0e0;"
            "selection-background-color:#1f4a80;}"
        )
        self._deck_sort.currentIndexChanged.connect(lambda _: self._refresh_deck_list())
        sort_row.addWidget(self._deck_sort, stretch=1)
        lv.addLayout(sort_row)

        # Deck list
        self._deck_list = QListWidget()
        self._deck_list.setStyleSheet(
            "QListWidget{background:#0d1b2a;border:1px solid #1f3460;border-radius:5px;"
            "color:#e0e0e0;font-size:11px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #111829;}"
            "QListWidget::item:selected{background:#1f4a80;}"
        )
        self._deck_list.currentRowChanged.connect(self._on_deck_selected)
        self._deck_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._deck_list.customContextMenuRequested.connect(self._on_deck_list_context)
        lv.addWidget(self._deck_list, stretch=1)

        # List buttons
        lbtns = QHBoxLayout()
        lbtns.setSpacing(4)
        new_btn = QPushButton("＋ New")
        new_btn.setStyleSheet(
            "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
            "border-radius:4px;padding:5px 8px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;}"
        )
        new_btn.clicked.connect(self._new_deck)
        lbtns.addWidget(new_btn)
        del_btn = QPushButton("🗑 Delete")
        del_btn.setStyleSheet(
            "QPushButton{background:#5c1b1b;color:#e0e0e0;border:none;"
            "border-radius:4px;padding:5px 8px;font-size:11px;}"
            "QPushButton:hover{background:#e94560;}"
        )
        del_btn.clicked.connect(self._delete_deck)
        lbtns.addWidget(del_btn)
        lv.addLayout(lbtns)

        splitter.addWidget(left)

        # ── CENTER: metadata + deck cards + save ──────────────────
        center = QWidget()
        center.setStyleSheet("background:#1a1a2e;")
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)

        self._meta_panel = DeckMetaPanel()
        cv.addWidget(self._meta_panel)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#0f3460;")
        cv.addWidget(sep)

        self._deck_panel = DeckPanel(self.conn)
        self._deck_panel.changed.connect(self._on_deck_changed)
        cv.addWidget(self._deck_panel, stretch=1)

        save_bar = QWidget()
        save_bar.setFixedHeight(44)
        save_bar.setStyleSheet("background:#16213e; border-top:1px solid #0f3460;")
        sv = QHBoxLayout(save_bar)
        sv.setContentsMargins(12, 6, 12, 6)
        sv.setSpacing(8)
        sv.addStretch()
        self._save_btn = QPushButton("💾 Save Deck")
        self._save_btn.setStyleSheet(
            "QPushButton{background:#0f4d2e;color:#9fffcf;border:1px solid #1f6a44;"
            "border-radius:5px;padding:6px 18px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#156b40;}"
        )
        self._save_btn.clicked.connect(self._save_current)
        sv.addWidget(self._save_btn)
        cv.addWidget(save_bar)

        splitter.addWidget(center)

        # ── RIGHT: spell picker — same stretch as center ──────────
        right = QWidget()
        right.setStyleSheet("background:#16213e; border-left:1px solid #0f3460;")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self._picker = SpellPickerPanel(self.conn)
        self._picker.spell_selected.connect(self._on_spell_selected)
        rv.addWidget(self._picker)
        splitter.addWidget(right)

        # Left stays fixed; center and right share remaining space equally
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([220, 500, 500])

        root.addWidget(splitter, stretch=1)
        self._refresh_deck_list()

    # ── DECK LIST (with search / school / sort) ───────────────────────

    def _refresh_deck_list(self):
        search_text = self._deck_search.text().strip().lower()
        school_filter = self._deck_school_filter.currentText()
        sort_idx      = self._deck_sort.currentIndex()   # 0=NameAZ 1=NameZA 2=School 3=Tag 4=Cards

        decks = ds.list_decks(self.conn)

        # Filter
        filtered = []
        for deck in decks:
            if search_text and search_text not in deck["name"].lower():
                continue
            if school_filter != "All Schools" and deck.get("school", "") != school_filter:
                continue
            cards = deck.get("cards", [])
            total = sum(c.get("quantity", 1) for c in cards if not c.get("is_side_deck"))
            deck["_total_main"] = total
            filtered.append(deck)

        # Sort
        if sort_idx == 0:
            filtered.sort(key=lambda d: d["name"].lower())
        elif sort_idx == 1:
            filtered.sort(key=lambda d: d["name"].lower(), reverse=True)
        elif sort_idx == 2:
            filtered.sort(key=lambda d: d.get("school", "").lower())
        elif sort_idx == 3:
            filtered.sort(key=lambda d: d.get("tag", "").lower())
        elif sort_idx == 4:
            filtered.sort(key=lambda d: d.get("_total_main", 0), reverse=True)

        # Rebuild list
        prev_id = self._current_deck_id
        self._deck_list.blockSignals(True)
        self._deck_list.clear()
        restore_row = -1

        for i, deck in enumerate(filtered):
            school = deck.get("school", "")
            tag    = deck.get("tag", "")
            total  = deck.get("_total_main", 0)
            color  = _sc(school)
            text   = deck["name"]
            sub    = []
            if school:
                sub.append(school)
            if tag:
                sub.append(tag)
            sub.append(f"{total} cards")
            if sub:
                text += "\n  " + "  •  ".join(sub)
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, deck["id"])
            item.setForeground(QBrush(QColor(color)))
            self._deck_list.addItem(item)
            if deck["id"] == prev_id:
                restore_row = i

        self._deck_list.blockSignals(False)
        if restore_row >= 0:
            self._deck_list.setCurrentRow(restore_row)

    def _on_deck_selected(self, row: int):
        if row < 0:
            return
        item = self._deck_list.item(row)
        if not item:
            return
        did = item.data(Qt.UserRole)
        if did is None:
            return
        deck = ds.get_deck(self.conn, did)
        if not deck:
            return
        self._current_deck_id = did
        self._meta_panel.load(deck)
        main = [c for c in deck["cards"] if not c.get("is_side_deck")]
        side = [c for c in deck["cards"] if c.get("is_side_deck")]
        self._deck_panel.load_cards(main, side)
        self._unsaved = False

    # ── DECK ACTIONS ─────────────────────────────────────────────────

    def _new_deck(self):
        self._current_deck_id = None
        self._meta_panel.load({"name": "", "school": "Fire", "tag": "", "description": ""})
        self._deck_panel.clear()
        self._unsaved = False

    def _delete_deck(self):
        did = self._current_deck_id
        if did is None:
            QMessageBox.information(self, "No deck", "Select a deck first.")
            return
        name = self._meta_panel.get_meta().get("name", "?")
        if QMessageBox.question(
            self, "Delete Deck",
            f"Delete deck '{name}'?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        ds.delete_deck(self.conn, did)
        self._current_deck_id = None
        self._deck_panel.clear()
        self._refresh_deck_list()

    def _on_deck_list_context(self, pos):
        """Right-click on any deck in the saved list -> delete it directly,
        without first needing to select/load it."""
        item = self._deck_list.itemAt(pos)
        if not item:
            return
        did = item.data(Qt.UserRole)
        if did is None:
            return
        deck_name = item.text().split("\n")[0]
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        act_delete = menu.addAction(f"🗑 Delete '{deck_name}'")
        chosen = menu.exec_(self._deck_list.mapToGlobal(pos))
        if chosen == act_delete:
            if QMessageBox.question(
                self, "Delete Deck",
                f"Delete deck '{deck_name}'? This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
            ds.delete_deck(self.conn, did)
            if self._current_deck_id == did:
                self._current_deck_id = None
                self._deck_panel.clear()
            self._refresh_deck_list()

    def _on_deck_changed(self):
        self._unsaved = True

    def _save_current(self):
        meta  = self._meta_panel.get_meta()
        cards = self._deck_panel.get_cards()
        all_cards = (
            [dict(c, is_side_deck=0) for c in cards["main"]] +
            [dict(c, is_side_deck=1) for c in cards["side"]]
        )
        data = dict(meta, cards=all_cards)
        if self._current_deck_id is not None:
            data["id"] = self._current_deck_id
        did = ds.upsert_deck(self.conn, data)
        self._current_deck_id = did
        self._unsaved = False
        self._refresh_deck_list()

    def _on_spell_selected(self, name: str, school: str, to_side: bool):
        self._deck_panel.add_spell(name, school, to_side)

    # ── EXPORT / IMPORT ──────────────────────────────────────────────

    def _export_code(self):
        meta  = self._meta_panel.get_meta()
        cards = self._deck_panel.get_cards()
        all_cards = (
            [dict(c, is_side_deck=0) for c in cards["main"]] +
            [dict(c, is_side_deck=1) for c in cards["side"]]
        )
        code = ds.export_deck_code(dict(meta, cards=all_cards))

        dlg = QDialog(self)
        dlg.setWindowTitle("Deck Share Code")
        dlg.resize(540, 170)
        dlg.setStyleSheet(
            "QDialog,QWidget{background:#1a1a2e;color:#e0e0e0;}"
            "QLineEdit{background:#0d1b2a;color:#e0e0e0;border:1px solid #0f3460;"
            "border-radius:5px;padding:8px;font-family:Consolas;font-size:11px;}"
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 14px;}"
            "QPushButton:hover{background:#4d96ff;}"
        )
        dv = QVBoxLayout(dlg)
        dv.addWidget(QLabel("Share this code with other players:"))
        code_field = QLineEdit(code)
        code_field.setReadOnly(True)
        dv.addWidget(code_field)
        brow = QHBoxLayout()
        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(code))
        brow.addWidget(copy_btn)
        brow.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        brow.addWidget(close_btn)
        dv.addLayout(brow)
        dlg.exec_()

    def _import_code(self):
        code, ok = QInputDialog.getText(
            self, "Import Deck Code", "Paste the base64 deck code here:"
        )
        if not ok or not code.strip():
            return
        deck = ds.import_deck_code(code.strip())
        if not deck:
            QMessageBox.warning(self, "Invalid Code", "Could not decode the code. Check that you copied it correctly.")
            return
        self._current_deck_id = None
        self._meta_panel.load(deck)
        main = [c for c in deck["cards"] if not c.get("is_side_deck")]
        side = [c for c in deck["cards"] if c.get("is_side_deck")]
        self._deck_panel.load_cards(main, side)
        self._unsaved = True
        QMessageBox.information(
            self, "Deck Imported",
            f"Deck '{deck.get('name','')}' loaded.\nClick 💾 Save Deck to keep it."
        )

    def refresh(self):
        self._refresh_deck_list()
        self._picker.refresh()
