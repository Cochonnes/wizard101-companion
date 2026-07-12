"""
Wizard101 Companion - Local Edition
════════════════════════════════════
* Tree view: World → Area → Boss
* Fetch single / Fetch all via db_builder.py subprocess
* Remove boss button
* Search + OCR toggle
* Quest Tracker (separate window)
"""

import sys
import os
import json
import logging
import time
from typing import Dict, List, Optional
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QTabWidget, QTextEdit, QTextBrowser,
    QCheckBox, QProgressBar, QTreeWidget, QTreeWidgetItem,
    QSplitter, QGroupBox, QMessageBox, QStatusBar,
    QCompleter, QComboBox, QDialog, QScrollArea, QFrame,
    QListWidget, QListWidgetItem, QSpinBox, QInputDialog,
    QSizePolicy, QTableWidget, QTableWidgetItem, QHeaderView,
    QStackedWidget, QGridLayout, QAbstractItemView
)
from PyQt5.QtCore import Qt, QProcess, QTimer, QStringListModel, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QBrush, QPixmap, QIcon

# ═══════════════════════════════════════════════════════════════
# FIRST-RUN TEMPLATE SEEDING  (must run BEFORE database imports
# because importing database.py creates an empty boss_wiki.db)
# ═══════════════════════════════════════════════════════════════

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATE_DIR = os.path.join(_APP_DIR, "templates")
_SEED_FILES = [
    "boss_wiki.db",
    "hud_settings.json",
    "keybinds.json",
    "world_order.json",
]

def _seed_from_templates():
    """Copy template files into the app directory if the real ones don't exist."""
    if not os.path.isdir(_TEMPLATE_DIR):
        return
    import shutil
    for filename in _SEED_FILES:
        dest = os.path.join(_APP_DIR, filename)
        src = os.path.join(_TEMPLATE_DIR, filename)
        if not os.path.exists(dest) and os.path.isfile(src):
            try:
                shutil.copy2(src, dest)
                print(f"  [SEED] Copied templates/{filename} → {filename}")
            except Exception as e:
                print(f"  [SEED] Could not copy {filename}: {e}")

_seed_from_templates()

# ── Project imports (these may create files if they don't exist) ──
import database as db
import database_quests as dq
import database_gear as dg

# Exporter
try:
    import exporter as exp
    EXPORTER_AVAILABLE = True
except ImportError:
    EXPORTER_AVAILABLE = False
    exp = None

# Keybind Manager
try:
    from keybind_manager import KeybindManager, KeybindSettingsWidget
    KEYBINDS_AVAILABLE = True
except ImportError:
    KEYBINDS_AVAILABLE = False
    KeybindManager = None
    KeybindSettingsWidget = None

# HUD Overlays
try:
    from hud_overlays import OverlayManager, overlay_settings
    HUD_AVAILABLE = True
except ImportError:
    HUD_AVAILABLE = False
    OverlayManager = None
    overlay_settings = None

# Cloudflare-challenge alert (shared marker + GUI sound player)
try:
    import cf_alert
except Exception:
    cf_alert = None

# Optional Quest Tracker
try:
    from quest_window import QuestTrackerWindow
    QUEST_TRACKER_AVAILABLE = True
except ImportError:
    QUEST_TRACKER_AVAILABLE = False
    QuestTrackerWindow = None

# Gear Guide
try:
    from gear_guide import GearGuideWidget
    GEAR_GUIDE_AVAILABLE = True
except ImportError:
    GEAR_GUIDE_AVAILABLE = False
    GearGuideWidget = None

# Spell Browser
try:
    from spell_browser import SpellBrowserWidget
    SPELL_BROWSER_AVAILABLE = True
except ImportError:
    SPELL_BROWSER_AVAILABLE = False
    SpellBrowserWidget = None

# Deck Builder
try:
    from deck_builder import DeckBuilderWidget
    DECK_BUILDER_AVAILABLE = True
except ImportError:
    DECK_BUILDER_AVAILABLE = False
    DeckBuilderWidget = None

# Damage Calculator + Characters
try:
    from damage_calc import DamageCalcWidget, CharacterManagerWidget
    import database_calc as dccalc
    DAMAGE_CALC_AVAILABLE = True
except ImportError:
    DAMAGE_CALC_AVAILABLE = False
    DamageCalcWidget = None
    CharacterManagerWidget = None
    dccalc = None

# Optional OCR
try:
    from ocr_module import OCRScanner, OCR_AVAILABLE, OCR_MODE_DYNAMIC, OCR_MODE_STRICT
except ImportError:
    OCR_AVAILABLE = False
    OCRScanner = None
    OCR_MODE_DYNAMIC = "dynamic"
    OCR_MODE_STRICT = "strict"
except OSError as _ocr_err:
    print(f"[WARN] OCR disabled — DLL load error: {_ocr_err}")
    OCR_AVAILABLE = False
    OCRScanner = None
    OCR_MODE_DYNAMIC = "dynamic"
    OCR_MODE_STRICT = "strict"
except Exception as _ocr_err:
    print(f"[WARN] OCR disabled — unexpected error: {_ocr_err}")
    OCR_AVAILABLE = False
    OCRScanner = None
    OCR_MODE_DYNAMIC = "dynamic"
    OCR_MODE_STRICT = "strict"

# Global UI scaling (main window + boxes + text + HUD overlays)
try:
    import ui_scale
    UI_SCALE_AVAILABLE = True
except Exception as _uiscale_err:
    print(f"[WARN] UI scaling disabled: {_uiscale_err}")
    UI_SCALE_AVAILABLE = False
    ui_scale = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('boss_wiki.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── App Version & GitHub ──────────────────────────────────────
APP_VERSION = "2.1.0"
# Set this to your GitHub repo, e.g. "YourUsername/Wizard101Companion"
GITHUB_REPO = "Cochonnes/wizard101-companion"


# [hub_settings_patcher v1]
# ── Hub order / visibility / favorites persistence ───────────────────
_HUB_ORDER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "hub_order.json")

_HUB_DEFAULTS = [
    {"id": "boss_wiki",      "icon": "👾",  "title": "Boss Wiki",
     "desc": "Browse bosses, cheats, spells and drops by world",
     "title_color": "#e94560", "action_key": "boss_wiki",
     "favorite": False, "hidden": False},
    {"id": "gear_guide",     "icon": "🎒",  "title": "Gear Guide",
     "desc": "Build and browse gear loadouts for every school and level range",
     "title_color": "#4db8ff", "action_key": "gear_guide",
     "favorite": False, "hidden": False},
    {"id": "damage_calc",    "icon": "🧮",  "title": "Damage Calculator",
     "desc": "Quick damage maths with blades, traps, enchants and boss resists",
     "title_color": "#6bcb77", "action_key": "damage_calc",
     "favorite": False, "hidden": False},
    {"id": "characters",     "icon": "🧙",  "title": "Characters",
     "desc": "Create and manage your wizards and their stats for the calculator",
     "title_color": "#ffd93d", "action_key": "characters",
     "favorite": False, "hidden": False},
    {"id": "quest_tracker",  "icon": "🗺",  "title": "Quest Tracker",
     "desc": "Track active quests and monitor objectives",
     "title_color": "#4d96ff", "action_key": "quest_tracker",
     "favorite": False, "hidden": False},
    {"id": "round_counters", "icon": "⏱",  "title": "Round Counters",
     "desc": "Manage cheat-tracking round counters linked to bosses",
     "title_color": "#ffd93d", "action_key": "boss_wiki_counters",
     "favorite": False, "hidden": False},
    {"id": "strategy_guides","icon": "📖",  "title": "Strategy Guides",
     "desc": "Create and browse boss-linked strategy guides with tables",
     "title_color": "#c39bd3", "action_key": "boss_wiki_guides",
     "favorite": False, "hidden": False},
    {"id": "spell_browser",  "icon": "✨",  "title": "Spell Browser",
     "desc": "Browse, search and fetch spell cards by school with OCR data",
     "title_color": "#c39bd3", "action_key": "spells",
     "favorite": False, "hidden": False},
    {"id": "deck_builder",   "icon": "🃏",  "title": "Deck Builder",
     "desc": "Create, edit and share deck presets with base64 codes",
     "title_color": "#6bcb77", "action_key": "deck_builder",
     "favorite": False, "hidden": False},
    {"id": "hud_settings",   "icon": "⚙",   "title": "HUD & Settings",
     "desc": "Configure overlay windows, icon presets, world order and display preferences",
     "title_color": "#aaaaaa", "action_key": "settings",
     "favorite": False, "hidden": False},
]


def _load_hub_order() -> list:
    """Load hub button order from hub_order.json, merging with defaults."""
    saved = []
    if os.path.exists(_HUB_ORDER_FILE):
        try:
            with open(_HUB_ORDER_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = []

    defaults_by_id = {d["id"]: d for d in _HUB_DEFAULTS}
    if not saved:
        return list(_HUB_DEFAULTS)

    # Merge: preserve saved order/favorite/hidden, add any new defaults
    saved_ids = {s["id"] for s in saved}
    result = []
    for s in saved:
        if s["id"] in defaults_by_id:
            merged = dict(defaults_by_id[s["id"]])
            merged["favorite"] = s.get("favorite", False)
            merged["hidden"]   = s.get("hidden", False)
            result.append(merged)
    for d in _HUB_DEFAULTS:
        if d["id"] not in saved_ids:
            result.append(dict(d))
    return result


def _save_hub_order(order: list):
    """Persist hub order to hub_order.json."""
    payload = [{"id": b["id"], "favorite": b.get("favorite", False),
                "hidden": b.get("hidden", False)} for b in order]
    with open(_HUB_ORDER_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)



def confirm_delete(parent, title: str, item_name: str, extra_detail: str = "") -> bool:
    """Single-step delete confirmation. No system sounds."""
    from PyQt5.QtWidgets import QMessageBox as _MB
    box = _MB(parent)
    box.setWindowTitle(title)
    box.setText(f"Delete <b>{item_name}</b>?")
    if extra_detail:
        box.setInformativeText(extra_detail)
    box.setStandardButtons(_MB.Yes | _MB.No)
    box.setDefaultButton(_MB.No)
    box.setIcon(_MB.NoIcon)
    return box.exec_() == _MB.Yes


DB_BUILDER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db_builder.py')
_WORLD_ORDER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'world_order.json')

# Canonical fallback (used when world_order.json does not exist yet)
_WORLD_ORDER_DEFAULT = [
    'Wizard City', 'Krokotopia', 'Grizzleheim', 'Marleybone', 'MooShu',
    'Dragonspyre', 'Celestia', 'Zafaria', 'Wysteria', 'Avalon', 'Azteca',
    'Aquila', 'Khrysalis', 'Polaris', 'Arcanum', 'Mirage', 'Empyrea',
    'Karamelle', 'Lemuria', 'Novus', 'Wallaru', 'Selenopolis', 'Darkmoor',
]


def get_world_order() -> list:
    """Return the current world order list. Reads world_order.json if present."""
    if os.path.exists(_WORLD_ORDER_FILE):
        try:
            with open(_WORLD_ORDER_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return list(_WORLD_ORDER_DEFAULT)


def save_world_order(order: list):
    """Persist the world order list to world_order.json."""
    with open(_WORLD_ORDER_FILE, 'w', encoding='utf-8') as f:
        json.dump(order, f, ensure_ascii=False, indent=2)


# Runtime reference — updated whenever the manager saves
WORLD_ORDER = get_world_order()


# ═══════════════════════════════════════════════════════════════
# DARK THEME
# ═══════════════════════════════════════════════════════════════

DARK_STYLE = """
    QMainWindow, QWidget {
        background-color: #1a1a2e;
        color: #e0e0e0;
        font-family: 'Segoe UI', Tahoma, sans-serif;
    }
    QLineEdit, QComboBox {
        background-color: #16213e;
        color: #e0e0e0;
        border: 2px solid #0f3460;
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
    }
    QLineEdit:focus { border-color: #e94560; }
    QPushButton {
        background-color: #0f3460;
        color: #e0e0e0;
        border: none;
        border-radius: 6px;
        padding: 7px 16px;
        font-size: 12px;
        font-weight: bold;
    }
    QPushButton:hover { background-color: #e94560; }
    QPushButton:pressed { background-color: #c81e45; }
    QPushButton:disabled { background-color: #2a2a3e; color: #666; }
    QPushButton#dangerBtn { background-color: #6b1a2e; }
    QPushButton#dangerBtn:hover { background-color: #e94560; }
    QPushButton#fetchAllBtn { background-color: #1b6b3a; }
    QPushButton#fetchAllBtn:hover { background-color: #27ae60; }
    QTabWidget::pane {
        background-color: #16213e;
        border: 1px solid #0f3460;
        border-radius: 4px;
    }
    QTabBar::tab {
        background-color: #1a1a2e; color: #999;
        border: 1px solid #0f3460;
        padding: 8px 14px; margin-right: 2px;
        border-top-left-radius: 6px; border-top-right-radius: 6px;
    }
    QTabBar::tab:selected { background-color: #16213e; color: #e94560; border-bottom-color: #16213e; }
    QTextEdit {
        background-color: #0d1b2a; color: #e0e0e0;
        border: none; font-size: 13px; padding: 10px;
    }
    QTreeWidget {
        background-color: #16213e; color: #e0e0e0;
        border: 1px solid #0f3460; border-radius: 4px;
        font-size: 13px;
    }
    QTreeWidget::item { padding: 3px 6px; }
    QTreeWidget::item:selected { background-color: #e94560; color: white; }
    QTreeWidget::item:hover { background-color: #0f3460; }
    QTreeWidget::branch { background-color: #16213e; }
    QProgressBar {
        background-color: #16213e; border: 1px solid #0f3460;
        border-radius: 4px; text-align: center; color: #e0e0e0;
    }
    QProgressBar::chunk { background-color: #e94560; border-radius: 3px; }
    QCheckBox { color: #e0e0e0; spacing: 8px; font-size: 13px; }
    QCheckBox::indicator { width: 18px; height: 18px; border: 2px solid #0f3460; border-radius: 4px; background-color: #16213e; }
    QCheckBox::indicator:checked { background-color: #e94560; border-color: #e94560; }
    QGroupBox {
        border: 1px solid #0f3460; border-radius: 6px;
        margin-top: 12px; padding-top: 16px;
        font-weight: bold; color: #e94560;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
    QStatusBar { background-color: #0d1b2a; color: #999; font-size: 12px; }
    QComboBox QAbstractItemView { background-color: #16213e; color: #e0e0e0; selection-background-color: #e94560; }
"""


# ═══════════════════════════════════════════════════════════════
# ROUND COUNTER — EDITOR DIALOG
# ═══════════════════════════════════════════════════════════════

ROUND_EDITOR_STYLE = """
    QDialog {
        background-color: #12121f;
        color: #e0e0e0;
        font-family: 'Segoe UI', Tahoma, sans-serif;
    }
    QLabel { color: #e0e0e0; }
    QLabel#sectionLabel {
        color: #e94560;
        font-weight: bold;
        font-size: 11px;
        letter-spacing: 1px;
        text-transform: uppercase;
    }
    QLineEdit, QSpinBox {
        background-color: #1a1a2e;
        color: #e0e0e0;
        border: 2px solid #0f3460;
        border-radius: 5px;
        padding: 5px 8px;
        font-size: 13px;
    }
    QLineEdit:focus, QSpinBox:focus { border-color: #e94560; }
    QPushButton {
        background-color: #0f3460; color: #e0e0e0;
        border: none; border-radius: 5px;
        padding: 6px 14px; font-size: 12px; font-weight: bold;
    }
    QPushButton:hover { background-color: #e94560; }
    QPushButton#addBtn { background-color: #1b5c38; }
    QPushButton#addBtn:hover { background-color: #27ae60; }
    QPushButton#removeBtn { background-color: #5c1b1b; }
    QPushButton#removeBtn:hover { background-color: #e94560; }
    QListWidget {
        background-color: #1a1a2e; color: #e0e0e0;
        border: 1px solid #0f3460; border-radius: 4px;
        font-size: 12px;
    }
    QListWidget::item:selected { background-color: #e94560; }
    QListWidget::item:hover { background-color: #0f3460; }
    QScrollArea { border: none; background: transparent; }
    QFrame#roundRow {
        background-color: #1a1a2e;
        border: 1px solid #0f3460;
        border-radius: 6px;
    }
"""


class RoundRowWidget(QFrame):
    """Single row in the round editor: [Round #N] [Label input] [Remove button]"""
    removed = None  # set by editor

    def __init__(self, idx: int, label: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("roundRow")
        self.idx = idx
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        self.num_label = QLabel(f"<b style='color:#e94560'>Round {idx}</b>")
        self.num_label.setFixedWidth(64)
        layout.addWidget(self.num_label)

        self.label_input = QLineEdit(label)
        self.label_input.setPlaceholderText("Describe what happens this round...")
        layout.addWidget(self.label_input, stretch=1)

        remove_btn = QPushButton("🗑")
        remove_btn.setObjectName("removeBtn")
        remove_btn.setFixedSize(28, 28)
        remove_btn.setToolTip("Remove this round")
        remove_btn.clicked.connect(lambda: self._confirm_remove())
        layout.addWidget(remove_btn)

    def _confirm_remove(self):
        label = self.label_input.text().strip() or f"Round {self.idx}"
        if confirm_delete(self, "Remove Round", label, "This round will be removed from the counter."):
            self.removed(self)

    def get_label(self) -> str:
        return self.label_input.text().strip()

    def set_index(self, idx: int):
        self.idx = idx
        self.num_label.setText(f"<b style='color:#e94560'>Round {idx}</b>")


class RoundCounterEditor(QDialog):
    """Dialog to create / edit a round counter."""

    def __init__(self, conn, boss_names: list, existing: dict = None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.boss_names = boss_names
        self.existing = existing or {}
        self.row_widgets: list[RoundRowWidget] = []

        self.setWindowTitle("Round Counter Editor" if not existing else f"Edit: {existing.get('name', '')}")
        self.setMinimumSize(600, 580)
        self.resize(680, 640)
        self.setStyleSheet(ROUND_EDITOR_STYLE)

        self._build_ui()
        if existing:
            self._load_existing()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(10)

        # ── Name ──
        name_lbl = QLabel("COUNTER NAME")
        name_lbl.setObjectName("sectionLabel")
        main_layout.addWidget(name_lbl)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Malistaire Cheat Rotation")
        main_layout.addWidget(self.name_input)

        # ── Description ──
        desc_lbl = QLabel("DESCRIPTION (optional)")
        desc_lbl.setObjectName("sectionLabel")
        main_layout.addWidget(desc_lbl)
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("Notes about this counter...")
        main_layout.addWidget(self.desc_input)

        # ── Rounds ──
        rounds_header = QHBoxLayout()
        rounds_lbl = QLabel("ROUNDS")
        rounds_lbl.setObjectName("sectionLabel")
        rounds_header.addWidget(rounds_lbl)
        rounds_header.addStretch()
        add_round_btn = QPushButton("＋ Add Round")
        add_round_btn.setObjectName("addBtn")
        add_round_btn.clicked.connect(self._add_round)
        rounds_header.addWidget(add_round_btn)
        main_layout.addLayout(rounds_header)

        # Scrollable round rows
        self.rounds_scroll = QScrollArea()
        self.rounds_scroll.setWidgetResizable(True)
        self.rounds_scroll.setMinimumHeight(200)
        self.rounds_container = QWidget()
        self.rounds_layout = QVBoxLayout(self.rounds_container)
        self.rounds_layout.setContentsMargins(0, 0, 0, 0)
        self.rounds_layout.setSpacing(4)
        self.rounds_layout.addStretch()
        self.rounds_scroll.setWidget(self.rounds_container)
        main_layout.addWidget(self.rounds_scroll, stretch=1)

        # ── Linked Bosses ──
        bosses_header = QHBoxLayout()
        bosses_lbl = QLabel("LINKED BOSSES")
        bosses_lbl.setObjectName("sectionLabel")
        bosses_header.addWidget(bosses_lbl)
        bosses_header.addStretch()
        main_layout.addLayout(bosses_header)

        boss_input_row = QHBoxLayout()
        self.boss_search = QLineEdit()
        self.boss_search.setPlaceholderText("Type boss name and press Add...")
        completer = QCompleter(self.boss_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.boss_search.setCompleter(completer)
        boss_input_row.addWidget(self.boss_search, stretch=1)
        add_boss_btn = QPushButton("＋ Link Boss")
        add_boss_btn.setObjectName("addBtn")
        add_boss_btn.clicked.connect(self._link_boss)
        self.boss_search.returnPressed.connect(self._link_boss)
        boss_input_row.addWidget(add_boss_btn)
        main_layout.addLayout(boss_input_row)

        self.linked_list = QListWidget()
        self.linked_list.setMaximumHeight(100)
        main_layout.addWidget(self.linked_list)

        unlink_btn = QPushButton("✕ Unlink Selected")
        unlink_btn.setObjectName("removeBtn")
        unlink_btn.clicked.connect(self._unlink_boss)
        main_layout.addWidget(unlink_btn)

        # ── Save / Cancel ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾 Save Counter")
        save_btn.setStyleSheet("background:#1b5c38;padding:7px 20px;")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        main_layout.addLayout(btn_row)

    def _load_existing(self):
        self.name_input.setText(self.existing.get('name', ''))
        self.desc_input.setText(self.existing.get('description', ''))
        for r in self.existing.get('rounds', []):
            self._add_round(r.get('label', ''))
        for boss in self.existing.get('linked_bosses', []):
            self.linked_list.addItem(boss)

    def _add_round(self, label: str = ""):
        if isinstance(label, bool):  # called from button click signal
            label = ""
        idx = len(self.row_widgets) + 1
        row = RoundRowWidget(idx, label, self.rounds_container)
        row.removed = self._remove_round
        # Insert before the stretch
        self.rounds_layout.insertWidget(self.rounds_layout.count() - 1, row)
        self.row_widgets.append(row)

    def _remove_round(self, row_widget: RoundRowWidget):
        self.row_widgets.remove(row_widget)
        self.rounds_layout.removeWidget(row_widget)
        row_widget.deleteLater()
        # Renumber
        for i, rw in enumerate(self.row_widgets, 1):
            rw.set_index(i)

    def _link_boss(self):
        name = self.boss_search.text().strip()
        if not name:
            return
        existing_items = [self.linked_list.item(i).text() for i in range(self.linked_list.count())]
        if name not in existing_items:
            self.linked_list.addItem(name)
        self.boss_search.clear()

    def _unlink_boss(self):
        for item in self.linked_list.selectedItems():
            self.linked_list.takeItem(self.linked_list.row(item))

    def _save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please give this counter a name.")
            return
        if not self.row_widgets:
            QMessageBox.warning(self, "No Rounds", "Add at least one round.")
            return

        rounds = [{'label': rw.get_label()} for rw in self.row_widgets]
        linked = [self.linked_list.item(i).text() for i in range(self.linked_list.count())]

        data = {
            'name': name,
            'description': self.desc_input.text().strip(),
            'rounds': rounds,
            'linked_bosses': linked,
        }
        if self.existing.get('id'):
            data['id'] = self.existing['id']

        db.upsert_round_counter(self.conn, data)
        self.accept()


# ═══════════════════════════════════════════════════════════════
# ROUND COUNTER — LIVE WIDGET (used in the panel & boss tab)
# ═══════════════════════════════════════════════════════════════

class RoundCounterWidget(QFrame):
    """
    Compact live-play widget for one round counter.
    Shows rounds as ticking rows. Buttons: tick/untick, reset ticks, full reset.
    """

    def __init__(self, counter: dict, compact: bool = False, parent=None):
        super().__init__(parent)
        self.counter = counter
        self.compact = compact
        self.current_round = 0  # 0-indexed
        self.setObjectName("rcWidget")
        self.setStyleSheet("""
            QFrame#rcWidget {
                background-color: #111827;
                border: 1px solid #1f3a6e;
                border-radius: 8px;
            }
        """)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        # Header row
        hdr = QHBoxLayout()
        name_lbl = QLabel(f"⚔ {self.counter['name']}")
        name_lbl.setStyleSheet("color:#e94560;font-weight:bold;font-size:13px;")
        hdr.addWidget(name_lbl)
        hdr.addStretch()

        if self.counter.get('linked_bosses'):
            tag = QLabel("  ".join(f"👾 {b}" for b in self.counter['linked_bosses'][:2]))
            tag.setStyleSheet("color:#4d96ff;font-size:10px;")
            hdr.addWidget(tag)
        layout.addLayout(hdr)

        if self.counter.get('description'):
            desc = QLabel(self.counter['description'])
            desc.setStyleSheet("color:#888;font-size:11px;")
            desc.setWordWrap(True)
            layout.addWidget(desc)

        # Round rows
        rounds = self.counter.get('rounds', [])
        self.round_labels = []
        self.round_frames = []

        for i, r in enumerate(rounds):
            row_frame = QFrame()
            row_frame.setStyleSheet("""
                QFrame { background: #0d1b2a; border-radius: 4px; }
            """)
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 4, 8, 4)
            row_layout.setSpacing(6)

            num = QLabel(f"<b style='color:#555'>R{i+1}</b>")
            num.setFixedWidth(28)
            row_layout.addWidget(num)

            lbl_text = r.get('label', '') or f"Round {i+1}"
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet("color:#c0c0c0;font-size:12px;")
            lbl.setWordWrap(True)
            row_layout.addWidget(lbl, stretch=1)

            tick_btn = QPushButton("○")
            tick_btn.setFixedSize(26, 26)
            tick_btn.setCheckable(True)
            tick_btn.setStyleSheet("""
                QPushButton { background:#1a1a2e; color:#666; border:1px solid #333;
                              border-radius:13px; font-size:14px; font-weight:bold; }
                QPushButton:checked { background:#e94560; color:white; border-color:#e94560; }
                QPushButton:hover { border-color:#e94560; }
            """)
            tick_btn.toggled.connect(lambda checked, idx=i: self._on_tick(idx, checked))
            row_layout.addWidget(tick_btn)

            layout.addWidget(row_frame)
            self.round_labels.append((num, lbl, tick_btn))
            self.round_frames.append(row_frame)

        # Control buttons
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        reset_ticks_btn = QPushButton("↺ Reset Ticks")
        reset_ticks_btn.setStyleSheet("""
            QPushButton { background:#1a2a3a; color:#4d96ff; border:1px solid #1f3a6e;
                          border-radius:5px; padding:4px 10px; font-size:11px; }
            QPushButton:hover { background:#1f3a6e; }
        """)
        reset_ticks_btn.clicked.connect(self._reset_ticks)
        ctrl.addWidget(reset_ticks_btn)

        full_reset_btn = QPushButton("⚡ Full Reset")
        full_reset_btn.setStyleSheet("""
            QPushButton { background:#1a2a3a; color:#ffd93d; border:1px solid #3a3010;
                          border-radius:5px; padding:4px 10px; font-size:11px; }
            QPushButton:hover { background:#3a3010; }
        """)
        full_reset_btn.clicked.connect(self._full_reset)
        ctrl.addWidget(full_reset_btn)

        ctrl.addStretch()
        layout.addLayout(ctrl)

        self._update_highlight()

    def _on_tick(self, idx: int, checked: bool):
        if checked:
            # Auto-advance current round to next after this one
            self.current_round = min(idx + 1, len(self.round_labels) - 1)
        self._update_highlight()

    def _reset_ticks(self):
        for num, lbl, btn in self.round_labels:
            btn.setChecked(False)
        self.current_round = 0
        self._update_highlight()

    def _full_reset(self):
        self._reset_ticks()

    def _update_highlight(self):
        for i, (num, lbl, btn) in enumerate(self.round_labels):
            if i == self.current_round and not btn.isChecked():
                self.round_frames[i].setStyleSheet(
                    "QFrame { background:#0f2b1a; border-radius:4px; border-left:3px solid #27ae60; }"
                )
                num.setText(f"<b style='color:#27ae60'>R{i+1}</b>")
            elif btn.isChecked():
                self.round_frames[i].setStyleSheet(
                    "QFrame { background:#080d12; border-radius:4px; }"
                )
                num.setText(f"<b style='color:#333'>R{i+1}</b>")
            else:
                self.round_frames[i].setStyleSheet(
                    "QFrame { background:#0d1b2a; border-radius:4px; }"
                )
                num.setText(f"<b style='color:#555'>R{i+1}</b>")


# ═══════════════════════════════════════════════════════════════
# ROUND COUNTER MANAGER PANEL (full side panel)
# ═══════════════════════════════════════════════════════════════

class RoundCounterPanel(QWidget):
    """
    Side panel showing all round counters with management controls.
    Embedded as its own tab in the main window.
    """

    def __init__(self, conn, boss_names: list, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.boss_names = boss_names
        self.hud_toggle_callback = None  # set by BossWikiApp after construction
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header + New button
        hdr = QHBoxLayout()
        title = QLabel("⏱ Round Counters")
        title.setStyleSheet("color:#e94560;font-size:15px;font-weight:bold;")
        hdr.addWidget(title)
        hdr.addStretch()

        # HUD overlay toggle
        self._hud_btn = QPushButton("🖥 HUD")
        self._hud_btn.setToolTip("Toggle Round Counter HUD overlay")
        self._hud_btn.setCheckable(True)
        self._hud_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#aaa;border:1px solid #1f3460;"
            "border-radius:5px;padding:4px 10px;font-size:11px;}"
            "QPushButton:checked{background:#ffd93d;color:#1a1a2e;border-color:#ffd93d;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        self._hud_btn.toggled.connect(self._on_hud_toggled)
        hdr.addWidget(self._hud_btn)

        imp_btn = QPushButton("📥 Import Code")
        imp_btn.setToolTip("Create a counter from a base64 share code")
        imp_btn.setStyleSheet("background:#0f3460;color:#e0e0e0;border:none;border-radius:5px;"
                              "padding:6px 14px;font-weight:bold;")
        imp_btn.clicked.connect(self._import_counter_code)
        hdr.addWidget(imp_btn)

        new_btn = QPushButton("＋ New Counter")
        new_btn.setStyleSheet("background:#1b5c38;color:#e0e0e0;border:none;border-radius:5px;"
                              "padding:6px 14px;font-weight:bold;")
        new_btn.clicked.connect(self._new_counter)
        hdr.addWidget(new_btn)
        layout.addLayout(hdr)

        hint = QLabel("Create counters to track round-based cheats. Link them to bosses to see them automatically.")
        hint.setStyleSheet("color:#666;font-size:11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Scroll area holding counter cards
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.scroll_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(10)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll, stretch=1)

        self.refresh()

    def refresh(self):
        """Reload all counter cards from DB."""
        # Clear existing cards (preserve stretch)
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        counters = db.list_round_counters(self.conn)

        if not counters:
            empty = QLabel("No round counters yet.\nClick '＋ New Counter' to create one.")
            empty.setStyleSheet("color:#555;font-size:13px;")
            empty.setAlignment(Qt.AlignCenter)
            self.cards_layout.insertWidget(0, empty)
            return

        for counter in counters:
            card = self._build_counter_card(counter)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _build_counter_card(self, counter: dict) -> QWidget:
        """Build a compact management card: name, linked bosses, edit/delete only."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background: #16213e;
                border: 1px solid #0f3460;
                border-radius: 8px;
            }
        """)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        # Left: name + info
        info_col = QVBoxLayout()
        info_col.setSpacing(3)
        name = QLabel(f"<b style='color:#e94560'>{counter['name']}</b>")
        name.setStyleSheet("font-size:13px;")
        info_col.addWidget(name)

        info_parts = [f"📋 {len(counter.get('rounds', []))} rounds"]
        if counter.get('linked_bosses'):
            info_parts.append("👾 " + ", ".join(counter['linked_bosses']))
        info = QLabel("  ·  ".join(info_parts))
        info.setStyleSheet("color:#666;font-size:11px;")
        info.setWordWrap(True)
        info_col.addWidget(info)
        layout.addLayout(info_col, stretch=1)

        # Right: Share + Edit + Delete
        share_btn = QPushButton("🔗")
        share_btn.setStyleSheet("background:#0f3460;color:#e0e0e0;border:none;border-radius:4px;"
                                "padding:5px 10px;font-size:13px;")
        share_btn.setFixedWidth(36)
        share_btn.setToolTip(f"Share '{counter['name']}' as a code")
        share_btn.clicked.connect(
            lambda checked=False, c=counter: self._share_counter(c)
        )
        layout.addWidget(share_btn)

        edit_btn = QPushButton("✏ Edit")
        edit_btn.setStyleSheet("background:#0f3460;color:#e0e0e0;border:none;border-radius:4px;"
                               "padding:5px 12px;font-size:11px;font-weight:bold;")
        edit_btn.setFixedWidth(70)
        edit_btn.clicked.connect(lambda checked, c=counter: self._edit_counter(c))
        layout.addWidget(edit_btn)

        del_btn = QPushButton("🗑")
        del_btn.setStyleSheet("background:#5c1b1b;color:#e0e0e0;border:none;border-radius:4px;"
                              "padding:5px 10px;font-size:13px;")
        del_btn.setFixedWidth(36)
        del_btn.setToolTip(f"Delete '{counter['name']}'")
        del_btn.clicked.connect(lambda checked, c=counter: self._delete_counter(c))
        layout.addWidget(del_btn)

        return card

    def _new_counter(self):
        dlg = RoundCounterEditor(self.conn, self.boss_names, parent=self)
        dlg.setStyleSheet(ROUND_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def _on_hud_toggled(self, checked: bool):
        if self.hud_toggle_callback:
            self.hud_toggle_callback("counter", checked, self._hud_btn)

    def sync_hud_btn(self, checked: bool):
        """Called by main app to keep button in sync with settings page."""
        self._hud_btn.blockSignals(True)
        self._hud_btn.setChecked(checked)
        self._hud_btn.blockSignals(False)

    def _edit_counter(self, counter: dict):
        full = db.get_round_counter(self.conn, counter['id'])
        dlg = RoundCounterEditor(self.conn, self.boss_names, existing=full, parent=self)
        dlg.setStyleSheet(ROUND_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def _delete_counter(self, counter: dict):
        if confirm_delete(self, "Delete Counter", counter['name'],
                          "This will also unlink it from all bosses."):
            db.delete_round_counter(self.conn, counter['id'])
            self.refresh()

    def _share_counter(self, counter: dict):
        import share_codes
        full = db.get_round_counter(self.conn, counter['id'])
        if not full:
            return
        code = db.export_counter_code(full)
        share_codes.show_share_dialog(self, "Round Counter Share Code", code)

    def _import_counter_code(self):
        import share_codes, importer
        code = share_codes.prompt_import_code(self, "Import Round Counter")
        if not code:
            return
        data = db.import_counter_code(code)
        if not data:
            QMessageBox.warning(self, "Invalid Code",
                                "Could not decode that code. Check you copied it correctly.")
            return
        importer._import_counter(self.conn, data)
        self.conn.commit()
        self.refresh()
        QMessageBox.information(self, "Counter Imported",
                                f"Imported counter '{data.get('name','')}'.")

    def update_boss_names(self, boss_names: list):
        self.boss_names = boss_names


# ═══════════════════════════════════════════════════════════════
# W101 SCHOOLS (for guide column picker)
# ═══════════════════════════════════════════════════════════════

W101_SCHOOLS = ['Fire', 'Ice', 'Storm', 'Myth', 'Life', 'Death', 'Balance']

SCHOOL_COLORS = {
    'Fire':    '#e85d04',
    'Ice':     '#48cae4',
    'Storm':   '#9b5de5',
    'Myth':    '#f4a261',
    'Life':    '#57cc99',
    'Death':   '#9d4edd',
    'Balance': '#ffd166',
}

GUIDE_EDITOR_STYLE = """
    QDialog {
        background-color: #12121f;
        color: #e0e0e0;
        font-family: 'Segoe UI', Tahoma, sans-serif;
    }
    QLabel { color: #e0e0e0; }
    QLabel#sectionLabel {
        color: #4d96ff;
        font-weight: bold;
        font-size: 11px;
        letter-spacing: 1px;
    }
    QLineEdit, QTextEdit, QSpinBox {
        background-color: #1a1a2e;
        color: #e0e0e0;
        border: 2px solid #0f3460;
        border-radius: 5px;
        padding: 5px 8px;
        font-size: 13px;
    }
    QLineEdit:focus, QTextEdit:focus, QSpinBox:focus { border-color: #4d96ff; }
    QPushButton {
        background-color: #0f3460; color: #e0e0e0;
        border: none; border-radius: 5px;
        padding: 6px 14px; font-size: 12px; font-weight: bold;
    }
    QPushButton:hover { background-color: #4d96ff; color: #0a0a1a; }
    QPushButton#addBtn { background-color: #1b5c38; }
    QPushButton#addBtn:hover { background-color: #27ae60; }
    QPushButton#removeBtn { background-color: #5c1b1b; }
    QPushButton#removeBtn:hover { background-color: #e94560; }
    QPushButton#schoolBtn {
        border: 2px solid #1f3a6e; border-radius: 14px;
        padding: 3px 10px; font-size: 11px; font-weight: bold;
        background: #0f1b2a;
    }
    QPushButton#schoolBtn:checked { border-color: #4d96ff; background: #1a3060; }
    QListWidget {
        background-color: #1a1a2e; color: #e0e0e0;
        border: 1px solid #0f3460; border-radius: 4px;
        font-size: 12px;
    }
    QListWidget::item:selected { background-color: #4d96ff; color: #0a0a1a; }
    QScrollArea { border: none; background: transparent; }
    QTableWidget {
        background-color: #0d1b2a; color: #e0e0e0;
        gridline-color: #1f3a6e;
        border: 1px solid #1f3a6e;
        font-size: 12px;
    }
    QTableWidget::item { padding: 4px 6px; }
    QTableWidget::item:selected { background-color: #1a3060; }
    QHeaderView::section {
        background-color: #0f3460; color: #e0e0e0;
        padding: 5px; border: 1px solid #1f3a6e; font-size: 11px; font-weight: bold;
    }
"""


class SchoolDropdown(QPushButton):
    """
    Button that opens a multi-select school picker popup.
    Each column can have zero, one, or many schools selected.
    Displays all selected schools in their colors, or '— None —'.
    school_changed: callable() — called whenever selection changes.
    """
    school_changed = None  # set by owner: callable()

    def __init__(self, selected: list = None, parent=None):
        super().__init__(parent)
        self._selected: list = list(selected) if selected else []
        self._update_display()
        self.clicked.connect(self._open_popup)
        self.setFixedHeight(32)

    def _update_display(self):
        if self._selected:
            parts = []
            for s in self._selected:
                c = SCHOOL_COLORS.get(s, '#e0e0e0')
                parts.append(f"<span style='color:{c}'>{s}</span>")
            label = " + ".join(parts)
        else:
            label = "<span style='color:#555'>— None —</span>"
        self.setText(f"▾ {' + '.join(self._selected) if self._selected else '— None —'}")
        # Build a gradient border from all selected school colors
        border_color = SCHOOL_COLORS.get(self._selected[0], '#1f3a6e') if self._selected else '#1f3a6e'
        self.setStyleSheet(f"""
            QPushButton {{
                border: 1px solid {border_color}; border-radius: 4px;
                padding: 2px 6px; font-size: 10px; font-weight: bold;
                background: #0f1b2a; color: #e0e0e0; text-align: left;
            }}
            QPushButton:hover {{ border-color: #4d96ff; background: #1a2a3a; }}
        """)
        # Show colored text via tooltip as well
        tooltip = ", ".join(self._selected) if self._selected else "No schools selected"
        self.setToolTip(tooltip)

    def get_schools(self) -> list:
        return list(self._selected)

    def set_schools(self, schools: list):
        self._selected = list(schools)
        self._update_display()
        if self.school_changed:
            self.school_changed()

    def _open_popup(self):
        popup = QDialog(self, Qt.Popup | Qt.FramelessWindowHint)
        popup.setStyleSheet("""
            QDialog {
                background:#1a1a2e; border:1px solid #0f3460; border-radius:6px;
            }
            QCheckBox {
                color:#ccc; padding:5px 10px; font-size:12px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width:15px; height:15px;
                border:1px solid #0f3460; border-radius:3px; background:#0d1b2a;
            }
            QCheckBox::indicator:checked { background:#e94560; border-color:#e94560; }
            QCheckBox:hover { color:#fff; }
            QPushButton {
                background:#0f3460; color:#e0e0e0; border:none; border-radius:4px;
                padding:4px 12px; font-size:11px; font-weight:bold;
            }
            QPushButton:hover { background:#4d96ff; color:#0a0a1a; }
        """)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(2)

        header = QLabel("Select schools for this column:")
        header.setStyleSheet("color:#888;font-size:10px;padding:2px 4px;")
        layout.addWidget(header)

        checkboxes = {}
        for school in W101_SCHOOLS:
            color = SCHOOL_COLORS[school]
            cb = QCheckBox(school)
            cb.setChecked(school in self._selected)
            cb.setStyleSheet(f"""
                QCheckBox {{ color:{color}; padding:5px 10px; font-size:12px; spacing:8px; }}
                QCheckBox::indicator {{ width:15px; height:15px;
                    border:1px solid {color}; border-radius:3px; background:#0d1b2a; }}
                QCheckBox::indicator:checked {{ background:{color}; border-color:{color}; }}
            """)
            layout.addWidget(cb)
            checkboxes[school] = cb

        # Done button
        done_btn = QPushButton("✓ Done")
        done_btn.setStyleSheet("background:#1b5c38;color:#e0e0e0;margin-top:4px;")

        def _apply():
            new = [s for s in W101_SCHOOLS if checkboxes[s].isChecked()]
            self.set_schools(new)
            popup.close()

        done_btn.clicked.connect(_apply)
        layout.addWidget(done_btn)

        pos = self.mapToGlobal(self.rect().bottomLeft())
        popup.move(pos)
        popup.exec_()


class GuideCellEditor(QDialog):
    """Full-size text editor popup for a single table cell."""
    def __init__(self, current_text: str, row: int, col_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Round {row + 1} — {col_name}")
        self.setMinimumSize(400, 220)
        self.setStyleSheet("""
            QDialog { background:#12121f; color:#e0e0e0; font-family:'Segoe UI'; }
            QTextEdit { background:#1a1a2e; color:#e0e0e0; border:1px solid #0f3460;
                        border-radius:5px; font-size:13px; padding:8px; }
            QPushButton { background:#0f3460; color:#e0e0e0; border:none; border-radius:5px;
                          padding:6px 18px; font-weight:bold; }
            QPushButton:hover { background:#4d96ff; color:#0a0a1a; }
            QPushButton#save { background:#1b5c38; }
            QPushButton#save:hover { background:#27ae60; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        hint = QLabel(f"<b style='color:#4d96ff'>Round {row + 1}</b>  ·  <span style='color:#888'>{col_name}</span>")
        hint.setStyleSheet("font-size:12px;")
        layout.addWidget(hint)
        self.editor = QTextEdit(current_text)
        self.editor.setPlaceholderText("Enter spells, notes, strategy...")
        layout.addWidget(self.editor, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)
        save = QPushButton("✓ Apply"); save.setObjectName("save")
        save.clicked.connect(self.accept)
        btn_row.addWidget(save)
        layout.addLayout(btn_row)

    def get_text(self) -> str:
        return self.editor.toPlainText()


class GuideTable(QTableWidget):
    """
    QTableWidget with:
    - Always exactly 4 columns
    - Row 0: SchoolDropdown widgets (multi-select, one per column)
    - Data rows start from row 1, keyed by r_c{col}
    - Word-wrap, auto row height, double-click full editor
    """
    def __init__(self, num_rounds: int, schools_per_col: list, table_data: dict, parent=None):
        # schools_per_col: list of 4 lists e.g. [['Fire','Ice'], ['Storm'], [], ['Balance']]
        super().__init__(num_rounds + 1, 4, parent)
        self.num_rounds = num_rounds
        self.school_dropdowns: list = []
        self._setup(schools_per_col, table_data)
        self.cellDoubleClicked.connect(self._open_cell_editor)

    def _setup(self, schools_per_col: list, table_data: dict):
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setVisible(True)

        labels = ['Schools ↓'] + [f'Round {i+1}' for i in range(self.num_rounds)]
        self.setVerticalHeaderLabels(labels)

        # Row 0: multi-select school dropdowns
        self.school_dropdowns = []
        for c in range(4):
            col_schools = schools_per_col[c] if c < len(schools_per_col) else []
            if not isinstance(col_schools, list):
                col_schools = [col_schools] if col_schools else []
            dd = SchoolDropdown(col_schools, self)
            dd.school_changed = lambda col=c: self._on_school_changed(col)
            self.setCellWidget(0, c, dd)
            self.school_dropdowns.append(dd)

        # Data rows — key is "r_c{col}"
        for r in range(self.num_rounds):
            self.setRowHeight(r + 1, 60)
            for c in range(4):
                key = f"{r}_c{c}"
                text = table_data.get(key, '')
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() | Qt.ItemIsEditable)
                self.setItem(r + 1, c, item)

        self.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.setRowHeight(0, 36)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.ElideNone)

    def _on_school_changed(self, col: int):
        pass  # display only; data keys are column-indexed so nothing needs remapping

    def get_schools_per_col(self) -> list:
        """Returns list of 4 lists of school names."""
        return [dd.get_schools() for dd in self.school_dropdowns]

    def read_table_data(self) -> dict:
        data = {}
        for r in range(self.num_rounds):
            for c in range(4):
                item = self.item(r + 1, c)
                key = f"{r}_c{c}"
                data[key] = item.text() if item else ''
        return data

    def _col_label(self, c: int) -> str:
        schools = self.school_dropdowns[c].get_schools() if c < len(self.school_dropdowns) else []
        return " + ".join(schools) if schools else f"Column {c + 1}"

    def _open_cell_editor(self, row: int, col: int):
        if row == 0:
            return
        data_row = row - 1
        col_name = self._col_label(col)
        item = self.item(row, col)
        current = item.text() if item else ''
        dlg = GuideCellEditor(current, data_row, col_name, self)
        if dlg.exec_() == QDialog.Accepted:
            new_text = dlg.get_text()
            if not item:
                item = QTableWidgetItem()
                self.setItem(row, col, item)
            item.setText(new_text)
            self.resizeRowToContents(row)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for r in range(1, self.rowCount()):
            self.resizeRowToContents(r)
            if self.rowHeight(r) < 50:
                self.setRowHeight(r, 50)


class GuideEditor(QDialog):
    """Dialog to create / edit a strategy guide."""

    def __init__(self, conn, boss_names: list, existing: dict = None, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.boss_names = boss_names
        self.existing = existing or {}

        # schools is now a list of 4 lists
        raw = existing.get('schools', [['Fire'], ['Ice'], ['Storm'], ['Myth']]) if existing else [['Fire'], ['Ice'], ['Storm'], ['Myth']]
        # Migrate old format (flat list of strings) to list-of-lists
        if raw and not isinstance(raw[0], list):
            raw = [[s] if s else [] for s in raw]
        while len(raw) < 4:
            raw.append([])
        self._init_schools = [list(x) for x in raw[:4]]

        self.setWindowTitle("Guide Editor" if not existing else f"Edit Guide: {existing.get('name', '')}")
        self.setMinimumSize(700, 600)
        self.resize(820, 680)
        self.setStyleSheet(GUIDE_EDITOR_STYLE)
        self._build_ui()
        if existing:
            self._load_existing()

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(10)

        n_lbl = QLabel("GUIDE NAME"); n_lbl.setObjectName("sectionLabel")
        main.addWidget(n_lbl)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. Malistaire Strategy Guide")
        main.addWidget(self.name_input)

        ft_lbl = QLabel("FREE TEXT / NOTES"); ft_lbl.setObjectName("sectionLabel")
        main.addWidget(ft_lbl)
        self.free_text = QTextEdit()
        self.free_text.setPlaceholderText("General strategy notes, tips, links...")
        self.free_text.setMaximumHeight(90)
        main.addWidget(self.free_text)

        rounds_row = QHBoxLayout()
        r_lbl = QLabel("NUMBER OF ROUNDS:"); r_lbl.setObjectName("sectionLabel")
        rounds_row.addWidget(r_lbl)
        self.rounds_spin = QSpinBox()
        self.rounds_spin.setRange(1, 50)
        self.rounds_spin.setValue(self.existing.get('num_rounds', 3))
        self.rounds_spin.setFixedWidth(70)
        self.rounds_spin.valueChanged.connect(self._rebuild_table)
        rounds_row.addWidget(self.rounds_spin)
        hint_lbl = QLabel("  Click header dropdowns to pick schools. Double-click any cell for full editor.")
        hint_lbl.setStyleSheet("color:#555;font-size:11px;")
        rounds_row.addWidget(hint_lbl)
        rounds_row.addStretch()
        main.addLayout(rounds_row)

        tbl_lbl = QLabel("SPELL TABLE  (4 columns · multi-school per column)"); tbl_lbl.setObjectName("sectionLabel")
        main.addWidget(tbl_lbl)
        self.guide_table = GuideTable(
            self.rounds_spin.value(),
            self._init_schools,
            self.existing.get('table_data', {}),
        )
        self.guide_table.setMinimumHeight(180)
        main.addWidget(self.guide_table, stretch=1)

        b_lbl = QLabel("LINKED BOSSES"); b_lbl.setObjectName("sectionLabel")
        main.addWidget(b_lbl)
        boss_row = QHBoxLayout()
        self.boss_search = QLineEdit()
        self.boss_search.setPlaceholderText("Type boss name...")
        completer = QCompleter(self.boss_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.boss_search.setCompleter(completer)
        boss_row.addWidget(self.boss_search, stretch=1)
        add_b = QPushButton("＋ Link"); add_b.setObjectName("addBtn")
        add_b.clicked.connect(self._link_boss)
        self.boss_search.returnPressed.connect(self._link_boss)
        boss_row.addWidget(add_b)
        main.addLayout(boss_row)

        self.linked_list = QListWidget()
        self.linked_list.setMaximumHeight(70)
        main.addWidget(self.linked_list)

        unl = QPushButton("✕ Unlink Selected"); unl.setObjectName("removeBtn")
        unl.clicked.connect(lambda: [self.linked_list.takeItem(self.linked_list.row(i))
                                     for i in self.linked_list.selectedItems()])
        main.addWidget(unl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel"); cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("💾 Save Guide")
        save_btn.setStyleSheet("background:#1b5c38;padding:7px 20px;")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        main.addLayout(btn_row)

    def _rebuild_table(self):
        schools = self.guide_table.get_schools_per_col()
        old_data = self.guide_table.read_table_data()
        new_tbl = GuideTable(self.rounds_spin.value(), schools, old_data)
        new_tbl.setMinimumHeight(180)
        layout = self.layout()
        idx = layout.indexOf(self.guide_table)
        layout.removeWidget(self.guide_table)
        self.guide_table.deleteLater()
        self.guide_table = new_tbl
        layout.insertWidget(idx, self.guide_table, stretch=1)

    def _load_existing(self):
        self.name_input.setText(self.existing.get('name', ''))
        self.free_text.setPlainText(self.existing.get('free_text', ''))
        for boss in self.existing.get('linked_bosses', []):
            self.linked_list.addItem(boss)

    def _link_boss(self):
        name = self.boss_search.text().strip()
        if not name:
            return
        existing = [self.linked_list.item(i).text() for i in range(self.linked_list.count())]
        if name not in existing:
            self.linked_list.addItem(name)
        self.boss_search.clear()

    def _save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please give this guide a name.")
            return
        linked = [self.linked_list.item(i).text() for i in range(self.linked_list.count())]
        data = {
            'name': name,
            'free_text': self.free_text.toPlainText(),
            'schools': self.guide_table.get_schools_per_col(),
            'table_data': self.guide_table.read_table_data(),
            'num_rounds': self.rounds_spin.value(),
            'linked_bosses': linked,
        }
        if self.existing.get('id'):
            data['id'] = self.existing['id']
        db.upsert_guide(self.conn, data)
        self.accept()


# ═══════════════════════════════════════════════════════════════
# GUIDE PANEL (side panel, same style as RoundCounterPanel)
# ═══════════════════════════════════════════════════════════════

class GuidePanel(QWidget):
    def __init__(self, conn, boss_names: list, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.boss_names = boss_names
        self.hud_toggle_callback = None  # set by BossWikiApp after construction
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel("📖 Strategy Guides")
        title.setStyleSheet("color:#4d96ff;font-size:15px;font-weight:bold;")
        hdr.addWidget(title)
        hdr.addStretch()

        # HUD overlay toggle
        self._hud_btn = QPushButton("🖥 HUD")
        self._hud_btn.setToolTip("Toggle Strategy Guide HUD overlay")
        self._hud_btn.setCheckable(True)
        self._hud_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#aaa;border:1px solid #1f3460;"
            "border-radius:5px;padding:4px 10px;font-size:11px;}"
            "QPushButton:checked{background:#c39bd3;color:#1a1a2e;border-color:#c39bd3;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        self._hud_btn.toggled.connect(self._on_hud_toggled)
        hdr.addWidget(self._hud_btn)

        imp_btn = QPushButton("📥 Import Code")
        imp_btn.setToolTip("Create a guide from a base64 share code")
        imp_btn.setStyleSheet("background:#0f3460;color:#e0e0e0;border:none;border-radius:5px;"
                              "padding:6px 14px;font-weight:bold;")
        imp_btn.clicked.connect(self._import_guide_code)
        hdr.addWidget(imp_btn)

        new_btn = QPushButton("＋ New Guide")
        new_btn.setStyleSheet("background:#1b3a6e;color:#e0e0e0;border:none;border-radius:5px;"
                              "padding:6px 14px;font-weight:bold;")
        new_btn.clicked.connect(self._new_guide)
        hdr.addWidget(new_btn)
        layout.addLayout(hdr)

        hint = QLabel("Create strategy guides with notes and a school-based spell table. Link them to bosses.")
        hint.setStyleSheet("color:#666;font-size:11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.scroll_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.scroll.setWidget(self.scroll_widget)
        layout.addWidget(self.scroll, stretch=1)

        self.refresh()

    def refresh(self):
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        guides = db.list_guides(self.conn)
        if not guides:
            empty = QLabel("No guides yet.\nClick '＋ New Guide' to create one.")
            empty.setStyleSheet("color:#555;font-size:13px;")
            empty.setAlignment(Qt.AlignCenter)
            self.cards_layout.insertWidget(0, empty)
            return

        for guide in guides:
            card = self._build_card(guide)
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

    def _build_card(self, guide: dict) -> QWidget:
        card = QFrame()
        card.setStyleSheet("QFrame { background:#16213e; border:1px solid #1f3a6e; border-radius:8px; }")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        info_col = QVBoxLayout()
        info_col.setSpacing(3)
        name = QLabel(f"<b style='color:#4d96ff'>{guide['name']}</b>")
        name.setStyleSheet("font-size:13px;")
        info_col.addWidget(name)

        active_schools = []
        for col_schools in guide.get('schools', []):
            if isinstance(col_schools, list):
                active_schools.extend(col_schools)
            elif col_schools:
                active_schools.append(col_schools)
        active_schools = list(dict.fromkeys(active_schools))  # dedupe, preserve order
        school_tags = " ".join(
            "<span style='color:" + SCHOOL_COLORS.get(s, '#e0e0e0') + "'>" + s + "</span>"
            for s in active_schools
        )
        parts = [f"📋 {guide.get('num_rounds', 0)} rounds"]
        if school_tags:
            parts.append(school_tags)
        if guide.get('linked_bosses'):
            parts.append("👾 " + ", ".join(guide['linked_bosses']))
        info = QLabel("  ·  ".join(parts))
        info.setStyleSheet("color:#666;font-size:11px;")
        info.setWordWrap(True)
        info_col.addWidget(info)
        layout.addLayout(info_col, stretch=1)

        share_btn_g = QPushButton("🔗")
        share_btn_g.setStyleSheet("background:#1f3a6e;color:#e0e0e0;border:none;border-radius:4px;"
                                  "padding:5px 10px;font-size:13px;")
        share_btn_g.setFixedWidth(36)
        share_btn_g.setToolTip(f"Share '{guide['name']}' as a code")
        share_btn_g.clicked.connect(
            lambda checked=False, g=guide: self._share_guide(g)
        )
        layout.addWidget(share_btn_g)

        edit_btn = QPushButton("✏ Edit")
        edit_btn.setStyleSheet("background:#1f3a6e;color:#e0e0e0;border:none;border-radius:4px;"
                               "padding:5px 12px;font-size:11px;font-weight:bold;")
        edit_btn.setFixedWidth(70)
        edit_btn.clicked.connect(lambda checked, g=guide: self._edit_guide(g))
        layout.addWidget(edit_btn)

        del_btn = QPushButton("🗑")
        del_btn.setStyleSheet("background:#5c1b1b;color:#e0e0e0;border:none;border-radius:4px;"
                              "padding:5px 10px;font-size:13px;")
        del_btn.setFixedWidth(36)
        del_btn.clicked.connect(lambda checked, g=guide: self._delete_guide(g))
        layout.addWidget(del_btn)

        return card

    def _new_guide(self):
        dlg = GuideEditor(self.conn, self.boss_names, parent=self)
        dlg.setStyleSheet(GUIDE_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def _on_hud_toggled(self, checked: bool):
        if self.hud_toggle_callback:
            self.hud_toggle_callback("guide", checked, self._hud_btn)

    def sync_hud_btn(self, checked: bool):
        """Called by main app to keep button in sync with settings page."""
        self._hud_btn.blockSignals(True)
        self._hud_btn.setChecked(checked)
        self._hud_btn.blockSignals(False)

    def _edit_guide(self, guide: dict):
        full = db.get_guide(self.conn, guide['id'])
        dlg = GuideEditor(self.conn, self.boss_names, existing=full, parent=self)
        dlg.setStyleSheet(GUIDE_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            self.refresh()

    def _delete_guide(self, guide: dict):
        if confirm_delete(self, "Delete Guide", guide['name']):
            db.delete_guide(self.conn, guide['id'])
            self.refresh()

    def _share_guide(self, guide: dict):
        import share_codes
        full = db.get_guide(self.conn, guide['id'])
        if not full:
            return
        code = db.export_guide_code(full)
        share_codes.show_share_dialog(self, "Strategy Guide Share Code", code)

    def _import_guide_code(self):
        import share_codes, importer
        code = share_codes.prompt_import_code(self, "Import Strategy Guide")
        if not code:
            return
        data = db.import_guide_code(code)
        if not data:
            QMessageBox.warning(self, "Invalid Code",
                                "Could not decode that code. Check you copied it correctly.")
            return
        importer._import_guide(self.conn, data)
        self.conn.commit()
        self.refresh()
        QMessageBox.information(self, "Guide Imported",
                                f"Imported guide '{data.get('name','')}'.")

    def update_boss_names(self, boss_names: list):
        self.boss_names = boss_names


# ═══════════════════════════════════════════════════════════════
# INLINE GUIDE VIEW WIDGET (shown in boss tab)
# ═══════════════════════════════════════════════════════════════

class GuideViewWidget(QFrame):
    """Editable inline guide shown in boss tab — always 4 columns with multi-school dropdowns."""

    def __init__(self, guide: dict, conn, parent=None):
        super().__init__(parent)
        self.guide = guide
        self.conn = conn
        self.setStyleSheet("QFrame { background:#111827; border:1px solid #1f3a6e; border-radius:8px; }")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        hdr = QHBoxLayout()
        title = QLabel(f"📖 {self.guide['name']}")
        title.setStyleSheet("color:#4d96ff;font-weight:bold;font-size:13px;")
        hdr.addWidget(title)
        hdr.addStretch()
        layout.addLayout(hdr)

        if self.guide.get('free_text'):
            ft = QTextEdit()
            ft.setReadOnly(True)
            ft.setPlainText(self.guide['free_text'])
            ft.setMaximumHeight(80)
            ft.setStyleSheet("background:#0d1b2a;color:#c0c0c0;border:none;font-size:12px;padding:6px;")
            layout.addWidget(ft)

        # Normalise schools to list-of-lists of 4
        raw = list(self.guide.get('schools', []))
        if raw and not isinstance(raw[0], list):
            raw = [[s] if s else [] for s in raw]
        while len(raw) < 4:
            raw.append([])
        schools_per_col = [list(x) for x in raw[:4]]

        self.tbl = GuideTable(
            self.guide.get('num_rounds', 3),
            schools_per_col,
            self.guide.get('table_data', {}),
        )
        self.tbl.setStyleSheet("""
            QTableWidget { background:#0d1b2a; color:#e0e0e0; gridline-color:#1f3a6e;
                           border:none; font-size:12px; }
            QTableWidget::item { padding:4px 6px; }
            QTableWidget::item:selected { background:#1a3060; }
            QHeaderView::section { background:#0f3460; color:#e0e0e0;
                padding:4px; border:1px solid #1f3a6e; font-size:11px; }
        """)
        self.tbl.itemChanged.connect(self._save_changes)
        for dd in self.tbl.school_dropdowns:
            dd.school_changed = lambda: self._save_changes()
        layout.addWidget(self.tbl)

    def _save_changes(self, *args):
        full = db.get_guide(self.conn, self.guide['id'])
        if not full:
            return
        full['schools'] = self.tbl.get_schools_per_col()
        full['table_data'] = self.tbl.read_table_data()
        db.upsert_guide(self.conn, full)
        self.guide['schools'] = full['schools']
        self.guide['table_data'] = full['table_data']


# ═══════════════════════════════════════════════════════════════
# WORLD ORDER MANAGER DIALOG
# ═══════════════════════════════════════════════════════════════

class WorldSettingsManager(QDialog):
    """
    Unified World Settings dialog.
    Manages: name, order, source URL, level range.
    Syncs everything to both world_order.json and the quest DB on save.
    """

    STYLE = """
        QDialog, QWidget {
            background-color: #12121f; color: #e0e0e0;
            font-family: 'Segoe UI', Tahoma, sans-serif; font-size: 13px;
        }
        QPushButton {
            background-color: #1a2a4a; color: #e0e0e0;
            border: none; border-radius: 6px;
            padding: 7px 14px; font-size: 12px; font-weight: bold;
        }
        QPushButton:hover  { background-color: #2a3a6a; }
        QPushButton#btnAdd  { background:#1b5c38; }
        QPushButton#btnAdd:hover  { background:#27ae60; }
        QPushButton#btnDel  { background:#5c1b1b; }
        QPushButton#btnDel:hover  { background:#e94560; }
        QPushButton#btnSort { background:#1f3a6e; }
        QPushButton#btnSort:hover { background:#4d96ff; }
        QPushButton#btnSave  { background:#0f3460; }
        QPushButton#btnSave:hover  { background:#e94560; }
        QPushButton#btnUp   { background:#1a2a4a; color:#66ccff;
                               border:1px solid #2a4a7a; }
        QPushButton#btnUp:hover   { background:#2a4a7a; color:#fff; }
        QPushButton#btnDown { background:#1a2a4a; color:#66ccff;
                               border:1px solid #2a4a7a; }
        QPushButton#btnDown:hover { background:#2a4a7a; color:#fff; }
        QPushButton#btnApply { background:#1f3a6e; color:#a8c8ff;
                                border:1px solid #2a5a9e; }
        QPushButton#btnApply:hover { background:#4d96ff; color:#fff; }
        QListWidget {
            background:#1a1a2e; color:#e0e0e0;
            border: 1px solid #2a3a5a; border-radius: 6px; font-size: 13px;
        }
        QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #1f2a3a; }
        QListWidget::item:selected { background: #1f3a6e; color:#e0e0e0; }
        QListWidget::item:hover { background: #1a2a3a; }
        QLineEdit, QSpinBox {
            background:#1a1a2e; color:#e0e0e0;
            border: 1px solid #2a3a5a; border-radius: 5px;
            padding: 5px 8px; font-size: 12px;
        }
        QLineEdit:focus, QSpinBox:focus { border-color: #4d96ff; }
        QFrame#detailPanel {
            background:#0f1830; border: 1px solid #2a3a5a; border-radius: 8px;
        }
        QLabel#sectionLbl {
            color:#66ccff; font-weight:bold; font-size:11px; letter-spacing:1px;
        }
    """

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        # world_data: dict keyed by name → {source_url, level_min, level_max, id}
        self._world_data: dict = {}
        self._removed_worlds: list = []   # (name, db_id) pairs for worlds removed from the list
        self._prev_selected_name: str = None  # tracks which world's detail panel was last shown
        self.setWindowTitle("World Settings")
        self.setMinimumSize(700, 600)
        self.resize(760, 660)
        self.setStyleSheet(self.STYLE)
        self._build()
        self._load()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: ordered list ──────────────────────────────────────
        left = QWidget()
        left.setObjectName("wsLeft")
        left.setStyleSheet("QWidget#wsLeft { background:#0d0d1a; border-right:1px solid #1f2a3a; }")
        left.setFixedWidth(300)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.setSpacing(8)

        header = QLabel("<b style='color:#66ccff;font-size:15px'>🌍 World Settings</b>")
        ll.addWidget(header)

        hint = QLabel("Drag to reorder  •  Click to edit  •  Double-click to rename")
        hint.setStyleSheet("color:#444; font-size:10px;")
        hint.setWordWrap(True)
        ll.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._rename_world)
        ll.addWidget(self.list_widget, stretch=1)

        # Move up/down
        mv = QHBoxLayout()
        up_btn = QPushButton("▲")
        up_btn.setObjectName("btnUp")
        up_btn.setFixedWidth(40)
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(self._move_up)
        mv.addWidget(up_btn)
        dn_btn = QPushButton("▼")
        dn_btn.setObjectName("btnDown")
        dn_btn.setFixedWidth(40)
        dn_btn.setToolTip("Move down")
        dn_btn.clicked.connect(self._move_down)
        mv.addWidget(dn_btn)
        mv.addStretch()
        ll.addLayout(mv)

        # Sort buttons (own row so they're readable)
        sort_row = QHBoxLayout()
        sort_btn = QPushButton("↕ Default Order")
        sort_btn.setObjectName("btnSort")
        sort_btn.setToolTip("Reset to built-in canonical story order")
        sort_btn.clicked.connect(self._sort_default)
        sort_row.addWidget(sort_btn)

        sort_lvl_btn = QPushButton("↕ Sort by Level")
        sort_lvl_btn.setObjectName("btnSort")
        sort_lvl_btn.setToolTip("Sort worlds by their level range (lowest first)")
        sort_lvl_btn.clicked.connect(self._sort_by_level)
        sort_row.addWidget(sort_lvl_btn)
        ll.addLayout(sort_row)

        # Add / Remove
        ar = QHBoxLayout()
        add_btn = QPushButton("＋ Add World")
        add_btn.setObjectName("btnAdd")
        add_btn.clicked.connect(self._add_world)
        ar.addWidget(add_btn)

        self.del_btn = QPushButton("🗑 Remove")
        self.del_btn.setObjectName("btnDel")
        self.del_btn.clicked.connect(self._remove_world)
        ar.addWidget(self.del_btn)
        ll.addLayout(ar)

        root.addWidget(left)

        # ── Right: detail panel ─────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        rl.setSpacing(16)

        self.detail_title = QLabel("<span style='color:#555'>Select a world to edit its settings</span>")
        self.detail_title.setStyleSheet("font-size:14px; font-weight:bold;")
        rl.addWidget(self.detail_title)

        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("detailPanel")
        self.detail_panel.setVisible(False)
        dl = QVBoxLayout(self.detail_panel)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.setSpacing(12)

        # URL
        url_lbl = QLabel("SOURCE URL")
        url_lbl.setObjectName("sectionLbl")
        dl.addWidget(url_lbl)
        url_hint = QLabel("FinalBastion quest guide URL for this world (used by Quest Tracker fetcher)")
        url_hint.setStyleSheet("color:#444; font-size:10px;")
        dl.addWidget(url_hint)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://finalbastion.com/wizard101-guides/…")
        dl.addWidget(self.url_input)

        # Level range
        lvl_lbl = QLabel("LEVEL RANGE  (optional)")
        lvl_lbl.setObjectName("sectionLbl")
        dl.addWidget(lvl_lbl)
        lvl_hint = QLabel("If set, a small badge will appear on the Quest Tracker world card")
        lvl_hint.setStyleSheet("color:#444; font-size:10px;")
        dl.addWidget(lvl_hint)

        lvl_row = QHBoxLayout()
        lvl_row.setSpacing(8)
        lvl_row.addWidget(QLabel("From"))
        self.lvl_min_spin = QSpinBox()
        self.lvl_min_spin.setRange(0, 200)
        self.lvl_min_spin.setValue(0)
        self.lvl_min_spin.setSpecialValueText("—")
        self.lvl_min_spin.setFixedWidth(80)
        lvl_row.addWidget(self.lvl_min_spin)
        lvl_row.addWidget(QLabel("to"))
        self.lvl_max_spin = QSpinBox()
        self.lvl_max_spin.setRange(0, 200)
        self.lvl_max_spin.setValue(0)
        self.lvl_max_spin.setSpecialValueText("—")
        self.lvl_max_spin.setFixedWidth(80)
        lvl_row.addWidget(self.lvl_max_spin)
        lvl_row.addStretch()
        dl.addLayout(lvl_row)

        dl.addStretch()

        apply_btn = QPushButton("✓ Apply Changes")
        apply_btn.setObjectName("btnApply")
        apply_btn.clicked.connect(self._apply_detail)
        dl.addWidget(apply_btn, 0, Qt.AlignRight)

        rl.addWidget(self.detail_panel, stretch=1)
        rl.addStretch()

        # ── Save & Close bar ────────────────────────────────────────
        save_bar = QHBoxLayout()
        save_bar.addStretch()
        save_btn = QPushButton("💾 Save & Apply All")
        save_btn.setObjectName("btnSave")
        save_btn.setMinimumWidth(160)
        save_btn.clicked.connect(self._save_and_close)
        save_bar.addWidget(save_btn)
        rl.addLayout(save_bar)

        root.addWidget(right, stretch=1)

    # ── Data loading ──────────────────────────────────────────────

    def _load(self):
        """Load world list from world_order.json + supplement with quest DB data."""
        self.list_widget.clear()
        self._world_data = {}

        # Merge: start from canonical order, add any worlds in quest DB not yet listed
        order = get_world_order()
        db_worlds = {w["name"]: w for w in dq.get_all_worlds(self.conn)}

        # Worlds in order list
        all_names = list(order)
        # Add any quest-DB worlds not yet in the order
        for name in db_worlds:
            if name not in all_names:
                all_names.append(name)

        for name in all_names:
            w = db_worlds.get(name, {})
            self._world_data[name] = {
                "id":          w.get("id"),
                "source_url":  w.get("source_url", ""),
                "level_min":   w.get("level_min") or 0,
                "level_max":   w.get("level_max") or 0,
            }
            item = QListWidgetItem(self._item_text(name))
            item.setData(Qt.UserRole, name)
            self.list_widget.addItem(item)

    def _item_text(self, name: str) -> str:
        d = self._world_data.get(name, {})
        url_icon = "🔗" if d.get("source_url") else "○"
        lv = ""
        lmin = d.get("level_min") or 0
        lmax = d.get("level_max") or 0
        if lmin or lmax:
            lv = f"  [Lv {lmin or '?'}–{lmax or '?'}]"
        return f"{url_icon}  {name}{lv}"

    def _refresh_item_text(self, name: str):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == name:
                item.setText(self._item_text(name))
                break

    # ── Detail panel ─────────────────────────────────────────────

    def _on_selection_changed(self, row: int):
        # Auto-save the previously selected world's detail edits
        # (the spinboxes still hold the OLD world's values at this point)
        if hasattr(self, '_prev_selected_name') and self._prev_selected_name:
            prev = self._prev_selected_name
            if prev in self._world_data:
                self._world_data[prev]["source_url"] = self.url_input.text().strip()
                self._world_data[prev]["level_min"]  = self.lvl_min_spin.value()
                self._world_data[prev]["level_max"]  = self.lvl_max_spin.value()
                self._refresh_item_text(prev)

        if row < 0:
            self.detail_panel.setVisible(False)
            self._prev_selected_name = None
            return
        item = self.list_widget.item(row)
        if not item:
            return
        name = item.data(Qt.UserRole)
        d = self._world_data.get(name, {})
        self.detail_title.setText(f"<b style='color:#66ccff'>{name}</b>")
        self.url_input.setText(d.get("source_url", "") or "")
        self.lvl_min_spin.setValue(d.get("level_min") or 0)
        self.lvl_max_spin.setValue(d.get("level_max") or 0)
        self.detail_panel.setVisible(True)
        self._prev_selected_name = name

    def _apply_detail(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        name = self.list_widget.item(row).data(Qt.UserRole)
        if name not in self._world_data:
            self._world_data[name] = {}
        self._world_data[name]["source_url"] = self.url_input.text().strip()
        self._world_data[name]["level_min"]  = self.lvl_min_spin.value()
        self._world_data[name]["level_max"]  = self.lvl_max_spin.value()
        self._refresh_item_text(name)

    # ── List management ───────────────────────────────────────────

    def _current_row(self):
        return self.list_widget.currentRow()

    def _move_up(self):
        row = self._current_row()
        if row <= 0:
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(row - 1, item)
        self.list_widget.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._current_row()
        if row < 0 or row >= self.list_widget.count() - 1:
            return
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(row + 1, item)
        self.list_widget.setCurrentRow(row + 1)

    def _add_world(self):
        name, ok = QInputDialog.getText(self, "Add World", "World name:")
        name = name.strip()
        if not ok or not name:
            return
        existing = [self.list_widget.item(i).data(Qt.UserRole)
                    for i in range(self.list_widget.count())]
        if name in existing:
            QMessageBox.information(self, "Exists", f"'{name}' is already in the list.")
            return
        self._world_data[name] = {"source_url": "", "level_min": None, "level_max": None, "id": None}
        item = QListWidgetItem(self._item_text(name))
        item.setData(Qt.UserRole, name)
        self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(self.list_widget.count() - 1)

    def _rename_world(self, item):
        old = item.data(Qt.UserRole)
        new, ok = QInputDialog.getText(self, "Rename World", "New name:", text=old)
        new = new.strip()
        if not ok or not new or new == old:
            return
        existing = [self.list_widget.item(i).data(Qt.UserRole)
                    for i in range(self.list_widget.count())
                    if self.list_widget.item(i) is not item]
        if new in existing:
            QMessageBox.warning(self, "Duplicate", f"'{new}' already exists.")
            return
        # Move data
        self._world_data[new] = self._world_data.pop(old, {})
        item.setData(Qt.UserRole, new)
        item.setText(self._item_text(new))
        self.detail_title.setText(f"<b style='color:#66ccff'>{new}</b>")
        # Rename in quest DB
        try:
            self.conn.execute("UPDATE quest_worlds SET name=? WHERE name=?", (new, old))
            self.conn.commit()
        except Exception:
            pass

    def _remove_world(self):
        row = self._current_row()
        if row < 0:
            return
        name = self.list_widget.item(row).data(Qt.UserRole)
        if confirm_delete(self, "Remove World", name,
                          "This removes the world and all its quest data from the Quest Tracker."):
            # Track for deletion from quest DB on save
            d = self._world_data.pop(name, {})
            db_id = d.get("id")
            if db_id:
                self._removed_worlds.append((name, db_id))
            self.list_widget.takeItem(row)
            self.detail_panel.setVisible(False)

    def _sort_default(self):
        names = [self.list_widget.item(i).data(Qt.UserRole)
                 for i in range(self.list_widget.count())]
        def key(n):
            try:
                return _WORLD_ORDER_DEFAULT.index(n)
            except ValueError:
                return 9999
        names.sort(key=key)
        self.list_widget.clear()
        for name in names:
            item = QListWidgetItem(self._item_text(name))
            item.setData(Qt.UserRole, name)
            self.list_widget.addItem(item)

    def _sort_by_level(self):
        """Sort worlds by level_min ascending; worlds without levels go to the bottom."""
        # Auto-save current selection first
        self._apply_detail()
        names = [self.list_widget.item(i).data(Qt.UserRole)
                 for i in range(self.list_widget.count())]
        def key(n):
            d = self._world_data.get(n, {})
            lv_min = d.get("level_min") or 0
            lv_max = d.get("level_max") or 0
            has_level = bool(lv_min or lv_max)
            # Worlds with no level set sort to the bottom
            # Among leveled worlds: sort by min first, then by max descending
            # (higher max = further in that tier → lower priority)
            return (0 if has_level else 1, lv_min or 9999, lv_max or 9999)
        names.sort(key=key)
        self.list_widget.clear()
        for name in names:
            item = QListWidgetItem(self._item_text(name))
            item.setData(Qt.UserRole, name)
            self.list_widget.addItem(item)

    # ── Save ─────────────────────────────────────────────────────

    def _save_and_close(self):
        global WORLD_ORDER

        # Apply any pending detail changes (in case user forgot to click Apply)
        self._apply_detail()

        # Build ordered name list
        new_order = [self.list_widget.item(i).data(Qt.UserRole)
                     for i in range(self.list_widget.count())]

        # 1. Persist order file
        save_world_order(new_order)
        WORLD_ORDER = new_order

        # 2. Delete removed worlds from quest DB
        for name, db_id in self._removed_worlds:
            try:
                dq.delete_world_data(self.conn, db_id)
            except Exception:
                logger.warning(f"Failed to delete world '{name}' (id={db_id}) from quest DB")

        # 3. Sync to quest DB: upsert every world with its settings + display_order
        for i, name in enumerate(new_order):
            d = self._world_data.get(name, {})
            existing = dq.get_world_by_name(self.conn, name)
            base = dict(existing) if existing else {}
            base.update({
                "name":         name,
                "display_order": i,
                "source_url":   d.get("source_url") or base.get("source_url", ""),
                "level_min":    d.get("level_min"),
                "level_max":    d.get("level_max"),
            })
            dq.upsert_world(self.conn, base)

        self.conn.commit()
        self.accept()

# ═══════════════════════════════════════════════════════════════
# FIRST-RUN TEMPLATE SEEDING
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═══════════════════════════════════════════════════════════════


from PyQt5.QtCore import pyqtSignal as _pyqtSignal

class IconPresetManagerWidget(QWidget):
    """
    Full CRUD panel for icon presets — create, rename, change description,
    change/add image, reorder, delete. Lives as a sub-page inside
    HUD & Settings. Follows the PresetEditorPanel pattern from damage_calc.py.
    """
    changed = _pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._build()
        self._refresh()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel("🔍 Icon Preset Library")
        title.setStyleSheet("color:#c39bd3;font-size:14px;font-weight:bold;background:transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        add_btn = QPushButton("＋ New Preset")
        add_btn.setStyleSheet(
            "QPushButton{background:#3a1f60;color:#c39bd3;border:none;"
            "border-radius:5px;padding:5px 12px;font-size:11px;}"
            "QPushButton:hover{background:#6a3fa0;}"
        )
        add_btn.clicked.connect(self._on_add)
        hdr.addWidget(add_btn)
        root.addLayout(hdr)

        note = QLabel(
            "These presets appear in the Spell Browser's 'Detected Icons' legend, "
            "and their image is what the visual detector matches against spell cards. "
            "Use a small, tightly-cropped PNG. Drag rows to reorder (manual sort only). "
            "Right-click to edit or delete."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#555;font-size:10px;background:transparent;")
        root.addWidget(note)

        # ── Search + sort controls ───────────────────────────────────
        self._sort_mode = "manual"   # "manual" | "az" | "za"
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Search presets…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            "QLineEdit{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;"
            "border-radius:4px;padding:5px 8px;font-size:11px;}"
            "QLineEdit:focus{border-color:#c39bd3;}"
        )
        self._search.textChanged.connect(self._refresh)
        tools.addWidget(self._search, stretch=1)

        def _sort_btn(text):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;"
                "border-radius:4px;padding:5px 10px;font-size:11px;}"
                "QPushButton:hover{background:#1f4a80;}"
                "QPushButton:checked{background:#3a1f60;color:#c39bd3;border-color:#6a3fa0;}"
            )
            return b
        self._az_btn = _sort_btn("A→Z")
        self._za_btn = _sort_btn("Z→A")
        self._az_btn.clicked.connect(lambda: self._set_sort("az"))
        self._za_btn.clicked.connect(lambda: self._set_sort("za"))
        tools.addWidget(self._az_btn)
        tools.addWidget(self._za_btn)
        root.addLayout(tools)

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.setStyleSheet(
            "QListWidget{background:#0d1b2a;border:1px solid #1f3460;border-radius:6px;"
            "color:#e0e0e0;font-size:12px;}"
            "QListWidget::item{padding:8px 10px;border-bottom:1px solid #16213e;}"
            "QListWidget::item:selected{background:#3a1f60;}"
        )
        self._list.itemDoubleClicked.connect(lambda it: self._on_edit(it.data(Qt.UserRole)))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context)
        self._list.model().rowsMoved.connect(self._on_reorder)
        root.addWidget(self._list, stretch=1)

        btn_row = QHBoxLayout()
        edit_btn = QPushButton("✎ Edit")
        edit_btn.clicked.connect(lambda: self._on_edit(self._selected_id()))
        del_btn = QPushButton("🗑 Delete")
        del_btn.clicked.connect(self._on_delete)
        for b in (edit_btn, del_btn):
            b.setStyleSheet(
                "QPushButton{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;"
                "border-radius:4px;padding:5px 12px;font-size:11px;}"
                "QPushButton:hover{background:#1f4a80;}"
            )
            btn_row.addWidget(b)
        btn_row.addStretch()
        root.addLayout(btn_row)

    def _set_sort(self, mode):
        # Clicking the active sort button toggles back to manual order.
        if self._sort_mode == mode:
            self._sort_mode = "manual"
        else:
            self._sort_mode = mode
        self._az_btn.setChecked(self._sort_mode == "az")
        self._za_btn.setChecked(self._sort_mode == "za")
        self._refresh()

    def _refresh(self):
        self._list.clear()
        import database_spells as _ds
        presets = _ds.list_icon_presets(self.conn)   # DB order = manual (sort_order)

        query = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        if query:
            presets = [p for p in presets
                       if query in (p.get("name", "") or "").lower()
                       or query in (p.get("description", "") or "").lower()]

        mode = getattr(self, "_sort_mode", "manual")
        if mode == "az":
            presets = sorted(presets, key=lambda p: (p.get("name", "") or "").lower())
        elif mode == "za":
            presets = sorted(presets, key=lambda p: (p.get("name", "") or "").lower(),
                             reverse=True)

        # Drag-to-reorder only makes sense in manual, unfiltered view —
        # otherwise dropped positions wouldn't map back to sort_order.
        allow_drag = (mode == "manual" and not query)
        self._list.setDragDropMode(
            QAbstractItemView.InternalMove if allow_drag else QAbstractItemView.NoDragDrop
        )

        for p in presets:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, p["id"])
            img_p = p.get("image_path", "")
            if img_p and os.path.exists(img_p):
                item.setIcon(QIcon(QPixmap(img_p).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            item.setText(f"  {p['name']}  —  {p['description'][:70]}")
            self._list.addItem(item)

    def _selected_id(self):
        it = self._list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _on_context(self, pos):
        from PyQt5.QtWidgets import QMenu
        item = self._list.itemAt(pos)
        if not item:
            return
        pid = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        act_edit = menu.addAction("✎ Edit")
        act_del  = menu.addAction("🗑 Delete")
        chosen = menu.exec_(self._list.mapToGlobal(pos))
        if chosen == act_edit:
            self._on_edit(pid)
        elif chosen == act_del:
            self._delete_id(pid)

    def _on_reorder(self):
        ids = [self._list.item(i).data(Qt.UserRole) for i in range(self._list.count())]
        import database_spells as _ds
        _ds.reorder_icon_presets(self.conn, ids)
        self.changed.emit()

    def _on_add(self):
        self._on_edit(None)

    def _on_edit(self, pid):
        import database_spells as _ds
        existing = _ds.get_icon_preset(self.conn, pid) if pid is not None else None
        dlg = _IconPresetDialog(existing, self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.result_data()
            if pid is not None:
                data["id"] = pid
            _ds.upsert_icon_preset(self.conn, data)
            self._refresh()
            self.changed.emit()

    def _on_delete(self):
        self._delete_id(self._selected_id())

    def _delete_id(self, pid):
        if pid is None:
            return
        if QMessageBox.question(
            self, "Delete Preset",
            "Delete this icon preset?\nThis will also remove it from all spell icon legends.",
            QMessageBox.Yes | QMessageBox.No
        ) != QMessageBox.Yes:
            return
        import database_spells as _ds
        _ds.delete_icon_preset(self.conn, pid)
        self._refresh()
        self.changed.emit()


class _IconPresetDialog(QDialog):
    def __init__(self, existing, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Icon Preset" if existing else "New Icon Preset")
        self.setStyleSheet(
            "QDialog{background:#1a1a2e;}"
            "QLabel{color:#e0e0e0;background:transparent;font-size:12px;}"
            "QLineEdit,QTextEdit{background:#0d1b2a;color:#e0e0e0;border:1px solid #1f3460;"
            "border-radius:4px;padding:5px 8px;font-size:12px;}"
            "QLineEdit:focus,QTextEdit:focus{border-color:#c39bd3;}"
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 16px;font-size:12px;}"
            "QPushButton:hover{background:#4d96ff;}"
        )
        self.resize(420, 320)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 12)
        v.setSpacing(10)

        from PyQt5.QtWidgets import QFormLayout
        form = QFormLayout()
        form.setSpacing(8)

        self._name_fld = QLineEdit(existing["name"] if existing else "")
        self._name_fld.setPlaceholderText("e.g. Damage Over Time")
        form.addRow("Name:", self._name_fld)

        self._desc_fld = QTextEdit()
        self._desc_fld.setFixedHeight(80)
        self._desc_fld.setPlainText(existing.get("description", "") if existing else "")
        self._desc_fld.setPlaceholderText("Description shown in the spell's icon legend")
        form.addRow("Description:", self._desc_fld)

        self._img_path = existing.get("image_path", "") if existing else ""
        self._img_row = QHBoxLayout()
        self._img_preview = QLabel()
        self._img_preview.setFixedSize(40, 40)
        self._img_preview.setStyleSheet(
            "background:#0d1b2a;border:1px solid #1f3460;border-radius:4px;"
        )
        self._refresh_img_preview()
        self._img_row.addWidget(self._img_preview)
        pick_btn = QPushButton("📂 Choose Image…")
        pick_btn.clicked.connect(self._pick_image)
        clear_btn = QPushButton("✕ Clear")
        clear_btn.clicked.connect(self._clear_image)
        self._img_row.addWidget(pick_btn)
        self._img_row.addWidget(clear_btn)
        self._img_row.addStretch()
        form.addRow("Image (PNG):", self._img_row)
        v.addLayout(form)

        v.addStretch()
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(
            "QPushButton{background:#2a1a2e;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 16px;font-size:12px;}"
            "QPushButton:hover{background:#5c1b1b;}"
        )
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        v.addLayout(btns)

    def _refresh_img_preview(self):
        if self._img_path and os.path.exists(self._img_path):
            pix = QPixmap(self._img_path).scaled(
                38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._img_preview.setPixmap(pix)
        else:
            self._img_preview.setPixmap(QPixmap())
            self._img_preview.setText("—")

    def _pick_image(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Icon Image", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            self._img_path = path
            self._refresh_img_preview()

    def _clear_image(self):
        self._img_path = ""
        self._refresh_img_preview()

    def result_data(self) -> dict:
        return {
            "name": self._name_fld.text().strip(),
            "description": self._desc_fld.toPlainText().strip(),
            "image_path": self._img_path,
        }

class _GitUpdateWorker(QThread):
    """
    Runs git operations off the UI thread.

    mode="check": fetch + compare local vs remote HEAD, emit checked(...).
    mode="pull" : pull the latest (stash-on-conflict fallback), emit pulled(...).

    Only ever touches an existing repo — it never bootstraps a fresh checkout
    (that stays a deliberate, user-initiated action in the Settings page).
    """
    checked = pyqtSignal(bool, str, str, str)   # available, local_sha, remote_sha, error
    pulled  = pyqtSignal(bool, str)             # success, error

    def __init__(self, git_exe: str, repo_dir: str, remote_url: str,
                 mode: str = "check", parent=None):
        super().__init__(parent)
        self.git_exe = git_exe
        self.repo_dir = repo_dir
        self.remote_url = remote_url
        self.mode = mode

    def _run(self, args, timeout=60):
        import subprocess
        kwargs = dict(cwd=self.repo_dir, capture_output=True, text=True, timeout=timeout)
        if os.name == "nt":
            # Suppress the console window flash on Windows for silent background runs.
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        try:
            r = subprocess.run([self.git_exe] + args, **kwargs)
            return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
        except Exception as exc:
            return -1, "", str(exc)

    def run(self):
        if self.mode == "pull":
            ok, err = self._do_pull()
            self.pulled.emit(ok, err)
        else:
            avail, loc, rem, err = self._do_check()
            self.checked.emit(avail, loc, rem, err)

    def _do_check(self):
        rc, out, _ = self._run(["rev-parse", "--is-inside-work-tree"], timeout=15)
        if rc != 0 or out != "true":
            return False, "", "", "not-a-repo"
        if self.remote_url:
            self._run(["remote", "set-url", "origin", self.remote_url], timeout=15)
        rc, _, err = self._run(["fetch", "--quiet", "origin"], timeout=90)
        if rc != 0:
            return False, "", "", err or "fetch failed"
        rc_l, local_sha, _ = self._run(["rev-parse", "HEAD"], timeout=15)
        rc_r, remote_sha, _ = self._run(["rev-parse", "FETCH_HEAD"], timeout=15)
        if rc_l != 0 or rc_r != 0:
            return False, "", "", "could not read revisions"
        return (local_sha != remote_sha), local_sha, remote_sha, ""

    def _do_pull(self):
        rc, _, err = self._run(["pull", "--ff-only", "origin"], timeout=120)
        if rc == 0:
            return True, ""
        # Stash local changes, retry
        self._run(["stash", "--include-untracked"], timeout=60)
        rc, _, err = self._run(["pull", "--ff-only", "origin"], timeout=120)
        if rc == 0:
            return True, ""
        # Last resort: fetch + checkout
        self._run(["fetch", "origin"], timeout=120)
        rc, _, err = self._run(["checkout", "FETCH_HEAD", "--", "."], timeout=120)
        return (rc == 0), (err if rc != 0 else "")


class BossWikiApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wizard101 Companion")
        self.setMinimumSize(1100, 750)
        self.resize(1280, 860)

        self.conn = db.get_connection()
        db.init_db(self.conn)
        dq.init_quest_tables(self.conn)
        dg.init_gear_tables(self.conn)
        if DAMAGE_CALC_AVAILABLE and dccalc:
            dccalc.init_calc_tables(self.conn)
        # Spell + Deck tables
        if SPELL_BROWSER_AVAILABLE or DECK_BUILDER_AVAILABLE:
            try:
                import database_spells as _dspells
                _dspells.init_spell_tables(self.conn)
            except Exception as _e:
                print(f"  [WARN] Could not init spell tables: {_e}")
        self.boss_names = db.get_boss_names(self.conn)

        # Subprocess handles for fetch operations
        self.fetch_process = None
        self._in_search_mode = False

        # Quest tracker window reference
        self._quest_tracker_window = None

        # ── Boss OCR ──
        self.ocr_scanner = None
        if OCR_AVAILABLE and OCRScanner:
            self.ocr_scanner = OCRScanner()
            self.ocr_scanner.bosses_detected.connect(self._on_bosses_detected)
            self.ocr_scanner.debug_text.connect(self._update_ocr_debug)
            self.ocr_scanner.set_known_names(self.boss_names)
            # Restore the saved OCR matching mode
            if overlay_settings is not None:
                try:
                    self.ocr_scanner.set_mode(overlay_settings.get_ocr_mode())
                except Exception:
                    pass

        # ── HUD Overlay Manager ──
        self.overlay_manager = OverlayManager() if HUD_AVAILABLE else None
        if self.overlay_manager:
            self.overlay_manager.set_conn(self.conn)

        # ── Keybind Manager (needs overlay_manager ref before UI is built) ──
        self.keybind_manager = None
        if KEYBINDS_AVAILABLE and KeybindManager:
            self.keybind_manager = KeybindManager(self.overlay_manager)

        self._build_ui()
        self.setStyleSheet(DARK_STYLE)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status_bar()

        # ── Wire boss overlay signals ──
        if self.overlay_manager and HUD_AVAILABLE:
            # The boss overlay is lazy-created; wire it now if it already
            # exists (e.g. restored from a previous session), otherwise
            # _wire_boss_overlay() is called from _on_overlay_enabled_changed.
            self._boss_overlay_wired = False
            self._wire_boss_overlay()

            self._calc_overlay_wired = False
            self._wire_calc_overlay()

            # Register callback so settings page buttons stay in sync
            # when overlays are closed via their own X button
            overlay_settings.add_enabled_changed_callback(self._on_overlay_enabled_changed)

        # ── Wire keybind OCR toggle ──
        if self.keybind_manager:
            self.keybind_manager.set_ocr_toggle_callback(self._toggle_boss_ocr)

        logger.info(f"App started. {len(self.boss_names)} bosses in local database.")

    # ═══════════════════════════════════════════════════════════════
    # HUB LANDING PAGE
    # ═══════════════════════════════════════════════════════════════

    def _build_hub(self) -> QWidget:
        """StreamDeck-style landing page with draggable, favouritable feature tiles."""
        self._hub_buttons = _load_hub_order()

        page = QWidget()
        page.setStyleSheet("QWidget { background:#1a1a2e; }")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(50, 40, 50, 40)
        outer.setSpacing(32)

        title = QLabel("🧙 Wizard101 Companion")
        title.setFont(QFont("Segoe UI", 26, QFont.Bold))
        title.setStyleSheet("color:#e0e0e0; background:transparent;")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._hub_grid_container = QWidget()
        self._hub_grid_container.setStyleSheet("background:transparent;")
        scroll.setWidget(self._hub_grid_container)
        outer.addWidget(scroll, stretch=1)

        self._rebuild_hub_grid()
        return page

    def _action_for_key(self, key: str):
        """Return the callable for a hub button's action_key."""
        return {
            "boss_wiki":         lambda: self._nav_to("boss_wiki"),
            "gear_guide":        lambda: self._nav_to("gear_guide"),
            "damage_calc":       lambda: self._nav_to("damage_calc"),
            "characters":        lambda: self._nav_to("characters"),
            "quest_tracker":     self._open_quest_tracker,
            "boss_wiki_counters":lambda: self._nav_to("boss_wiki", tab="counters"),
            "boss_wiki_guides":  lambda: self._nav_to("boss_wiki", tab="guides"),
            "spells":            lambda: self._nav_to("spells"),
            "deck_builder":      lambda: self._nav_to("decks"),
            "settings":          lambda: self._nav_to("settings"),
        }.get(key, lambda: None)

    def _rebuild_hub_grid(self):
        """Rebuild the hub grid in-place after order/visibility changes."""
        # Remove all existing children
        old_layout = self._hub_grid_container.layout()
        if old_layout:
            while old_layout.count():
                it = old_layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            QWidget().setLayout(old_layout)  # orphan the old layout

        grid = QGridLayout(self._hub_grid_container)
        grid.setSpacing(16)
        grid.setContentsMargins(0, 0, 0, 0)

        visible = [b for b in self._hub_buttons if not b.get("hidden")]
        favorites    = [b for b in visible if b.get("favorite")]
        non_favorites = [b for b in visible if not b.get("favorite")]

        COLS = 3
        row = col = 0
        for btn in favorites:
            card = self._make_hub_card_v2(btn, is_favorite=True)
            grid.addWidget(card, row, col)
            col += 1
            if col >= COLS:
                col = 0; row += 1

        if favorites and non_favorites:
            # Advance to next full row for the separator, then skip a row
            if col != 0:
                row += 1; col = 0
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("QFrame{border:none;border-top:1px solid #1f3460;}")
            sep.setFixedHeight(4)
            grid.addWidget(sep, row, 0, 1, COLS)
            row += 1

        for btn in non_favorites:
            card = self._make_hub_card_v2(btn, is_favorite=False)
            grid.addWidget(card, row, col)
            col += 1
            if col >= COLS:
                col = 0; row += 1

        grid.setRowStretch(row + 1, 1)

    def _make_hub_card_v2(self, btn: dict, is_favorite: bool) -> QFrame:
        """Hub card v2: favourites star + right-click context menu + drag (via mouse)."""
        title_color = btn.get("title_color", "#e0e0e0")

        card = QFrame()
        card.setObjectName("hubCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setMinimumSize(240, 155)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setStyleSheet("""
            QFrame#hubCard {
                background-color: #16213e;
                border: 1px solid #1f3460;
                border-radius: 14px;
            }
            QFrame#hubCard:hover {
                border: 1px solid #4d6fa8;
                background-color: #1c2a50;
            }
        """)
        card.setContextMenuPolicy(Qt.CustomContextMenu)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 16, 22, 20)
        layout.setSpacing(6)

        # Top row: icon (left) + drag handle text (right) + favourite star
        top_row = QHBoxLayout()
        icon_lbl = QLabel(btn["icon"])
        icon_lbl.setStyleSheet("font-size:28px; background:transparent; color:#e0e0e0;")
        top_row.addWidget(icon_lbl)

        # Update-available indicator on the HUD & Settings card.
        if btn.get("id") == "hud_settings" and getattr(self, "_update_available", False):
            dot = QLabel("●")
            dot.setStyleSheet("color:#e94560; font-size:16px; background:transparent;")
            dot.setToolTip("A new version is available — open HUD & Settings to update")
            top_row.addWidget(dot)

        top_row.addStretch()

        drag_hint = QLabel("⠿")
        drag_hint.setStyleSheet("color:#333;font-size:14px;background:transparent;")
        drag_hint.setToolTip("Drag to reorder")
        top_row.addWidget(drag_hint)

        fav_lbl = QLabel("⭐" if is_favorite else "☆")
        fav_lbl.setStyleSheet(
            ("font-size:16px;color:#ffd93d;background:transparent;"
             if is_favorite else
             "font-size:16px;color:#333;background:transparent;")
        )
        fav_lbl.setToolTip("Remove from favorites" if is_favorite else "Add to favorites")
        fav_lbl.setCursor(Qt.PointingHandCursor)
        _bid = btn["id"]
        fav_lbl.mousePressEvent = lambda e, bid=_bid: self._toggle_hub_favorite(bid)
        top_row.addWidget(fav_lbl)
        layout.addLayout(top_row)

        title_lbl = QLabel(btn["title"])
        title_lbl.setStyleSheet(
            f"color:{title_color}; font-size:15px; font-weight:bold; background:transparent;"
        )
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(btn["desc"])
        desc_lbl.setStyleSheet("color:#555; font-size:11px; background:transparent;")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        layout.addStretch()

        action = self._action_for_key(btn.get("action_key", ""))
        for child in [icon_lbl, title_lbl, desc_lbl]:
            child.mousePressEvent = lambda e, a=action, fl=fav_lbl, dh=drag_hint: (
                None if e.button() != Qt.LeftButton else
                (None if e.pos() in [fl.rect(), dh.rect()] else (a() if a else None))
            )
        card.mousePressEvent = lambda e, a=action: (
            a() if e.button() == Qt.LeftButton else None
        )

        # Right-click context menu
        card.customContextMenuRequested.connect(
            lambda pos, bid=btn["id"]: self._hub_card_context_menu(bid, card, pos)
        )

        # Mouse-drag for reordering
        card._drag_start_pos = None
        card._btn_id = btn["id"]
        card._is_favorite = is_favorite

        original_press = card.mousePressEvent
        def _on_press(event, c=card, orig=original_press):
            c._drag_start_pos = event.globalPos()
            orig(event)
        card.mousePressEvent = _on_press

        return card

    def _hub_card_context_menu(self, btn_id: str, card: QFrame, pos):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#16213e;color:#e0e0e0;border:1px solid #0f3460;}"
            "QMenu::item:selected{background:#1f4a80;}"
        )
        btn = next((b for b in self._hub_buttons if b["id"] == btn_id), None)
        if btn is None:
            return

        if btn.get("favorite"):
            act_fav = menu.addAction("☆ Remove from Favorites")
        else:
            act_fav = menu.addAction("⭐ Mark as Favorite")

        menu.addSeparator()
        # Never allow hiding the Settings button — it's the only path
        # back to settings if all other navigation is misconfigured.
        ALWAYS_VISIBLE = {"hud_settings"}
        if btn_id not in ALWAYS_VISIBLE:
            act_hide = menu.addAction("🙈 Hide this button")
        else:
            act_hide = None
        act_move_up = menu.addAction("↑ Move Up")
        act_move_dn = menu.addAction("↓ Move Down")

        chosen = menu.exec_(card.mapToGlobal(pos))
        if chosen == act_fav:
            self._toggle_hub_favorite(btn_id)
        elif act_hide and chosen == act_hide:
            btn["hidden"] = True
            _save_hub_order(self._hub_buttons)
            self._rebuild_hub_grid()
        elif chosen == act_move_up:
            self._move_hub_btn(btn_id, -1)
        elif chosen == act_move_dn:
            self._move_hub_btn(btn_id, +1)

    def _toggle_hub_favorite(self, btn_id: str):
        btn = next((b for b in self._hub_buttons if b["id"] == btn_id), None)
        if btn is None:
            return
        btn["favorite"] = not btn["favorite"]
        # Move favorites to the front, non-favorites to the back
        self._hub_buttons.sort(key=lambda b: (0 if b.get("favorite") else 1))
        _save_hub_order(self._hub_buttons)
        self._rebuild_hub_grid()

    def _move_hub_btn(self, btn_id: str, delta: int):
        ids = [b["id"] for b in self._hub_buttons]
        idx = next((i for i, b in enumerate(self._hub_buttons) if b["id"] == btn_id), None)
        if idx is None:
            return
        new_idx = max(0, min(len(self._hub_buttons) - 1, idx + delta))
        btn = self._hub_buttons.pop(idx)
        self._hub_buttons.insert(new_idx, btn)
        _save_hub_order(self._hub_buttons)
        self._rebuild_hub_grid()


    def _build_boss_wiki_panel(self) -> QWidget:
        """Build the full original boss wiki UI as a self-contained widget."""
        panel = QWidget()
        # Do NOT set a background stylesheet here — it would break QPushButton
        # style inheritance from DARK_STYLE for all child buttons in this panel.
        main_layout = QVBoxLayout(panel)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(8)

        # ── Back button row ──
        back_row = QHBoxLayout()
        back_btn = QPushButton("← Hub")
        back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back_btn.clicked.connect(lambda: self._nav_to("hub"))
        back_row.addWidget(back_btn)
        back_row.addStretch()

        header = QLabel("👾 Boss Wiki")
        header.setFont(QFont("Segoe UI", 18, QFont.Bold))
        header.setStyleSheet("color: #e94560; padding: 4px;")
        back_row.addWidget(header)
        back_row.addStretch()
        main_layout.addLayout(back_row)

        # ── Search row ──
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search bosses by name, school, or location...")
        self.search_input.returnPressed.connect(self._do_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        completer = QCompleter(self.boss_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(completer)
        # When user picks from autocomplete dropdown, display that boss immediately
        completer.activated.connect(self._on_completer_activated)

        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self._do_search)

        self.clear_search_btn = QPushButton("✕")
        self.clear_search_btn.setToolTip("Clear search")
        self.clear_search_btn.setFixedWidth(32)
        self.clear_search_btn.setVisible(False)
        self.clear_search_btn.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#e94560;border:none;border-radius:5px;"
            "padding:6px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        self.clear_search_btn.clicked.connect(self._clear_search)

        self.scrape_btn = QPushButton("🌐 Fetch from Wiki")
        self.scrape_btn.setToolTip("Fetch a single boss via db_builder")
        self.scrape_btn.clicked.connect(self._fetch_single)

        self.ocr_toggle = QCheckBox("👾 Boss OCR")
        self.ocr_toggle.setEnabled(OCR_AVAILABLE and self.ocr_scanner is not None)
        self.ocr_toggle.toggled.connect(self._toggle_boss_ocr)
        if not OCR_AVAILABLE:
            self.ocr_toggle.setText("👾 Boss OCR (not installed)")

        search_row.addWidget(self.search_input, stretch=4)
        search_row.addWidget(self.clear_search_btn)
        search_row.addWidget(search_btn)
        search_row.addWidget(self.scrape_btn)
        search_row.addWidget(self.ocr_toggle)
        main_layout.addLayout(search_row)

        # ── Action row ──
        action_row = QHBoxLayout()

        fetch_all_btn = QPushButton("🌐 Fetch ALL Bosses")
        fetch_all_btn.setObjectName("fetchAllBtn")
        fetch_all_btn.clicked.connect(self._fetch_all)

        # Resume: when ticked, "Fetch ALL Bosses" skips bosses already in the DB,
        # so an interrupted fetch continues where it left off (and a routine
        # re-fetch grabs only newly-added bosses).
        self.boss_resume_chk = QCheckBox("Resume")
        self.boss_resume_chk.setToolTip(
            "Skip bosses already fetched — resume an interrupted fetch, or grab "
            "only new bosses, without re-processing the ones you already have.")
        self.boss_resume_chk.setStyleSheet(
            "QCheckBox{color:#9fd0b0;font-size:12px;font-weight:bold;spacing:5px;}"
            "QCheckBox::indicator{width:14px;height:14px;}"
        )

        self.boss_del_all_btn = QPushButton("🗑 Delete All Bosses")
        self.boss_del_all_btn.setObjectName("dangerBtn")
        self.boss_del_all_btn.setToolTip("Delete ALL bosses from the database")
        self.boss_del_all_btn.clicked.connect(self._delete_all_bosses)

        self.remove_btn = QPushButton("🗑 Remove Selected")
        self.remove_btn.setObjectName("dangerBtn")
        self.remove_btn.clicked.connect(self._remove_boss)

        from PyQt5.QtWidgets import QMenu as _QMenu
        self.reparse_btn = QPushButton("♻ Reparse ▾")
        self.reparse_btn.setToolTip("Re-parse cached wikitext without re-downloading")
        reparse_menu = _QMenu(self)
        reparse_menu.setStyleSheet("""
            QMenu { background:#16213e; color:#e0e0e0; border:1px solid #0f3460;
                    border-radius:4px; padding:4px; }
            QMenu::item { padding:6px 20px 6px 12px; border-radius:3px; }
            QMenu::item:selected { background:#e94560; color:#fff; }
            QMenu::separator { height:1px; background:#0f3460; margin:3px 6px; }
        """)
        act_rp_sel = reparse_menu.addAction("♻ Reparse Selected Boss")
        act_rp_sel.triggered.connect(self._reparse_selected_boss)
        act_rp_all = reparse_menu.addAction("♻ Reparse ALL Bosses")
        act_rp_all.triggered.connect(self._reparse_all_bosses)
        self.reparse_btn.setMenu(reparse_menu)

        self.counter_panel_btn = QPushButton("⏱ Round Counters")
        self.counter_panel_btn.setCheckable(True)
        self.counter_panel_btn.toggled.connect(self._toggle_counter_panel)

        self.guide_panel_btn = QPushButton("📖 Guides")
        self.guide_panel_btn.setCheckable(True)
        self.guide_panel_btn.toggled.connect(self._toggle_guide_panel)

        self.hud_boss_btn = QPushButton("🖥 HUD")
        self.hud_boss_btn.setToolTip("Toggle Boss Info HUD overlay")
        self.hud_boss_btn.setCheckable(True)
        self.hud_boss_btn.setChecked(HUD_AVAILABLE and overlay_settings is not None and overlay_settings.is_enabled("boss"))
        self.hud_boss_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#aaa;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 12px;font-size:12px;}"
            "QPushButton:checked{background:#e94560;color:#fff;border-color:#e94560;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        self.hud_boss_btn.toggled.connect(lambda checked: self._on_page_hud_toggle("boss", checked, self.hud_boss_btn))

        action_row.addWidget(fetch_all_btn)
        action_row.addWidget(self.boss_resume_chk)
        action_row.addWidget(self.boss_del_all_btn)
        action_row.addWidget(self.remove_btn)
        action_row.addWidget(self.reparse_btn)
        action_row.addWidget(self.counter_panel_btn)
        action_row.addWidget(self.guide_panel_btn)
        action_row.addWidget(self.hud_boss_btn)
        action_row.addStretch()
        main_layout.addLayout(action_row)

        # ── Progress bar ──
        self.progress_group = QGroupBox("Fetch Progress")
        prog_layout = QVBoxLayout(self.progress_group)
        self.progress_label = QLabel("Ready")
        self.progress_output = QTextEdit()
        self.progress_output.setReadOnly(True)
        self.progress_output.setMaximumHeight(120)
        self.progress_output.setStyleSheet(
            "font-family:Consolas;font-size:11px;color:#66ff66;background:#0a0a15;"
        )
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setToolTip("Stop the current fetch — bosses already fetched are kept")
        self.stop_btn.clicked.connect(self._cancel_fetch)
        prog_layout.addWidget(self.progress_label)
        prog_layout.addWidget(self.progress_output)
        prog_layout.addWidget(self.stop_btn)
        self.progress_group.setVisible(False)
        main_layout.addWidget(self.progress_group)

        # ── Splitter ──
        splitter = QSplitter(Qt.Horizontal)

        # Left: tree
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel("Boss List"))
        sort_row.addStretch()

        collapse_btn = QPushButton("⊖ Collapse All")
        collapse_btn.setMinimumWidth(110)
        collapse_btn.setStyleSheet("QPushButton{background:#0f3460;color:#e0e0e0;border:none;border-radius:5px;padding:5px 10px;font-size:12px;font-weight:bold;}QPushButton:hover{background:#e94560;}")
        collapse_btn.clicked.connect(lambda: self.boss_tree.collapseAll())
        sort_row.addWidget(collapse_btn)

        expand_btn = QPushButton("⊕ Expand All")
        expand_btn.setMinimumWidth(100)
        expand_btn.setStyleSheet("QPushButton{background:#0f3460;color:#e0e0e0;border:none;border-radius:5px;padding:5px 10px;font-size:12px;font-weight:bold;}QPushButton:hover{background:#e94560;}")
        expand_btn.clicked.connect(lambda: self.boss_tree.expandAll())
        sort_row.addWidget(expand_btn)

        sort_row.addWidget(QLabel("Sort:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Chronological", "A-Z", "Z-A"])
        self.sort_combo.currentIndexChanged.connect(self._refresh_tree)
        sort_row.addWidget(self.sort_combo)
        left_layout.addLayout(sort_row)

        self.boss_tree = QTreeWidget()
        self.boss_tree.setHeaderHidden(True)
        self.boss_tree.setIndentation(16)
        self.boss_tree.itemClicked.connect(self._on_tree_item_clicked)
        self.boss_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.boss_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        left_layout.addWidget(self.boss_tree)

        self.list_count_label = QLabel("")
        self.list_count_label.setStyleSheet("color:#999;font-size:11px;")
        left_layout.addWidget(self.list_count_label)
        splitter.addWidget(left_panel)

        # Right: tabs
        self.tabs = QTabWidget()
        self.info_display = QTextBrowser(); self.info_display.setReadOnly(True)
        self.info_display.setOpenExternalLinks(True)
        self.tabs.addTab(self.info_display, "📋 Boss Info")
        self.cheats_display = QTextEdit(); self.cheats_display.setReadOnly(True)
        self.tabs.addTab(self.cheats_display, "⚔ Cheats")
        self.spells_display = QTextEdit(); self.spells_display.setReadOnly(True)
        self.tabs.addTab(self.spells_display, "✨ Spells")
        self.minions_display = QTextEdit(); self.minions_display.setReadOnly(True)
        self.tabs.addTab(self.minions_display, "👾 Minions")
        self.drops_display = QTextEdit(); self.drops_display.setReadOnly(True)
        self.tabs.addTab(self.drops_display, "🎁 Drops")

        self.round_tab_scroll = QScrollArea()
        self.round_tab_scroll.setWidgetResizable(True)
        self.round_tab_scroll.setStyleSheet("QScrollArea{border:none;background:#1a1a2e;}")
        self.round_tab_inner = QWidget()
        self.round_tab_inner.setStyleSheet("background:#1a1a2e;")
        self.round_tab_layout = QVBoxLayout(self.round_tab_inner)
        self.round_tab_layout.setContentsMargins(8, 8, 8, 8)
        self.round_tab_layout.setSpacing(8)
        self.round_tab_layout.addStretch()
        self.round_tab_scroll.setWidget(self.round_tab_inner)
        self.tabs.addTab(self.round_tab_scroll, "⏱ Round Counters")

        self.guide_tab_scroll = QScrollArea()
        self.guide_tab_scroll.setWidgetResizable(True)
        self.guide_tab_scroll.setStyleSheet("QScrollArea{border:none;background:#1a1a2e;}")
        self.guide_tab_inner = QWidget()
        self.guide_tab_inner.setStyleSheet("background:#1a1a2e;")
        self.guide_tab_layout = QVBoxLayout(self.guide_tab_inner)
        self.guide_tab_layout.setContentsMargins(8, 8, 8, 8)
        self.guide_tab_layout.setSpacing(8)
        self.guide_tab_layout.addStretch()
        self.guide_tab_scroll.setWidget(self.guide_tab_inner)
        self.tabs.addTab(self.guide_tab_scroll, "📖 Guides")

        self.ocr_debug_display = QTextEdit(); self.ocr_debug_display.setReadOnly(True)
        self.ocr_debug_display.setStyleSheet("background-color:#0a0a15;color:#66ff66;font-family:Consolas;")
        self.tabs.addTab(self.ocr_debug_display, "👾 OCR Debug")

        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 700])

        self.counter_panel = RoundCounterPanel(self.conn, self.boss_names)
        self.counter_panel.setVisible(False)
        self.counter_panel.hud_toggle_callback = self._on_page_hud_toggle
        splitter.addWidget(self.counter_panel)

        self.guide_panel = GuidePanel(self.conn, self.boss_names)
        self.guide_panel.setVisible(False)
        self.guide_panel.hud_toggle_callback = self._on_page_hud_toggle
        splitter.addWidget(self.guide_panel)

        splitter.setSizes([300, 700, 0, 0])
        self.main_splitter = splitter
        main_layout.addWidget(splitter, stretch=1)

        self._refresh_tree()
        return panel

    # ═══════════════════════════════════════════════════════════════
    # MAIN STACKED LAYOUT + NAVIGATION
    # ═══════════════════════════════════════════════════════════════

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        # Page 0: Hub
        self._hub_page = self._build_hub()
        self.stack.addWidget(self._hub_page)

        # Page 1: Boss Wiki
        self._boss_wiki_page = self._build_boss_wiki_panel()
        self.stack.addWidget(self._boss_wiki_page)

        # Page 2: Gear Guide
        if GEAR_GUIDE_AVAILABLE and GearGuideWidget:
            self._gear_guide_page = GearGuideWidget(self.conn, self)
            self._gear_guide_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self.stack.addWidget(self._gear_guide_page)
        else:
            placeholder = QLabel("Gear Guide not available.\nEnsure gear_guide.py is present.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(placeholder)

        # Page 3: Damage Calculator
        if DAMAGE_CALC_AVAILABLE and DamageCalcWidget:
            self._calc_page = DamageCalcWidget(self.conn, self)
            self._calc_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self._calc_page.calc_panel.boss_selected.connect(self._on_calc_boss_selected)
            self.stack.addWidget(self._calc_page)
        else:
            self._calc_page = None
            ph_calc = QLabel("Damage Calculator not available.\nEnsure damage_calc.py is present.")
            ph_calc.setAlignment(Qt.AlignCenter)
            ph_calc.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(ph_calc)

        # Page 4: Characters
        if DAMAGE_CALC_AVAILABLE and CharacterManagerWidget:
            self._char_page = CharacterManagerWidget(self.conn, self)
            self._char_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self.stack.addWidget(self._char_page)
        else:
            self._char_page = None
            ph_char = QLabel("Characters not available.\nEnsure damage_calc.py is present.")
            ph_char.setAlignment(Qt.AlignCenter)
            ph_char.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(ph_char)

        # Page 6: Spell Browser
        if SPELL_BROWSER_AVAILABLE and SpellBrowserWidget:
            self._spell_browser_page = SpellBrowserWidget(self.conn, self)
            self._spell_browser_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self.stack.addWidget(self._spell_browser_page)
        else:
            self._spell_browser_page = None
            _ph_sp = QLabel("Spell Browser not available.\nEnsure spell_browser.py is present.")
            _ph_sp.setAlignment(Qt.AlignCenter)
            _ph_sp.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(_ph_sp)

        # Page 7: Deck Builder
        if DECK_BUILDER_AVAILABLE and DeckBuilderWidget:
            self._deck_builder_page = DeckBuilderWidget(self.conn, self)
            self._deck_builder_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self.stack.addWidget(self._deck_builder_page)
        else:
            self._deck_builder_page = None
            _ph_dk = QLabel("Deck Builder not available.\nEnsure deck_builder.py is present.")
            _ph_dk.setAlignment(Qt.AlignCenter)
            _ph_dk.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(_ph_dk)

        # Page 5: Settings / HUD
        self._settings_page = self._build_settings_page()
        self.stack.addWidget(self._settings_page)

        # Start on hub
        self.stack.setCurrentIndex(0)

        # ── Update-check state ────────────────────────────────────
        self._update_available = False
        self._update_check_worker = None
        self._update_pull_worker = None
        self._auto_update = (overlay_settings.get_auto_update()
                             if overlay_settings is not None else False)
        # Run a silent, background check for updates shortly after startup so it
        # never blocks the window appearing. A dot on the HUD & Settings card
        # lights up if a new version is available.
        QTimer.singleShot(1500, self._startup_update_check)

    def _switch_settings_subpage(self, page_key: str):
        idx_map = {'main': 0, 'icon_presets': 1, 'world_settings': 2}
        if hasattr(self, '_settings_substack'):
            self._settings_substack.setCurrentIndex(idx_map.get(page_key, 0))
        for k, btn in getattr(self, '_settings_tab_btns', {}).items():
            btn.setChecked(k == page_key)

    def _nav_to(self, section: str, tab: str = None):
        """Navigate to a named section, optionally jumping to a specific tab."""
        PAGE = {"hub": 0, "boss_wiki": 1, "gear_guide": 2,
                "damage_calc": 3, "characters": 4,
                "spells": 5, "decks": 6, "settings": 7}
        idx = PAGE.get(section, 0)
        self.stack.setCurrentIndex(idx)

        # Refresh calc surfaces whenever the calculator is opened so newly
        # added wizards / presets / bosses appear in its dropdowns.
        if section == "damage_calc" and getattr(self, "_calc_page", None):
            self._calc_page.refresh()
            if self.overlay_manager:
                self.overlay_manager.refresh_calc_data()
        if section == "characters" and getattr(self, "_char_page", None):
            self._char_page._refresh_list()
        if section == "spells" and getattr(self, "_spell_browser_page", None):
            self._spell_browser_page.refresh()
        if section == "decks" and getattr(self, "_deck_builder_page", None):
            self._deck_builder_page.refresh()

        if section == "boss_wiki" and tab:
            TAB_MAP = {
                "counters": 5,
                "guides":   6,
                "ocr":      7,
                "fetch":    0,
            }
            if tab in TAB_MAP:
                self.tabs.setCurrentIndex(TAB_MAP[tab])
            if tab == "fetch":
                self._fetch_all()
            elif tab == "ocr" and OCR_AVAILABLE and self.ocr_scanner:
                self.ocr_toggle.setChecked(True)

    # ═══════════════════════════════════════════════════════════════
    # SETTINGS / HUD PAGE
    # ═══════════════════════════════════════════════════════════════

    def _build_settings_page(self) -> QWidget:
        """Settings page with HUD overlay toggles and options."""
        page = QWidget()
        page.setStyleSheet("QWidget { background:#1a1a2e; }")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ──
        header_bar = QWidget()
        header_bar.setStyleSheet("background:#16213e; border-bottom: 1px solid #0f3460;")
        header_row = QHBoxLayout(header_bar)
        header_row.setContentsMargins(16, 10, 16, 10)

        back_btn = QPushButton("← Hub")
        back_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#4d96ff;border:1px solid #1f3460;"
            "border-radius:5px;padding:5px 14px;font-size:12px;}"
            "QPushButton:hover{background:#1f3460;}"
        )
        back_btn.clicked.connect(lambda: self._nav_to("hub"))
        header_row.addWidget(back_btn)
        header_row.addStretch()

        title_lbl = QLabel("⚙ HUD & Settings")
        title_lbl.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title_lbl.setStyleSheet("color:#e0e0e0; background:transparent;")
        title_lbl.setAlignment(Qt.AlignCenter)
        header_row.addWidget(title_lbl)
        header_row.addStretch()

        # Sub-page tabs
        for label, page_key in [("Main Settings", "main"),
                                 ("🔍 Icon Presets", "icon_presets"),
                                 ("🌍 World Settings", "world_settings")]:
            tab_btn = QPushButton(label)
            tab_btn.setCheckable(True)
            tab_btn.setStyleSheet(
                "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
                "border-radius:5px;padding:5px 12px;font-size:11px;margin-left:4px;}"
                "QPushButton:checked{background:#e94560;}"
                "QPushButton:hover:!checked{background:#1f4a80;}"
            )
            _pk = page_key
            tab_btn.clicked.connect(lambda checked, pk=_pk: self._switch_settings_subpage(pk))
            header_row.addWidget(tab_btn)
            if not hasattr(self, "_settings_tab_btns"):
                self._settings_tab_btns = {}
            self._settings_tab_btns[page_key] = tab_btn

        outer.addWidget(header_bar)

        # Sub-page stack — main settings scroll | icon presets | world settings
        self._settings_substack = QStackedWidget()
        self._settings_substack.setStyleSheet("background:#1a1a2e;")
        outer.addWidget(self._settings_substack, stretch=1)

        # ── Sub-page 0: main settings (the existing scroll body) ────────

        # ── Scroll body ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#1a1a2e;}")
        body = QWidget()
        body.setStyleSheet("background:#1a1a2e;")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(40, 24, 40, 40)
        body_layout.setSpacing(24)
        scroll.setWidget(body)
        self._settings_substack.addWidget(scroll)  # index 0: main


        # ══ HUB BUTTON VISIBILITY SECTION ════════════════════════════
        vis_group = self._make_settings_group("🧩 Hub Button Visibility",
            "Show or hide buttons on the main hub. Use ⭐ on any hub card to favourite it.")
        body_layout.addWidget(vis_group)

        self._vis_checks = {}
        vis_grid = QGridLayout()
        vis_grid.setSpacing(10)
        vis_group.layout().addLayout(vis_grid)

        # "hud_settings" is always visible — it's the only way to reach
        # this settings page. Never give the user a way to hide it.
        ALWAYS_VISIBLE = {"hud_settings"}

        buttons = getattr(self, "_hub_buttons", _load_hub_order())
        checkbox_i = 0
        for btn in buttons:
            if btn["id"] in ALWAYS_VISIBLE:
                continue
            chk = QCheckBox(f"{btn['icon']}  {btn['title']}")
            chk.setChecked(not btn.get("hidden", False))
            chk.setStyleSheet("color:#ccc; font-size:12px; background:transparent;")
            _bid = btn["id"]
            def _on_vis(state, bid=_bid):
                b = next((x for x in self._hub_buttons if x["id"] == bid), None)
                if b:
                    b["hidden"] = (state == 0)
                    _save_hub_order(self._hub_buttons)
                    self._rebuild_hub_grid()
            chk.stateChanged.connect(_on_vis)
            vis_grid.addWidget(chk, checkbox_i // 3, checkbox_i % 3)
            self._vis_checks[btn["id"]] = chk
            checkbox_i += 1

        # ══ HUD OVERLAYS SECTION ══════════════════════════════════
        hud_group = self._make_settings_group("🖥 HUD Overlays")
        body_layout.addWidget(hud_group)

        hud_grid = QGridLayout()
        hud_grid.setSpacing(12)
        hud_group.layout().addLayout(hud_grid)

        overlay_defs = [
            ("boss",    "👾 Boss Info",       "#e94560"),
            ("quest",   "🗺 Quest Tracker",   "#4d96ff"),
            ("counter", "⏱ Round Counter",   "#ffd93d"),
            ("guide",   "📖 Strategy Guide", "#c39bd3"),
            ("calc",    "🧮 Damage Calc",    "#6bcb77"),
        ]

        self._hud_toggle_btns = {}
        self._hud_ct_checks = {}

        for col, (key, label, color) in enumerate(overlay_defs):
            card = self._make_hud_overlay_card(key, label, color)
            hud_grid.addWidget(card, col // 4, col % 4)

        # ══ APPEARANCE SECTION ════════════════════════════════════
        appear_group = self._make_settings_group("🎨 Appearance")
        body_layout.addWidget(appear_group)

        # Opacity slider
        opacity_row = QHBoxLayout()
        opacity_lbl = QLabel("Overlay transparency:")
        opacity_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        opacity_row.addWidget(opacity_lbl)

        from PyQt5.QtWidgets import QSlider
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(10, 95)  # 10% to 95% transparent
        # Current value: HUD_BG_ALPHA maps 51/255 → ~80% transparent → slider val = 80
        current_alpha = getattr(__import__('hud_overlays', fromlist=['HUD_BG_ALPHA']),
                                'HUD_BG_ALPHA', 51) if HUD_AVAILABLE else 51
        # alpha 51/255 → 20% opaque → 80% transparent → slider = 80
        current_pct = round(100 - (current_alpha / 255 * 100))
        self._opacity_slider.setValue(current_pct)
        self._opacity_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:#0f3460; height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#e94560; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#e94560; border-radius:3px; }
        """)
        self._opacity_value_spin = QSpinBox()
        self._opacity_value_spin.setRange(10, 95)
        self._opacity_value_spin.setValue(current_pct)
        self._opacity_value_spin.setSuffix("% transparent")
        self._opacity_value_spin.setFixedWidth(130)
        self._opacity_value_spin.setStyleSheet("""
            QSpinBox {
                background:#0a1628; color:#ccc; border:1px solid #0f3460;
                border-radius:4px; font-size:12px; padding:2px 4px;
            }
            QSpinBox:focus { border-color:#e94560; }
            QSpinBox::up-button, QSpinBox::down-button { width:0; height:0; border:none; }
        """)
        self._opacity_value_spin.setAlignment(Qt.AlignCenter)
        # Slider → SpinBox sync
        self._opacity_slider.valueChanged.connect(
            lambda v: (
                self._opacity_value_spin.blockSignals(True),
                self._opacity_value_spin.setValue(v),
                self._opacity_value_spin.blockSignals(False),
            )
        )
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        # SpinBox → Slider sync
        self._opacity_value_spin.valueChanged.connect(
            lambda v: (
                self._opacity_slider.blockSignals(True),
                self._opacity_slider.setValue(v),
                self._opacity_slider.blockSignals(False),
            )
        )
        self._opacity_value_spin.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self._opacity_slider, stretch=1)
        opacity_row.addWidget(self._opacity_value_spin)
        appear_group.layout().addLayout(opacity_row)

        appear_note = QLabel(
            "⚠  Transparency changes apply to newly opened overlays. "
            "Toggle an overlay off and on to see the change."
        )
        appear_note.setStyleSheet("color:#555; font-size:11px; background:transparent;")
        appear_note.setWordWrap(True)
        appear_group.layout().addWidget(appear_note)

        # ══ DISPLAY SCALE SECTION ══════════════════════════════════
        if UI_SCALE_AVAILABLE and ui_scale is not None:
            body_layout.addWidget(self._build_display_scale_section())

        # ══ CLOUDFLARE ALERT SOUND SECTION ═════════════════════════
        if HUD_AVAILABLE and overlay_settings is not None:
            body_layout.addWidget(self._build_cf_sound_section())

        # ══ OCR SETTINGS SECTION ═══════════════════════════════════
        if OCR_AVAILABLE:
            ocr_group = self._make_settings_group("👾 OCR Settings")
            body_layout.addWidget(ocr_group)

            ocr_mode_row = QHBoxLayout()
            ocr_mode_lbl = QLabel("Matching mode:")
            ocr_mode_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
            ocr_mode_row.addWidget(ocr_mode_lbl)

            self._ocr_mode_combo = QComboBox()
            self._ocr_mode_combo.addItem("Dynamic  (fuzzy — may show near-matches)", OCR_MODE_DYNAMIC)
            self._ocr_mode_combo.addItem("Strict  (exact name match only)", OCR_MODE_STRICT)
            # Restore saved mode
            current_mode = (overlay_settings.get_ocr_mode()
                            if overlay_settings is not None else OCR_MODE_DYNAMIC)
            idx = self._ocr_mode_combo.findData(current_mode)
            if idx >= 0:
                self._ocr_mode_combo.setCurrentIndex(idx)
            self._ocr_mode_combo.setStyleSheet(
                "QComboBox{background:#0f3460;color:#e0e0e0;border:1px solid #1f3460;"
                "border-radius:6px;padding:6px 12px;font-size:12px;min-width:260px;}"
                "QComboBox:hover{border-color:#e94560;}"
                "QComboBox::drop-down{border:none;}"
                "QComboBox QAbstractItemView{background:#0f3460;color:#e0e0e0;"
                "selection-background-color:#e94560;border:1px solid #1f3460;}"
            )
            self._ocr_mode_combo.currentIndexChanged.connect(self._on_ocr_mode_changed)
            ocr_mode_row.addWidget(self._ocr_mode_combo)
            ocr_mode_row.addStretch()
            ocr_group.layout().addLayout(ocr_mode_row)

            ocr_mode_note = QLabel(
                "<b>Dynamic</b>: Uses fuzzy matching (Levenshtein distance) to detect bosses even "
                "when OCR misreads a character. May produce false positives.<br>"
                "<b>Strict</b>: Only shows a boss when the OCR text exactly matches a boss name "
                "(case-insensitive). Fewer false positives but may miss OCR typos."
            )
            ocr_mode_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
            ocr_mode_note.setWordWrap(True)
            ocr_group.layout().addWidget(ocr_mode_note)

            # ── Spell card image-scan strength ─────────────────────────
            # Separate from the boss matching-mode above: this controls how
            # hard EasyOCR works when reading spell-card images during a
            # fetch / reparse. Saved immediately; the scraper subprocess
            # reads it from hud_settings.json on its next run.
            if overlay_settings is not None:
                strength_row = QHBoxLayout()
                strength_lbl = QLabel("Spell scan strength:")
                strength_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
                strength_row.addWidget(strength_lbl)

                self._spell_ocr_strength_combo = QComboBox()
                self._spell_ocr_strength_combo.addItem("Standard  (fastest — default)", "standard")
                self._spell_ocr_strength_combo.addItem("Enhanced  (upscales small text)", "enhanced")
                self._spell_ocr_strength_combo.addItem("High  (slower — catches faint text)", "high")
                self._spell_ocr_strength_combo.addItem("Maximum  (slowest — most thorough)", "maximum")
                cur_strength = overlay_settings.get_spell_ocr_strength()
                s_idx = self._spell_ocr_strength_combo.findData(cur_strength)
                if s_idx >= 0:
                    self._spell_ocr_strength_combo.setCurrentIndex(s_idx)
                self._spell_ocr_strength_combo.setStyleSheet(
                    "QComboBox{background:#0f3460;color:#e0e0e0;border:1px solid #1f3460;"
                    "border-radius:6px;padding:6px 12px;font-size:12px;min-width:260px;}"
                    "QComboBox:hover{border-color:#e94560;}"
                    "QComboBox::drop-down{border:none;}"
                    "QComboBox QAbstractItemView{background:#0f3460;color:#e0e0e0;"
                    "selection-background-color:#e94560;border:1px solid #1f3460;}"
                )
                self._spell_ocr_strength_combo.currentIndexChanged.connect(
                    self._on_spell_ocr_strength_changed
                )
                strength_row.addWidget(self._spell_ocr_strength_combo)
                strength_row.addStretch()
                ocr_group.layout().addLayout(strength_row)

                strength_note = QLabel(
                    "How hard the scanner works when reading spell-card images while "
                    "fetching or reparsing spells. Stronger levels enlarge the card and "
                    "lower the detection thresholds so tiny or faint badges (pip cost, "
                    "damage numbers, %) are read more reliably — but each scan takes longer. "
                    "Takes effect on the next fetch / reparse."
                )
                strength_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
                strength_note.setWordWrap(True)
                ocr_group.layout().addWidget(strength_note)

                # ── Toggle: use description text to assign icons ───────────
                self._spell_icons_from_desc_cb = QCheckBox(
                    "Assign icons from description text (not just the card image)"
                )
                self._spell_icons_from_desc_cb.setChecked(
                    overlay_settings.get_spell_ocr_icons_from_description()
                )
                self._spell_icons_from_desc_cb.setStyleSheet("background:transparent;")
                self._spell_icons_from_desc_cb.toggled.connect(
                    self._on_spell_icons_from_desc_changed
                )
                ocr_group.layout().addWidget(self._spell_icons_from_desc_cb)

                icons_note = QLabel(
                    "When on, the Detected Icons legend is filled from the wiki "
                    "description and card fields as well as the image scan — so a word "
                    "like \"caster\" in the description adds a Caster icon even if it "
                    "isn't drawn on the card. Turn this off to assign icons from the "
                    "true card-image scan only (text OCR + visual matching). "
                    "Takes effect on the next fetch / reparse."
                )
                icons_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
                icons_note.setWordWrap(True)
                ocr_group.layout().addWidget(icons_note)

                # ── Visual icon sensitivity (match-confidence threshold) ───
                from PyQt5.QtWidgets import QSlider
                conf_row = QHBoxLayout()
                conf_lbl = QLabel("Visual icon sensitivity:")
                conf_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
                conf_row.addWidget(conf_lbl)

                cur_conf = overlay_settings.get_spell_icon_visual_confidence()
                self._spell_icon_conf_slider = QSlider(Qt.Horizontal)
                # Slider is the match-confidence % (60–95). Higher threshold =
                # stricter = fewer icons, so a higher slider value = LESS
                # sensitive. We label the ends to make that direction clear.
                self._spell_icon_conf_slider.setRange(60, 95)
                self._spell_icon_conf_slider.setValue(int(round(cur_conf * 100)))
                self._spell_icon_conf_slider.setFixedWidth(200)
                self._spell_icon_conf_slider.setStyleSheet("""
                    QSlider::groove:horizontal { background:#0f3460; height:6px; border-radius:3px; }
                    QSlider::handle:horizontal { background:#e94560; width:16px; height:16px;
                        margin:-5px 0; border-radius:8px; }
                    QSlider::sub-page:horizontal { background:#e94560; border-radius:3px; }
                """)
                conf_row.addWidget(self._spell_icon_conf_slider)

                self._spell_icon_conf_value = QLabel(f"{int(round(cur_conf * 100))}% confidence")
                self._spell_icon_conf_value.setStyleSheet(
                    "color:#e0e0e0; font-size:12px; background:transparent; min-width:110px;")
                conf_row.addWidget(self._spell_icon_conf_value)
                conf_row.addStretch()
                ocr_group.layout().addLayout(conf_row)

                self._spell_icon_conf_slider.valueChanged.connect(
                    self._on_spell_icon_conf_changed
                )

                conf_note = QLabel(
                    "How confident a template match must be to count as a detected icon "
                    "on the card. Lower = detects MORE icons (and more wrong ones); higher "
                    "= stricter, fewer icons. This is the main dial for card-image-only "
                    "detection (it doesn't use OCR strength). Default 85%. "
                    "Takes effect on the next fetch / reparse."
                )
                conf_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
                conf_note.setWordWrap(True)
                ocr_group.layout().addWidget(conf_note)

        # ══ KEYBINDS SECTION ══════════════════════════════════════
        if KEYBINDS_AVAILABLE and self.keybind_manager:
            kb_group = self._make_settings_group("⌨ Overlay Keybinds")
            body_layout.addWidget(kb_group)
            kb_widget = KeybindSettingsWidget(self.keybind_manager)
            kb_group.layout().addWidget(kb_widget)

        # ══ BACKUP & RESTORE SECTION ══════════════════════════════
        if EXPORTER_AVAILABLE:
            backup_group = self._make_settings_group("💾 Backup & Restore")
            body_layout.addWidget(backup_group)

            backup_note = QLabel(
                "Save a full backup of your data — bosses, round counters, strategy guides, "
                "gear loadouts, quest progress, decks, calculator presets and characters — "
                "in a single file. When restoring, you can choose exactly which parts to bring back.<br>"
                "To share a single loadout, guide, counter or deck, use its 🔗 button instead."
            )
            backup_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
            backup_note.setWordWrap(True)
            backup_group.layout().addWidget(backup_note)

            bk_row = QHBoxLayout()
            bk_row.setSpacing(10)

            backup_btn = QPushButton("💾 Backup…")
            backup_btn.setToolTip("Save a full backup of all your data to a file")
            backup_btn.setStyleSheet(
                "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
                "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#27ae60;}"
            )
            backup_btn.clicked.connect(self._run_backup)
            bk_row.addWidget(backup_btn)

            restore_btn = QPushButton("📥 Import from Backup…")
            restore_btn.setToolTip(
                "Open a backup file and choose which parts to restore"
            )
            restore_btn.setStyleSheet(
                "QPushButton{background:#1a3a1a;color:#66ff99;border:1px solid #27ae60;"
                "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#27ae60;color:#fff;}"
            )
            restore_btn.clicked.connect(self._run_import)
            bk_row.addWidget(restore_btn)
            bk_row.addStretch()
            backup_group.layout().addLayout(bk_row)

        # ══ MAINTENANCE SECTION ══════════════════════════════════
        maint_group = self._make_settings_group("🛠 Maintenance")
        body_layout.addWidget(maint_group)

        maint_note = QLabel(
            "Clear log files to free disk space, or wipe cached data to force a "
            "fresh scrape.  Boss data includes all boss stats, cheats and drops. "
            "World quest data includes quest lists and areas but not your user markers."
        )
        maint_note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
        maint_note.setWordWrap(True)
        maint_group.layout().addWidget(maint_note)

        maint_grid = QGridLayout()
        maint_grid.setSpacing(10)

        def _maint_btn_rgba(hex_color: str, alpha_hex: str) -> str:
            h = hex_color.lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{int(alpha_hex, 16)})"

        def _maint_btn(label: str, tip: str, color: str, callback) -> QPushButton:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setStyleSheet(
                f"QPushButton{{background:#1a1a2e;color:{color};"
                f"border:1px solid {_maint_btn_rgba(color, '55')};border-radius:6px;"
                f"padding:8px 14px;font-size:12px;font-weight:bold;}}"
                f"QPushButton:hover{{background:{color};color:#fff;}}"
            )
            btn.clicked.connect(callback)
            return btn

        maint_buttons = [
            ("🗒 Clear Log Files",
             "Delete boss_wiki.log and db_builder.log",
             "#4d96ff",
             self._clear_log_files),
            ("🌍 Delete World Quest Cache",
             "Remove all scraped quest worlds, areas and quest lists. Your user markers (notes/completion) are preserved.",
             "#ffd93d",
             self._clear_quest_cache),
            ("👾 Delete Boss Data Cache",
             "Remove all scraped boss entries from the local database. Round counters, guides and gear loadouts are preserved.",
             "#e94560",
             self._clear_boss_cache),
            ("✨ Delete Spell Cache",
             "Delete everything downloaded for spells — wikitext, rendered-HTML and card images (spell_cache, spell_html_cache, spell_images, spell_images_fusion) — forcing a clean re-download on next fetch. Fixes wrong card art. Your spell database rows are kept.",
             "#c39bd3",
             self._clear_spell_cache),
        ]

        for i, (lbl, tip, color, cb) in enumerate(maint_buttons):
            maint_grid.addWidget(_maint_btn(lbl, tip, color, cb), i // 3, i % 3)

        maint_group.layout().addLayout(maint_grid)

        # ══ UPDATES SECTION ══════════════════════════════════════
        update_group = self._make_settings_group("🔄 Updates")
        body_layout.addWidget(update_group)

        # Current version label
        ver_row = QHBoxLayout()
        ver_lbl = QLabel(f"Current version:  <b>{APP_VERSION}</b>")
        ver_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        ver_row.addWidget(ver_lbl)
        ver_row.addStretch()
        update_group.layout().addLayout(ver_row)

        # Check / Update button + status label
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self._update_btn = QPushButton("Check for Update")
        self._update_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;}"
            "QPushButton:disabled{background:#1a1a2e;color:#555;}"
        )
        self._update_btn.clicked.connect(self._check_for_updates)
        btn_row.addWidget(self._update_btn)

        self._update_status_lbl = QLabel("")
        self._update_status_lbl.setStyleSheet("color:#888; font-size:12px; background:transparent;")
        self._update_status_lbl.setWordWrap(True)
        btn_row.addWidget(self._update_status_lbl, stretch=1)
        update_group.layout().addLayout(btn_row)

        # Auto-update toggle (off by default): when on, an available update is
        # downloaded automatically in the background at startup.
        self._auto_update_chk = QCheckBox("Auto update — download new versions automatically on startup")
        self._auto_update_chk.setChecked(
            overlay_settings.get_auto_update() if overlay_settings is not None else False)
        self._auto_update_chk.setStyleSheet("color:#ccc; font-size:12px; background:transparent;")
        self._auto_update_chk.toggled.connect(self._on_auto_update_toggled)
        update_group.layout().addWidget(self._auto_update_chk)

        update_note = QLabel(
            "Updates are pulled from GitHub via <code>git</code>. "
            "On first use the app will automatically connect your folder to the repo — "
            "even if you downloaded a ZIP. Git must be installed "
            "(<a style='color:#4d96ff' href='https://git-scm.com'>git-scm.com</a>). "
            "Your databases, settings and user data are never overwritten."
        )
        update_note.setOpenExternalLinks(True)
        update_note.setStyleSheet("color:#555; font-size:11px; background:transparent;")
        update_note.setWordWrap(True)
        update_group.layout().addWidget(update_note)

        if not GITHUB_REPO:
            no_repo_lbl = QLabel(
                "⚠  No GitHub repository configured. Set <code>GITHUB_REPO</code> "
                "at the top of boss_wiki.py (e.g. <code>\"YourUser/Wizard101Companion\"</code>)."
            )
            no_repo_lbl.setStyleSheet("color:#e94560; font-size:11px; background:transparent;")
            no_repo_lbl.setWordWrap(True)
            update_group.layout().addWidget(no_repo_lbl)

        body_layout.addStretch()

        # ── Sub-page 1: Icon Preset Manager ─────────────────────
        self._icon_preset_mgr = IconPresetManagerWidget(self.conn, self)
        self._settings_substack.addWidget(self._icon_preset_mgr)  # index 1

        # ── Sub-page 2: World Settings (embedded) ────────────────
        from PyQt5.QtWidgets import QSplitter
        ws_container = QWidget()
        ws_container.setStyleSheet('background:#1a1a2e;')
        ws_layout = QVBoxLayout(ws_container)
        ws_layout.setContentsMargins(20, 16, 20, 16)
        ws_info = QLabel(
            '🌍  World Settings manage the world list used across the app.\n'
            'Click Open World Settings to manage worlds.'
        )
        ws_info.setStyleSheet('color:#aaa;font-size:12px;background:transparent;')
        ws_info.setWordWrap(True)
        ws_layout.addWidget(ws_info)
        ws_open_btn = QPushButton('🌍 Open World Settings')
        ws_open_btn.setStyleSheet(
            'QPushButton{background:#0f3460;color:#e0e0e0;border:none;'
            'border-radius:6px;padding:8px 18px;font-size:12px;}'
            'QPushButton:hover{background:#4d96ff;}'
        )
        ws_open_btn.clicked.connect(self._open_world_settings)
        ws_layout.addWidget(ws_open_btn, 0, Qt.AlignLeft)
        ws_layout.addStretch()
        self._settings_substack.addWidget(ws_container)  # index 2

        # Select main settings tab by default
        if hasattr(self, '_settings_tab_btns'):
            self._settings_tab_btns.get('main', QPushButton()).setChecked(True)

        return page

    def _clear_log_files(self):
        """Delete log files on disk. Re-opens them fresh via logging handlers."""
        import logging as _logging
        log_dir = os.path.dirname(os.path.abspath(__file__))
        log_files = [
            os.path.join(log_dir, 'boss_wiki.log'),
            os.path.join(log_dir, 'db_builder.log'),
        ]
        cleared, missing = [], []

        # Close and reopen all FileHandlers so the files can be truncated
        root = _logging.getLogger()
        for handler in list(root.handlers) + list(logger.handlers):
            if isinstance(handler, _logging.FileHandler):
                handler.close()

        for path in log_files:
            if os.path.exists(path):
                try:
                    open(path, 'w').close()   # truncate to zero bytes
                    cleared.append(os.path.basename(path))
                except Exception as e:
                    missing.append(f"{os.path.basename(path)}: {e}")
            else:
                missing.append(f"{os.path.basename(path)} (not found)")

        # Re-open any FileHandlers we closed
        for handler in list(root.handlers) + list(logger.handlers):
            if isinstance(handler, _logging.FileHandler):
                try:
                    handler.stream = open(handler.baseFilename, handler.mode,
                                          encoding=handler.encoding or 'utf-8')
                except Exception:
                    pass

        parts = []
        if cleared:
            parts.append(f"Cleared: {', '.join(cleared)}")
        if missing:
            parts.append(f"Skipped: {', '.join(missing)}")
        msg = "\n".join(parts) if parts else "Nothing to clear."
        QMessageBox.information(self, "Clear Log Files", msg)
        self.status_bar.showMessage("Log files cleared.", 4000)

    def _clear_quest_cache(self):
        """Delete all quest world / area / quest rows, preserving user markers."""
        count = self.conn.execute("SELECT COUNT(*) FROM quest_worlds").fetchone()[0]
        if count == 0:
            QMessageBox.information(self, "World Quest Cache",
                                    "No quest data in the database — nothing to clear.")
            return

        box = QMessageBox(self)
        box.setWindowTitle("Delete World Quest Cache")
        box.setText(f"Delete all <b>{count}</b> scraped quest world(s)?")
        box.setInformativeText(
            "This removes all quest worlds, areas and quest lists.\n"
            "Your notes and completion markers are preserved.\n\n"
            "You can re-scrape worlds from the Quest Tracker at any time."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setIcon(QMessageBox.NoIcon)
        if box.exec_() != QMessageBox.Yes:
            return

        # Preserve markers by detaching them first (quest rows cascade-delete them)
        # We back up the marker data keyed by quest name+world, then restore after.
        # For simplicity: just delete everything except markers on quests we keep —
        # since quests are being deleted, markers go too (ON DELETE CASCADE).
        self.conn.execute("DELETE FROM quest_worlds")
        self.conn.commit()

        self.status_bar.showMessage(
            f"Deleted {count} quest world(s) from the cache.", 5000
        )
        QMessageBox.information(self, "World Quest Cache",
                                f"Deleted {count} quest world(s).")

    def _clear_boss_cache(self):
        """Delete all boss rows from the database."""
        count = self.conn.execute(
            "SELECT COUNT(*) FROM bosses WHERE is_active=1"
        ).fetchone()[0]
        if count == 0:
            QMessageBox.information(self, "Boss Data Cache",
                                    "No bosses in the database — nothing to clear.")
            return

        box = QMessageBox(self)
        box.setWindowTitle("Delete Boss Data Cache")
        box.setText(f"Delete all <b>{count}</b> cached boss{'es' if count != 1 else ''}?")
        box.setInformativeText(
            "This removes all scraped boss stats, cheats, spells and drops.\n"
            "Round counters, strategy guides and gear loadouts are preserved.\n\n"
            "You can re-scrape bosses using 'Fetch ALL Bosses' in the Boss Wiki."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setIcon(QMessageBox.NoIcon)
        if box.exec_() != QMessageBox.Yes:
            return

        self.conn.execute("DELETE FROM bosses")
        # Rebuild the FTS index to stay consistent
        try:
            self.conn.execute("DELETE FROM bosses_fts")
        except Exception:
            pass
        self.conn.commit()

        # Refresh in-memory name list and OCR scanner
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self._refresh_tree()

        self.status_bar.showMessage(
            f"Deleted {count} boss{'es' if count != 1 else ''} from the cache.", 5000
        )
        QMessageBox.information(self, "Boss Data Cache",
                                f"Deleted {count} boss{'es' if count != 1 else ''}.")

    def _clear_spell_cache(self):
        """
        Delete everything downloaded for spells — cached wikitext, rendered
        HTML, and the card images (spell_cache, spell_html_cache, spell_images,
        spell_images_fusion) — so the next fetch re-pulls it all cleanly. This
        is the way to replace card images the wiki previously linked wrong: a
        normal fetch reuses an existing image file, so the images must be
        removed here to force a fresh download. Spell database rows are kept;
        their image will simply re-download on the next Fetch / Download Images.
        """
        from pathlib import Path as _Path
        app_dir = _Path(__file__).parent
        cache_dirs = [
            app_dir / "spell_cache",         # raw wikitext
            app_dir / "spell_html_cache",    # rendered HTML (training/fusion)
            app_dir / "spell_images",        # downloaded card images
            app_dir / "spell_images_fusion", # fusion reagent image fallbacks
        ]
        files = []
        for d in cache_dirs:
            if d.exists():
                files.extend(p for p in d.iterdir() if p.is_file())
        if not files:
            QMessageBox.information(self, "Spell Cache",
                                    "No cached spell files — nothing to clear.")
            return

        n_img = sum(1 for f in files if f.suffix.lower() == ".png"
                    and f.parent.name in ("spell_images", "spell_images_fusion"))
        box = QMessageBox(self)
        box.setWindowTitle("Delete Spell Cache")
        box.setText(f"Delete all <b>{len(files)}</b> cached spell file(s)?")
        box.setInformativeText(
            f"This removes cached wikitext, rendered-HTML and {n_img} card "
            "image(s) — everything downloaded for spells.\n\n"
            "Spell database entries are kept; images and text re-download on "
            "the next Fetch / Download Images (which fixes any wrong card art)."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setIcon(QMessageBox.NoIcon)
        if box.exec_() != QMessageBox.Yes:
            return

        removed = 0
        for f in files:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass

        self.status_bar.showMessage(
            f"Deleted {removed} cached spell file(s).", 5000
        )
        QMessageBox.information(self, "Spell Cache",
                                f"Deleted {removed} cached spell file(s).\n\n"
                                "Run a Fetch or Download Images to re-pull them.")

    # ── Startup / background update check ──────────────────────────────────

    def _startup_update_check(self):
        """Kick off a silent background update check (existing repos only)."""
        if not GITHUB_REPO:
            return
        git_exe = self._find_git()
        if not git_exe:
            return
        if self._update_check_worker is not None and self._update_check_worker.isRunning():
            return
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        remote_url = f"https://github.com/{GITHUB_REPO}.git"
        self._update_check_worker = _GitUpdateWorker(
            git_exe, repo_dir, remote_url, mode="check", parent=self)
        self._update_check_worker.checked.connect(self._on_startup_check_done)
        self._update_check_worker.start()

    def _on_startup_check_done(self, available, local_sha, remote_sha, error):
        if error or not available:
            # Silent on error (offline, not-a-repo, etc.) — nothing to show.
            return
        self._update_available = True
        self._refresh_hub_update_dot()
        # Reflect it on the Settings page if that button exists yet.
        if hasattr(self, "_update_status_lbl"):
            self._update_ready = True
            self._update_btn.setText("⬇ Update")
            self._update_btn.setStyleSheet(
                "QPushButton{background:#27ae60;color:#fff;border:none;"
                "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#2ecc71;}"
                "QPushButton:disabled{background:#1a1a2e;color:#555;}"
            )
            self._update_status_lbl.setStyleSheet(
                "color:#ffd93d; font-size:12px; background:transparent;")
            self._update_status_lbl.setText(
                f"🆕 A new version is available!  Local {local_sha[:8]} → "
                f"Remote {remote_sha[:8]}")

        if self._auto_update:
            self._start_auto_pull()

    def _start_auto_pull(self):
        """Auto-download an available update in the background."""
        if self._update_pull_worker is not None and self._update_pull_worker.isRunning():
            return
        git_exe = self._find_git()
        if not git_exe:
            return
        try:
            self._ensure_gitignore()
        except Exception:
            pass
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        remote_url = f"https://github.com/{GITHUB_REPO}.git" if GITHUB_REPO else ""
        self.status_bar.showMessage("⏳ Auto-update: downloading new version…", 8000)
        self._update_pull_worker = _GitUpdateWorker(
            git_exe, repo_dir, remote_url, mode="pull", parent=self)
        self._update_pull_worker.pulled.connect(self._on_auto_pull_done)
        self._update_pull_worker.start()

    def _on_auto_pull_done(self, ok, error):
        if ok:
            self.status_bar.showMessage(
                "✅ Update downloaded — restart the app to use the new version.", 0)
            if hasattr(self, "_update_status_lbl"):
                self._update_status_lbl.setStyleSheet(
                    "color:#27ae60; font-size:12px; background:transparent;")
                self._update_status_lbl.setText(
                    "✅ Auto-update applied. Please restart the app.")
                self._update_ready = False
                self._update_btn.setText("Check for Update")
        else:
            self.status_bar.showMessage(
                f"⚠ Auto-update failed: {error or 'unknown error'}", 10000)

    def _on_auto_update_toggled(self, checked: bool):
        """Persist the auto-update preference."""
        self._auto_update = bool(checked)
        if overlay_settings is not None:
            overlay_settings.set_auto_update(bool(checked))
        # If they just turned it on and we already know an update is waiting,
        # start downloading it now.
        if checked and getattr(self, "_update_available", False):
            self._start_auto_pull()

    def _refresh_hub_update_dot(self):
        """Rebuild the hub grid so the update dot on HUD & Settings appears/clears."""
        if hasattr(self, "_hub_grid_container"):
            try:
                self._rebuild_hub_grid()
            except Exception:
                pass

    # ── GitHub Update ─────────────────────────────────────────────────────────

    # User data files that must NEVER be touched by git.
    # Written to .gitignore before any git reset/pull operation.
    _USER_DATA_PATTERNS = [
        "boss_wiki.db", "boss_wiki.db-shm", "boss_wiki.db-wal",
        "hud_settings.json", "keybinds.json", "world_order.json",
        "boss_wiki.log", "db_builder.log",
        "quest_debug/", "wikitext_cache/", "__pycache__/",
        "spell_cache/", "spell_html_cache/",
        "spell_images/", "spell_images_fusion/",
        "venv/", "mingit/",
    ]

    def _find_git(self) -> str:
        """
        Return the path to a working git executable.
        Tries system PATH first, then the local mingit/ folder
        that install.bat may have downloaded.
        """
        import shutil
        if shutil.which("git"):
            return "git"
        app_dir = os.path.dirname(os.path.abspath(__file__))
        local_git = os.path.join(app_dir, "mingit", "cmd", "git.exe")
        if os.path.isfile(local_git):
            return local_git
        return ""

    def _ensure_gitignore(self):
        """
        Create or update .gitignore in the app directory so that user data
        files are never staged, committed, or overwritten by git operations.
        """
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        gi_path = os.path.join(repo_dir, ".gitignore")
        existing = set()
        if os.path.isfile(gi_path):
            with open(gi_path, "r", encoding="utf-8") as f:
                existing = {line.strip() for line in f if line.strip() and not line.startswith("#")}
        added = []
        for pat in self._USER_DATA_PATTERNS:
            if pat not in existing:
                added.append(pat)
        if added:
            with open(gi_path, "a", encoding="utf-8") as f:
                if existing:
                    f.write("\n")
                f.write("# Wizard101 Companion - user data (auto-generated)\n")
                for pat in added:
                    f.write(pat + "\n")

    def _check_for_updates(self):
        """
        Check the GitHub repo for updates via git and, if one exists, pull it.

        If the folder is not yet a git repo (user downloaded a ZIP), the updater
        automatically runs git init + git remote add + git fetch + git checkout
        to bootstrap it into a working repo without losing any user data files.
        Uses git checkout instead of git reset to avoid unlinking locked files.
        """
        import subprocess

        repo_dir = os.path.dirname(os.path.abspath(__file__))
        remote_url = f"https://github.com/{GITHUB_REPO}.git" if GITHUB_REPO else ""
        git_exe = self._find_git()

        def _set_status(msg: str, color: str = "#888"):
            self._update_status_lbl.setStyleSheet(
                f"color:{color}; font-size:12px; background:transparent;"
            )
            self._update_status_lbl.setText(msg)
            QApplication.processEvents()

        def _run(args: list, timeout: int = 30) -> tuple:
            try:
                result = subprocess.run(
                    args, cwd=repo_dir, capture_output=True,
                    text=True, timeout=timeout,
                )
                return result.returncode, result.stdout.strip(), result.stderr.strip()
            except FileNotFoundError:
                return -1, "", "git not found"
            except subprocess.TimeoutExpired:
                return -1, "", "Timed out"
            except Exception as exc:
                return -1, "", str(exc)

        # ── Pre-checks ─────────────────────────────────────────────
        if not GITHUB_REPO:
            _set_status(
                "❌ No GitHub repo configured. Set GITHUB_REPO at the top of boss_wiki.py.",
                "#e94560",
            )
            return

        if not git_exe:
            _set_status(
                "❌ git is not installed. Run install.bat to set up MinGit,\n"
                "or install Git from https://git-scm.com.",
                "#e94560",
            )
            return

        self._update_btn.setEnabled(False)

        # Ensure .gitignore protects user data before ANY git operation
        try:
            self._ensure_gitignore()
        except Exception:
            pass

        # If button says "Update", user already confirmed — apply it
        if getattr(self, '_update_ready', False):
            _set_status("⏳ Downloading update…", "#888")

            # First try clean pull
            rc, out, err = _run([git_exe, "pull", "--ff-only", "origin"], timeout=60)
            if rc != 0:
                # Stash any local changes, then pull
                _set_status("⏳ Stashing local changes and retrying…", "#888")
                _run([git_exe, "stash", "--include-untracked"])
                rc, out, err = _run([git_exe, "pull", "--ff-only", "origin"], timeout=60)
                if rc != 0:
                    # Last resort: fetch + checkout
                    _set_status("⏳ Fetching and checking out latest…", "#888")
                    _run([git_exe, "fetch", "origin"], timeout=60)
                    rc, _, err = _run([git_exe, "checkout", "FETCH_HEAD", "--", "."], timeout=60)
                    if rc != 0:
                        _set_status(f"❌ Update failed.\n{err}", "#e94560")
                        self._update_btn.setEnabled(True)
                        return

            _set_status(
                "✅ Update applied!  Please restart the app to use the new version.",
                "#27ae60",
            )
            self._update_ready = False
            self._update_available = False
            self._refresh_hub_update_dot()
            self._update_btn.setText("Check for Update")
            self._update_btn.setStyleSheet(
                "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
                "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#e94560;}"
                "QPushButton:disabled{background:#1a1a2e;color:#555;}"
            )
            self._update_btn.setEnabled(True)
            return

        _set_status("⏳ Checking for updates…", "#888")

        # 1. Check if this is a git repo — if not, bootstrap it
        rc, out, err = _run([git_exe, "rev-parse", "--is-inside-work-tree"])
        is_fresh = (rc != 0 or out != "true")

        if is_fresh:
            _set_status("⏳ First-time setup — connecting to GitHub…", "#4d96ff")

            # git init
            rc, _, err = _run([git_exe, "init"])
            if rc != 0:
                _set_status(f"❌ git init failed:\n{err}", "#e94560")
                self._update_btn.setEnabled(True)
                return

            # Re-ensure .gitignore is tracked right away
            try:
                self._ensure_gitignore()
                _run([git_exe, "add", ".gitignore"])
            except Exception:
                pass

            # git remote add origin
            _run([git_exe, "remote", "remove", "origin"])
            rc, _, err = _run([git_exe, "remote", "add", "origin", remote_url])
            if rc != 0:
                _set_status(f"❌ Could not set remote origin:\n{err}", "#e94560")
                self._update_btn.setEnabled(True)
                return

            # Fetch
            _set_status("⏳ Downloading repository info…", "#4d96ff")
            rc, _, err = _run([git_exe, "fetch", "--quiet", "origin"], timeout=120)
            if rc != 0:
                _set_status(
                    f"❌ Could not reach GitHub — check your internet.\n{err}",
                    "#e94560",
                )
                self._update_btn.setEnabled(True)
                return

            # Detect default branch
            rc, branches, _ = _run([git_exe, "branch", "-r"])
            default_branch = "main"
            if "origin/main" in branches:
                default_branch = "main"
            elif "origin/master" in branches:
                default_branch = "master"

            # Use checkout instead of reset --hard to avoid unlinking locked files.
            # .gitignore ensures user data files are never touched.
            _set_status("⏳ Syncing files…", "#4d96ff")

            rc, _, err = _run([git_exe, "checkout", "-B", default_branch,
                               f"origin/{default_branch}"])
            if rc != 0:
                # If checkout fails (locked files), try file-by-file approach
                _run([git_exe, "branch", "-M", default_branch])
                _run([git_exe, "branch", f"--set-upstream-to=origin/{default_branch}"])
                rc2, _, err2 = _run([git_exe, "checkout", f"origin/{default_branch}",
                                     "--", "."], timeout=60)
                if rc2 != 0:
                    _set_status(
                        f"❌ Could not sync files:\n{err2 or err}\n\n"
                        "Try closing the app, deleting boss_wiki.db, and retrying.",
                        "#e94560",
                    )
                    self._update_btn.setEnabled(True)
                    return

            _set_status(
                "✅ Repository connected!  You are now on the latest version.\n"
                "    Future updates will be incremental.",
                "#27ae60",
            )
            self._update_btn.setEnabled(True)
            return

        # ── Existing repo: normal fetch + compare ──────────────────
        _run([git_exe, "remote", "set-url", "origin", remote_url])

        _set_status("⏳ Contacting GitHub…", "#888")
        rc, out, err = _run([git_exe, "fetch", "--quiet", "origin"])
        if rc != 0:
            _set_status(
                f"❌ Could not reach GitHub — check your internet.\n{err}",
                "#e94560",
            )
            self._update_btn.setEnabled(True)
            return

        rc_l, local_sha, _ = _run([git_exe, "rev-parse", "HEAD"])
        rc_r, remote_sha, _ = _run([git_exe, "rev-parse", "FETCH_HEAD"])

        # If HEAD doesn't exist, this repo was partially initialized
        # (e.g. git init + fetch ran but checkout failed on a previous attempt).
        # Fix it by checking out the remote branch now.
        if rc_l != 0:
            _set_status("⏳ Completing first-time setup…", "#4d96ff")
            # Detect default branch
            rc_b, branches, _ = _run([git_exe, "branch", "-r"])
            default_branch = "main"
            if "origin/main" in branches:
                default_branch = "main"
            elif "origin/master" in branches:
                default_branch = "master"

            rc_co, _, err_co = _run([git_exe, "checkout", "-B", default_branch,
                                     f"origin/{default_branch}"])
            if rc_co != 0:
                # Fallback: file-by-file checkout
                _run([git_exe, "branch", "-M", default_branch])
                _run([git_exe, "branch", f"--set-upstream-to=origin/{default_branch}"])
                rc_co2, _, err_co2 = _run([git_exe, "checkout",
                                           f"origin/{default_branch}", "--", "."],
                                          timeout=60)
                if rc_co2 != 0:
                    _set_status(
                        f"❌ Could not complete setup:\n{err_co2 or err_co}",
                        "#e94560",
                    )
                    self._update_btn.setEnabled(True)
                    return

            _set_status(
                "✅ Repository connected!  You are now on the latest version.\n"
                "    Future updates will be incremental.",
                "#27ae60",
            )
            self._update_btn.setEnabled(True)
            return

        if rc_r != 0:
            _set_status("❌ Could not read remote revision info.", "#e94560")
            self._update_btn.setEnabled(True)
            return

        if local_sha == remote_sha:
            _set_status("✅ You are already on the latest version.", "#27ae60")
            self._update_available = False
            self._refresh_hub_update_dot()
            self._update_btn.setEnabled(True)
            return

        # Update available
        self._update_ready = True
        self._update_available = True
        self._refresh_hub_update_dot()
        self._update_btn.setText("⬇ Update")
        self._update_btn.setStyleSheet(
            "QPushButton{background:#27ae60;color:#fff;border:none;"
            "border-radius:6px;padding:8px 18px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#2ecc71;}"
            "QPushButton:disabled{background:#1a1a2e;color:#555;}"
        )
        _set_status(
            f"🆕 A new version is available!  Click \'Update\' to download it.\n"
            f"    Local: {local_sha[:8]}  →  Remote: {remote_sha[:8]}",
            "#ffd93d",
        )
        self._update_btn.setEnabled(True)

    # ── OCR mode ──────────────────────────────────────────────────────────────

    def _on_ocr_mode_changed(self, _index: int):
        """User changed OCR matching mode in settings."""
        if not hasattr(self, '_ocr_mode_combo'):
            return
        mode = self._ocr_mode_combo.currentData()
        if self.ocr_scanner:
            self.ocr_scanner.set_mode(mode)
        if overlay_settings is not None:
            overlay_settings.set_ocr_mode(mode)
        label = "Dynamic (fuzzy)" if mode == OCR_MODE_DYNAMIC else "Strict (exact)"
        self.status_bar.showMessage(f"OCR mode changed to: {label}", 4000)

    def _on_spell_ocr_strength_changed(self, _index: int):
        """User changed the spell card image-scan strength in settings.

        Saved immediately to hud_settings.json; the scraper subprocess reads
        it on its next fetch / reparse run.
        """
        if not hasattr(self, '_spell_ocr_strength_combo'):
            return
        level = self._spell_ocr_strength_combo.currentData()
        if overlay_settings is not None:
            overlay_settings.set_spell_ocr_strength(level)
        label = self._spell_ocr_strength_combo.currentText().split("  ")[0]
        self.status_bar.showMessage(
            f"Spell scan strength set to: {label} (applies on next fetch/reparse)", 5000
        )

    def _on_spell_icons_from_desc_changed(self, checked: bool):
        """User toggled whether the description may assign icons. Saved
        immediately; applies on the next fetch / reparse."""
        if overlay_settings is not None:
            overlay_settings.set_spell_ocr_icons_from_description(bool(checked))
        msg = ("Icons will use description + image scan"
               if checked else
               "Icons will use the card-image scan only")
        self.status_bar.showMessage(f"{msg} (applies on next fetch/reparse)", 5000)

    def _on_spell_icon_conf_changed(self, value: int):
        """User dragged the visual icon sensitivity slider. `value` is the
        match-confidence percent (60–95). Saved immediately; applies on the
        next fetch / reparse."""
        if hasattr(self, "_spell_icon_conf_value"):
            self._spell_icon_conf_value.setText(f"{value}% confidence")
        if overlay_settings is not None:
            overlay_settings.set_spell_icon_visual_confidence(value / 100.0)
        self.status_bar.showMessage(
            f"Visual icon sensitivity set to {value}% confidence "
            "(applies on next fetch/reparse)", 4000
        )

    # ── Cloudflare alert sound settings ───────────────────────────────────
    def _build_cf_sound_section(self) -> QGroupBox:
        """
        Build the 🔔 Cloudflare Alert Sound settings group: enable/disable,
        choose a custom sound file, volume slider, and a test button. Plays
        during any wiki fetch when the "verify you are human" checkbox appears.
        """
        from PyQt5.QtWidgets import QSlider

        group = self._make_settings_group(
            "🔔 Cloudflare Alert Sound",
            "Plays a sound during any wiki fetch (bosses or spells) the moment "
            "Cloudflare's \u201cverify you are human\u201d checkbox appears, so you "
            "know to click it in the Chrome window. Detected from the page itself, "
            "not the screen.",
        )

        # Enable / disable
        self._cf_sound_chk = QCheckBox("Play alert sound when the checkbox appears")
        self._cf_sound_chk.setChecked(overlay_settings.get_cf_sound_enabled())
        self._cf_sound_chk.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        self._cf_sound_chk.stateChanged.connect(self._on_cf_sound_toggle)
        group.layout().addWidget(self._cf_sound_chk)

        # Sound file row
        file_row = QHBoxLayout()
        file_lbl = QLabel("Sound file:")
        file_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        file_row.addWidget(file_lbl)

        self._cf_sound_path_lbl = QLabel()
        self._cf_sound_path_lbl.setStyleSheet(
            "color:#8fb7ff; font-size:12px; background:#0a1628; border:1px solid #0f3460;"
            "border-radius:4px; padding:4px 8px;"
        )
        self._cf_sound_path_lbl.setWordWrap(False)
        self._cf_sound_path_lbl.setMinimumWidth(220)
        self._refresh_cf_sound_path_label()
        file_row.addWidget(self._cf_sound_path_lbl, stretch=1)

        choose_btn = QPushButton("📂 Choose…")
        choose_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;border-radius:5px;"
            "padding:5px 12px;font-size:12px;}QPushButton:hover{background:#1f4a80;}"
        )
        choose_btn.clicked.connect(self._choose_cf_sound)
        file_row.addWidget(choose_btn)

        reset_btn = QPushButton("↺ Default")
        reset_btn.setToolTip("Use the built-in default chime")
        reset_btn.setStyleSheet(
            "QPushButton{background:#1a1a2e;color:#8a8a9a;border:1px solid #0f3460;"
            "border-radius:5px;padding:5px 10px;font-size:12px;}"
            "QPushButton:hover{background:#0f3460;color:#e0e0e0;}"
        )
        reset_btn.clicked.connect(self._reset_cf_sound)
        file_row.addWidget(reset_btn)
        group.layout().addLayout(file_row)

        # Volume row
        vol_row = QHBoxLayout()
        vol_lbl = QLabel("Volume:")
        vol_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        vol_row.addWidget(vol_lbl)

        self._cf_vol_slider = QSlider(Qt.Horizontal)
        self._cf_vol_slider.setRange(0, 100)
        self._cf_vol_slider.setValue(overlay_settings.get_cf_sound_volume())
        self._cf_vol_slider.setStyleSheet("""
            QSlider::groove:horizontal { background:#0f3460; height:6px; border-radius:3px; }
            QSlider::handle:horizontal { background:#e94560; width:16px; height:16px;
                margin:-5px 0; border-radius:8px; }
            QSlider::sub-page:horizontal { background:#e94560; border-radius:3px; }
        """)
        vol_row.addWidget(self._cf_vol_slider, stretch=1)

        self._cf_vol_spin = QSpinBox()
        self._cf_vol_spin.setRange(0, 100)
        self._cf_vol_spin.setValue(overlay_settings.get_cf_sound_volume())
        self._cf_vol_spin.setSuffix("%")
        self._cf_vol_spin.setFixedWidth(70)
        self._cf_vol_spin.setAlignment(Qt.AlignCenter)
        self._cf_vol_spin.setStyleSheet("""
            QSpinBox { background:#0a1628; color:#ccc; border:1px solid #0f3460;
                border-radius:4px; font-size:12px; padding:2px 4px; }
            QSpinBox:focus { border-color:#e94560; }
            QSpinBox::up-button, QSpinBox::down-button { width:0; height:0; border:none; }
        """)
        vol_row.addWidget(self._cf_vol_spin)

        # Two-way sync + persist
        self._cf_vol_slider.valueChanged.connect(
            lambda v: (self._cf_vol_spin.blockSignals(True),
                       self._cf_vol_spin.setValue(v),
                       self._cf_vol_spin.blockSignals(False))
        )
        self._cf_vol_slider.valueChanged.connect(self._on_cf_volume_changed)
        self._cf_vol_spin.valueChanged.connect(
            lambda v: (self._cf_vol_slider.blockSignals(True),
                       self._cf_vol_slider.setValue(v),
                       self._cf_vol_slider.blockSignals(False))
        )
        self._cf_vol_spin.valueChanged.connect(self._on_cf_volume_changed)

        test_btn = QPushButton("▶ Test")
        test_btn.setStyleSheet(
            "QPushButton{background:#6bcb77;color:#0a1628;border:none;border-radius:5px;"
            "padding:5px 14px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#8ee69a;}"
        )
        test_btn.clicked.connect(self._test_cf_sound)
        vol_row.addWidget(test_btn)
        group.layout().addLayout(vol_row)

        note = QLabel(
            "Tip: <b>.wav</b> files are the most reliable. MP3/OGG also work if your "
            "system has the codecs. Volume applies to the sound file; the fallback "
            "system beep ignores it."
        )
        note.setStyleSheet("color:#666; font-size:11px; background:transparent;")
        note.setWordWrap(True)
        group.layout().addWidget(note)
        return group

    def _refresh_cf_sound_path_label(self):
        if not hasattr(self, "_cf_sound_path_lbl"):
            return
        path = overlay_settings.get_cf_sound_path()
        if path and os.path.exists(path):
            self._cf_sound_path_lbl.setText(os.path.basename(path))
            self._cf_sound_path_lbl.setToolTip(path)
        elif path:
            self._cf_sound_path_lbl.setText(f"⚠ missing: {os.path.basename(path)}")
            self._cf_sound_path_lbl.setToolTip(path)
        else:
            self._cf_sound_path_lbl.setText("Default chime (built-in)")
            self._cf_sound_path_lbl.setToolTip("Using the generated default alert sound")

    def _on_cf_sound_toggle(self, state):
        overlay_settings.set_cf_sound_enabled(state != 0)

    def _choose_cf_sound(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Cloudflare alert sound", "",
            "Audio (*.wav *.mp3 *.ogg *.flac);;All files (*)"
        )
        if not path:
            return
        overlay_settings.set_cf_sound_path(path)
        self._refresh_cf_sound_path_label()
        self._test_cf_sound()

    def _reset_cf_sound(self):
        overlay_settings.set_cf_sound_path("")
        self._refresh_cf_sound_path_label()

    def _on_cf_volume_changed(self, value):
        overlay_settings.set_cf_sound_volume(int(value))

    def _test_cf_sound(self):
        try:
            from hud_overlays import cf_alert_player
            cf_alert_player.play(force=True)
        except Exception:
            self.status_bar.showMessage("Could not play sound (audio backend unavailable)", 4000)

    def _make_settings_group(self, title: str, subtitle: str = "") -> QGroupBox:
        """Styled settings section group box."""
        box = QGroupBox(title)
        box.setStyleSheet("""
            QGroupBox {
                background:#16213e;
                border:1px solid #0f3460;
                border-radius:10px;
                margin-top:14px;
                padding-top:18px;
                font-size:14px;
                font-weight:bold;
                color:#e0e0e0;
            }
            QGroupBox::title {
                subcontrol-origin:margin;
                left:14px;
                padding:0 8px;
                background:#16213e;
                color:#e94560;
            }
        """)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setStyleSheet("color:#666; font-size:11px; font-weight:normal; background:transparent;")
            sub.setWordWrap(True)
            layout.addWidget(sub)
        return box

    def _make_hud_overlay_card(self, key: str, label: str, color: str) -> QFrame:
        """Create a card for one HUD overlay with enable toggle + clickthrough + per-overlay opacity."""
        from PyQt5.QtWidgets import QSlider
        card = QFrame()
        card.setStyleSheet(
            "QFrame{background:#1a1a2e;border:1px solid #0f3460;border-radius:10px;}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        title_lbl = QLabel(label)
        title_lbl.setStyleSheet(f"color:{color};font-size:14px;font-weight:bold;background:transparent;")
        layout.addWidget(title_lbl)

        # Toggle button
        is_on = HUD_AVAILABLE and overlay_settings.is_enabled(key)
        toggle_btn = QPushButton("● ON" if is_on else "○ OFF")
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(is_on)
        toggle_btn.setStyleSheet(self._overlay_btn_style(is_on, color))
        toggle_btn.toggled.connect(
            lambda checked, k=key, c=color, b=toggle_btn: self._on_hud_toggle(k, checked, c, b)
        )
        layout.addWidget(toggle_btn)
        self._hud_toggle_btns[key] = toggle_btn

        # Click-through checkbox
        ct_check = QCheckBox("Click-through (display only)")
        ct_check.setStyleSheet("""
            QCheckBox { color:#888; font-size:11px; background:transparent; }
            QCheckBox::indicator { width:14px; height:14px; border:1px solid #0f3460;
                                   border-radius:3px; background:#16213e; }
            QCheckBox::indicator:checked { background:#e94560; border-color:#e94560; }
        """)
        ct_check.setChecked(HUD_AVAILABLE and overlay_settings.is_clickthrough(key))
        ct_check.stateChanged.connect(
            lambda state, k=key: self._on_hud_clickthrough(k, bool(state))
        )
        layout.addWidget(ct_check)
        self._hud_ct_checks[key] = ct_check

        # Per-overlay opacity slider
        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(6)
        opa_lbl = QLabel("Opacity:")
        opa_lbl.setStyleSheet("color:#666; font-size:11px; background:transparent;")
        opacity_row.addWidget(opa_lbl)

        opa_slider = QSlider(Qt.Horizontal)
        opa_slider.setRange(10, 95)

        # Current value: read per-overlay alpha; -1 means "use global"
        if HUD_AVAILABLE:
            per_alpha = overlay_settings.get(key).get("alpha", -1)
            if per_alpha >= 0:
                cur_pct = round(100 - (per_alpha / 255 * 100))
            else:
                global_alpha = overlay_settings.get_alpha()
                cur_pct = round(100 - (global_alpha / 255 * 100))
        else:
            cur_pct = 80
        opa_slider.setValue(cur_pct)
        opa_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background:#0f3460; height:4px; border-radius:2px;
            }}
            QSlider::handle:horizontal {{
                background:{color}; width:12px; height:12px;
                margin:-4px 0; border-radius:6px;
            }}
            QSlider::sub-page:horizontal {{ background:{color}; border-radius:2px; }}
        """)

        opa_val_spin = QSpinBox()
        opa_val_spin.setRange(10, 95)
        opa_val_spin.setValue(cur_pct)
        opa_val_spin.setSuffix("%")
        opa_val_spin.setFixedWidth(58)
        opa_val_spin.setStyleSheet(f"""
            QSpinBox {{
                background:#0a1628; color:#ccc; border:1px solid #0f3460;
                border-radius:4px; font-size:11px; padding:1px 2px;
            }}
            QSpinBox:focus {{ border-color:{color}; }}
            QSpinBox::up-button, QSpinBox::down-button {{ width:0; height:0; border:none; }}
        """)
        opa_val_spin.setAlignment(Qt.AlignCenter)
        # Slider → SpinBox sync
        opa_slider.valueChanged.connect(
            lambda v, k=key, s=opa_val_spin: (
                s.blockSignals(True), s.setValue(v), s.blockSignals(False),
                self._on_overlay_opacity_changed(k, v, s)
            )
        )
        # SpinBox → Slider sync
        opa_val_spin.valueChanged.connect(
            lambda v, sl=opa_slider: (sl.blockSignals(True), sl.setValue(v), sl.blockSignals(False))
        )
        opa_val_spin.valueChanged.connect(
            lambda v, k=key, s=opa_val_spin: self._on_overlay_opacity_changed(k, v, s)
        )
        opacity_row.addWidget(opa_slider, stretch=1)
        opacity_row.addWidget(opa_val_spin)
        layout.addLayout(opacity_row)

        # "Show now" link
        show_btn = QPushButton("↗ Show overlay")
        show_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#4d96ff;border:none;"
            "font-size:11px;text-align:left;padding:0;}"
            "QPushButton:hover{color:#88ccff;}"
        )
        show_btn.setCursor(Qt.PointingHandCursor)
        show_btn.clicked.connect(lambda: self._force_show_overlay(key))
        layout.addWidget(show_btn)

        return card

    @staticmethod
    def _overlay_btn_style(on: bool, color: str) -> str:
        if on:
            return (f"QPushButton{{background:{color};color:#fff;border:none;"
                    "border-radius:6px;padding:6px 14px;font-size:12px;font-weight:bold;}"
                    f"QPushButton:hover{{background:{color};color:rgba(255,255,255,200);}}")
        return ("QPushButton{background:#0f3460;color:#888;border:1px solid #1f3460;"
                "border-radius:6px;padding:6px 14px;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:#1f3460;color:#e0e0e0;}")

    def _on_hud_toggle(self, key: str, checked: bool, color: str, btn: QPushButton):
        if not HUD_AVAILABLE or not self.overlay_manager:
            return
        btn.setText("● ON" if checked else "○ OFF")
        btn.setStyleSheet(self._overlay_btn_style(checked, color))
        self.overlay_manager.toggle(key, checked)
        # Push current data into freshly opened overlay
        if checked:
            self._push_hud_data(key)

    def _on_hud_clickthrough(self, key: str, enabled: bool):
        if not HUD_AVAILABLE or not self.overlay_manager:
            return
        self.overlay_manager.set_clickthrough(key, enabled)

    def _force_show_overlay(self, key: str):
        """Toggle the overlay on (and enable its settings toggle button)."""
        if not HUD_AVAILABLE or not self.overlay_manager:
            return
        btn = self._hud_toggle_btns.get(key)
        if btn and not btn.isChecked():
            btn.setChecked(True)  # triggers _on_hud_toggle
        else:
            self.overlay_manager.toggle(key, True)

    # ─── DISPLAY SCALE ───────────────────────────────────────────
    def _build_display_scale_section(self) -> QWidget:
        """Whole-app scaling: main window, boxes, text and every HUD overlay.

        Scales UP only (100%–200%) via Qt's global QT_SCALE_FACTOR, because
        Qt renders broken below 1.0. Auto-adjust sizes the app to the screen
        automatically; a manual value overrides it. Takes effect on restart.
        """
        from PyQt5.QtWidgets import QSlider

        cfg = ui_scale.load_config()
        auto_scale = ui_scale.compute_auto_scale(ui_scale.get_detected_height())
        shown_scale = cfg["scale"] if cfg["manual"] else auto_scale

        group = self._make_settings_group(
            "🔍 Display Scale",
            "Make the whole app — main window, boxes, text and the HUD "
            "overlays — larger. Leave “Auto-adjust” on to size the app to "
            "your screen automatically, or turn it off to pick your own "
            "value. Changes take effect after you restart the app."
        )

        min_pct = int(round(ui_scale.MIN_SCALE * 100))
        max_pct = int(round(ui_scale.MAX_SCALE * 100))
        step_pct = int(round(ui_scale.STEP * 100))

        note = QLabel(
            f"100% is the normal size; the scale goes from {min_pct}% up to "
            f"{max_pct}%. (Values below 100% aren't available — Qt renders "
            "them incorrectly, which is what caused the giant empty boxes.)"
        )
        note.setStyleSheet("color:#8ea6c8; font-size:12px; background:transparent;")
        note.setWordWrap(True)
        group.layout().addWidget(note)

        suggest_lbl = QLabel(
            f"Suggested for your screen: <b>{int(round(auto_scale * 100))}%</b>")
        suggest_lbl.setStyleSheet("color:#8ea6c8; font-size:12px; background:transparent;")
        suggest_lbl.setWordWrap(True)
        group.layout().addWidget(suggest_lbl)

        # Auto-adjust toggle
        self._scale_auto_check = QCheckBox("Auto-adjust to my screen")
        self._scale_auto_check.setChecked(not cfg["manual"])
        self._scale_auto_check.setStyleSheet(
            "color:#ccc; font-size:13px; background:transparent;")
        self._scale_auto_check.stateChanged.connect(self._on_scale_auto_toggled)
        group.layout().addWidget(self._scale_auto_check)

        # Slider + spinbox row
        scale_row = QHBoxLayout()
        scale_lbl = QLabel("Scale:")
        scale_lbl.setStyleSheet("color:#ccc; font-size:13px; background:transparent;")
        scale_row.addWidget(scale_lbl)

        self._scale_slider = QSlider(Qt.Horizontal)
        self._scale_slider.setRange(min_pct, max_pct)
        self._scale_slider.setSingleStep(step_pct)
        self._scale_slider.setPageStep(step_pct * 2)
        self._scale_slider.setValue(int(round(shown_scale * 100)))
        self._scale_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background:#0f3460; height:6px; border-radius:3px;
            }
            QSlider::handle:horizontal {
                background:#4d96ff; width:16px; height:16px;
                margin:-5px 0; border-radius:8px;
            }
            QSlider::sub-page:horizontal { background:#4d96ff; border-radius:3px; }
            QSlider::groove:horizontal:disabled { background:#152238; }
            QSlider::handle:horizontal:disabled { background:#2a3a52; }
            QSlider::sub-page:horizontal:disabled { background:#2a3a52; }
        """)

        self._scale_spin = QSpinBox()
        self._scale_spin.setRange(min_pct, max_pct)
        self._scale_spin.setSingleStep(step_pct)
        self._scale_spin.setValue(int(round(shown_scale * 100)))
        self._scale_spin.setSuffix("%")
        self._scale_spin.setFixedWidth(90)
        self._scale_spin.setAlignment(Qt.AlignCenter)
        self._scale_spin.setStyleSheet("""
            QSpinBox {
                background:#0a1628; color:#ccc; border:1px solid #0f3460;
                border-radius:4px; font-size:12px; padding:2px 4px;
            }
            QSpinBox:focus { border-color:#4d96ff; }
            QSpinBox:disabled { color:#556; border-color:#152238; }
            QSpinBox::up-button, QSpinBox::down-button { width:0; height:0; border:none; }
        """)

        # Two-way sync (guarded so they don't fight each other)
        self._scale_slider.valueChanged.connect(
            lambda v: (
                self._scale_spin.blockSignals(True),
                self._scale_spin.setValue(v),
                self._scale_spin.blockSignals(False),
                self._on_scale_value_changed(v),
            )
        )
        self._scale_spin.valueChanged.connect(
            lambda v: (
                self._scale_slider.blockSignals(True),
                self._scale_slider.setValue(v),
                self._scale_slider.blockSignals(False),
                self._on_scale_value_changed(v),
            )
        )
        scale_row.addWidget(self._scale_slider, stretch=1)
        scale_row.addWidget(self._scale_spin)
        group.layout().addLayout(scale_row)

        # Reset button + pending-restart status
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        reset_btn = QPushButton("↺ Reset to auto")
        reset_btn.setToolTip("Turn auto-adjust back on and use the size detected for your screen")
        reset_btn.setStyleSheet(
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:6px;padding:8px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#4d96ff;color:#fff;}"
        )
        reset_btn.clicked.connect(self._on_scale_reset)
        btn_row.addWidget(reset_btn)

        self._scale_restart_btn = QPushButton("↻ Restart to apply")
        self._scale_restart_btn.setToolTip("Close and relaunch the app now to apply the new scale")
        self._scale_restart_btn.setStyleSheet(
            "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
            "border-radius:6px;padding:8px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;color:#fff;}"
            "QPushButton:disabled{background:#1a1a2e;color:#555;}"
        )
        self._scale_restart_btn.clicked.connect(self._on_scale_restart)
        btn_row.addWidget(self._scale_restart_btn)
        btn_row.addStretch()
        group.layout().addLayout(btn_row)

        self._scale_status_lbl = QLabel("")
        self._scale_status_lbl.setStyleSheet("font-size:12px; background:transparent;")
        self._scale_status_lbl.setWordWrap(True)
        group.layout().addWidget(self._scale_status_lbl)

        # Auto mode disables the manual controls
        self._scale_slider.setEnabled(cfg["manual"])
        self._scale_spin.setEnabled(cfg["manual"])
        self._refresh_scale_status()
        return group

    def _current_scale_choice(self) -> float:
        """The scale currently selected in the settings controls."""
        if getattr(self, "_scale_auto_check", None) and self._scale_auto_check.isChecked():
            return ui_scale.compute_auto_scale(ui_scale.get_detected_height())
        val = self._scale_spin.value() if getattr(self, "_scale_spin", None) else 100
        return ui_scale.clamp_scale(val / 100.0)

    def _refresh_scale_status(self):
        """Show whether the chosen scale matches what's running (restart hint)."""
        if not getattr(self, "_scale_status_lbl", None):
            return
        chosen = self._current_scale_choice()
        active = ui_scale.get_applied_scale()
        chosen_pct = int(round(chosen * 100))
        active_pct = int(round(active * 100))
        pending = abs(chosen - active) > 1e-3
        if getattr(self, "_scale_restart_btn", None):
            self._scale_restart_btn.setEnabled(pending)
        if pending:
            self._scale_status_lbl.setText(
                f"⚠  Pending: <b>{chosen_pct}%</b> — currently running at "
                f"{active_pct}%. Restart the app to apply."
            )
            self._scale_status_lbl.setStyleSheet(
                "color:#ffd93d; font-size:12px; background:transparent;")
        else:
            self._scale_status_lbl.setText(
                f"✓  Active at <b>{active_pct}%</b>.")
            self._scale_status_lbl.setStyleSheet(
                "color:#6bcb77; font-size:12px; background:transparent;")

    def _save_scale_choice(self):
        """Persist the current controls to ui_scale.json."""
        manual = not (getattr(self, "_scale_auto_check", None)
                      and self._scale_auto_check.isChecked())
        if manual:
            scale = ui_scale.clamp_scale(self._scale_spin.value() / 100.0)
        else:
            scale = ui_scale.compute_auto_scale(ui_scale.get_detected_height())
        ui_scale.save_config(scale, manual)

    def _on_scale_auto_toggled(self, state):
        auto_on = (state != 0)
        if getattr(self, "_scale_slider", None):
            self._scale_slider.setEnabled(not auto_on)
        if getattr(self, "_scale_spin", None):
            self._scale_spin.setEnabled(not auto_on)
        if auto_on:
            auto_pct = int(round(
                ui_scale.compute_auto_scale(ui_scale.get_detected_height()) * 100))
            for w in (self._scale_slider, self._scale_spin):
                w.blockSignals(True)
                w.setValue(auto_pct)
                w.blockSignals(False)
        self._save_scale_choice()
        self._refresh_scale_status()

    def _on_scale_value_changed(self, value: int):
        self._save_scale_choice()
        self._refresh_scale_status()

    def _on_scale_reset(self):
        """Reset → auto-adjust on, snap to the detected-screen scale."""
        if getattr(self, "_scale_auto_check", None):
            self._scale_auto_check.blockSignals(True)
            self._scale_auto_check.setChecked(True)
            self._scale_auto_check.blockSignals(False)
        auto_pct = int(round(
            ui_scale.compute_auto_scale(ui_scale.get_detected_height()) * 100))
        for w in (self._scale_slider, self._scale_spin):
            w.setEnabled(False)
            w.blockSignals(True)
            w.setValue(auto_pct)
            w.blockSignals(False)
        self._save_scale_choice()
        self._refresh_scale_status()

    def _on_scale_restart(self):
        """Save the chosen scale and relaunch the app so it takes effect."""
        self._save_scale_choice()
        chosen_pct = int(round(self._current_scale_choice() * 100))

        box = QMessageBox(self)
        box.setWindowTitle("Restart to apply scale")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Restart Wizard101 Companion now to apply {chosen_pct}% scaling?")
        box.setInformativeText("Any unsaved fetches will be stopped.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec_() != QMessageBox.Yes:
            return

        # Relaunch a fresh process, then close this one. The new process
        # re-reads ui_scale.json and sets QT_SCALE_FACTOR at startup.
        relaunched = False
        try:
            script = os.path.abspath(__file__)
            py = sys.executable
            if py and os.path.exists(script):
                relaunched = QProcess.startDetached(py, [script])
        except Exception as e:
            logger.warning(f"UI-scale relaunch failed: {e}")
            relaunched = False

        if relaunched:
            QApplication.quit()
        else:
            QMessageBox.information(
                self, "Restart needed",
                "Couldn't relaunch automatically. Please close the app and "
                "open it again to apply the new scale."
            )

    def _on_opacity_changed(self, value: int):
        """Global transparency slider changed."""
        if HUD_AVAILABLE:
            new_alpha = int((1 - value / 100) * 255)
            overlay_settings.set_alpha(max(10, min(245, new_alpha)))

    def _on_overlay_opacity_changed(self, key: str, value: int, widget=None):
        """Per-overlay transparency slider/spinbox changed."""
        # widget may be a QLabel (legacy) or QSpinBox — only QLabel needs setText
        if widget is not None and hasattr(widget, 'setText') and not hasattr(widget, 'setValue'):
            widget.setText(f"{value}%")
        if HUD_AVAILABLE:
            if value == -1:
                overlay_settings.set_overlay_alpha(key, -1)
            else:
                new_alpha = int((1 - value / 100) * 255)
                overlay_settings.set_overlay_alpha(key, max(10, min(245, new_alpha)))
        # Force repaint on any visible overlay
        if self.overlay_manager:
            ov = self.overlay_manager._overlays.get(key)
            if ov and ov.isVisible():
                ov._container.update()

    def _push_hud_data(self, key: str):
        """Push the most recent data into the named overlay."""
        if not self.overlay_manager:
            return
        if key == "boss":
            # Get currently displayed boss
            current_item = self.boss_tree.currentItem() if hasattr(self, 'boss_tree') else None
            if current_item:
                boss_name = current_item.data(0, Qt.UserRole)
                if boss_name:
                    self.overlay_manager.update_boss(db.get_boss(self.conn, boss_name))
        elif key == "quest":
            self._push_quest_hud()
        elif key == "counter":
            self._push_counter_hud()
        elif key == "guide":
            self._push_guide_hud()

    def _push_quest_hud(self):
        """Build quest data dict and push to quest HUD."""
        if not self.overlay_manager:
            return
        try:
            worlds = dq.list_worlds(self.conn)
            quests = []
            for w in worlds:
                wq = dq.get_quests_for_world(self.conn, w['id'])
                for q in wq:
                    q['world_name'] = w['name']
                    quests.append(q)
            self.overlay_manager.update_quests({"quests": quests})
        except Exception:
            pass

    def _push_counter_hud(self, boss_name: str = ""):
        """Build counter data dict and push to counter HUD."""
        if not self.overlay_manager:
            return
        try:
            if not boss_name:
                current_item = self.boss_tree.currentItem() if hasattr(self, 'boss_tree') else None
                if current_item:
                    boss_name = current_item.data(0, Qt.UserRole) or ""
            counters = db.get_round_counters_for_boss(self.conn, boss_name) if boss_name else db.list_round_counters(self.conn)
            self.overlay_manager.update_counters({"boss": boss_name, "counters": counters})
        except Exception:
            pass

    def _push_guide_hud(self, boss_name: str = ""):
        """Build guide data dict and push to guide HUD."""
        if not self.overlay_manager:
            return
        try:
            if not boss_name:
                current_item = self.boss_tree.currentItem() if hasattr(self, 'boss_tree') else None
                if current_item:
                    boss_name = current_item.data(0, Qt.UserRole) or ""
            guides = db.get_guides_for_boss(self.conn, boss_name) if boss_name else db.list_guides(self.conn)
            self.overlay_manager.update_guides({"boss": boss_name, "guides": guides})
        except Exception:
            pass

    # ─── TREE VIEW ──────────────────────────────────────────────

    def _refresh_tree(self):
        """
        Full reset: exit search mode, clear search text state, rebuild tree.
        Called by sort changes, after fetch, after remove, and by clear-search.
        """
        self._in_search_mode = False
        self.boss_tree.clear()
        self._rebuild_tree_content()

    def _rebuild_tree_content(self):
        """
        Rebuild tree items from DB without touching search/mode state.
        Shared between _refresh_tree and _display_boss (which may call it
        mid-search to restore the full tree behind the scenes).
        """
        all_bosses = db.list_bosses_by_location(self.conn)
        sort_mode = self.sort_combo.currentIndex()  # 0=Chrono, 1=A-Z, 2=Z-A

        # Build nested dict: {world: {area: {subarea: {subsubarea: ... : [bosses]}}}}
        # We use a recursive nested dict structure
        tree = {}
        for boss in all_bosses:
            parts = boss['loc_parts']
            node = tree
            for part in parts:
                if part not in node:
                    node[part] = {}
                node = node[part]
            # Store bosses under a special key
            if '__bosses__' not in node:
                node['__bosses__'] = []
            node['__bosses__'].append(boss)

        # Sort function for world names — reads current order dynamically
        _current_order = get_world_order()
        def world_sort_key(name):
            if sort_mode == 0:  # Chronological
                try:
                    return _current_order.index(name)
                except ValueError:
                    return 9999
            return name.lower()

        reverse = (sort_mode == 2)
        total_bosses = [0]

        def build_node(parent_item, subtree, depth=0):
            """Recursively build tree items from nested dict."""
            # Separate child locations from bosses
            child_keys = sorted(
                [k for k in subtree if k != '__bosses__'],
                key=lambda k: (world_sort_key(k) if depth == 0 else k.lower()),
                reverse=reverse,
            )
            bosses = subtree.get('__bosses__', [])

            # Add bosses at this level
            for boss in sorted(bosses, key=lambda b: b['name'], reverse=reverse):
                cheat_icon = "👾 " if boss.get('has_cheats') else ""
                hp = boss.get('health', '?')
                boss_text = f"{cheat_icon}{boss['name']}  ·  ♥ {hp}"
                boss_item = QTreeWidgetItem([boss_text])
                boss_item.setData(0, Qt.UserRole, boss['name'])
                if boss.get('has_cheats'):
                    boss_item.setForeground(0, QColor("#ffd93d"))
                parent_item.addChild(boss_item)
                total_bosses[0] += 1

            # Add child location nodes
            for key in child_keys:
                child_subtree = subtree[key]

                # Count total bosses under this node
                def count_bosses(node):
                    c = len(node.get('__bosses__', []))
                    for k, v in node.items():
                        if k != '__bosses__' and isinstance(v, dict):
                            c += count_bosses(v)
                    return c

                boss_count = count_bosses(child_subtree)

                if depth == 0:
                    # World level
                    node_item = QTreeWidgetItem([f"\U0001F30D {key} ({boss_count})"])
                    node_item.setFont(0, QFont("Segoe UI", 11, QFont.Bold))
                    node_item.setForeground(0, QColor("#e94560"))
                    node_item.setData(0, Qt.UserRole + 1, ("world", key))
                else:
                    # Area / SubArea level — build full prefix from parent chain
                    # Walk up parent items to reconstruct the full location prefix
                    prefix_parts = [key]
                    p = parent_item
                    while p is not None:
                        p_data = p.data(0, Qt.UserRole + 1)
                        if p_data and p_data[0] in ("world", "area"):
                            prefix_parts.insert(0, p_data[1].split(" > ")[-1] if p_data[0] == "area" else p_data[1])
                        p = p.parent()
                    # Simpler: store full prefix by reading parent chain
                    parent_prefix = parent_item.data(0, Qt.UserRole + 1)
                    if parent_prefix:
                        if parent_prefix[0] == "world":
                            full_prefix = f"{parent_prefix[1]} > {key}"
                        else:
                            full_prefix = f"{parent_prefix[1]} > {key}"
                    else:
                        full_prefix = key
                    node_item = QTreeWidgetItem([f"{key} ({boss_count})"])
                    node_item.setForeground(0, QColor("#4d96ff"))
                    node_item.setData(0, Qt.UserRole + 1, ("area", full_prefix))

                build_node(node_item, child_subtree, depth + 1)
                parent_item.addChild(node_item)

        # Build top-level world nodes
        root_keys = sorted(
            [k for k in tree if k != '__bosses__'],
            key=world_sort_key,
            reverse=reverse,
        )

        for world_name in root_keys:
            def count_bosses(node):
                c = len(node.get('__bosses__', []))
                for k, v in node.items():
                    if k != '__bosses__' and isinstance(v, dict):
                        c += count_bosses(v)
                return c

            boss_count = count_bosses(tree[world_name])
            world_item = QTreeWidgetItem([f"\U0001F30D {world_name} ({boss_count})"])
            world_item.setFont(0, QFont("Segoe UI", 11, QFont.Bold))
            world_item.setForeground(0, QColor("#e94560"))
            world_item.setData(0, Qt.UserRole + 1, ("world", world_name))

            build_node(world_item, tree[world_name], depth=1)
            self.boss_tree.addTopLevelItem(world_item)

        # Bosses with no location
        root_bosses = tree.get('__bosses__', [])
        for boss in root_bosses:
            cheat_icon = "👾 " if boss.get('has_cheats') else ""
            hp = boss.get('health', '?')
            boss_text = f"{cheat_icon}{boss['name']}  ·  ♥ {hp}"
            boss_item = QTreeWidgetItem([boss_text])
            boss_item.setData(0, Qt.UserRole, boss['name'])
            self.boss_tree.addTopLevelItem(boss_item)
            total_bosses[0] += 1

        self.list_count_label.setText(f"{total_bosses[0]} bosses in database")

    def _on_tree_context_menu(self, pos):
        """Right-click context menu on the boss tree."""
        from PyQt5.QtWidgets import QMenu
        item = self.boss_tree.itemAt(pos)
        if not item:
            return

        boss_name = item.data(0, Qt.UserRole)
        node_data = item.data(0, Qt.UserRole + 1)

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#16213e; color:#e0e0e0; border:1px solid #0f3460;
                    border-radius:4px; padding:4px; }
            QMenu::item { padding:6px 20px 6px 12px; border-radius:3px; }
            QMenu::item:selected { background:#e94560; color:#fff; }
            QMenu::separator { height:1px; background:#0f3460; margin:3px 6px; }
        """)

        if boss_name:
            # Boss leaf node
            act_view = menu.addAction(f"📋 View: {boss_name}")
            act_view.triggered.connect(lambda: self._display_boss(boss_name))
            menu.addSeparator()
            act_del = menu.addAction(f"🗑 Delete Boss: {boss_name}")
            act_del.triggered.connect(lambda: self._remove_boss_by_name(boss_name))
            menu.addSeparator()
            act_rp = menu.addAction(f"♻ Reparse: {boss_name}")
            act_rp.triggered.connect(
                lambda checked=False, bn=boss_name: self._reparse_boss_by_name(bn)
            )

        elif node_data:
            node_type, location_key = node_data
            # Count bosses in this subtree for the menu label
            names = db.get_boss_names_by_location_prefix(self.conn, location_key)
            count = len(names)
            label = "World" if node_type == "world" else "Area"

            act_exp = menu.addAction("⊕ Expand")
            act_exp.triggered.connect(lambda: item.setExpanded(True))
            act_col = menu.addAction("⊖ Collapse")
            act_col.triggered.connect(lambda: item.setExpanded(False))
            menu.addSeparator()
            if count > 0:
                act_del = menu.addAction(
                    f"🗑 Delete all {count} boss{'es' if count != 1 else ''} in this {label}"
                )
                act_del.triggered.connect(
                    lambda checked=False, lk=location_key, lb=label, n=count:
                        self._delete_location_subtree(lk, lb, n)
                )
                menu.addSeparator()
                act_rp_loc = menu.addAction(
                    f"♻ Reparse all {count} boss{'es' if count != 1 else ''} in this {label}"
                )
                act_rp_loc.triggered.connect(
                    lambda checked=False, lk=location_key: self._reparse_location(lk)
                )
            else:
                no_act = menu.addAction(f"(No bosses in this {label})")
                no_act.setEnabled(False)
        else:
            return

        menu.exec_(self.boss_tree.viewport().mapToGlobal(pos))

    def _remove_boss_by_name(self, boss_name: str):
        """Delete a single boss via context menu (same as the toolbar button)."""
        if not confirm_delete(self, "Remove Boss", boss_name,
                              "This will remove the boss and all its data from the local database."):
            return
        self._delete_cache_file(boss_name)
        db.delete_boss(self.conn, boss_name)
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self._refresh_tree()
        self.status_bar.showMessage(f"Removed '{boss_name}'", 5000)

    def _delete_location_subtree(self, location_prefix: str, label: str, count: int):
        """Delete all bosses under a world or area node."""
        if not confirm_delete(
            self,
            f"Delete {label}",
            location_prefix,
            f"This will permanently delete all {count} boss{'es' if count != 1 else ''} "
            f"in this {label.lower()} and all sub-areas."
        ):
            return
        # Delete cache files for each boss in this subtree
        names = db.get_boss_names_by_location_prefix(self.conn, location_prefix)
        for name in names:
            self._delete_cache_file(name)
        deleted = db.delete_bosses_by_location_prefix(self.conn, location_prefix)
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self.counter_panel.update_boss_names(self.boss_names)
        self.guide_panel.update_boss_names(self.boss_names)
        self._refresh_tree()
        self.status_bar.showMessage(
            f"Deleted {deleted} boss{'es' if deleted != 1 else ''} from '{location_prefix}'", 6000
        )

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        boss_name = item.data(0, Qt.UserRole)
        if boss_name:
            self._display_boss(boss_name)

    def _select_boss_in_tree(self, boss_name: str):
        """
        Find the tree item for boss_name, collapse all other top-level nodes,
        expand only the ancestor chain leading to this boss, and select it.
        Works in both the normal tree and a search-results tree.
        """
        target = boss_name.lower()

        def find_item(parent: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            """Recursively search for a boss item matching target."""
            for i in range(parent.childCount()):
                child = parent.child(i)
                stored = child.data(0, Qt.UserRole)
                if stored and stored.lower() == target:
                    return child
                found = find_item(child)
                if found:
                    return found
            return None

        # Search all top-level items
        match_item = None
        match_top_level = None
        for i in range(self.boss_tree.topLevelItemCount()):
            top = self.boss_tree.topLevelItem(i)
            # Check the top-level item itself (rare, but possible for unlisted bosses)
            if top.data(0, Qt.UserRole) and top.data(0, Qt.UserRole).lower() == target:
                match_item = top
                match_top_level = top
                break
            found = find_item(top)
            if found:
                match_item = found
                match_top_level = top
                break

        if not match_item:
            return   # not in tree (search mode or unlisted)

        # Collapse all top-level nodes except the one containing the match
        for i in range(self.boss_tree.topLevelItemCount()):
            top = self.boss_tree.topLevelItem(i)
            if top is not match_top_level:
                top.setExpanded(False)

        # Expand the full ancestor chain of the matched item
        ancestor = match_item.parent()
        while ancestor is not None:
            ancestor.setExpanded(True)
            ancestor = ancestor.parent()

        # Select and scroll to the item
        self.boss_tree.setCurrentItem(match_item)
        self.boss_tree.scrollToItem(match_item)

    # ─── SEARCH ─────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str):
        """Show/hide clear button; restore full tree only when field is fully cleared."""
        has_text = bool(text.strip())
        self.clear_search_btn.setVisible(has_text)
        # Only restore tree if we were in search mode and the field is now empty
        if not has_text and self._in_search_mode:
            self._refresh_tree()

    def _clear_search(self):
        """Clear the search field, loaded boss results, and restore the full boss tree."""
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.clear_search_btn.setVisible(False)
        self._in_search_mode = False

        # Clear all right-panel boss info tabs
        self.info_display.setHtml(
            '<p style="color:#555;font-style:italic">Select a boss from the tree or search.</p>'
        )
        self.cheats_display.setHtml('')
        self.spells_display.setHtml('')
        self.minions_display.setHtml('')
        self.drops_display.setHtml('')

        # Clear round-counter and guide tabs
        while self.round_tab_layout.count() > 1:
            item = self.round_tab_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        while self.guide_tab_layout.count() > 1:
            item = self.guide_tab_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # Reset HUD overlay boss data
        if self.overlay_manager:
            boss_ov = self.overlay_manager.get_boss_overlay()
            if boss_ov:
                boss_ov.refresh({})

        self._refresh_tree()

    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            self._clear_search()
            return
        results = db.search_bosses(self.conn, query)
        self._in_search_mode = True

        self.boss_tree.clear()
        search_root = QTreeWidgetItem([f"🔍 Search: '{query}' ({len(results)} results)"])
        search_root.setForeground(0, QColor("#e94560"))

        for boss in results:
            cheat_icon = "👾 " if (boss.get('cheats') and len(boss.get('cheats', [])) > 0) else ""
            hp = boss.get('health', '?')
            text = f"{cheat_icon}{boss['name']}  ·  ♥ {hp}"
            item = QTreeWidgetItem([text])
            item.setData(0, Qt.UserRole, boss['name'])
            search_root.addChild(item)

        self.boss_tree.addTopLevelItem(search_root)
        search_root.setExpanded(True)
        self.clear_search_btn.setVisible(True)
        self.list_count_label.setText(f"{len(results)} results")

        if search_root.childCount() > 0:
            first = search_root.child(0)
            self.boss_tree.setCurrentItem(first)
            boss_name = first.data(0, Qt.UserRole)
            if boss_name:
                self._display_boss(boss_name)

    # ─── DISPLAY BOSS ───────────────────────────────────────────

    def _display_boss(self, boss_name: str):
        data = db.get_boss(self.conn, boss_name)
        if not data:
            self.info_display.setHtml(
                f'<h2 style="color:#e94560">{boss_name}</h2>'
                '<p style="color:#999">Not in local database. Click "Fetch from Wiki" to scrape it.</p>'
            )
            return

        # Always make sure the full tree is showing, then select + expand to this boss.
        # If we're currently in search mode, switch back to the full tree first so the
        # user can see where the boss lives in the world hierarchy.
        if self._in_search_mode:
            # Rebuild full tree without clearing the search text (user can still see it)
            self._in_search_mode = False
            self.boss_tree.clear()
            self._rebuild_tree_content()

        # Now select and expand the boss in the tree
        self._select_boss_in_tree(boss_name)

        # ── Push to HUD overlays ──
        if self.overlay_manager:
            self.overlay_manager.update_boss(data)
            self._push_counter_hud(boss_name)
            self._push_guide_hud(boss_name)
            self.overlay_manager.update_calc_boss(boss_name)

        # ── Keep the damage calculator boss in sync ──
        if getattr(self, "_calc_page", None):
            self._calc_page.calc_panel.set_boss_external(boss_name)

        # ── Cheats ──
        cheats = data.get('cheats', [])
        if cheats:
            import re as _re

            TYPE_META = {
                'start_of_battle': {'label': 'Start of Battle', 'color': '#ff6b6b', 'bg': '#1e0a0a'},
                'interrupt':       {'label': 'Interrupt',        'color': '#ffd93d', 'bg': '#1a1500'},
                'conditional':     {'label': 'Conditional',      'color': '#6bcb77', 'bg': '#071a0d'},
                'passive':         {'label': 'Passive',          'color': '#4d96ff', 'bg': '#050f1f'},
                'cycle_header':    {'label': 'Cycle',            'color': '#e0a0ff', 'bg': '#1a0a2a'},
                'cycle_info':      {'label': 'Info',             'color': '#aaaaaa', 'bg': '#111111'},
                'unknown':         {'label': 'Unknown',          'color': '#888888', 'bg': '#111111'},
            }

            # Spacer row between cards
            SPACER = '<table width="100%" cellspacing="0" cellpadding="0"><tr><td height="10"></td></tr></table>'

            html = SPACER  # top breathing room

            for i, cheat in enumerate(cheats, 1):
                if not isinstance(cheat, dict):
                    html += f'<p style="color:#e0e0e0;margin:8px 4px">{cheat}</p>'
                    continue

                raw_text = cheat.get('text', '')
                ctype    = cheat.get('type', 'unknown')
                meta     = TYPE_META.get(ctype, TYPE_META['unknown'])
                sub_pts  = cheat.get('sub_points', [])

                # ── Cycle header: rendered as a section divider, NOT a card ──
                if ctype == 'cycle_header':
                    html += (
                        f'<table width="100%" cellspacing="0" cellpadding="0">'
                        f'<tr><td height="16"></td></tr></table>'  # extra spacing before header
                        f'<table width="98%" cellspacing="0" cellpadding="0" align="center">'
                        f'<tr>'
                        f'<td style="border-bottom:2px solid {meta["color"]}44;'
                        f'padding:6px 14px 6px 14px">'
                        f'<span style="color:{meta["color"]};font-weight:bold;font-size:14px;'
                        f'letter-spacing:0.5px">{raw_text}</span>'
                        f'</td></tr></table>'
                    )
                    html += SPACER
                    continue

                # ── Cycle info: plain text line (e.g. "All cycles are 5 Rounds...") ──
                if ctype == 'cycle_info':
                    html += (
                        f'<table width="98%" cellspacing="0" cellpadding="0" align="center">'
                        f'<tr><td style="padding:4px 14px;color:#999999;font-size:12px;'
                        f'font-style:italic">{raw_text}</td></tr></table>'
                    )
                    html += SPACER
                    continue

                speech_parts = _re.findall(r'"([^"]{3,})"', raw_text)
                body_text    = _re.sub(r'"[^"]{3,}"', '', raw_text)
                body_text    = _re.sub(r'\[[^\]]+\]\s*', '', body_text).strip(' -–—,;').strip()

                # ── Card: left accent bar + content column ──────────────
                html += (
                    f'<table width="98%" cellspacing="0" cellpadding="0" align="center" '
                    f'style="background-color:{meta["bg"]}">'
                    f'<tr>'
                    # Left accent bar (6px wide, full height, colored)
                    f'<td width="6" bgcolor="{meta["color"]}"></td>'
                    # Content cell
                    f'<td style="padding:0">'
                )

                # — Type label row —
                html += (
                    f'<table width="100%" cellspacing="0" cellpadding="0">'
                    f'<tr><td style="padding:8px 14px 4px 14px">'
                    f'<span style="color:{meta["color"]};font-weight:bold;font-size:11px;'
                    f'letter-spacing:1px">{meta["label"].upper()}</span>'
                    f'</td></tr></table>'
                )

                # — Quote —
                if speech_parts:
                    for sp in speech_parts:
                        html += (
                            f'<table width="100%" cellspacing="0" cellpadding="0">'
                            f'<tr><td style="padding:2px 14px 6px 14px;'
                            f'color:#aaaaaa;font-style:italic;font-size:13px">'
                            f'&ldquo;{sp}&rdquo;</td></tr></table>'
                        )

                # — Body —
                if body_text:
                    html += (
                        f'<table width="100%" cellspacing="0" cellpadding="0">'
                        f'<tr><td style="padding:4px 14px 10px 14px;'
                        f'color:#e0e0e0;font-size:13px">'
                        f'{body_text}</td></tr></table>'
                    )

                # — Sub-points —
                if sub_pts:
                    for sp in sub_pts:
                        html += (
                            f'<table width="94%" cellspacing="0" cellpadding="0" align="right">'
                            f'<tr>'
                            f'<td width="3" bgcolor="#333333"></td>'
                            f'<td style="padding:4px 10px;color:#999999;font-size:12px">'
                            f'{sp}</td></tr></table>'
                        )
                    html += '<table width="100%" cellspacing="0" cellpadding="0"><tr><td height="6"></td></tr></table>'

                html += '</td></tr></table>'  # end card
                html += SPACER               # gap between cards

            self.cheats_display.setHtml(html)
        else:
            self.cheats_display.setHtml('<p style="color:#999;font-style:italic;padding:12px">No cheats found for this boss.</p>')

        # ── Boss Info (with Battle Stats merged in) ──
        battle_stats = data.get('battle_stats', {})
        resistances = data.get('resistances', {})

        html = f'''
        <div style="padding:4px">
          <h2 style="color:#e94560;margin:0 0 16px 0;font-size:18px;letter-spacing:0.5px">{data["name"]}</h2>
          <div style="display:flex;flex-wrap:wrap;gap:0">
        '''
        core_stats = [
            ('♥ Health', data.get('health', '?')),
            ('⭐ Rank',   data.get('rank',   '?')),
            ('🔮 School', data.get('school', '?')),
            ('📍 Location', data.get('location', '?')),
        ]
        for label, val in core_stats:
            html += f'''
            <div style="padding:8px 14px 8px 0;margin-bottom:6px;min-width:200px">
              <span style="color:#e94560;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:0.8px">{label}</span><br>
              <span style="color:#e0e0e0;font-size:15px;font-weight:600">{val}</span>
            </div>'''
        html += '</div>'

        if data.get('description'):
            html += f'<p style="color:#bbb;margin:10px 0 16px 0;padding:10px 14px;background:#0d1b2a;border-left:3px solid #e94560;border-radius:0 4px 4px 0;font-size:13px">{data["description"]}</p>'

        # Battle Stats section
        if battle_stats:
            html += '<h3 style="color:#4d96ff;margin:16px 0 8px 0;font-size:13px;text-transform:uppercase;letter-spacing:0.8px">📊 Battle Statistics</h3>'
            html += '<table style="width:100%;border-collapse:collapse">'
            for key, val in battle_stats.items():
                html += f'<tr style="border-bottom:1px solid #1a1a2e"><td style="padding:5px 10px;color:#7ab3ff;font-size:12px;width:45%">{key}</td><td style="padding:5px 10px;color:#e0e0e0;font-size:13px">{val}</td></tr>'
            html += '</table>'

        if resistances:
            html += '<h3 style="color:#6bcb77;margin:16px 0 8px 0;font-size:13px;text-transform:uppercase;letter-spacing:0.8px">🛡 Resistances & Boosts</h3>'
            html += '<table style="width:100%;border-collapse:collapse">'
            for key, val in resistances.items():
                html += f'<tr style="border-bottom:1px solid #1a1a2e"><td style="padding:5px 10px;color:#6bcb77;font-size:12px;width:45%">{key}</td><td style="padding:5px 10px;color:#e0e0e0;font-size:13px">{val}</td></tr>'
            html += '</table>'

        if data.get('url'):
            html += f'<p style="margin-top:14px"><a style="color:#4d96ff;font-size:12px" href="{data["url"]}">📖 View on Wiki</a></p>'
        if data.get('last_updated_at'):
            from datetime import datetime
            updated = datetime.fromtimestamp(data['last_updated_at']).strftime('%Y-%m-%d %H:%M')
            html += f'<p style="color:#555;font-size:11px;margin-top:6px">Last updated: {updated}</p>'
        html += '</div>'
        self.info_display.setHtml(html)

        # ── Spells ──
        spells = data.get('spells', [])
        if spells:
            html = '<h2 style="color:#e94560;margin-bottom:12px">✨ Known Spells</h2>'
            for s in spells:
                html += f'<div style="padding:4px 10px;margin:2px 0;background:#0d1b2a;border-radius:4px;color:#e0e0e0">* {s}</div>'
            self.spells_display.setHtml(html)
        else:
            self.spells_display.setHtml('<p style="color:#999;font-style:italic">No spells data.</p>')

        # ── Drops ──
        drops = data.get('drops', [])
        if drops:
            html = '<h2 style="color:#e94560;margin-bottom:12px">🎁 Drops</h2>'
            for d in drops:
                html += f'<div style="padding:4px 10px;margin:2px 0;background:#0d1b2a;border-radius:4px;color:#e0e0e0">* {d}</div>'
            self.drops_display.setHtml(html)
        else:
            self.drops_display.setHtml('<p style="color:#999;font-style:italic">No drop data.</p>')

        # ── Minions ──
        minions = data.get('minions', [])
        if minions:
            html = '<h2 style="color:#e94560;margin-bottom:12px">👾 Minions</h2>'
            for m in minions:
                if isinstance(m, dict):
                    html += f'<div style="padding:8px;margin:4px 0;background:#0d1b2a;border-radius:6px"><b style="color:#ffd93d">{m.get("name", "?")}</b></div>'
                else:
                    html += f'<div style="padding:4px 10px;color:#e0e0e0">* {m}</div>'
            self.minions_display.setHtml(html)
        else:
            self.minions_display.setHtml('<p style="color:#999;font-style:italic">No minion data.</p>')

        # ── Round Counters Tab ──
        self._populate_round_counters_tab(boss_name)

        # ── Guides Tab ──
        self._populate_guides_tab(boss_name)

    def _populate_round_counters_tab(self, boss_name: str):
        """Populate the Round Counters tab with counters linked to this boss."""
        # Clear layout
        while self.round_tab_layout.count() > 1:
            item = self.round_tab_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        counters = db.get_counters_for_boss(self.conn, boss_name)

        if not counters:
            msg = QLabel(
                f"<div style='text-align:center;padding:20px'>"
                f"<p style='color:#555;font-size:13px'>No round counters linked to <b style='color:#e94560'>{boss_name}</b>.</p>"
                f"</div>"
            )
            msg.setAlignment(Qt.AlignCenter)
            msg.setWordWrap(True)
            self.round_tab_layout.insertWidget(0, msg)
        else:
            for counter in counters:
                card = QFrame()
                card.setStyleSheet("""
                    QFrame {
                        background:#16213e;
                        border:1px solid #0f3460;
                        border-radius:8px;
                    }
                """)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(0, 0, 0, 6)
                card_layout.setSpacing(0)

                strip = QHBoxLayout()
                strip.setContentsMargins(12, 8, 8, 4)
                strip.addStretch()
                edit_btn = QPushButton("✏ Edit")
                edit_btn.setStyleSheet("background:#0f3460;color:#e0e0e0;border:none;border-radius:4px;"
                                       "padding:3px 10px;font-size:11px;")
                edit_btn.clicked.connect(lambda checked, c=counter: self._edit_counter_from_tab(c))
                strip.addWidget(edit_btn)

                unlink_btn = QPushButton("Unlink")
                unlink_btn.setStyleSheet("background:#5c1b1b;color:#e0e0e0;border:none;border-radius:4px;"
                                         "padding:3px 10px;font-size:11px;")
                unlink_btn.clicked.connect(lambda checked, c=counter, bn=boss_name: self._unlink_counter_from_boss(c, bn))
                strip.addWidget(unlink_btn)
                card_layout.addLayout(strip)

                live = RoundCounterWidget(counter, parent=card)
                card_layout.addWidget(live)

                self.round_tab_layout.insertWidget(self.round_tab_layout.count() - 1, card)

        # Always show link button
        link_btn = QPushButton("＋ Link a Counter to this Boss")
        link_btn.setStyleSheet("""
            QPushButton { background:#0f1b2a; color:#4d96ff; border:1px dashed #1f3a6e;
                          border-radius:6px; padding:8px; font-size:12px; }
            QPushButton:hover { background:#1a3060; }
        """)
        link_btn.clicked.connect(lambda: self._link_existing_counter_to_boss(boss_name))
        self.round_tab_layout.insertWidget(self.round_tab_layout.count() - 1, link_btn)

    def _edit_counter_from_tab(self, counter: dict):
        full = db.get_round_counter(self.conn, counter['id'])
        dlg = RoundCounterEditor(self.conn, self.boss_names, existing=full, parent=self)
        dlg.setStyleSheet(ROUND_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            # Re-display current boss to refresh tab
            name = counter['linked_bosses'][0] if counter.get('linked_bosses') else None
            current = self.boss_tree.currentItem()
            if current:
                bn = current.data(0, Qt.UserRole)
                if bn:
                    self._populate_round_counters_tab(bn)
            if self.counter_panel.isVisible():
                self.counter_panel.refresh()

    def _unlink_counter_from_boss(self, counter: dict, boss_name: str):
        """Remove this boss from the counter's linked_bosses list."""
        full = db.get_round_counter(self.conn, counter['id'])
        if not full:
            return
        updated_bosses = [b for b in full['linked_bosses'] if b.lower() != boss_name.lower()]
        full['linked_bosses'] = updated_bosses
        db.upsert_round_counter(self.conn, full)
        self._populate_round_counters_tab(boss_name)
        if self.counter_panel.isVisible():
            self.counter_panel.refresh()

    def _link_existing_counter_to_boss(self, boss_name: str):
        """Open a picker to link an existing counter (or create new) to this boss."""
        counters = db.list_round_counters(self.conn)
        if not counters:
            # No counters exist — open editor
            dlg = RoundCounterEditor(self.conn, self.boss_names,
                                     existing={'linked_bosses': [boss_name]}, parent=self)
            dlg.setStyleSheet(ROUND_EDITOR_STYLE)
            if dlg.exec_() == QDialog.Accepted:
                self._populate_round_counters_tab(boss_name)
                if self.counter_panel.isVisible():
                    self.counter_panel.refresh()
            return

        # Show a picker dialog
        names = [c['name'] for c in counters]
        chosen, ok = QInputDialog.getItem(self, "Link Counter",
                                          f"Select a counter to link to '{boss_name}':",
                                          names, 0, False)
        if ok and chosen:
            counter = next(c for c in counters if c['name'] == chosen)
            full = db.get_round_counter(self.conn, counter['id'])
            if boss_name not in full['linked_bosses']:
                full['linked_bosses'].append(boss_name)
                db.upsert_round_counter(self.conn, full)
            self._populate_round_counters_tab(boss_name)
            if self.counter_panel.isVisible():
                self.counter_panel.refresh()

    # ─── GUIDES TAB ─────────────────────────────────────────────

    def _populate_guides_tab(self, boss_name: str):
        """Populate the Guides tab with guides linked to this boss."""
        while self.guide_tab_layout.count() > 1:
            item = self.guide_tab_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        guides = db.get_guides_for_boss(self.conn, boss_name)

        if not guides:
            msg = QLabel(
                f"<div style='text-align:center;padding:20px'>"
                f"<p style='color:#555;font-size:13px'>No guides linked to <b style='color:#4d96ff'>{boss_name}</b>.</p>"
                f"</div>"
            )
            msg.setAlignment(Qt.AlignCenter)
            msg.setWordWrap(True)
            self.guide_tab_layout.insertWidget(0, msg)
        else:
            for guide in guides:
                card = QFrame()
                card.setStyleSheet("QFrame { background:#16213e; border:1px solid #1f3a6e; border-radius:8px; }")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(0, 0, 0, 6)
                card_layout.setSpacing(0)

                strip = QHBoxLayout()
                strip.setContentsMargins(12, 8, 8, 4)
                strip.addStretch()

                edit_btn = QPushButton("✏ Edit")
                edit_btn.setStyleSheet("background:#1f3a6e;color:#e0e0e0;border:none;border-radius:4px;"
                                       "padding:3px 10px;font-size:11px;")
                edit_btn.clicked.connect(lambda checked, g=guide: self._edit_guide_from_tab(g))
                strip.addWidget(edit_btn)

                unlink_btn = QPushButton("Unlink")
                unlink_btn.setStyleSheet("background:#5c1b1b;color:#e0e0e0;border:none;border-radius:4px;"
                                         "padding:3px 10px;font-size:11px;")
                unlink_btn.clicked.connect(lambda checked, g=guide, bn=boss_name: self._unlink_guide_from_boss(g, bn))
                strip.addWidget(unlink_btn)
                card_layout.addLayout(strip)

                view = GuideViewWidget(guide, self.conn, parent=card)
                card_layout.addWidget(view)
                self.guide_tab_layout.insertWidget(self.guide_tab_layout.count() - 1, card)

        # Always show link button
        link_btn = QPushButton("＋ Link a Guide to this Boss")
        link_btn.setStyleSheet("""
            QPushButton { background:#0f1b2a; color:#4d96ff; border:1px dashed #1f3a6e;
                          border-radius:6px; padding:8px; font-size:12px; }
            QPushButton:hover { background:#1a3060; }
        """)
        link_btn.clicked.connect(lambda: self._link_existing_guide_to_boss(boss_name))
        self.guide_tab_layout.insertWidget(self.guide_tab_layout.count() - 1, link_btn)

    def _edit_guide_from_tab(self, guide: dict):
        full = db.get_guide(self.conn, guide['id'])
        dlg = GuideEditor(self.conn, self.boss_names, existing=full, parent=self)
        dlg.setStyleSheet(GUIDE_EDITOR_STYLE)
        if dlg.exec_() == QDialog.Accepted:
            current = self.boss_tree.currentItem()
            if current:
                bn = current.data(0, Qt.UserRole)
                if bn:
                    self._populate_guides_tab(bn)
            if self.guide_panel.isVisible():
                self.guide_panel.refresh()

    def _unlink_guide_from_boss(self, guide: dict, boss_name: str):
        full = db.get_guide(self.conn, guide['id'])
        if not full:
            return
        full['linked_bosses'] = [b for b in full['linked_bosses'] if b.lower() != boss_name.lower()]
        db.upsert_guide(self.conn, full)
        self._populate_guides_tab(boss_name)
        if self.guide_panel.isVisible():
            self.guide_panel.refresh()

    def _link_existing_guide_to_boss(self, boss_name: str):
        guides = db.list_guides(self.conn)
        if not guides:
            dlg = GuideEditor(self.conn, self.boss_names,
                              existing={'linked_bosses': [boss_name]}, parent=self)
            dlg.setStyleSheet(GUIDE_EDITOR_STYLE)
            if dlg.exec_() == QDialog.Accepted:
                self._populate_guides_tab(boss_name)
                if self.guide_panel.isVisible():
                    self.guide_panel.refresh()
            return

        names = [g['name'] for g in guides]
        chosen, ok = QInputDialog.getItem(self, "Link Guide",
                                          f"Select a guide to link to '{boss_name}':",
                                          names, 0, False)
        if ok and chosen:
            guide = next(g for g in guides if g['name'] == chosen)
            full = db.get_guide(self.conn, guide['id'])
            if boss_name not in full['linked_bosses']:
                full['linked_bosses'].append(boss_name)
                db.upsert_guide(self.conn, full)
            self._populate_guides_tab(boss_name)
            if self.guide_panel.isVisible():
                self.guide_panel.refresh()

    # ─── HUD PAGE-LEVEL TOGGLE ───────────────────────────────────

    def _on_page_hud_toggle(self, key: str, checked: bool, btn: QPushButton):
        """Toggle a HUD overlay from within a feature page (not the settings page)."""
        if not HUD_AVAILABLE or not self.overlay_manager:
            return
        self.overlay_manager.toggle(key, checked)
        if checked:
            self._push_hud_data(key)
        # Sync the settings page toggle button if settings page is built
        if hasattr(self, '_hud_toggle_btns') and key in self._hud_toggle_btns:
            settings_btn = self._hud_toggle_btns[key]
            settings_btn.blockSignals(True)
            settings_btn.setChecked(checked)
            settings_btn.blockSignals(False)
        # Sync panel HUD buttons
        if key == "counter" and hasattr(self, 'counter_panel'):
            self.counter_panel.sync_hud_btn(checked)
        elif key == "guide" and hasattr(self, 'guide_panel'):
            self.guide_panel.sync_hud_btn(checked)
        # Sync the boss wiki page HUD btn
        if key == "boss" and hasattr(self, 'hud_boss_btn'):
            self.hud_boss_btn.blockSignals(True)
            self.hud_boss_btn.setChecked(checked)
            self.hud_boss_btn.blockSignals(False)

    def _wire_boss_overlay(self):
        """
        Wire search / OCR signals on the Boss HUD overlay.
        Safe to call multiple times — wires only once per overlay instance.
        """
        if not (self.overlay_manager and HUD_AVAILABLE):
            return
        boss_ov = self.overlay_manager.get_boss_overlay()
        if boss_ov and not self._boss_overlay_wired:
            boss_ov.search_requested.connect(self._on_hud_boss_search)
            boss_ov.ocr_toggled.connect(self._on_hud_boss_ocr)
            boss_ov.set_ocr_available(OCR_AVAILABLE and self.ocr_scanner is not None)
            boss_ov.set_boss_names(self.boss_names)
            # Sync current OCR running state to the overlay checkbox
            ocr_running = (self.ocr_scanner is not None
                           and self.ocr_scanner.isRunning())
            boss_ov.set_ocr_checked(ocr_running)
            self._boss_overlay_wired = True

    def _wire_calc_overlay(self):
        """Wire the Damage Calc HUD overlay's boss selection back into the app.
        Safe to call multiple times — wires only once per overlay instance."""
        if not (self.overlay_manager and HUD_AVAILABLE):
            return
        calc_ov = self.overlay_manager.get_calc_overlay()
        if calc_ov and not self._calc_overlay_wired:
            calc_ov.boss_selected.connect(self._on_calc_boss_selected)
            calc_ov.refresh_data()
            self._calc_overlay_wired = True

    def _on_calc_boss_selected(self, name: str):
        """A calculator (hub page or HUD) picked a boss — sync it everywhere."""
        self._broadcast_boss(name)

    def _broadcast_boss(self, name: str):
        """A calculator picked a boss — open it everywhere (boss wiki card, tree,
        overlays, counters and both calculator surfaces) so all the boss fields
        stay in lock-step.  set_boss_external()/update_calc_boss() don't re-emit,
        so this cannot loop."""
        if not name:
            return
        if hasattr(self, 'search_input') and self.search_input is not None:
            self.search_input.blockSignals(True)
            self.search_input.setText(name)
            self.search_input.blockSignals(False)
        # _display_boss centralises: boss card, tree, boss/counter/guide HUD and
        # the calculator surfaces.  Only call it for bosses we actually have.
        if db.get_boss(self.conn, name):
            self._display_boss(name)
        elif self.overlay_manager:
            self.overlay_manager.update_calc_boss(name)

    def _on_overlay_enabled_changed(self, key: str, enabled: bool):
        """
        Called by overlay_settings when any overlay's enabled state changes
        (including when closed via its own X button).  Syncs all UI indicators.
        """
        # Lazy-wire overlays on first enable (they're created on demand)
        if key == "boss" and enabled:
            self._wire_boss_overlay()
        if key == "calc" and enabled:
            self._wire_calc_overlay()

        color_map = {
            "boss":    "#e94560",
            "quest":   "#4d96ff",
            "counter": "#ffd93d",
            "guide":   "#c39bd3",
            "calc":    "#6bcb77",
        }
        color = color_map.get(key, "#e0e0e0")

        # Settings page cards
        if hasattr(self, '_hud_toggle_btns') and key in self._hud_toggle_btns:
            btn = self._hud_toggle_btns[key]
            btn.blockSignals(True)
            btn.setChecked(enabled)
            btn.setText("● ON" if enabled else "○ OFF")
            btn.setStyleSheet(self._overlay_btn_style(enabled, color))
            btn.blockSignals(False)

        # Boss wiki HUD button
        if key == "boss" and hasattr(self, 'hud_boss_btn'):
            self.hud_boss_btn.blockSignals(True)
            self.hud_boss_btn.setChecked(enabled)
            self.hud_boss_btn.blockSignals(False)

        # Panel HUD buttons
        if key == "counter" and hasattr(self, 'counter_panel'):
            self.counter_panel.sync_hud_btn(enabled)
        elif key == "guide" and hasattr(self, 'guide_panel'):
            self.guide_panel.sync_hud_btn(enabled)

    # ─── PANEL TOGGLES ──────────────────────────────────────────

    def _toggle_counter_panel(self, checked: bool):
        self.counter_panel.setVisible(checked)
        if checked:
            self.counter_panel.refresh()
        self._rebalance_splitter()

    def _toggle_guide_panel(self, checked: bool):
        self.guide_panel.setVisible(checked)
        if checked:
            self.guide_panel.refresh()
        self._rebalance_splitter()

    def _open_world_settings(self):
        """Open the World Settings dialog."""
        dlg = WorldSettingsManager(self.conn, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            # Rebuild the boss tree with the new ordering
            self._refresh_tree()
            # Refresh the quest tracker if it's open so it picks up
            # world deletions, level changes, and reordering
            if self._quest_tracker_window is not None and self._quest_tracker_window.isVisible():
                self._quest_tracker_window.landing.refresh()
            self.status_bar.showMessage("World order updated and applied.", 4000)

    def _open_quest_tracker(self):
        """Open the Quest Tracker as a separate floating window."""
        if not QUEST_TRACKER_AVAILABLE or QuestTrackerWindow is None:
            QMessageBox.warning(
                self, "Quest Tracker Unavailable",
                "quest_window.py could not be loaded.\n"
                "Make sure quest_window.py, quest_scraper.py and database_quests.py\n"
                "are in the same folder as boss_wiki.py."
            )
            return
        if self._quest_tracker_window is not None and self._quest_tracker_window.isVisible():
            # Refresh landing page to pick up any world changes made outside the tracker
            self._quest_tracker_window.landing.refresh()
            self._quest_tracker_window.raise_()
            self._quest_tracker_window.activateWindow()
            return
        self._quest_tracker_window = QuestTrackerWindow(self.conn, parent=self)
        self._quest_tracker_window.show()
        # Push current quest data to HUD if it's open
        if self.overlay_manager:
            self._push_quest_hud()

    def _rebalance_splitter(self):
        sizes = self.main_splitter.sizes()
        total = sum(sizes)
        show_counter = self.counter_panel.isVisible()
        show_guide = self.guide_panel.isVisible()

        if not show_counter and not show_guide:
            self.main_splitter.setSizes([int(total * 0.30), int(total * 0.70), 0, 0])
        elif show_counter and not show_guide:
            self.main_splitter.setSizes([int(total * 0.25), int(total * 0.45), int(total * 0.30), 0])
        elif not show_counter and show_guide:
            self.main_splitter.setSizes([int(total * 0.25), int(total * 0.45), 0, int(total * 0.30)])
        else:
            self.main_splitter.setSizes([int(total * 0.22), int(total * 0.36), int(total * 0.21), int(total * 0.21)])

    # ─── FETCH SINGLE BOSS (via db_builder subprocess) ──────────

    def _fetch_single(self):
        name = self.search_input.text().strip()
        if not name:
            self.status_bar.showMessage("Enter a boss name first", 3000)
            return
        # Delete existing boss + cache so db_builder does a fresh download
        existing = db.get_boss(self.conn, name)
        if existing:
            self._delete_cache_file(name)
            db.delete_boss(self.conn, name)
            logger.info(f"Cleared existing data for '{name}' before re-fetch")
        self._run_db_builder(['--test', name], f"Fetching {name}...")

    # ─── FETCH ALL BOSSES ───────────────────────────────────────

    def _fetch_all(self):
        resume = self.boss_resume_chk.isChecked()
        extra = ("\n\nResume is on: bosses already in your database will be skipped."
                 if resume else "")
        reply = QMessageBox.question(
            self, "Fetch ALL Bosses",
            "This will open Chrome to bypass Cloudflare,\n"
            "then fetch all bosses via the wiki API.\n\n"
            "This takes ~10-15 minutes. Chrome stays open during this."
            + extra + "\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        args = ["--resume"] if resume else []
        label = ("Fetching all bosses (resume — skipping already-fetched)..."
                 if resume else "Fetching all bosses...")
        self._run_db_builder(args, label)

    def _delete_all_bosses(self):
        """Delete every boss from the database after confirmation."""
        count = len(db.get_boss_names(self.conn))
        if count == 0:
            QMessageBox.information(self, "Delete All Bosses", "There are no bosses to delete.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Delete All Bosses")
        box.setText(f"Delete <b>all {count}</b> bosses?")
        box.setInformativeText(
            "This permanently removes every boss from the database. Round counters, "
            "guides, quests and your other data are not affected. You can re-fetch "
            "bosses any time with 'Fetch ALL Bosses'."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setIcon(QMessageBox.Warning)
        if box.exec_() != QMessageBox.Yes:
            return
        deleted = db.delete_all_bosses(self.conn)
        # Refresh all boss-dependent surfaces
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self.counter_panel.update_boss_names(self.boss_names)
        self.guide_panel.update_boss_names(self.boss_names)
        if self.overlay_manager:
            boss_ov = self.overlay_manager.get_boss_overlay()
            if boss_ov:
                boss_ov.set_boss_names(self.boss_names)
        self._refresh_tree()
        self._update_status_bar()
        completer = QCompleter(self.boss_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(completer)
        QMessageBox.information(self, "Deleted", f"Removed {deleted} boss(es).")

    # ─── SUBPROCESS RUNNER ──────────────────────────────────────

    def _run_db_builder(self, args: list, label: str):
        """Run db_builder.py as a subprocess with given args."""
        if self.fetch_process and self.fetch_process.state() != QProcess.NotRunning:
            self.status_bar.showMessage("A fetch is already running", 3000)
            return

        self.progress_group.setVisible(True)
        self.progress_label.setText(label)
        self.progress_output.clear()
        self.scrape_btn.setEnabled(False)
        if hasattr(self, "boss_del_all_btn"):
            self.boss_del_all_btn.setEnabled(False)

        self.fetch_process = QProcess(self)
        self.fetch_process.setProcessChannelMode(QProcess.MergedChannels)
        self.fetch_process.readyReadStandardOutput.connect(self._on_fetch_output)
        self.fetch_process.finished.connect(self._on_fetch_finished)

        cmd_args = [DB_BUILDER_SCRIPT] + args
        logger.info(f"Running: python {' '.join(cmd_args)}")
        self.fetch_process.start(sys.executable, cmd_args)

    def _on_fetch_output(self):
        data = self.fetch_process.readAllStandardOutput().data().decode('utf-8', errors='replace')
        # Cloudflare "verify you are human" checkbox appeared in the scraper's
        # Chrome window → play the alert sound so the user knows to click it.
        if cf_alert is not None and cf_alert.MARKER in data:
            self._play_cf_alert()
            data = cf_alert.strip_marker(data)
        self.progress_output.append(data.rstrip())
        # Auto-scroll
        sb = self.progress_output.verticalScrollBar()
        sb.setValue(sb.maximum())
        # Update label with last meaningful line
        for line in reversed(data.strip().split('\n')):
            line = line.strip()
            if line and not line.startswith('='):
                self.progress_label.setText(line[:100])
                break

    def _play_cf_alert(self):
        """Play the Cloudflare-challenge alert sound (best-effort, never raises)."""
        try:
            from hud_overlays import cf_alert_player
            cf_alert_player.play()
        except Exception:
            pass

    def _on_fetch_finished(self, exit_code, exit_status):
        self.scrape_btn.setEnabled(True)
        if hasattr(self, "boss_del_all_btn"):
            self.boss_del_all_btn.setEnabled(True)
        self.progress_label.setText(f"Done (exit code: {exit_code})")

        # Refresh DB
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self.counter_panel.update_boss_names(self.boss_names)
        self.guide_panel.update_boss_names(self.boss_names)
        # Update boss overlay name list
        if self.overlay_manager:
            boss_ov = self.overlay_manager.get_boss_overlay()
            if boss_ov:
                boss_ov.set_boss_names(self.boss_names)
        self._refresh_tree()
        self._update_status_bar()

        # Update completer
        completer = QCompleter(self.boss_names)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.search_input.setCompleter(completer)

        if exit_code == 0:
            self.status_bar.showMessage(f"[OK] Fetch complete! {len(self.boss_names)} bosses in DB", 10000)
        else:
            self.status_bar.showMessage(f"[FAIL] Fetch failed (exit code {exit_code})", 10000)

        # Auto-hide progress after 3 seconds
        QTimer.singleShot(3000, lambda: self.progress_group.setVisible(False))

    def _cancel_fetch(self):
        if self.fetch_process and self.fetch_process.state() != QProcess.NotRunning:
            self.fetch_process.kill()
            self.progress_label.setText(
                "Stopped — bosses already fetched are saved. "
                "Tick Resume and Fetch again to continue.")

    # ─── CACHE FILE HELPERS ────────────────────────────────────

    def _cache_dir(self) -> Path:
        """Return the wikitext_cache directory path."""
        return Path(os.path.dirname(os.path.abspath(__file__))) / "wikitext_cache"

    def _delete_cache_file(self, boss_name: str):
        """Delete the wikitext_cache .txt file for a boss, if it exists."""
        import re as _re
        safe_name = _re.sub(r'[<>:"/\\|?*]', '_', boss_name)
        cache_file = self._cache_dir() / f"{safe_name}.txt"
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.info(f"Deleted cache file: {cache_file}")
            except Exception as e:
                logger.warning(f"Could not delete cache file {cache_file}: {e}")

    # ─── REMOVE BOSS ────────────────────────────────────────────

    def _remove_boss(self):
        current = self.boss_tree.currentItem()
        if not current:
            self.status_bar.showMessage("Select a boss first", 3000)
            return

        boss_name = current.data(0, Qt.UserRole)
        if not boss_name:
            self.status_bar.showMessage("Select a boss (not a world/area)", 3000)
            return

        if not confirm_delete(self, "Remove Boss", boss_name,
                              "This will remove the boss and all its data from the local database."):
            return

        self._delete_cache_file(boss_name)
        db.delete_boss(self.conn, boss_name)
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self._refresh_tree()
        self.status_bar.showMessage(f"Removed '{boss_name}'", 5000)

    # ─── REPARSE BOSSES ──────────────────────────────────────────

    def _import_wikitext_parser(self):
        """Lazily import WikitextParser from db_builder."""
        try:
            from db_builder import WikitextParser
            return WikitextParser
        except ImportError:
            self.status_bar.showMessage(
                "Cannot import WikitextParser — db_builder.py not found", 5000
            )
            return None

    def _reparse_single(self, boss_name: str) -> bool:
        """
        Re-parse a single boss from its stored raw wikitext.
        Returns True on success, False on failure.
        """
        Parser = self._import_wikitext_parser()
        if not Parser:
            return False
        wikitext = db.get_boss_raw_wikitext(self.conn, boss_name)
        if not wikitext:
            logger.warning(f"No stored wikitext for '{boss_name}' — cannot reparse")
            return False
        try:
            data = Parser.parse_boss(wikitext, boss_name)
            # Preserve the existing wiki_path / url from the DB
            existing = db.get_boss(self.conn, boss_name)
            if existing:
                data['wiki_path'] = existing.get('wiki_path', data.get('wiki_path', ''))
                data['url'] = existing.get('url', data.get('url', ''))
            data['raw_html'] = wikitext  # keep the wikitext stored
            db.upsert_boss(self.conn, data)
            return True
        except Exception as e:
            logger.error(f"Reparse failed for '{boss_name}': {e}")
            return False

    def _reparse_selected_boss(self):
        """Reparse the currently selected boss from its stored wikitext."""
        current = self.boss_tree.currentItem()
        if not current:
            self.status_bar.showMessage("Select a boss first", 3000)
            return
        boss_name = current.data(0, Qt.UserRole)
        if not boss_name:
            self.status_bar.showMessage("Select a boss (not a world/area)", 3000)
            return
        self._reparse_boss_by_name(boss_name)

    def _reparse_boss_by_name(self, boss_name: str):
        """Reparse a single boss by name (used by context menu and toolbar)."""
        if self._reparse_single(boss_name):
            self._display_boss(boss_name)
            self.status_bar.showMessage(f"♻ Reparsed '{boss_name}'", 5000)
        else:
            self.status_bar.showMessage(
                f"Could not reparse '{boss_name}' — no stored wikitext", 5000
            )

    def _reparse_all_bosses(self):
        """Reparse ALL bosses from their stored wikitext."""
        all_wt = db.get_all_boss_raw_wikitext(self.conn)
        if not all_wt:
            self.status_bar.showMessage("No bosses with stored wikitext to reparse", 5000)
            return

        reply = QMessageBox.question(
            self, "Reparse ALL Bosses",
            f"Re-parse all {len(all_wt)} bosses from cached wikitext?\n\n"
            "This does NOT re-download anything — it just re-runs the parser\n"
            "on the stored data (useful after parser improvements).",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        Parser = self._import_wikitext_parser()
        if not Parser:
            return

        ok = errors = 0
        for boss_name, wikitext in all_wt:
            try:
                data = Parser.parse_boss(wikitext, boss_name)
                data['raw_html'] = wikitext
                db.upsert_boss(self.conn, data)
                ok += 1
            except Exception as e:
                errors += 1
                logger.error(f"Reparse error for '{boss_name}': {e}")

        self.conn.commit()
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        self._refresh_tree()

        # Re-display current boss if one is selected
        current = self.boss_tree.currentItem()
        if current:
            bn = current.data(0, Qt.UserRole)
            if bn:
                self._display_boss(bn)

        msg = f"♻ Reparsed {ok} boss{'es' if ok != 1 else ''}"
        if errors:
            msg += f" ({errors} error{'s' if errors != 1 else ''})"
        self.status_bar.showMessage(msg, 8000)
        QMessageBox.information(self, "Reparse Complete", msg)

    def _reparse_location(self, location_prefix: str):
        """Reparse all bosses under a location prefix (world/area context menu)."""
        all_wt = db.get_boss_raw_wikitext_by_location(self.conn, location_prefix)
        if not all_wt:
            self.status_bar.showMessage(
                f"No bosses with stored wikitext in '{location_prefix}'", 5000
            )
            return

        Parser = self._import_wikitext_parser()
        if not Parser:
            return

        ok = errors = 0
        for boss_name, wikitext in all_wt:
            try:
                data = Parser.parse_boss(wikitext, boss_name)
                data['raw_html'] = wikitext
                db.upsert_boss(self.conn, data)
                ok += 1
            except Exception as e:
                errors += 1
                logger.error(f"Reparse error for '{boss_name}': {e}")

        self.conn.commit()
        self._refresh_tree()

        # Re-display current boss if one is selected
        current = self.boss_tree.currentItem()
        if current:
            bn = current.data(0, Qt.UserRole)
            if bn:
                self._display_boss(bn)

        msg = f"♻ Reparsed {ok} boss{'es' if ok != 1 else ''} in '{location_prefix}'"
        if errors:
            msg += f" ({errors} error{'s' if errors != 1 else ''})"
        self.status_bar.showMessage(msg, 8000)

    # ─── BOSS OCR ────────────────────────────────────────────────

    def _on_completer_activated(self, boss_name: str):
        """User selected a name from the autocomplete dropdown — navigate directly."""
        self._in_search_mode = False
        self._display_boss(boss_name)
        self.search_input.blockSignals(True)
        self.search_input.setText(boss_name)
        self.search_input.blockSignals(False)
        self.clear_search_btn.setVisible(True)

    def _on_hud_boss_search(self, boss_name: str):
        """Boss selected in HUD overlay — navigate main app directly to that boss."""
        self._nav_to("boss_wiki")
        # Navigate directly — no FTS search, no first-result guessing
        self._display_boss(boss_name)
        # Sync the search box text in the main app
        self.search_input.blockSignals(True)
        self.search_input.setText(boss_name)
        self.search_input.blockSignals(False)
        self.clear_search_btn.setVisible(True)

    def _on_hud_boss_ocr(self, checked: bool):
        """OCR toggle triggered from the Boss HUD overlay."""
        if hasattr(self, 'ocr_toggle'):
            self.ocr_toggle.blockSignals(True)
            self.ocr_toggle.setChecked(checked)
            self.ocr_toggle.blockSignals(False)
        self._toggle_boss_ocr(checked)

    def _toggle_boss_ocr(self, checked: bool):
        if self.ocr_scanner:
            if checked:
                if not self.ocr_scanner.isRunning():
                    self.ocr_scanner.start()
                self.status_bar.showMessage("Boss OCR started — scanning full screen", 4000)
            else:
                self.ocr_scanner.stop()
                self.status_bar.showMessage("Boss OCR stopped", 3000)
        # Keep HUD overlay checkbox in sync
        if self.overlay_manager:
            boss_ov = self.overlay_manager.get_boss_overlay()
            if boss_ov:
                boss_ov.set_ocr_checked(checked)
        # Keep keybind manager state in sync so hotkey toggles correctly
        if self.keybind_manager:
            self.keybind_manager.sync_ocr_state(checked)
        # Keep the OCR checkbox in sync (needed when hotkey fires)
        if hasattr(self, 'ocr_toggle'):
            self.ocr_toggle.blockSignals(True)
            self.ocr_toggle.setChecked(checked)
            self.ocr_toggle.blockSignals(False)

    # Legacy single-toggle kept so any code that calls _toggle_ocr still works
    def _toggle_ocr(self, checked: bool):
        self._toggle_boss_ocr(checked)

    def _on_ocr_detected(self, boss_name: str):
        """Legacy single-boss signal — kept for API compatibility but not connected."""
        pass

    def _on_bosses_detected(self, boss_names: list):
        """
        Multi-boss signal — shows the detected name in the search bar and opens
        the boss card if it's in the local DB.
        Uses blockSignals so setting the text doesn't trigger the completer or
        _on_search_text_changed, which would cause cycling/interference.
        """
        if not boss_names:
            return

        names_str = " + ".join(boss_names)

        # Find the first boss that's already in the local DB
        first_in_db = None
        for name in boss_names:
            if db.get_boss(self.conn, name):
                first_in_db = name
                break

        if first_in_db:
            # Navigate to boss wiki if not already there
            self._nav_to("boss_wiki")

            # Set search text with signals blocked — prevents completer/textChanged loop
            self.search_input.blockSignals(True)
            self.search_input.setText(first_in_db)
            self.clear_search_btn.setVisible(True)
            self.search_input.blockSignals(False)

            # Open the card directly
            self._display_boss(first_in_db)

            # Keep the calculator (hub page + HUD overlay) in sync with OCR
            if getattr(self, "_calc_page", None):
                self._calc_page.calc_panel.set_boss_external(first_in_db)
            if self.overlay_manager:
                self.overlay_manager.update_calc_boss(first_in_db)

            if len(boss_names) > 1:
                others = ", ".join(b for b in boss_names if b != first_in_db)
                self.status_bar.showMessage(
                    f"Boss OCR: {first_in_db}  |  Also detected: {others}", 8000)
            else:
                self.status_bar.showMessage(f"Boss OCR: opened '{first_in_db}'", 5000)
        else:
            # Not in DB — just show in status bar, don't touch the search field
            self.status_bar.showMessage(
                f"Boss OCR detected: {names_str} — not in local DB (use Fetch from Wiki to add)",
                8000,
            )

    def _update_ocr_debug(self, text: str):
        self.ocr_debug_display.setPlainText(text)

    # ─── QUEST OCR ───────────────────────────────────────────────
    # (Quest OCR removed — boss OCR only)

    def _refresh_quest_ocr_names(self):
        pass  # no-op, kept for safe call sites

    # ─── UTILITIES ──────────────────────────────────────────────

    def _update_status_bar(self):
        stats = db.get_stats(self.conn)
        self.status_bar.showMessage(
            f"📊 {stats['active']} bosses in DB  |  "
            f"OCR: {'ready' if OCR_AVAILABLE else 'not installed'}  |  "
            f"Fetch uses db_builder.py"
        )

    def _run_backup(self):
        """Save a full backup of all data to a file."""
        if not EXPORTER_AVAILABLE:
            return
        try:
            exp.create_backup(self.conn, self)
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox as _MB
            _MB.critical(self, "Backup failed", str(e))

    def _run_import(self):
        """Open a backup file and selectively restore chosen categories."""
        if not EXPORTER_AVAILABLE:
            return
        try:
            import importer as imp_mod
        except ImportError:
            from PyQt5.QtWidgets import QMessageBox as _MB
            _MB.warning(self, "Import unavailable", "importer.py not found.")
            return
        imp_mod.import_backup(self.conn, self)
        # Refresh all panels after import
        self._refresh_tree()
        self.boss_names = db.get_boss_names(self.conn)
        if self.ocr_scanner:
            self.ocr_scanner.set_known_names(self.boss_names)
        if hasattr(self, 'counter_panel') and self.counter_panel.isVisible():
            self.counter_panel.refresh()
        if hasattr(self, 'guide_panel') and self.guide_panel.isVisible():
            self.guide_panel.refresh()
        # Refresh calculator dropdowns (new bosses / presets / wizards may exist)
        if getattr(self, "_calc_page", None):
            self._calc_page.refresh()
        if self.overlay_manager:
            self.overlay_manager.refresh_calc_data()

    def closeEvent(self, event):
        if self.ocr_scanner:
            self.ocr_scanner.stop()
        if self.fetch_process and self.fetch_process.state() != QProcess.NotRunning:
            self.fetch_process.kill()
        for w in (getattr(self, "_update_check_worker", None),
                  getattr(self, "_update_pull_worker", None)):
            if w is not None and w.isRunning():
                w.wait(3000)
        if self.overlay_manager:
            self.overlay_manager.close_all()
        if self.keybind_manager:
            self.keybind_manager.cleanup()
        self.conn.close()
        event.accept()


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def main():
    # ── Global UI scale — MUST run before QApplication is created,
    #    because Qt reads QT_SCALE_FACTOR at QApplication construction. ──
    _ui_scale_applied = 1.0
    if UI_SCALE_AVAILABLE and ui_scale is not None:
        try:
            _ui_scale_applied = ui_scale.apply_to_environment()
        except Exception as _e:
            print(f"[WARN] Could not apply UI scale: {_e}")

    print("=" * 60)
    print("  Wizard101 Companion - Local Edition")
    print("  Boss Wiki  •  Gear Guide  •  Quest Tracker  •  OCR")
    print("=" * 60)
    print()
    if UI_SCALE_AVAILABLE and ui_scale is not None:
        print(f"  UI scale:      {int(round(_ui_scale_applied * 100))}%")
    print(f"  PyQt5:         [OK] loaded")
    print(f"  db_builder:    {'[OK] found' if os.path.exists(DB_BUILDER_SCRIPT) else '[FAIL] NOT FOUND'}")
    if OCR_AVAILABLE:
        print(f"  OCR:           [OK] loaded")
    else:
        try:
            from ocr_module import _OCR_LOAD_ERROR as _ocr_msg
            reason = _ocr_msg or "not installed"
        except Exception:
            reason = "not installed"
        print(f"  OCR:           [DISABLED] {reason}")
    print(f"  Quest Tracker: {'[OK] loaded' if QUEST_TRACKER_AVAILABLE else '[FAIL] quest_window.py not found'}")
    print(f"  Gear Guide:    {'[OK] loaded' if GEAR_GUIDE_AVAILABLE else '[FAIL] gear_guide.py not found'}")
    print()

    app = QApplication(sys.argv)
    app.setApplicationName("Wizard101 Companion")
    window = BossWikiApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
