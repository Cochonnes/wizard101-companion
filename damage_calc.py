"""
damage_calc.py
══════════════
Damage Calculator + Character Manager UI for Wizard101 Companion.

Public widgets (used by boss_wiki.py hub stack):
  DamageCalcWidget(conn)        — calculator page + preset manager
  CharacterManagerWidget(conn)  — create / edit / delete wizards

Reusable building block:
  CalcPanel(conn, compact=False) — the live calculator itself, embedded both
                                   in the hub page and in the HUD overlay.

All damage maths lives in database_calc.compute_damage() (Qt-free), so the
hub panel and the HUD overlay always agree to the last point.
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QScrollArea, QFrame, QStackedWidget, QGridLayout, QCompleter,
    QMessageBox, QSizePolicy, QSpinBox, QListWidget, QListWidgetItem,
    QCheckBox, QInputDialog, QAbstractItemView,
)
from PyQt5.QtCore import Qt, pyqtSignal, QStringListModel
from PyQt5.QtGui import QFont, QDoubleValidator, QIntValidator

import database_calc as dc

try:
    import exporter as _exp
    _EXPORTER_AVAILABLE = True
except ImportError:
    _exp = None
    _EXPORTER_AVAILABLE = False


# ════════════════════════════════════════════════════════════════
# SHARED STYLE + SMALL HELPERS
# ════════════════════════════════════════════════════════════════

CALC_STYLE = """
QWidget { background:#1a1a2e; color:#e0e0e0; }
QScrollArea { border:none; background:transparent; }
QLabel { background:transparent; }
QPushButton {
    background:#0f3460; color:#e0e0e0; border:1px solid #1f3460;
    border-radius:5px; padding:5px 12px; font-size:12px;
}
QPushButton:hover { background:#1f4a80; }
QLineEdit, QComboBox, QSpinBox {
    background:#0a1628; color:#e0e0e0; border:1px solid #1f3460;
    border-radius:4px; padding:3px 6px; font-size:12px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color:#4d96ff; }
QComboBox::drop-down { border:none; width:16px; }
QComboBox QAbstractItemView {
    background:#0a1628; color:#e0e0e0; selection-background-color:#1f4a80;
    border:1px solid #1f3460;
}
QCheckBox { background:transparent; color:#ccc; font-size:12px; }
"""

_INPUT_SS = (
    "background:#0a1628;color:#e0e0e0;border:1px solid #1f3460;"
    "border-radius:4px;padding:3px 6px;font-size:12px;"
)
_COMBO_SS = (
    "QComboBox{background:#0a1628;color:#e0e0e0;border:1px solid #1f3460;"
    "border-radius:4px;padding:3px 6px;font-size:12px;}"
    "QComboBox::drop-down{border:none;width:16px;}"
    "QComboBox QAbstractItemView{background:#0a1628;color:#e0e0e0;"
    "selection-background-color:#1f4a80;border:1px solid #1f3460;}"
)


def _make_search_combo(labels, placeholder="Search…", editable=True) -> QComboBox:
    """An editable combo with a contains-style completer."""
    combo = QComboBox()
    combo.setEditable(editable)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.addItems(labels)
    combo.setStyleSheet(_COMBO_SS)
    if editable:
        combo.lineEdit().setPlaceholderText(placeholder)
        comp = QCompleter(labels, combo)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setCompletionMode(QCompleter.PopupCompletion)
        combo.setCompleter(comp)
        combo.setCurrentIndex(-1)
        combo.lineEdit().clear()
    return combo


def _lbl(text, color="#9fb6d4", bold=False, size=11):
    l = QLabel(text)
    weight = "bold" if bold else "normal"
    l.setStyleSheet(f"color:{color};font-size:{size}px;font-weight:{weight};background:transparent;")
    return l


from PyQt5.QtWidgets import QLayout
from PyQt5.QtCore import QRect, QSize, QPoint


class FlowLayout(QLayout):
    """A layout that arranges children left-to-right and wraps to new rows,
    so modifier chips never get clipped no matter how many are added."""

    def __init__(self, parent=None, margin=0, spacing=5):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + w + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height - rect.y()


# ════════════════════════════════════════════════════════════════
# MODIFIER CHIP (one applied blade / trap / shield / armor)
# ════════════════════════════════════════════════════════════════

class ModifierChip(QFrame):
    removed = pyqtSignal(object)   # emits self

    def __init__(self, label: str, value: float, parent=None):
        super().__init__(parent)
        self.label = label
        self.value = value
        self.setStyleSheet(
            "QFrame{background:#16213e;border:1px solid #1f3460;border-radius:10px;}"
        )
        lo = QHBoxLayout(self)
        lo.setContentsMargins(8, 2, 4, 2)
        lo.setSpacing(4)
        txt = QLabel(label)
        txt.setStyleSheet("color:#a8c8ff;font-size:11px;background:transparent;border:none;")
        lo.addWidget(txt)
        x = QPushButton("✕")
        x.setFixedSize(16, 16)
        x.setCursor(Qt.PointingHandCursor)
        x.setStyleSheet(
            "QPushButton{background:transparent;color:#888;border:none;font-size:11px;}"
            "QPushButton:hover{color:#e94560;}"
        )
        x.clicked.connect(lambda: self.removed.emit(self))
        lo.addWidget(x)


class ModifierGroup(QWidget):
    """A labelled row with a dropdown that adds a chip the moment you pick an
    entry (no separate button).  Items read 'Name   +40%', sorted by value
    (highest first).  Chips wrap so they never get clipped."""
    changed = pyqtSignal()

    def __init__(self, conn, category: str, title: str, accent: str,
                 single: bool = False, compact: bool = False, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.category = category
        self.single = single
        self.accent = accent
        self.compact = compact
        self.chips: list[ModifierChip] = []
        self._build(title)
        self.reload_presets()

    def _build(self, title):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        row = QHBoxLayout()
        row.setSpacing(6)
        lbl = _lbl(title, self.accent, bold=True)
        lbl.setFixedWidth(58 if self.compact else 74)
        row.addWidget(lbl)
        self.combo = QComboBox()
        self.combo.setStyleSheet(_COMBO_SS)
        self.combo.setFixedWidth(160 if self.compact else 220)
        self.combo.activated.connect(self._on_pick)   # pick = add immediately
        row.addWidget(self.combo)
        row.addStretch()
        root.addLayout(row)

        self.chip_host = QWidget()
        self.chip_host.setStyleSheet("background:transparent;")
        self.chip_flow = FlowLayout(self.chip_host, margin=0, spacing=5)
        root.addWidget(self.chip_host)

    def reload_presets(self):
        presets = dc.list_presets(self.conn, self.category)
        # sort by value, highest first
        presets.sort(key=lambda p: p["value"], reverse=True)
        self._items = presets          # list of dicts, index-aligned with combo
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(f"＋ add {self.category.lower()}…")   # placeholder at index 0
        for p in presets:
            vs = dc.preset_value_str(p["category"], p["value"])
            self.combo.addItem(f"{p['label']}   {vs}")
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

    def _on_pick(self, index):
        if index <= 0 or index - 1 >= len(self._items):
            self.combo.setCurrentIndex(0)
            return
        p = self._items[index - 1]
        label, value = p["label"], p["value"]
        vs = dc.preset_value_str(p["category"], value)
        if self.single:
            self.clear()
        chip = ModifierChip(f"{label} {vs}", value)
        chip.removed.connect(self._remove_chip)
        self.chips.append(chip)
        self.chip_flow.addWidget(chip)
        self.combo.setCurrentIndex(0)      # reset to placeholder
        self.changed.emit()

    def _remove_chip(self, chip):
        if chip in self.chips:
            self.chips.remove(chip)
        chip.setParent(None)
        chip.deleteLater()
        self.changed.emit()

    def clear(self):
        for chip in list(self.chips):
            chip.setParent(None)
            chip.deleteLater()
        self.chips.clear()

    def values(self) -> list:
        return [c.value for c in self.chips]


# ════════════════════════════════════════════════════════════════
# THE LIVE CALCULATOR PANEL  (hub + HUD share this)
# ════════════════════════════════════════════════════════════════

class CalcPanel(QWidget):
    boss_selected = pyqtSignal(str)   # emitted when the user picks a boss here

    def __init__(self, conn, compact: bool = False, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.compact = compact
        self._current_boss = None      # boss dict or None
        self._loading = False
        self._build()
        self.refresh_lists()
        self._recalc()

    # ── construction ────────────────────────────────────────────
    def _build(self):
        # Width budget — narrower in the HUD overlay so nothing clips.
        c = self.compact
        self._lw = 58 if c else 74     # leading label width
        self._cw = 160 if c else 220   # main combo width
        self._bw = 200 if c else 300   # boss search width

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Wizard select
        wiz_row = QHBoxLayout()
        wiz_row.setSpacing(6)
        lblw = _lbl("Wizard", "#ffd93d", bold=True)
        lblw.setFixedWidth(self._lw)
        wiz_row.addWidget(lblw)
        self.wizard_combo = QComboBox()
        self.wizard_combo.setStyleSheet(_COMBO_SS)
        self.wizard_combo.setFixedWidth(self._cw)
        self.wizard_combo.currentIndexChanged.connect(self._on_wizard_changed)
        wiz_row.addWidget(self.wizard_combo)
        wiz_row.addWidget(_lbl("School"))
        self.school_combo = QComboBox()
        self.school_combo.setStyleSheet(_COMBO_SS)
        self.school_combo.setFixedWidth(80 if c else 90)
        self.school_combo.addItems(dc.WIZARD_SCHOOLS)
        self.school_combo.setToolTip("Attack school (used for boss resist & wizard stats)")
        self.school_combo.currentIndexChanged.connect(self._on_school_changed)
        wiz_row.addWidget(self.school_combo)
        wiz_row.addStretch()
        outer.addLayout(wiz_row)

        # Attack — clearly-labelled fields, fixed widths, no stretching
        atk = QGridLayout()
        atk.setHorizontalSpacing(8)
        atk.setVerticalSpacing(5)

        def _cell(label, widget, r, c):
            atk.addWidget(_lbl(label), r, c)
            atk.addWidget(widget, r, c + 1)

        self.base_input = self._num_input("0", 80)
        self.base_input.setToolTip("First-round (direct / single) damage of the spell")
        _cell("Initial hit", self.base_input, 0, 0)

        self.per_pip_input = self._num_input("0", 70)
        self.per_pip_input.setToolTip("Damage per pip (for spells that scale with pips)")
        _cell("Dmg / pip", self.per_pip_input, 0, 2)
        self.pips_input = self._num_input("0", 50, integer=True)
        self.pips_input.setToolTip("Number of pips used")
        _cell("× pips", self.pips_input, 0, 4)

        self.dot_input = self._num_input("0", 80)
        self.dot_input.setToolTip("Damage-over-time dealt each following round")
        _cell("DoT / round", self.dot_input, 1, 0)
        self.dot_rounds = self._num_input("0", 50, integer=True)
        self.dot_rounds.setToolTip("How many rounds the DoT ticks")
        _cell("× rounds", self.dot_rounds, 1, 2)

        self.targets_combo = QComboBox()
        self.targets_combo.setStyleSheet(_COMBO_SS)
        self.targets_combo.setFixedWidth(50)
        self.targets_combo.addItems(["1", "2", "3", "4"])
        self.targets_combo.setToolTip("Spells that hit several enemies and split their damage")
        self.targets_combo.currentIndexChanged.connect(lambda *_: self._recalc())
        _cell("Enemies (split)", self.targets_combo, 1, 4)

        atk_wrap = QHBoxLayout()
        atk_wrap.addLayout(atk)
        atk_wrap.addStretch()
        outer.addLayout(atk_wrap)

        # Fist enchant — click an entry to apply (single)
        ench_row = QHBoxLayout()
        ench_row.setSpacing(6)
        lble = _lbl("Fist enchant", "#ff9d5c", bold=True)
        lble.setFixedWidth(self._lw)
        ench_row.addWidget(lble)
        self.enchant_combo = QComboBox()
        self.enchant_combo.setStyleSheet(_COMBO_SS)
        self.enchant_combo.setFixedWidth(self._cw)
        self.enchant_combo.currentIndexChanged.connect(lambda *_: self._recalc())
        ench_row.addWidget(self.enchant_combo)
        ench_row.addStretch()
        outer.addLayout(ench_row)

        # Modifier groups
        self.blade_group = ModifierGroup(self.conn, "Blade", "Blades", "#6bcb77", compact=c)
        self.trap_group = ModifierGroup(self.conn, "Trap", "Traps", "#ffd93d", compact=c)
        self.shield_group = ModifierGroup(self.conn, "Shield", "Shields", "#e94560", compact=c)
        self.armor_group = ModifierGroup(self.conn, "Armor", "Armor", "#aaaaaa", compact=c)
        self.aura_group = ModifierGroup(self.conn, "Aura", "Auras", "#c39bd3", compact=c)
        self.circle_group = ModifierGroup(self.conn, "Battlecircle", "Circle", "#4d96ff", single=True, compact=c)
        for g in (self.blade_group, self.trap_group, self.shield_group,
                  self.armor_group, self.aura_group, self.circle_group):
            g.changed.connect(self._recalc)
            outer.addWidget(g)

        # Mechanic toggles
        tog = QHBoxLayout()
        tog.setSpacing(10)
        self.crit_check = QCheckBox("Critical")
        self.pierce_check = QCheckBox("Pierce")
        self.flat_check = QCheckBox("Flat + dmg")
        for c in (self.crit_check, self.pierce_check, self.flat_check):
            c.setChecked(True)
            c.setStyleSheet("QCheckBox{color:#ccc;font-size:11px;background:transparent;}")
            c.stateChanged.connect(lambda *_: self._recalc())
            tog.addWidget(c)
        tog.addStretch()
        outer.addLayout(tog)

        # Boss query
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background:#1f3460;")
        outer.addWidget(div)

        boss_row = QHBoxLayout()
        boss_row.setSpacing(6)
        lblb = _lbl("Boss", "#e94560", bold=True)
        lblb.setFixedWidth(self._lw)
        boss_row.addWidget(lblb)
        self.boss_combo = QComboBox()
        self.boss_combo.setEditable(True)
        self.boss_combo.setInsertPolicy(QComboBox.NoInsert)
        self.boss_combo.setStyleSheet(_COMBO_SS)
        self.boss_combo.setFixedWidth(self._bw)
        self.boss_combo.lineEdit().setPlaceholderText("search boss… (or leave empty)")
        self.boss_combo.activated.connect(self._on_boss_picked)
        boss_row.addWidget(self.boss_combo)
        clr = QPushButton("✕")
        clr.setFixedWidth(30)
        clr.setToolTip("No boss")
        clr.setCursor(Qt.PointingHandCursor)
        clr.setStyleSheet(
            "QPushButton{background:#3a1320;color:#ff5a6e;border:1px solid #5a2030;"
            "border-radius:5px;font-size:13px;font-weight:bold;padding:3px;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        clr.clicked.connect(self.clear_boss)
        boss_row.addWidget(clr)
        boss_row.addStretch()
        outer.addLayout(boss_row)

        # Resist + health (editable; prefilled from boss)
        rh = QHBoxLayout()
        rh.setSpacing(6)
        rh.addWidget(_lbl("Resist"))
        self.resist_combo = QComboBox()
        self.resist_combo.setStyleSheet(_COMBO_SS)
        self.resist_combo.setEditable(True)
        self.resist_combo.setFixedWidth(90)
        for v in ["-25", "-10", "0", "5", "10", "15", "20", "25", "30",
                  "35", "40", "45", "50", "60", "70"]:
            self.resist_combo.addItem(f"{v}%")
        self.resist_combo.setCurrentText("0%")
        self.resist_combo.currentTextChanged.connect(lambda *_: self._recalc())
        rh.addWidget(self.resist_combo)
        rh.addSpacing(12)
        rh.addWidget(_lbl("Boss HP"))
        self.hp_input = self._num_input("", 90, integer=True)
        self.hp_input.setPlaceholderText("HP")
        rh.addWidget(self.hp_input)
        rh.addStretch()
        outer.addLayout(rh)

        # All-school resist readout (filled when a boss is selected)
        self.resist_box = QFrame()
        self.resist_box.setStyleSheet(
            "QFrame{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;}")
        rg = QGridLayout(self.resist_box)
        rg.setContentsMargins(8, 5, 8, 5)
        rg.setHorizontalSpacing(4)
        rg.setVerticalSpacing(2)
        cap = _lbl("Boss resists", "#888", size=10)
        rg.addWidget(cap, 0, 0, 1, len(dc.STAT_SCHOOLS))
        self._resist_labels = {}
        for ci, school in enumerate(dc.STAT_SCHOOLS):
            h = _lbl(school[:3], dc.SCHOOL_COLORS.get(school, "#ccc"), bold=True, size=9)
            h.setAlignment(Qt.AlignCenter)
            rg.addWidget(h, 1, ci)
            val = _lbl("–", "#cfd8e6", size=10)
            val.setAlignment(Qt.AlignCenter)
            rg.addWidget(val, 2, ci)
            self._resist_labels[school] = val
        self.resist_box.setVisible(False)
        outer.addWidget(self.resist_box)

        # Result
        self.result = QLabel()
        self.result.setTextFormat(Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setStyleSheet(
            "background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;"
            "padding:8px;font-size:12px;color:#e0e0e0;"
        )
        self.result.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        outer.addWidget(self.result)

        # Reset
        reset = QPushButton("↺ Reset all")
        reset.setStyleSheet(
            "QPushButton{background:#2a1020;color:#ff8080;border:1px solid #5a2030;"
            "border-radius:5px;padding:5px 12px;font-size:12px;}"
            "QPushButton:hover{background:#3a1828;}"
        )
        reset.clicked.connect(self.reset_all)
        outer.addWidget(reset)
        outer.addStretch()

        # Live recalc on text edits
        for w in (self.base_input, self.per_pip_input, self.pips_input,
                  self.dot_input, self.dot_rounds, self.hp_input):
            w.textChanged.connect(lambda *_: self._recalc())

    def _num_input(self, default="", width=70, integer=False):
        le = QLineEdit(default)
        le.setFixedWidth(width)
        le.setStyleSheet(_INPUT_SS)
        le.setValidator(QIntValidator(0, 9999999) if integer else QDoubleValidator(-99999, 9999999, 2))
        return le

    # ── data loading ────────────────────────────────────────────
    def refresh_lists(self):
        """Reload wizards, presets and boss names from the DB."""
        import database as db
        self._loading = True

        cur_wiz = self.wizard_combo.currentData()
        self.wizard_combo.blockSignals(True)
        self.wizard_combo.clear()
        self.wizard_combo.addItem("— No wizard —", None)
        for w in dc.list_wizards(self.conn):
            self.wizard_combo.addItem(f"{w['name']}  ({w['school']})", w["id"])
        if cur_wiz is not None:
            idx = self.wizard_combo.findData(cur_wiz)
            if idx >= 0:
                self.wizard_combo.setCurrentIndex(idx)
        self.wizard_combo.blockSignals(False)

        # Enchant presets — value-sorted, "Name   +100", '— none —' at index 0
        ench = dc.list_presets(self.conn, "Enchant")
        ench.sort(key=lambda p: p["value"], reverse=True)
        self._enchant_items = ench            # index-aligned (offset by 1 for "none")
        cur_idx = self.enchant_combo.currentIndex()
        self.enchant_combo.blockSignals(True)
        self.enchant_combo.clear()
        self.enchant_combo.addItem("— none —")
        for p in ench:
            self.enchant_combo.addItem(f"{p['label']}   {dc.preset_value_str(p['category'], p['value'])}")
        self.enchant_combo.setCurrentIndex(cur_idx if 0 <= cur_idx <= len(ench) else 0)
        self.enchant_combo.blockSignals(False)

        for g in (self.blade_group, self.trap_group, self.shield_group,
                  self.armor_group, self.aura_group, self.circle_group):
            g.reload_presets()

        # Boss names
        try:
            names = db.get_boss_names(self.conn)
        except Exception:
            names = []
        self._boss_names = names
        cur_boss = self.boss_combo.currentText()
        self.boss_combo.blockSignals(True)
        self.boss_combo.clear()
        self.boss_combo.addItems(names)
        comp2 = QCompleter(names, self.boss_combo)
        comp2.setCaseSensitivity(Qt.CaseInsensitive)
        comp2.setFilterMode(Qt.MatchContains)
        self.boss_combo.setCompleter(comp2)
        self.boss_combo.setCurrentText(cur_boss)
        self.boss_combo.blockSignals(False)

        self._loading = False

    # ── wizard / boss selection ─────────────────────────────────
    def _on_wizard_changed(self, *_):
        wid = self.wizard_combo.currentData()
        if wid is not None:
            wiz = dc.get_wizard(self.conn, wid)
            if wiz and wiz.get("school") in dc.WIZARD_SCHOOLS:
                self.school_combo.blockSignals(True)
                self.school_combo.setCurrentText(wiz["school"])
                self.school_combo.blockSignals(False)
        self._reprefill_resist()
        self._recalc()

    def _on_school_changed(self, *_):
        self._reprefill_resist()
        self._recalc()

    def _reprefill_resist(self):
        """If a boss is loaded, re-derive its resist for the current school."""
        if not self._current_boss:
            return
        school = self.school_combo.currentText()
        resist = dc.boss_resist_for_school(self._current_boss, school)
        self.resist_combo.blockSignals(True)
        self.resist_combo.setCurrentText(f"{resist:g}%")
        self.resist_combo.blockSignals(False)

    def _on_boss_picked(self, *_):
        name = self.boss_combo.currentText().strip()
        self._load_boss(name, emit=True)

    def set_boss_external(self, name: str):
        """Called by the app (e.g. OCR or the other panel) to sync the boss."""
        if not name:
            return
        self.boss_combo.blockSignals(True)
        self.boss_combo.setCurrentText(name)
        self.boss_combo.blockSignals(False)
        self._load_boss(name, emit=False)

    def clear_boss(self):
        self._current_boss = None
        self.boss_combo.blockSignals(True)
        self.boss_combo.setCurrentText("")
        self.boss_combo.blockSignals(False)
        self.resist_box.setVisible(False)
        self._recalc()

    def _fill_resist_display(self):
        """Populate the all-school resist readout from the current boss."""
        if not self._current_boss:
            self.resist_box.setVisible(False)
            return
        all_res = dc.boss_all_resists(self._current_boss)
        for school, lbl in self._resist_labels.items():
            v = all_res.get(school, 0)
            if v > 0:
                lbl.setText(f"{v:g}%")
                lbl.setStyleSheet("color:#6bcb77;font-size:10px;background:transparent;")
            elif v < 0:
                lbl.setText(f"{v:g}%")
                lbl.setStyleSheet("color:#e94560;font-size:10px;background:transparent;")
            else:
                lbl.setText("0%")
                lbl.setStyleSheet("color:#6a7a90;font-size:10px;background:transparent;")
        self.resist_box.setVisible(True)

    def _load_boss(self, name: str, emit: bool):
        import database as db
        boss = db.get_boss(self.conn, name) if name else None
        self._current_boss = boss
        if boss:
            school = self.school_combo.currentText()
            resist = dc.boss_resist_for_school(boss, school)
            self.resist_combo.blockSignals(True)
            self.resist_combo.setCurrentText(f"{resist:g}%")
            self.resist_combo.blockSignals(False)
            hp = dc.parse_health(boss.get("health"))
            self.hp_input.blockSignals(True)
            self.hp_input.setText(str(hp) if hp is not None else "")
            self.hp_input.blockSignals(False)
            self._fill_resist_display()
            if emit:
                self.boss_selected.emit(name)
        else:
            self.resist_box.setVisible(False)
        self._recalc()

    # ── the calculation ─────────────────────────────────────────
    def _f(self, text, default=0.0):
        try:
            return float(str(text).replace("%", "").strip())
        except (ValueError, TypeError):
            return default

    def _gather_params(self) -> dict:
        school = self.school_combo.currentText()
        wid = self.wizard_combo.currentData()
        wiz = dc.get_wizard(self.conn, wid) if wid is not None else None

        damage_pct = gear_flat = crit_rating = pierce_pct = 0.0
        if wiz:
            damage_pct = self._f(wiz.get("damage", {}).get(school, 0))
            gear_flat = self._f(wiz.get("damage_flat", {}).get(school, 0))
            crit_rating = self._f(wiz.get("critical", {}).get(school, 0))
            pierce_pct = self._f(wiz.get("pierce", {}).get(school, 0))

        # enchant value from the selected dropdown index (0 = none)
        ei = self.enchant_combo.currentIndex()
        items = getattr(self, "_enchant_items", [])
        enchant = items[ei - 1]["value"] if 1 <= ei <= len(items) else 0.0

        # battlecircle + auras → additive damage %
        bc_pct = sum(self.circle_group.values()) + sum(self.aura_group.values())

        # armor → sum of flat absorbs
        armor = sum(self.armor_group.values()) if self.armor_group.values() else 0.0

        # boss crit block from battle stats
        block_rating = 0.0
        if self._current_boss:
            block_rating = dc.boss_critical_block(self._current_boss.get("battle_stats", {}))

        hp_txt = self.hp_input.text().strip()
        boss_hp = int(hp_txt) if hp_txt.isdigit() else None

        return {
            "base": self._f(self.base_input.text()),
            "per_pip": self._f(self.per_pip_input.text()),
            "pips": self._f(self.pips_input.text()),
            "enchant": enchant,
            "damage_pct": damage_pct,
            "gear_flat": gear_flat,
            "battlecircle_pct": bc_pct,
            "blades": self.blade_group.values(),
            "traps": self.trap_group.values(),
            "shields": self.shield_group.values(),
            "armor": armor,
            "resist_pct": self._f(self.resist_combo.currentText()),
            "pierce_pct": pierce_pct,
            "crit_rating": crit_rating,
            "block_rating": block_rating,
            "use_crit": self.crit_check.isChecked(),
            "use_pierce": self.pierce_check.isChecked(),
            "use_flat": self.flat_check.isChecked(),
            "boss_hp": boss_hp,
            "dot_per_round": self._f(self.dot_input.text()),
            "dot_rounds": int(self._f(self.dot_rounds.text())),
            "targets": int(self.targets_combo.currentText() or 1),
        }

    def _recalc(self):
        if self._loading:
            return
        r = dc.compute_damage(self._gather_params())
        self.result.setText(self._render(r))

    def _render(self, r: dict) -> str:
        def fmt(n):
            return f"{int(n):,}" if n is not None else "—"

        html = ['<div style="line-height:1.5">']
        split_note = ""
        if r.get("targets", 1) > 1:
            split_note = f' <span style="color:#9fb6d4;font-size:11px">(per enemy, ÷{r["targets"]})</span>'
        html.append(
            f'<div style="font-size:15px;font-weight:bold;color:#6bcb77">'
            f'Normal hit: {fmt(r["total_normal"])}{split_note}</div>'
        )
        if r.get("total_crit") is not None:
            html.append(
                f'<div style="font-size:13px;color:#ffd93d">'
                f'Critical (×{r["crit_mult"]}): {fmt(r["total_crit"])}</div>'
            )
        if r.get("dot_total"):
            html.append(
                f'<div style="font-size:11px;color:#9fb6d4">'
                f'includes DoT {fmt(r["dot_round"])}/round → {fmt(r["dot_total"])}</div>'
            )

        if r.get("boss_hp") is not None:
            html.append('<div style="margin-top:6px;border-top:1px solid #1f3460;padding-top:5px">')
            n_after = r.get("hp_after_normal")
            killed_n = r.get("killed_normal")
            col = "#6bcb77" if killed_n else "#e0e0e0"
            tag = "  ☠ DEFEATED" if killed_n else ""
            html.append(
                f'<div style="color:{col};font-size:12px">Boss HP {fmt(r["boss_hp"])} → '
                f'<b>{fmt(max(0, n_after))}</b> after normal{tag}</div>'
            )
            if r.get("hp_after_crit") is not None:
                killed_c = r.get("killed_crit")
                col2 = "#6bcb77" if killed_c else "#e0e0e0"
                tag2 = "  ☠ DEFEATED" if killed_c else ""
                html.append(
                    f'<div style="color:{col2};font-size:12px">→ '
                    f'<b>{fmt(max(0, r["hp_after_crit"]))}</b> after crit{tag2}</div>'
                )
            html.append('</div>')

        # step breakdown
        if r.get("steps"):
            html.append('<div style="margin-top:6px;color:#7a8aa0;font-size:10px;line-height:1.6">')
            for s in r["steps"]:
                html.append(f"• {s}<br>")
            html.append('</div>')
        html.append('</div>')
        return "".join(html)

    # ── reset ───────────────────────────────────────────────────
    def reset_all(self):
        self._loading = True
        self.wizard_combo.setCurrentIndex(0)
        self.base_input.setText("0")
        self.per_pip_input.setText("0")
        self.pips_input.setText("0")
        self.dot_input.setText("0")
        self.dot_rounds.setText("0")
        self.targets_combo.setCurrentIndex(0)
        self.enchant_combo.setCurrentIndex(0)
        for g in (self.blade_group, self.trap_group, self.shield_group,
                  self.armor_group, self.aura_group, self.circle_group):
            g.clear()
        for c in (self.crit_check, self.pierce_check, self.flat_check):
            c.setChecked(True)
        self.clear_boss()
        self.resist_combo.setCurrentText("0%")
        self.hp_input.setText("")
        self._loading = False
        self._recalc()


# ════════════════════════════════════════════════════════════════
# PRESET EDITOR
# ════════════════════════════════════════════════════════════════

class PresetEditorPanel(QWidget):
    changed = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._build()
        self._refresh()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        filt = QHBoxLayout()
        filt.addWidget(_lbl("Category"))
        self.cat_filter = QComboBox()
        self.cat_filter.setStyleSheet(_COMBO_SS)
        self.cat_filter.addItem("All")
        self.cat_filter.addItems(dc.PRESET_CATEGORIES)
        self.cat_filter.currentTextChanged.connect(lambda *_: self._refresh())
        filt.addWidget(self.cat_filter)
        filt.addStretch()
        add_btn = QPushButton("＋ Add preset")
        add_btn.clicked.connect(lambda: self._edit_dialog(None))
        filt.addWidget(add_btn)
        root.addLayout(filt)

        note = _lbl("Blades / Traps / Shields / Auras / Battlecircle use a percentage "
                    "(e.g. +25 or -30).  Enchant / Armor use a flat number.  "
                    "Right-click an entry to edit or delete it.", "#666", size=10)
        note.setWordWrap(True)
        root.addWidget(note)

        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;"
            "color:#e0e0e0;font-size:12px;}"
            "QListWidget::item{padding:6px 8px;border-bottom:1px solid #16213e;}"
            "QListWidget::item:selected{background:#1f4a80;}"
        )
        self.list.itemDoubleClicked.connect(self._on_double)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.list, stretch=1)

        btns = QHBoxLayout()
        edit_btn = QPushButton("✎ Edit")
        edit_btn.clicked.connect(self._on_edit)
        del_btn = QPushButton("🗑 Delete")
        del_btn.clicked.connect(self._on_delete)
        btns.addWidget(edit_btn)
        btns.addWidget(del_btn)
        btns.addStretch()
        root.addLayout(btns)

    def _refresh(self):
        self.list.clear()
        cat = self.cat_filter.currentText()
        presets = dc.list_presets(self.conn, cat)
        # group by category, ordered like PRESET_CATEGORIES
        by_cat = {}
        for p in presets:
            by_cat.setdefault(p["category"], []).append(p)
        ordered = [c for c in dc.PRESET_CATEGORIES if c in by_cat]
        ordered += [c for c in by_cat if c not in dc.PRESET_CATEGORIES]

        for category in ordered:
            header = QListWidgetItem(f"▸ {category}")
            header.setFlags(Qt.NoItemFlags)   # non-selectable heading
            header.setForeground(Qt.gray)
            f = header.font()
            f.setBold(True)
            header.setFont(f)
            self.list.addItem(header)
            for p in sorted(by_cat[category], key=lambda x: x["value"], reverse=True):
                vs = dc.preset_value_str(p["category"], p["value"])
                item = QListWidgetItem(f"      {p['label']}      {vs}")
                item.setData(Qt.UserRole, p["id"])
                self.list.addItem(item)

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None or item.data(Qt.UserRole) is None:
            return
        pid = item.data(Qt.UserRole)
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #1f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        act_edit = menu.addAction("✎ Edit")
        act_del = menu.addAction("🗑 Delete")
        chosen = menu.exec_(self.list.mapToGlobal(pos))
        if chosen == act_edit:
            self._edit_dialog(pid)
        elif chosen == act_del:
            self._delete_id(pid)

    def _selected_id(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _on_double(self, item):
        self._edit_dialog(item.data(Qt.UserRole))

    def _on_edit(self):
        pid = self._selected_id()
        if pid is not None:
            self._edit_dialog(pid)

    def _delete_id(self, pid):
        if pid is None:
            return
        if QMessageBox.question(self, "Delete preset", "Delete this preset?") == QMessageBox.Yes:
            dc.delete_preset(self.conn, pid)
            self._refresh()
            self.changed.emit()

    def _on_delete(self):
        self._delete_id(self._selected_id())

    def _edit_dialog(self, pid):
        existing = None
        if pid is not None:
            existing = next((p for p in dc.list_presets(self.conn) if p["id"] == pid), None)
        dlg = _PresetDialog(existing, self)
        if dlg.exec_():
            data = dlg.result_data()
            if pid is not None:
                data["id"] = pid
            dc.upsert_preset(self.conn, data)
            self._refresh()
            self.changed.emit()


from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QFormLayout


class _PresetDialog(QDialog):
    def __init__(self, existing, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preset")
        self.setStyleSheet(CALC_STYLE)
        self.setMinimumWidth(320)
        form = QFormLayout(self)

        self.cat = QComboBox()
        self.cat.addItems(dc.PRESET_CATEGORIES)
        self.label = QLineEdit()
        self.value = QLineEdit()
        self.value.setValidator(QDoubleValidator(-99999, 99999, 2))
        if existing:
            self.cat.setCurrentText(existing["category"])
            self.label.setText(existing["label"])
            self.value.setText(f"{existing['value']:g}")
        form.addRow("Category", self.cat)
        form.addRow("Label", self.label)
        form.addRow("Value", self.value)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def result_data(self):
        try:
            val = float(self.value.text() or 0)
        except ValueError:
            val = 0.0
        return {"category": self.cat.currentText(),
                "label": self.label.text().strip() or "Unnamed",
                "value": val}


# ════════════════════════════════════════════════════════════════
# HUB WRAPPER:  DAMAGE CALCULATOR  (calc page ↔ preset editor)
# ════════════════════════════════════════════════════════════════

class DamageCalcWidget(QWidget):
    nav_hub = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setStyleSheet(CALC_STYLE)
        dc.init_calc_tables(conn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.stack.addWidget(self._build_calc_page())     # 0
        self.stack.addWidget(self._build_preset_page())   # 1
        self.stack.setCurrentIndex(0)

    def _header(self, title, accent, right_btn=None):
        bar = QWidget()
        bar.setStyleSheet("background:#16213e;border-bottom:1px solid #0f3460;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        back = QPushButton("← Hub")
        back.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back.clicked.connect(self.nav_hub)
        row.addWidget(back)
        row.addStretch()
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 17, QFont.Bold))
        t.setStyleSheet(f"color:{accent};background:transparent;")
        row.addWidget(t)
        row.addStretch()
        if right_btn:
            row.addWidget(right_btn)
        else:
            spacer = QWidget()
            spacer.setFixedWidth(70)
            row.addWidget(spacer)
        return bar

    def _build_calc_page(self):
        page = QWidget()
        page.setStyleSheet("background:#1a1a2e;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        manage = QPushButton("⚙ Manage presets")
        manage.clicked.connect(lambda: (self.preset_editor._refresh(), self.stack.setCurrentIndex(1)))
        v.addWidget(self._header("🧮 Damage Calculator", "#6bcb77", manage))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#1a1a2e;}")
        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 16, 24, 16)
        self.calc_panel = CalcPanel(self.conn, compact=False)
        bl.addWidget(self.calc_panel)
        scroll.setWidget(body)
        v.addWidget(scroll, stretch=1)
        return page

    def _build_preset_page(self):
        page = QWidget()
        page.setStyleSheet("background:#1a1a2e;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        done = QPushButton("✓ Done")
        done.clicked.connect(self._presets_done)
        v.addWidget(self._header("⚙ Calculator Presets", "#4d96ff", done))

        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 16, 24, 16)
        self.preset_editor = PresetEditorPanel(self.conn)
        bl.addWidget(self.preset_editor)
        v.addWidget(body, stretch=1)
        return page

    def _presets_done(self):
        # presets changed → refresh the calculator's dropdowns
        self.calc_panel.refresh_lists()
        self.stack.setCurrentIndex(0)

    def refresh(self):
        self.calc_panel.refresh_lists()


# ════════════════════════════════════════════════════════════════
# WIZARD EDITOR + CHARACTER MANAGER
# ════════════════════════════════════════════════════════════════

class WizardEditorPanel(QWidget):
    saved = pyqtSignal()
    cancelled = pyqtSignal()

    GRID_ROWS = [
        ("damage", "Damage %"),
        ("damage_flat", "Damage +"),
        ("resist", "Resist %"),
        ("accuracy", "Accuracy %"),
        ("critical", "Critical"),
        ("block", "Block"),
        ("pierce", "Pierce %"),
    ]

    def __init__(self, conn, wizard_id, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.wizard_id = wizard_id
        self.grid_inputs = {}   # (field, school) -> QLineEdit
        self._build()
        if wizard_id is not None:
            self._load(wizard_id)

    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # Top fields
        top = QGridLayout()
        top.setHorizontalSpacing(8)
        top.setVerticalSpacing(6)
        top.addWidget(_lbl("Name"), 0, 0)
        self.name_input = QLineEdit()
        self.name_input.setStyleSheet(_INPUT_SS)
        top.addWidget(self.name_input, 0, 1)
        top.addWidget(_lbl("School"), 0, 2)
        self.school_input = QComboBox()
        self.school_input.setStyleSheet(_COMBO_SS)
        self.school_input.addItems(dc.WIZARD_SCHOOLS)
        self.school_input.setToolTip("Primary school")
        top.addWidget(self.school_input, 0, 3)
        top.addWidget(_lbl("Level"), 0, 4)
        self.level_input = self._n(60)
        top.addWidget(self.level_input, 0, 5)

        top.addWidget(_lbl("2nd school"), 1, 0)
        self.school2_input = QComboBox()
        self.school2_input.setStyleSheet(_COMBO_SS)
        self.school2_input.addItem("— none —")
        self.school2_input.addItems(dc.WIZARD_SCHOOLS)
        self.school2_input.setToolTip("Secondary school (spellweaving)")
        top.addWidget(self.school2_input, 1, 1)
        top.addWidget(_lbl("Health"), 1, 2)
        self.health_input = self._n(100)
        top.addWidget(self.health_input, 1, 3)
        top.addWidget(_lbl("Mana"), 1, 4)
        self.mana_input = self._n(80)
        top.addWidget(self.mana_input, 1, 5)
        v.addLayout(top)

        # Per-school stat grid
        grid_box = QFrame()
        grid_box.setStyleSheet("QFrame{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;}")
        gl = QGridLayout(grid_box)
        gl.setHorizontalSpacing(4)
        gl.setVerticalSpacing(4)
        gl.setContentsMargins(8, 8, 8, 8)
        gl.addWidget(_lbl("", "#888"), 0, 0)
        for ci, school in enumerate(dc.STAT_SCHOOLS):
            h = _lbl(school[:3], dc.SCHOOL_COLORS.get(school, "#ccc"), bold=True, size=10)
            h.setAlignment(Qt.AlignCenter)
            gl.addWidget(h, 0, ci + 1)
        for ri, (field, label) in enumerate(self.GRID_ROWS):
            gl.addWidget(_lbl(label, "#9fb6d4", size=10), ri + 1, 0)
            for ci, school in enumerate(dc.STAT_SCHOOLS):
                le = QLineEdit()
                le.setFixedWidth(48)
                le.setAlignment(Qt.AlignCenter)
                le.setStyleSheet(_INPUT_SS + "font-size:11px;padding:2px;")
                le.setValidator(QDoubleValidator(-99999, 99999, 2))
                self.grid_inputs[(field, school)] = le
                gl.addWidget(le, ri + 1, ci + 1)
        v.addWidget(grid_box)

        # Bottom single stats
        bot = QGridLayout()
        bot.setHorizontalSpacing(8)
        bot.addWidget(_lbl("Stun resist %"), 0, 0)
        self.stun_input = self._n(60)
        bot.addWidget(self.stun_input, 0, 1)
        bot.addWidget(_lbl("Heal in %"), 0, 2)
        self.heal_in_input = self._n(60)
        bot.addWidget(self.heal_in_input, 0, 3)
        bot.addWidget(_lbl("Heal out %"), 0, 4)
        self.heal_out_input = self._n(60)
        bot.addWidget(self.heal_out_input, 0, 5)
        v.addLayout(bot)

        # Buttons
        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancelled)
        save = QPushButton("💾 Save wizard")
        save.setStyleSheet(
            "QPushButton{background:#0f4d2e;color:#9fffcf;border:1px solid #1f6a44;"
            "border-radius:5px;padding:6px 16px;font-size:12px;}"
            "QPushButton:hover{background:#156b40;}"
        )
        save.clicked.connect(self._save)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(save)
        v.addLayout(btns)
        v.addStretch()

    def _n(self, width):
        le = QLineEdit()
        le.setFixedWidth(width)
        le.setStyleSheet(_INPUT_SS)
        le.setValidator(QDoubleValidator(-99999, 9999999, 2))
        return le

    def _load(self, wid):
        w = dc.get_wizard(self.conn, wid)
        if not w:
            return
        self.name_input.setText(w.get("name", ""))
        self.school_input.setCurrentText(w.get("school", "Storm"))
        s2 = w.get("school2", "")
        self.school2_input.setCurrentText(s2 if s2 in dc.WIZARD_SCHOOLS else "— none —")
        self.level_input.setText(str(w.get("level", "") or ""))
        self.health_input.setText(str(w.get("health", "") or ""))
        self.mana_input.setText(str(w.get("mana", "") or ""))
        self.stun_input.setText(f"{w.get('stun_resist', 0):g}")
        self.heal_in_input.setText(f"{w.get('heal_in', 0):g}")
        self.heal_out_input.setText(f"{w.get('heal_out', 0):g}")
        for (field, school), le in self.grid_inputs.items():
            val = w.get(field, {}).get(school)
            if val not in (None, ""):
                le.setText(str(val))

    def _save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Please enter a wizard name.")
            return

        def grids(field):
            out = {}
            for school in dc.STAT_SCHOOLS:
                txt = self.grid_inputs[(field, school)].text().strip()
                if txt:
                    try:
                        out[school] = float(txt) if "." in txt else int(txt)
                    except ValueError:
                        pass
            return out

        def num(le, integer=False):
            t = le.text().strip()
            if not t:
                return 0
            try:
                return int(float(t)) if integer else float(t)
            except ValueError:
                return 0

        data = {
            "name": name,
            "school": self.school_input.currentText(),
            "school2": (self.school2_input.currentText()
                        if self.school2_input.currentText() in dc.WIZARD_SCHOOLS else ""),
            "level": num(self.level_input, True),
            "health": num(self.health_input, True),
            "mana": num(self.mana_input, True),
            "damage": grids("damage"),
            "damage_flat": grids("damage_flat"),
            "resist": grids("resist"),
            "accuracy": grids("accuracy"),
            "critical": grids("critical"),
            "block": grids("block"),
            "pierce": grids("pierce"),
            "stun_resist": num(self.stun_input),
            "heal_in": num(self.heal_in_input),
            "heal_out": num(self.heal_out_input),
        }
        if self.wizard_id is not None:
            data["id"] = self.wizard_id
        dc.upsert_wizard(self.conn, data)
        self.saved.emit()


class CharacterManagerWidget(QWidget):
    nav_hub = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setStyleSheet(CALC_STYLE)
        dc.init_calc_tables(conn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.stack.addWidget(self._build_list_page())   # 0
        self.stack.setCurrentIndex(0)

    def _header(self, title, accent, right_btn=None):
        bar = QWidget()
        bar.setStyleSheet("background:#16213e;border-bottom:1px solid #0f3460;")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 10, 16, 10)
        back = QPushButton("← Hub")
        back.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back.clicked.connect(self.nav_hub)
        row.addWidget(back)
        row.addStretch()
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 17, QFont.Bold))
        t.setStyleSheet(f"color:{accent};background:transparent;")
        row.addWidget(t)
        row.addStretch()
        if right_btn:
            row.addWidget(right_btn)
        else:
            spacer = QWidget()
            spacer.setFixedWidth(70)
            row.addWidget(spacer)
        return bar

    def _build_list_page(self):
        page = QWidget()
        page.setStyleSheet("background:#1a1a2e;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        add = QPushButton("＋ New wizard")
        add.clicked.connect(lambda: self._open_editor(None))
        v.addWidget(self._header("🧙 Characters", "#ffd93d", add))

        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(24, 16, 24, 16)

        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;"
            "color:#e0e0e0;font-size:13px;}"
            "QListWidget::item{padding:10px 12px;border-bottom:1px solid #16213e;}"
            "QListWidget::item:selected{background:#1f4a80;}"
        )
        self.list.itemDoubleClicked.connect(
            lambda it: self._open_editor(it.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)
        bl.addWidget(self.list, stretch=1)

        btns = QHBoxLayout()
        edit = QPushButton("✎ Edit")
        edit.clicked.connect(self._edit_selected)
        dele = QPushButton("🗑 Delete")
        dele.clicked.connect(self._delete_selected)
        btns.addWidget(edit)
        btns.addWidget(dele)
        btns.addStretch()
        bl.addLayout(btns)

        v.addWidget(body, stretch=1)
        self._refresh_list()
        return page

    def _refresh_list(self):
        self.list.clear()
        wizards = dc.list_wizards(self.conn)
        if not wizards:
            placeholder = QListWidgetItem("No wizards yet — click ＋ New wizard to add one.")
            placeholder.setFlags(Qt.NoItemFlags)
            self.list.addItem(placeholder)
            return
        for w in wizards:
            color = dc.SCHOOL_COLORS.get(w["school"], "#ccc")
            school_txt = w["school"]
            if w.get("school2"):
                school_txt += f" / {w['school2']}"
            it = QListWidgetItem(
                f"{w['name']}   ·   {school_txt}   ·   Lv {w.get('level', 0)}   ·   "
                f"{int(w.get('health', 0)):,} HP"
            )
            it.setData(Qt.UserRole, w["id"])
            self.list.addItem(it)

    def _on_context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None or item.data(Qt.UserRole) is None:
            return
        wid = item.data(Qt.UserRole)
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #1f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        act_edit = menu.addAction("✎ Edit")
        act_del = menu.addAction("🗑 Delete")
        chosen = menu.exec_(self.list.mapToGlobal(pos))
        if chosen == act_edit:
            self._open_editor(wid)
        elif chosen == act_del:
            self._delete_id(wid)

    def _selected(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it and it.data(Qt.UserRole) is not None else None

    def _edit_selected(self):
        wid = self._selected()
        if wid is not None:
            self._open_editor(wid)

    def _delete_id(self, wid):
        if wid is None:
            return
        if QMessageBox.question(self, "Delete wizard", "Delete this wizard?") == QMessageBox.Yes:
            dc.delete_wizard(self.conn, wid)
            self._refresh_list()

    def _delete_selected(self):
        self._delete_id(self._selected())

    def _open_editor(self, wid):
        if self.stack.count() > 1:
            old = self.stack.widget(1)
            self.stack.removeWidget(old)
            old.deleteLater()

        page = QWidget()
        page.setStyleSheet("background:#1a1a2e;")
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header("🧙 Edit Wizard" if wid else "🧙 New Wizard", "#ffd93d"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#1a1a2e;}")
        inner = QWidget()
        inner.setStyleSheet("background:#1a1a2e;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(24, 16, 24, 16)
        editor = WizardEditorPanel(self.conn, wid)
        editor.saved.connect(self._after_save)
        editor.cancelled.connect(lambda: self.stack.setCurrentIndex(0))
        il.addWidget(editor)
        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

        self.stack.addWidget(page)
        self.stack.setCurrentIndex(1)

    def _after_save(self):
        self._refresh_list()
        self.stack.setCurrentIndex(0)
