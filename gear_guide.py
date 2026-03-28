"""
gear_guide.py
═════════════
Gear Guide feature for Wizard101 Companion.

Two panels (swapped via stacked widget):
  1. Browse / Search — filter by school + level range → result cards
  2. Loadout Editor   — full slot / option / pet-stat editor for one loadout
"""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QScrollArea, QFrame, QStackedWidget,
    QLineEdit, QTextEdit, QMessageBox, QSizePolicy, QGridLayout,
    QGroupBox, QInputDialog, QDialog, QDialogButtonBox, QFormLayout,
    QListWidget, QListWidgetItem, QAbstractItemView, QCompleter,
    QApplication,
)
from PyQt5.QtCore import Qt, pyqtSignal, QStringListModel
from PyQt5.QtGui import QFont, QColor

import database_gear as dg

# Exporter (optional)
# Exporter (optional)
try:
    import exporter as _exp
    _EXPORTER_AVAILABLE = True
except ImportError:
    _exp = None
    _EXPORTER_AVAILABLE = False

# ─── SCHOOL DEFINITIONS ──────────────────────────────────────────

SCHOOLS = [
    "Universal",
    "Fire",
    "Ice",
    "Storm",
    "Myth",
    "Life",
    "Death",
    "Balance",
]

SCHOOL_COLORS = {
    "Universal": "#a0a0a0",
    "Fire":      "#e05a00",
    "Ice":       "#4db8ff",
    "Storm":     "#9b59b6",
    "Myth":      "#d4ac0d",
    "Life":      "#27ae60",
    "Death":     "#8e44ad",
    "Balance":   "#c8a000",
}

SCHOOL_BG = {
    "Universal": "#1e1e1e",
    "Fire":      "#1f0d00",
    "Ice":       "#001525",
    "Storm":     "#15001e",
    "Myth":      "#1e1800",
    "Life":      "#041a09",
    "Death":     "#120020",
    "Balance":   "#1a1500",
}

SCHOOL_ICONS = {
    "Universal": "✦",
    "Fire":      "🔥",
    "Ice":       "❄",
    "Storm":     "⚡",
    "Myth":      "🔮",
    "Life":      "🌿",
    "Death":     "💀",
    "Balance":   "⚖",
}

# Standard gear slots — user can add more freely in editor
DEFAULT_SLOTS = [
    "Hat", "Robe", "Boots", "Wand", "Athame",
    "Amulet", "Ring", "Deck", "Mount",
]

# ─── STYLESHEET ADDITIONS ────────────────────────────────────────

GEAR_STYLE = """
    QFrame#loadoutCard {
        border-radius: 10px;
        border: 1px solid #2a2a3e;
    }
    QFrame#loadoutCard:hover {
        border-color: #e94560;
    }
    QFrame#slotFrame {
        background-color: #111827;
        border: 1px solid #1f3060;
        border-radius: 6px;
    }
    QFrame#optionRow {
        background-color: #0d1520;
        border-left: 3px solid #0f3460;
        border-radius: 4px;
    }
    QSpinBox {
        background-color: #16213e;
        color: #e0e0e0;
        border: 2px solid #0f3460;
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 13px;
    }
    QSpinBox:focus { border-color: #e94560; }
"""


# ═══════════════════════════════════════════════════════════════
# HELPERS — get all known categories across all loadouts
# ═══════════════════════════════════════════════════════════════

def get_all_categories(conn) -> list:
    """Return sorted list of all unique category tags used in any loadout."""
    rows = conn.execute("SELECT category FROM gear_loadouts WHERE category != ''").fetchall()
    cats = set()
    for row in rows:
        for tag in row[0].split(','):
            t = tag.strip()
            if t:
                cats.add(t)
    return sorted(cats, key=str.lower)


# ═══════════════════════════════════════════════════════════════
# TAG CHIP WIDGET  (single removable pill)
# ═══════════════════════════════════════════════════════════════

class TagChip(QFrame):
    """A single coloured tag pill with an × remove button."""
    removed = pyqtSignal(str)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text
        self.setStyleSheet("""
            QFrame {
                background-color: #1f3a6e;
                border: 1px solid #4d6fa8;
                border-radius: 10px;
            }
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(4)

        lbl = QLabel(text)
        lbl.setStyleSheet("color:#a8c8ff; font-size:11px; font-weight:bold; background:transparent; border:none;")
        layout.addWidget(lbl)

        btn = QPushButton("×")
        btn.setFixedSize(16, 16)
        btn.setStyleSheet("""
            QPushButton { background:transparent; color:#4d6fa8; border:none;
                          font-size:13px; font-weight:bold; padding:0; }
            QPushButton:hover { color:#e94560; }
        """)
        btn.clicked.connect(lambda: self.removed.emit(self.text))
        layout.addWidget(btn)


# ═══════════════════════════════════════════════════════════════
# CATEGORY TAG EDITOR  (flow row of chips + autocomplete input)
# ═══════════════════════════════════════════════════════════════

class CategoryTagEditor(QWidget):
    """
    Inline tag editor: shows existing tags as removable chips,
    has an autocomplete QLineEdit to add new ones.
    """
    tags_changed = pyqtSignal(list)

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._tags: list[str] = []
        self._build()

    def _build(self):
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        # Chip flow row (wraps naturally via a QWidget + flow layout workaround)
        self._chips_widget = QWidget()
        self._chips_widget.setStyleSheet("background:transparent;")
        self._chips_flow = QHBoxLayout(self._chips_widget)
        self._chips_flow.setContentsMargins(0, 0, 0, 0)
        self._chips_flow.setSpacing(6)
        self._chips_flow.addStretch()
        self._layout.addWidget(self._chips_widget)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Add category tag… (Enter to add)")
        self._input.setStyleSheet(
            "QLineEdit { background:#16213e; color:#e0e0e0; border:1px solid #2a3a6a;"
            "border-radius:5px; padding:4px 8px; font-size:12px; }"
            "QLineEdit:focus { border-color:#4d96ff; }"
        )
        self._input.returnPressed.connect(self._add_from_input)
        self._input.textChanged.connect(self._update_completer)
        input_row.addWidget(self._input, stretch=1)

        add_btn = QPushButton("＋")
        add_btn.setFixedSize(28, 28)
        add_btn.setStyleSheet(
            "QPushButton{background:#0d2a12;color:#27ae60;border:1px solid #1b5c38;"
            "border-radius:5px;font-size:15px;font-weight:bold;}"
            "QPushButton:hover{background:#1b5c38;}"
        )
        add_btn.clicked.connect(self._add_from_input)
        input_row.addWidget(add_btn)
        self._layout.addLayout(input_row)

        # Completer
        self._completer = QCompleter([], self._input)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchStartsWith)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated.connect(self._on_completer_activated)
        self._input.setCompleter(self._completer)

        self._refresh_completer()

    def _refresh_completer(self):
        """Update completer list from DB, excluding already-added tags."""
        all_cats = get_all_categories(self.conn)
        available = [c for c in all_cats if c not in self._tags]
        model = QStringListModel(available, self._completer)
        self._completer.setModel(model)

    def _update_completer(self, text: str):
        self._refresh_completer()

    def _on_completer_activated(self, text: str):
        self._input.clear()
        self._add_tag(text.strip())

    def _add_from_input(self):
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self._add_tag(text)

    def _add_tag(self, text: str):
        if not text or text in self._tags:
            return
        self._tags.append(text)
        self._rebuild_chips()
        self._refresh_completer()
        self.tags_changed.emit(list(self._tags))

    def _remove_tag(self, text: str):
        if text in self._tags:
            self._tags.remove(text)
        self._rebuild_chips()
        self._refresh_completer()
        self.tags_changed.emit(list(self._tags))

    def _rebuild_chips(self):
        # Clear flow layout (except stretch)
        while self._chips_flow.count() > 1:
            item = self._chips_flow.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Add chips
        for tag in self._tags:
            chip = TagChip(tag, self._chips_widget)
            chip.removed.connect(self._remove_tag)
            self._chips_flow.insertWidget(self._chips_flow.count() - 1, chip)

    # ── Public API ──
    def get_tags(self) -> list:
        return list(self._tags)

    def set_tags(self, tags: list):
        self._tags = [t.strip() for t in tags if t.strip()]
        self._rebuild_chips()
        self._refresh_completer()

    def get_category_string(self) -> str:
        """Return comma-separated string for DB storage."""
        return ", ".join(self._tags)

    def set_from_string(self, s: str):
        """Load tags from a comma-separated string."""
        self.set_tags([t.strip() for t in s.split(',') if t.strip()])


# ═══════════════════════════════════════════════════════════════
# CATEGORY MULTI-SELECT DROPDOWN  (browse filter)
# ═══════════════════════════════════════════════════════════════

class CategoryFilterButton(QPushButton):
    """
    A button that opens a popup list of all known categories
    with checkboxes. Selected categories are shown on the button.
    """
    selection_changed = pyqtSignal(list)  # emits list of selected category strings

    def __init__(self, conn, parent=None):
        super().__init__("All Categories ▾", parent)
        self.conn = conn
        self._selected: list = []
        self._update_style()
        self.clicked.connect(self._open_popup)
        self.setMinimumWidth(140)
        self.setStyleSheet("""
            QPushButton {
                background:#16213e; color:#a0a0a0;
                border:1px solid #2a3a6a; border-radius:5px;
                padding:5px 10px; font-size:12px; text-align:left;
            }
            QPushButton:hover { border-color:#4d96ff; }
        """)

    def _update_style(self):
        if self._selected:
            label = ", ".join(self._selected[:2])
            if len(self._selected) > 2:
                label += f" +{len(self._selected)-2}"
            self.setText(f"🏷 {label} ▾")
            self.setStyleSheet("""
                QPushButton {
                    background:#1f3a6e; color:#a8c8ff;
                    border:1px solid #4d6fa8; border-radius:5px;
                    padding:5px 10px; font-size:12px; text-align:left;
                }
                QPushButton:hover { border-color:#4d96ff; }
            """)
        else:
            self.setText("All Categories ▾")
            self.setStyleSheet("""
                QPushButton {
                    background:#16213e; color:#a0a0a0;
                    border:1px solid #2a3a6a; border-radius:5px;
                    padding:5px 10px; font-size:12px; text-align:left;
                }
                QPushButton:hover { border-color:#4d96ff; }
            """)

    def _open_popup(self):
        all_cats = get_all_categories(self.conn)
        if not all_cats:
            return

        popup = QDialog(self, Qt.Popup | Qt.FramelessWindowHint)
        popup.setStyleSheet("""
            QDialog { background:#1a1a2e; border:1px solid #2a3a6a; border-radius:6px; }
            QListWidget { background:#1a1a2e; color:#e0e0e0; border:none; font-size:12px; }
            QListWidget::item { padding:6px 10px; }
            QListWidget::item:selected { background:#1f3a6e; }
            QListWidget::item:hover { background:#1a2a3a; }
            QPushButton { background:#0f3460; color:#e0e0e0; border:none; border-radius:4px;
                          padding:4px 12px; font-size:11px; font-weight:bold; }
            QPushButton:hover { background:#4d96ff; color:#0a0a1a; }
        """)
        vl = QVBoxLayout(popup)
        vl.setContentsMargins(8, 8, 8, 8)
        vl.setSpacing(6)

        hint = QLabel("Select categories to filter")
        hint.setStyleSheet("color:#555; font-size:10px;")
        vl.addWidget(hint)

        lw = QListWidget()
        lw.setSelectionMode(QAbstractItemView.MultiSelection)
        for cat in all_cats:
            item = QListWidgetItem(cat)
            lw.addItem(item)
            if cat in self._selected:
                item.setSelected(True)
        vl.addWidget(lw)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: lw.clearSelection())
        btn_row.addWidget(clear_btn)
        ok_btn = QPushButton("Apply")
        ok_btn.clicked.connect(popup.accept)
        btn_row.addWidget(ok_btn)
        vl.addLayout(btn_row)

        # Position popup below button
        pos = self.mapToGlobal(self.rect().bottomLeft())
        popup.move(pos)
        popup.resize(220, min(300, 50 + len(all_cats) * 34))

        if popup.exec_() == QDialog.Accepted:
            self._selected = [lw.item(i).text()
                              for i in range(lw.count())
                              if lw.item(i).isSelected()]
            self._update_style()
            self.selection_changed.emit(list(self._selected))

    def get_selected(self) -> list:
        return list(self._selected)

    def refresh_categories(self, conn):
        """Refresh the internal conn reference when a new loadout is saved."""
        self.conn = conn


# ═══════════════════════════════════════════════════════════════
# LOADOUT CARD  (browse view)
# ═══════════════════════════════════════════════════════════════

class LoadoutCard(QFrame):
    """Clickable card shown in the browse grid."""
    clicked = pyqtSignal(int)   # emits loadout id

    def __init__(self, loadout: dict, parent=None):
        super().__init__(parent)
        self.loadout_id = loadout['id']
        school = loadout.get('school', 'Universal')
        color  = SCHOOL_COLORS.get(school, '#a0a0a0')
        bg     = SCHOOL_BG.get(school, '#1e1e1e')
        icon   = SCHOOL_ICONS.get(school, '✦')

        self.setObjectName("loadoutCard")
        self.setStyleSheet(f"""
            QFrame#loadoutCard {{
                background-color: {bg};
                border: 1px solid {color}44;
                border-radius: 10px;
            }}
            QFrame#loadoutCard:hover {{
                border: 2px solid {color};
            }}
        """)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        # Title
        name_lbl = QLabel(loadout.get('name', 'Unnamed'))
        name_lbl.setStyleSheet(
            f"color:{color}; font-size:15px; font-weight:bold;"
        )
        name_lbl.setWordWrap(True)
        layout.addWidget(name_lbl)

        # Tags row
        tags = QHBoxLayout()
        tags.setSpacing(5)
        tags.setContentsMargins(0, 0, 0, 0)

        def pill(text, clr="#e0e0e0", bg="#1f2a3a"):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"color:{clr}; background:{bg}; border-radius:3px;"
                f" padding:1px 6px; font-size:10px; font-weight:bold;"
            )
            return lbl

        lvl_str = f"Lv {loadout.get('level_min',1)}–{loadout.get('level_max',170)}"
        tags.addWidget(pill(lvl_str))

        world = loadout.get('world', '')
        if world:
            tags.addWidget(pill(f"🌍 {world}", "#aaaaaa", "#151515"))

        tags.addStretch()
        layout.addLayout(tags)

        # Category tags as individual pills
        cat_str = loadout.get('category', '')
        if cat_str:
            cat_tags = [t.strip() for t in cat_str.split(',') if t.strip()]
            if cat_tags:
                cat_row = QHBoxLayout()
                cat_row.setSpacing(4)
                cat_row.setContentsMargins(0, 0, 0, 0)
                for ct in cat_tags[:4]:  # show max 4 to avoid overflow
                    lbl = QLabel(ct)
                    lbl.setStyleSheet(
                        f"color:{color}; background:{bg}; border:1px solid {color}44;"
                        f"border-radius:8px; padding:1px 7px; font-size:10px; font-weight:bold;"
                    )
                    cat_row.addWidget(lbl)
                if len(cat_tags) > 4:
                    lbl = QLabel(f"+{len(cat_tags)-4}")
                    lbl.setStyleSheet("color:#555; font-size:10px; padding:1px 4px;")
                    cat_row.addWidget(lbl)
                cat_row.addStretch()
                layout.addLayout(cat_row)

        # School icon line
        school_lbl = QLabel(f"{icon} {school}")
        school_lbl.setStyleSheet(f"color:{color}88; font-size:11px;")
        layout.addWidget(school_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.loadout_id)
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════
# OPTION ROW WIDGET  (editor)
# ═══════════════════════════════════════════════════════════════

class OptionRowWidget(QFrame):
    removed = None   # set by parent

    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        self.setObjectName("optionRow")
        self.setStyleSheet("""
            QFrame#optionRow {
                background-color: #0d1520;
                border-left: 3px solid #0f3460;
                border-radius: 4px;
            }
        """)
        d = data or {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.label_input = QLineEdit(d.get('label', 'optimal'))
        self.label_input.setPlaceholderText("Label (optimal, farm…)")
        self.label_input.setFixedWidth(120)
        layout.addWidget(self.label_input)

        self.item_input = QLineEdit(d.get('item_name', ''))
        self.item_input.setPlaceholderText("Item name…")
        layout.addWidget(self.item_input, stretch=2)

        self.notes_input = QLineEdit(d.get('stats_notes', ''))
        self.notes_input.setPlaceholderText("Stats / notes…")
        layout.addWidget(self.notes_input, stretch=3)

        rem = QPushButton("✕")
        rem.setFixedSize(26, 26)
        rem.setStyleSheet(
            "QPushButton{background:#3a0a0a;color:#e94560;border:none;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        rem.clicked.connect(lambda: self.removed(self) if self.removed else None)
        layout.addWidget(rem)

    def get_data(self) -> dict:
        return {
            'label':       self.label_input.text().strip(),
            'item_name':   self.item_input.text().strip(),
            'stats_notes': self.notes_input.text().strip(),
        }


# ═══════════════════════════════════════════════════════════════
# SLOT WIDGET  (editor — one collapsible gear slot)
# ═══════════════════════════════════════════════════════════════

class SlotWidget(QFrame):
    slot_removed = None

    def __init__(self, slot_data: dict = None, parent=None):
        super().__init__(parent)
        self.setObjectName("slotFrame")
        self.setStyleSheet("""
            QFrame#slotFrame {
                background-color: #111827;
                border: 1px solid #1f3060;
                border-radius: 6px;
            }
        """)
        d = slot_data or {}
        self.option_widgets: list[OptionRowWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        self.slot_name_input = QLineEdit(d.get('slot_name', ''))
        self.slot_name_input.setPlaceholderText("Slot name (Hat, Robe…)")
        self.slot_name_input.setFixedWidth(160)
        self.slot_name_input.setStyleSheet(
            "font-weight:bold; color:#4d96ff; background:#0d1520;"
            "border:1px solid #1f3a6e; border-radius:4px; padding:4px 8px;"
        )
        hdr.addWidget(self.slot_name_input)
        hdr.addStretch()

        add_opt_btn = QPushButton("＋ Option")
        add_opt_btn.setStyleSheet(
            "QPushButton{background:#0d2a12;color:#27ae60;border:1px solid #1b5c38;"
            "border-radius:4px;padding:4px 10px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#1b5c38;}"
        )
        add_opt_btn.clicked.connect(lambda: self._add_option())
        hdr.addWidget(add_opt_btn)

        rem_slot_btn = QPushButton("🗑 Slot")
        rem_slot_btn.setStyleSheet(
            "QPushButton{background:#1a0808;color:#e94560;border:1px solid #3a1010;"
            "border-radius:4px;padding:4px 10px;font-size:11px;}"
            "QPushButton:hover{background:#3a1010;}"
        )
        rem_slot_btn.clicked.connect(lambda: self.slot_removed(self) if self.slot_removed else None)
        hdr.addWidget(rem_slot_btn)
        layout.addLayout(hdr)

        # Option column headers
        col_hdr = QHBoxLayout()
        for txt, w in [("Label", 120), ("Item Name", None), ("Stats / Notes", None)]:
            l = QLabel(txt)
            l.setStyleSheet("color:#555; font-size:10px; font-weight:bold;")
            if w:
                l.setFixedWidth(w)
            col_hdr.addWidget(l, 0 if w else 1)
        col_hdr.addSpacing(34)  # align with ✕ button
        layout.addLayout(col_hdr)

        # Options container
        self.opts_layout = QVBoxLayout()
        self.opts_layout.setSpacing(4)
        layout.addLayout(self.opts_layout)

        # Load existing options
        for opt in d.get('options', []):
            self._add_option(opt)

        if not d.get('options'):
            self._add_option({'label': 'optimal'})

    def _add_option(self, data: dict = None):
        row = OptionRowWidget(data, self)
        row.removed = self._remove_option
        self.opts_layout.addWidget(row)
        self.option_widgets.append(row)

    def _remove_option(self, row: OptionRowWidget):
        self.option_widgets.remove(row)
        self.opts_layout.removeWidget(row)
        row.deleteLater()

    def get_data(self) -> dict:
        return {
            'slot_name': self.slot_name_input.text().strip(),
            'options':   [w.get_data() for w in self.option_widgets],
        }


# ═══════════════════════════════════════════════════════════════
# PET STAT ROW
# ═══════════════════════════════════════════════════════════════

class PetStatRow(QFrame):
    removed = None

    def __init__(self, data: dict = None, parent=None):
        super().__init__(parent)
        d = data or {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.stat_name = QLineEdit(d.get('stat_name', ''))
        self.stat_name.setPlaceholderText("Stat (e.g. Spell-Proof)")
        layout.addWidget(self.stat_name, 2)

        self.stat_val = QLineEdit(d.get('stat_value', ''))
        self.stat_val.setPlaceholderText("Value / notes")
        layout.addWidget(self.stat_val, 3)

        rem = QPushButton("✕")
        rem.setFixedSize(26, 26)
        rem.setStyleSheet(
            "QPushButton{background:#3a0a0a;color:#e94560;border:none;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        rem.clicked.connect(lambda: self.removed(self) if self.removed else None)
        layout.addWidget(rem)

    def get_data(self) -> dict:
        return {
            'stat_name':  self.stat_name.text().strip(),
            'stat_value': self.stat_val.text().strip(),
        }


# ═══════════════════════════════════════════════════════════════
# GEAR GUIDE BROWSE PANEL
# ═══════════════════════════════════════════════════════════════

class GearBrowsePanel(QWidget):
    open_loadout = pyqtSignal(int)   # open editor for existing id
    create_new   = pyqtSignal()
    go_hub       = pyqtSignal()      # navigate back to hub

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # ── Title row ──
        title_row = QHBoxLayout()
        back_btn = QPushButton("← Hub")
        back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back_btn.clicked.connect(self.go_hub.emit)
        title_row.addWidget(back_btn)
        title_row.addStretch()
        title = QLabel("🎒 Gear Guide")
        title.setStyleSheet("color:#e0e0e0;font-size:18px;font-weight:bold;")
        title_row.addWidget(title)
        title_row.addStretch()
        new_btn = QPushButton("＋ New Loadout")
        new_btn.setStyleSheet(
            "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
            "border-radius:6px;padding:7px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;}"
        )
        new_btn.clicked.connect(self.create_new.emit)
        title_row.addWidget(new_btn)

        del_all_btn = QPushButton("🗑 Delete All Gear")
        del_all_btn.setStyleSheet(
            "QPushButton{background:#3a0a0a;color:#e94560;border:1px solid #5a1a1a;"
            "border-radius:6px;padding:7px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#5a1a1a;}"
        )
        del_all_btn.clicked.connect(self._delete_all_gear)
        title_row.addWidget(del_all_btn)

        layout.addLayout(title_row)

        # ── Filters ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        filter_row.addWidget(QLabel("School:"))
        self.school_combo = QComboBox()
        self.school_combo.addItem("All Schools")
        for s in SCHOOLS:
            self.school_combo.addItem(
                f"{SCHOOL_ICONS.get(s,'✦')} {s}", s
            )
        self.school_combo.currentIndexChanged.connect(self._refresh)
        filter_row.addWidget(self.school_combo)

        filter_row.addWidget(QLabel("Level from:"))
        self.lvl_min = QSpinBox()
        self.lvl_min.setRange(1, 999)
        self.lvl_min.setValue(1)
        self.lvl_min.valueChanged.connect(self._refresh)
        filter_row.addWidget(self.lvl_min)

        filter_row.addWidget(QLabel("to:"))
        self.lvl_max = QSpinBox()
        self.lvl_max.setRange(1, 999)
        self.lvl_max.setValue(999)
        self.lvl_max.valueChanged.connect(self._refresh)
        filter_row.addWidget(self.lvl_max)

        filter_row.addWidget(QLabel("Category:"))
        self.cat_filter_btn = CategoryFilterButton(self.conn, self)
        self.cat_filter_btn.selection_changed.connect(lambda _: self._refresh())
        filter_row.addWidget(self.cat_filter_btn)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        # ── Scrollable card grid ──
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")

        self.cards_container = QWidget()
        self.cards_container.setStyleSheet("background:transparent;")
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)

        self.scroll.setWidget(self.cards_container)
        layout.addWidget(self.scroll, stretch=1)

        self._refresh()

    def _refresh(self):
        # Clear existing cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        school_data = self.school_combo.currentData()
        school = school_data if school_data else None
        lmin = self.lvl_min.value()
        lmax = self.lvl_max.value()
        selected_cats = self.cat_filter_btn.get_selected()

        loadouts = dg.list_loadouts(self.conn, school, lmin, lmax)

        # Filter by categories (all selected must be present in the loadout's tags)
        if selected_cats:
            def has_all_cats(lo):
                lo_cats = {t.strip().lower() for t in lo.get('category', '').split(',') if t.strip()}
                return all(sc.lower() in lo_cats for sc in selected_cats)
            loadouts = [lo for lo in loadouts if has_all_cats(lo)]

        if not loadouts:
            placeholder = QLabel("No loadouts found. Click '＋ New Loadout' to create one.")
            placeholder.setStyleSheet("color:#555;font-size:13px;padding:40px;")
            placeholder.setAlignment(Qt.AlignCenter)
            self.cards_layout.addWidget(placeholder, 0, 0, 1, 3)
            return

        cols = 3
        for i, lo in enumerate(loadouts):
            card = LoadoutCard(lo)
            card.clicked.connect(self.open_loadout.emit)
            self.cards_layout.addWidget(card, i // cols, i % cols)

        # Push cards to top
        self.cards_layout.setRowStretch(
            (len(loadouts) - 1) // cols + 1, 1
        )

    def refresh(self):
        self.cat_filter_btn.refresh_categories(self.conn)
        self._refresh()

    def _delete_all_gear(self):
        """Delete all gear loadouts after user confirmation."""
        from PyQt5.QtWidgets import QMessageBox as _MB
        box = _MB(self)
        box.setWindowTitle("Delete All Gear")
        box.setText("Delete <b>all</b> gear loadouts?")
        box.setInformativeText(
            "This will permanently remove every loadout, including all gear slots, "
            "options, and pet stats. This cannot be undone."
        )
        box.setStandardButtons(_MB.Yes | _MB.No)
        box.setDefaultButton(_MB.No)
        box.setIcon(_MB.Warning)
        if box.exec_() == _MB.Yes:
            count = dg.delete_all_gear(self.conn)
            self._refresh()
            _MB.information(self, "Deleted", f"Removed {count} loadout(s).")


# ═══════════════════════════════════════════════════════════════
# GEAR GUIDE EDITOR PANEL
# ═══════════════════════════════════════════════════════════════

class GearEditorPanel(QWidget):
    saved  = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, conn, loadout_id: int = None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.loadout_id = loadout_id
        self.slot_widgets: list[SlotWidget] = []
        self.pet_stat_rows: list[PetStatRow] = []
        self._build()
        if loadout_id:
            self._load(loadout_id)
        else:
            # Add default slots
            for slot in DEFAULT_SLOTS:
                self._add_slot({'slot_name': slot})

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top bar ──
        bar = QWidget()
        bar.setStyleSheet("background:#0d1b2a;border-bottom:1px solid #1f3460;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)

        back_btn = QPushButton("← Back to Overview")
        back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back_btn.clicked.connect(self.cancelled.emit)
        bar_layout.addWidget(back_btn)
        bar_layout.addStretch()

        # Export (only for existing loadouts — shown/hidden after build)
        self._bar_exp_btn = QPushButton("📤 Export")
        self._bar_exp_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 14px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#4d96ff;}"
        )
        self._bar_exp_btn.setVisible(_EXPORTER_AVAILABLE and bool(self.loadout_id))
        self._bar_exp_btn.clicked.connect(self._export)
        bar_layout.addWidget(self._bar_exp_btn)

        # Delete (only for existing loadouts)
        self._bar_del_btn = QPushButton("🗑 Delete")
        self._bar_del_btn.setStyleSheet(
            "QPushButton{background:#3a0a0a;color:#e94560;border:1px solid #6b1a1a;"
            "border-radius:5px;padding:6px 14px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        self._bar_del_btn.setVisible(bool(self.loadout_id))
        self._bar_del_btn.clicked.connect(self._delete)
        bar_layout.addWidget(self._bar_del_btn)

        save_btn = QPushButton("💾 Save Loadout")
        save_btn.setStyleSheet(
            "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 18px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;}"
        )
        save_btn.clicked.connect(self._save)
        bar_layout.addWidget(save_btn)
        outer.addWidget(bar)

        # ── Scrollable body ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")

        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(16, 14, 16, 16)
        self.body_layout.setSpacing(14)

        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        # ── Meta fields ──
        meta_box = QGroupBox("Loadout Info")
        meta_box.setStyleSheet(
            "QGroupBox{border:1px solid #0f3460;border-radius:6px;"
            "margin-top:12px;padding-top:16px;font-weight:bold;color:#e94560;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;}"
        )
        meta_layout = QGridLayout(meta_box)
        meta_layout.setSpacing(10)

        def field_lbl(txt):
            l = QLabel(txt)
            l.setStyleSheet("color:#999;font-size:11px;font-weight:bold;")
            return l

        meta_layout.addWidget(field_lbl("Name"), 0, 0)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Darkmoor Death Loadout")
        meta_layout.addWidget(self.name_input, 0, 1, 1, 3)

        meta_layout.addWidget(field_lbl("School"), 1, 0)
        self.school_combo = QComboBox()
        for s in SCHOOLS:
            self.school_combo.addItem(f"{SCHOOL_ICONS.get(s,'✦')} {s}", s)
        meta_layout.addWidget(self.school_combo, 1, 1)

        meta_layout.addWidget(field_lbl("Level Min"), 1, 2)
        self.lvl_min = QSpinBox()
        self.lvl_min.setRange(1, 999)
        self.lvl_min.setValue(1)
        meta_layout.addWidget(self.lvl_min, 1, 3)

        meta_layout.addWidget(field_lbl("World"), 2, 0)
        self.world_input = QLineEdit()
        self.world_input.setPlaceholderText("e.g. Darkmoor")
        meta_layout.addWidget(self.world_input, 2, 1)

        meta_layout.addWidget(field_lbl("Level Max"), 2, 2)
        self.lvl_max = QSpinBox()
        self.lvl_max.setRange(1, 999)
        self.lvl_max.setValue(999)
        meta_layout.addWidget(self.lvl_max, 2, 3)

        meta_layout.addWidget(field_lbl("Notes"), 3, 0)
        self.notes_input = QLineEdit()
        self.notes_input.setPlaceholderText("Optional notes about this loadout…")
        meta_layout.addWidget(self.notes_input, 3, 1, 1, 3)

        self.body_layout.addWidget(meta_box)

        # ── Category tags ──
        cat_hdr = QHBoxLayout()
        cat_title = QLabel("🏷 Categories")
        cat_title.setStyleSheet("color:#a8c8ff;font-size:14px;font-weight:bold;")
        cat_hdr.addWidget(cat_title)
        cat_hdr.addStretch()
        cat_hint = QLabel("Tags used to filter loadouts in the browse view")
        cat_hint.setStyleSheet("color:#444;font-size:11px;")
        cat_hdr.addWidget(cat_hint)
        self.body_layout.addLayout(cat_hdr)

        self.cat_tag_editor = CategoryTagEditor(self.conn, self)
        self.body_layout.addWidget(self.cat_tag_editor)

        # ── Gear Slots section ──
        slots_hdr = QHBoxLayout()
        slots_title = QLabel("⚔ Gear Slots")
        slots_title.setStyleSheet("color:#4d96ff;font-size:14px;font-weight:bold;")
        slots_hdr.addWidget(slots_title)
        slots_hdr.addStretch()

        add_slot_btn = QPushButton("＋ Add Slot")
        add_slot_btn.setStyleSheet(
            "QPushButton{background:#0d2a12;color:#27ae60;border:1px solid #1b5c38;"
            "border-radius:5px;padding:5px 12px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#1b5c38;}"
        )
        add_slot_btn.clicked.connect(lambda: self._add_slot())
        slots_hdr.addWidget(add_slot_btn)
        self.body_layout.addLayout(slots_hdr)

        self.slots_container = QVBoxLayout()
        self.slots_container.setSpacing(8)
        self.body_layout.addLayout(self.slots_container)

        # ── Pet Stats section ──
        pet_hdr = QHBoxLayout()
        pet_title = QLabel("🐾 Pet Stats")
        pet_title.setStyleSheet("color:#ffd93d;font-size:14px;font-weight:bold;")
        pet_hdr.addWidget(pet_title)
        pet_hdr.addStretch()

        add_pet_btn = QPushButton("＋ Add Stat")
        add_pet_btn.setStyleSheet(
            "QPushButton{background:#1a1500;color:#ffd93d;border:1px solid #3a3010;"
            "border-radius:5px;padding:5px 12px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#3a3010;}"
        )
        add_pet_btn.clicked.connect(lambda: self._add_pet_stat())
        pet_hdr.addWidget(add_pet_btn)
        self.body_layout.addLayout(pet_hdr)

        # Column headers for pet stats
        pet_col_hdr = QHBoxLayout()
        for txt in ["Stat Name", "Value / Notes"]:
            l = QLabel(txt)
            l.setStyleSheet("color:#555;font-size:10px;font-weight:bold;")
            pet_col_hdr.addWidget(l, 1)
        pet_col_hdr.addSpacing(34)
        self.body_layout.addLayout(pet_col_hdr)

        self.pet_container = QVBoxLayout()
        self.pet_container.setSpacing(6)
        self.body_layout.addLayout(self.pet_container)

        self.body_layout.addStretch()

    def _load(self, loadout_id: int):
        data = dg.get_loadout_full(self.conn, loadout_id)
        if not data:
            return

        self.name_input.setText(data.get('name', ''))

        school = data.get('school', 'Universal')
        idx = next((i for i, s in enumerate(SCHOOLS) if s == school), 0)
        self.school_combo.setCurrentIndex(idx)

        self.lvl_min.setValue(data.get('level_min', 1))
        self.lvl_max.setValue(data.get('level_max', 170))
        self.world_input.setText(data.get('world', ''))
        self.cat_tag_editor.set_from_string(data.get('category', ''))
        self.notes_input.setText(data.get('notes', ''))

        for slot in data.get('slots', []):
            self._add_slot(slot)

        for ps in data.get('pet_stats', []):
            self._add_pet_stat(ps)

    def _add_slot(self, data: dict = None):
        w = SlotWidget(data, self)
        w.slot_removed = self._remove_slot
        self.slots_container.addWidget(w)
        self.slot_widgets.append(w)

    def _remove_slot(self, w: SlotWidget):
        self.slot_widgets.remove(w)
        self.slots_container.removeWidget(w)
        w.deleteLater()

    def _add_pet_stat(self, data: dict = None):
        row = PetStatRow(data, self)
        row.removed = self._remove_pet_stat
        self.pet_container.addWidget(row)
        self.pet_stat_rows.append(row)

    def _remove_pet_stat(self, row: PetStatRow):
        self.pet_stat_rows.remove(row)
        self.pet_container.removeWidget(row)
        row.deleteLater()

    def _save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please give this loadout a name.")
            return

        data = {
            'name':      name,
            'school':    self.school_combo.currentData() or 'Universal',
            'level_min': self.lvl_min.value(),
            'level_max': self.lvl_max.value(),
            'world':     self.world_input.text().strip(),
            'category':  self.cat_tag_editor.get_category_string(),
            'notes':     self.notes_input.text().strip(),
            'slots':     [w.get_data() for w in self.slot_widgets],
            'pet_stats': [r.get_data() for r in self.pet_stat_rows],
        }
        if self.loadout_id:
            data['id'] = self.loadout_id

        dg.upsert_loadout(self.conn, data)
        self.saved.emit()

    def _export(self):
        """Export this loadout via the top-bar button."""
        if _EXPORTER_AVAILABLE and self.loadout_id:
            _exp.export_gear_loadout(self.conn, self.loadout_id, self)

    def _delete(self):
        name = self.name_input.text().strip() or "this loadout"
        from PyQt5.QtWidgets import QMessageBox as _MB
        box = _MB(self); box.setWindowTitle("Delete Loadout")
        box.setText(f"Delete <b>{name}</b>?")
        box.setInformativeText("All gear slots, options and pet stats will be removed.")
        box.setStandardButtons(_MB.Yes | _MB.No); box.setDefaultButton(_MB.No)
        box.setIcon(_MB.NoIcon)
        if box.exec_() == _MB.Yes:
            dg.delete_loadout(self.conn, self.loadout_id)
            self.saved.emit()


# ═══════════════════════════════════════════════════════════════
# GEAR GUIDE — TOP-LEVEL WIDGET (stacked browse + editor)
# ═══════════════════════════════════════════════════════════════

class GearGuideWidget(QWidget):
    """Drop-in widget for the hub. Manages browse ↔ editor navigation."""
    nav_hub = pyqtSignal()   # emitted when user clicks ← Hub

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setStyleSheet(GEAR_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.browse = GearBrowsePanel(conn, self)
        self.browse.open_loadout.connect(self._open_editor)
        self.browse.create_new.connect(lambda: self._open_editor(None))
        self.browse.go_hub.connect(self.nav_hub)
        self.stack.addWidget(self.browse)   # index 0

        self.stack.setCurrentIndex(0)

    def _open_editor(self, loadout_id):
        # Remove any old editor at index 1
        if self.stack.count() > 1:
            old = self.stack.widget(1)
            self.stack.removeWidget(old)
            old.deleteLater()

        editor = GearEditorPanel(self.conn, loadout_id, self)
        editor.saved.connect(self._back_to_browse)
        editor.cancelled.connect(self._back_to_browse)
        self.stack.addWidget(editor)        # index 1
        self.stack.setCurrentIndex(1)

    def _back_to_browse(self):
        self.stack.setCurrentIndex(0)
        self.browse.refresh()
        # Remove editor
        if self.stack.count() > 1:
            old = self.stack.widget(1)
            self.stack.removeWidget(old)
            old.deleteLater()
