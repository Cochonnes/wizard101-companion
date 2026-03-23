"""
Quest Tracker Window — Wizard101 Companion
══════════════════════════════════════════
Features:
  • World landing page (grid of world cards with stats)
  • Per-world quest tree with area headers and coloured quest type badges
  • User markers (free-text notes + completion checkbox) per quest
  • Debug info panel per-world to diagnose zero-quest scraping issues
  • World Management dialog: add / delete worlds, drag-drop reorder,
    set/clear per-world source URL
  • Background scrape/update with graceful HTTP error handling
"""

import sys
import json
import time
import logging
from typing import Optional, Dict, List

from PyQt5.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QGridLayout,
    QStackedWidget, QProgressBar, QTextEdit, QLineEdit,
    QCheckBox, QApplication, QSizePolicy, QTabWidget,
    QMessageBox, QToolButton, QListWidget, QListWidgetItem,
    QInputDialog, QAbstractItemView,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QCursor

import database as db
import database_quests as dq

try:
    import exporter as _exp
    _EXPORTER_AVAILABLE = True
except ImportError:
    _exp = None
    _EXPORTER_AVAILABLE = False

try:
    import quest_scraper as qs
    SCRAPER_AVAILABLE = qs.SCRAPER_AVAILABLE
except ImportError:
    SCRAPER_AVAILABLE = False
    qs = None

logger = logging.getLogger(__name__)

# ── Default world order ───────────────────────────────────────────────────────
DEFAULT_WORLD_ORDER = dq.WORLD_DISPLAY_ORDER

# ── World accent colours ──────────────────────────────────────────────────────
WORLD_COLORS = {
    "Wizard City": "#4d96ff", "Krokotopia": "#ffaa22", "Grizzleheim": "#88ccff",
    "Marleybone": "#aaaaaa",  "MooShu": "#ff88aa",     "Dragonspyre": "#ff4444",
    "Celestia": "#22ddff",    "Zafaria": "#44cc66",    "Wysteria": "#cc88ff",
    "Avalon": "#4488ff",      "Azteca": "#ffcc44",     "Aquila": "#88aaff",
    "Khrysalis": "#ff8844",   "Polaris": "#aaddff",    "Arcanum": "#cc44ff",
    "Mirage": "#ffdd88",      "Empyrea": "#44ffcc",    "Karamelle": "#ff88cc",
    "Lemuria": "#44ffaa",     "Novus": "#88ddff",      "Wallaru": "#ffaa66",
    "Selenopolis": "#cc88ff", "Darkmoor": "#8844ff",
}

QUEST_TYPE_COLORS = {
    # Exact colors extracted from FinalBastion HTML span styles
    "talk":             "#c8c8c8",   # plain/white
    "mob":              "#99cc00",   # yellow-green
    "elite":            "#99cc00",   # same as mob
    "d&c":              "#00ccff",   # cyan
    "boss":             "#ff99cc",   # pink/rose
    "minor cheat":      "#ff99cc",   # pink (same as boss)
    "cheat":            "#ff0000",   # red
    "major cheat":      "#ff0000",   # red
    "quadruple cheat":  "#ff0000",   # red
    "solo minor cheat": "#ff99cc",   # pink
    "solo major cheat": "#ff0000",   # red
    "instance":         "#cc99ff",   # light purple
    "puzzle":           "#3366ff",   # blue
    "interact":         "#c8c8c8",   # plain
    "collect":          "#c8c8c8",   # plain
    "explore":          "#c8c8c8",   # plain
    "solo":             "#ffcc00",   # gold
}

# ─────────────────────────────────────────────────────────────────────────────
# STYLESHEET
# ─────────────────────────────────────────────────────────────────────────────

QUEST_STYLE = """
QDialog, QWidget {
    background-color: #12121f;
    color: #e0e0e0;
    font-family: 'Segoe UI', Tahoma, sans-serif;
    font-size: 13px;
}
QPushButton {
    background-color: #1a2a4a; color: #e0e0e0;
    border: none; border-radius: 6px;
    padding: 7px 16px; font-size: 12px; font-weight: bold;
}
QPushButton:hover  { background-color: #2a3a6a; }
QPushButton:pressed { background-color: #0f3460; }
QPushButton:disabled { background-color: #1a1a2e; color: #555; }
QPushButton#btnBack   { background:#1a2030; border:1px solid #2a3a5a; padding:6px 14px; }
QPushButton#btnBack:hover { background:#2a3050; }
QPushButton#btnFetch  { background:#1b5c38; }
QPushButton#btnFetch:hover { background:#27ae60; }
QPushButton#btnFetchAll { background:#1b5c38; }
QPushButton#btnFetchAll:hover { background:#27ae60; }
QPushButton#btnUpdate { background:#1f3a6e; }
QPushButton#btnUpdate:hover { background:#4d96ff; }
QPushButton#btnManage { background:#2a1f6e; }
QPushButton#btnManage:hover { background:#7755ff; }
QPushButton#btnDanger { background:#5c1b1b; }
QPushButton#btnDanger:hover { background:#e94560; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QFrame#worldCard { background:#1a1a2e; border:1px solid #1f3060; border-radius:10px; }
QFrame#worldCard:hover { border-color:#4d96ff; }
QFrame#questRow { background:transparent; border-bottom: 1px solid #1a1a2e; }
QFrame#questRow:hover { background: #141428; }
QLineEdit {
    background:#1a1a2e; color:#e0e0e0;
    border:1px solid #2a3a5a; border-radius:5px;
    padding:5px 8px; font-size:12px;
}
QLineEdit:focus { border-color:#4d96ff; }
QCheckBox { spacing:6px; }
QCheckBox::indicator { width:15px; height:15px; border:2px solid #2a3a5a;
                       border-radius:3px; background:#12121f; }
QCheckBox::indicator:checked { background:#27ae60; border-color:#27ae60; }
QProgressBar { background:#1a1a2e; border:1px solid #2a3a5a; border-radius:4px;
               height:10px; text-align:center; font-size:10px; color:#888; }
QProgressBar::chunk { background:#4d96ff; border-radius:3px; }
QListWidget { background:#1a1a2e; color:#e0e0e0; border:1px solid #2a3a5a;
              border-radius:6px; font-size:13px; }
QListWidget::item { padding:8px 10px; border-bottom:1px solid #1f2a3a; }
QListWidget::item:selected { background:#1f3a6e; }
QListWidget::item:hover { background:#1a2a3a; }
"""


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND SCRAPE WORKER
# ─────────────────────────────────────────────────────────────────────────────

class ScrapeWorker(QThread):
    progress    = pyqtSignal(str)
    world_done  = pyqtSignal(str, bool, str)  # world_name, success, debug_info
    all_done    = pyqtSignal()

    def __init__(self, db_path: str, worlds: list, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.worlds  = worlds
        self._abort  = False

    def abort(self):
        self._abort = True

    def run(self):
        if not SCRAPER_AVAILABLE or qs is None:
            self.progress.emit(
                "❌ requests/beautifulsoup4 not installed.\n"
                "Run: pip install requests beautifulsoup4"
            )
            self.all_done.emit()
            return

        # Each thread must have its own SQLite connection
        thread_conn = db.get_connection(self.db_path)
        dq.init_quest_tables(thread_conn)

        try:
            for entry in self.worlds:
                if self._abort:
                    break
                world_name = entry["world"]
                url        = entry.get("url", "")

                if not url:
                    self.progress.emit(f"⚠ {world_name}: no URL set — skipped")
                    self.world_done.emit(world_name, False, "No URL configured")
                    continue

                self.progress.emit(f"⏳ Scraping {world_name}…")
                try:
                    data = qs.scrape_world_guide(url, world_name)
                    dq.import_world_data(thread_conn, data)
                    total = sum(len(a["quests"]) for a in data.get("areas", []))
                    debug = data.get("debug_info", "")
                    if total == 0:
                        self.progress.emit(
                            f"⚠ {world_name}: scraped OK but 0 quests found — "
                            "check URL or page structure"
                        )
                        self.world_done.emit(world_name, False, debug)
                    else:
                        self.progress.emit(f"✅ {world_name}: {total} quests imported")
                        self.world_done.emit(world_name, True, debug)

                except Exception as e:
                    # Includes requests.HTTPError (404, etc.) and parse errors
                    err_msg = str(e)
                    logger.exception(f"Error scraping {world_name}")
                    self.progress.emit(f"❌ {world_name}: {err_msg}")
                    self.world_done.emit(world_name, False, err_msg)

                time.sleep(0.8)
        finally:
            thread_conn.close()

        self.all_done.emit()


# ─────────────────────────────────────────────────────────────────────────────
# RE-PARSE WORKER (from cached files, no network)
# ─────────────────────────────────────────────────────────────────────────────

class ReparseWorker(QThread):
    """Re-parse all cached worlds from their saved plain text files."""
    progress   = pyqtSignal(str)
    world_done = pyqtSignal(str, bool, str)
    all_done   = pyqtSignal()

    def __init__(self, db_path: str, worlds: list, parent=None):
        """worlds: list of world name strings to re-parse."""
        super().__init__(parent)
        self.db_path = db_path
        self.worlds  = worlds
        self._abort  = False

    def abort(self):
        self._abort = True

    def run(self):
        if qs is None:
            self.progress.emit("❌ quest_scraper not available")
            self.all_done.emit()
            return

        thread_conn = db.get_connection(self.db_path)
        dq.init_quest_tables(thread_conn)

        try:
            for world_name in self.worlds:
                if self._abort:
                    break
                self.progress.emit(f"♻ Re-parsing {world_name} from cache…")
                try:
                    data = qs.reparse_from_cache(world_name)
                    if data is None:
                        self.progress.emit(f"⚠ {world_name}: no cached files found")
                        self.world_done.emit(world_name, False, "No cache")
                        continue
                    # Preserve existing source_url
                    existing = dq.get_world_by_name(thread_conn, world_name)
                    if existing and existing.get("source_url"):
                        data["source_url"] = existing["source_url"]
                    dq.import_world_data(thread_conn, data)
                    total = sum(len(a["quests"]) for a in data.get("areas", []))
                    self.progress.emit(f"✅ {world_name}: {total} quests re-imported")
                    self.world_done.emit(world_name, True, data.get("debug_info", ""))
                except Exception as e:
                    logger.exception(f"Reparse error: {world_name}")
                    self.progress.emit(f"❌ {world_name}: {e}")
                    self.world_done.emit(world_name, False, str(e))
        finally:
            thread_conn.close()

        self.all_done.emit()


# ─────────────────────────────────────────────────────────────────────────────
# DEBUG VIEW DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class DebugViewDialog(QDialog):
    """
    Shows the debug files for a world:
    - Plain text (what we parsed)
    - Parse log (line-by-line decisions)
    - Raw HTML path (too large to show, but opens folder)
    Also has a 'Re-parse from cache' button.
    """

    reparse_requested = pyqtSignal(str)  # world_name

    def __init__(self, world_name: str, parent=None):
        super().__init__(parent)
        self.world_name = world_name
        self.setWindowTitle(f"Debug — {world_name}")
        self.setMinimumSize(900, 600)
        self.resize(1000, 680)
        self.setStyleSheet(QUEST_STYLE + """
            QTabWidget::pane { background:#0f1830; border:1px solid #1f3060; }
            QTabBar::tab { background:#12121f; color:#888; padding:6px 16px;
                           border:1px solid #1f3060; border-bottom:none; }
            QTabBar::tab:selected { background:#0f1830; color:#e0e0e0; }
            QTextEdit { background:#0a0a14; color:#c8ffc8; font-family:Consolas,monospace;
                        font-size:11px; border:none; }
        """)
        self._build()
        self._load()

    def _build(self):
        lo = QVBoxLayout(self)
        lo.setContentsMargins(12, 12, 12, 12)
        lo.setSpacing(8)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel(
            f"<b style='color:#ffdd88;font-size:14px'>🔍 Debug: {self.world_name}</b>"
        ))
        hdr.addStretch()
        open_btn = QPushButton("📂 Open Folder")
        open_btn.clicked.connect(self._open_folder)
        hdr.addWidget(open_btn)
        reparse_btn = QPushButton("♻ Re-parse from Cache")
        reparse_btn.setObjectName("btnUpdate")
        reparse_btn.clicked.connect(lambda: self.reparse_requested.emit(self.world_name))
        hdr.addWidget(reparse_btn)
        lo.addLayout(hdr)

        self.tabs = QTabWidget()
        lo.addWidget(self.tabs, stretch=1)

        self.plain_edit = QTextEdit(); self.plain_edit.setReadOnly(True)
        self.log_edit   = QTextEdit(); self.log_edit.setReadOnly(True)
        self.tabs.addTab(self.plain_edit, "📄 Plain Text")
        self.tabs.addTab(self.log_edit,   "📋 Parse Log")

        # Bottom status
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color:#666;font-size:11px;")
        lo.addWidget(self.status_lbl)

    def _load(self):
        if qs is None:
            self.status_lbl.setText("quest_scraper not available")
            return

        slug = qs._slug(self.world_name)
        d    = qs._world_debug_dir(self.world_name)

        plain_path = d / f"{slug}_plain.txt"
        log_path   = d / f"{slug}_parse_log.txt"
        raw_path   = d / f"{slug}_raw.html"

        if plain_path.exists():
            self.plain_edit.setPlainText(plain_path.read_text(encoding="utf-8", errors="replace"))
        else:
            self.plain_edit.setPlainText(
                f"No plain text file found.\n"
                f"Expected: {plain_path}\n\n"
                f"Run 'Update from FinalBastion' to fetch this world first."
            )

        if log_path.exists():
            self.log_edit.setPlainText(log_path.read_text(encoding="utf-8", errors="replace"))
        else:
            self.log_edit.setPlainText("No parse log yet — fetch the world first.")

        parts = []
        if plain_path.exists():
            size = plain_path.stat().st_size
            parts.append(f"plain.txt: {size:,} bytes")
        if log_path.exists():
            parts.append("parse_log: ✓")
        if raw_path.exists():
            size = raw_path.stat().st_size
            parts.append(f"raw.html: {size:,} bytes")
        parts.append(f"folder: {d}")
        self.status_lbl.setText("  |  ".join(parts))

    def _open_folder(self):
        if qs is None:
            return
        import subprocess, sys
        d = qs._world_debug_dir(self.world_name)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(d)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(d)])
        else:
            subprocess.Popen(["xdg-open", str(d)])


# ─────────────────────────────────────────────────────────────────────────────
# WORLD MANAGEMENT DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class WorldManagementDialog(QDialog):
    """
    Manage the world list:
    • Add new world (custom name)
    • Delete existing world (and its quest data)
    • Drag-and-drop reorder
    • Set / clear source URL per world
    """

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("World Management")
        self.setMinimumSize(620, 560)
        self.resize(680, 620)
        self.setStyleSheet(QUEST_STYLE)
        self._build()
        self._load()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("<b style='color:#7755ff;font-size:15px'>🌍 World Management</b>")
        layout.addWidget(title)

        hint = QLabel(
            "Drag rows to reorder  •  Select a world to edit its URL  •  "
            "Double-click to rename"
        )
        hint.setStyleSheet("color:#666;font-size:11px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # List widget (drag-drop enabled)
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.MoveAction)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._rename_world)
        layout.addWidget(self.list_widget, stretch=1)

        # URL editor (shown when a world is selected)
        self.url_frame = QFrame()
        self.url_frame.setStyleSheet(
            "QFrame { background:#0f1830; border:1px solid #2a3a5a; border-radius:6px; }"
        )
        url_layout = QVBoxLayout(self.url_frame)
        url_layout.setContentsMargins(12, 10, 12, 10)
        url_layout.setSpacing(6)

        self.url_world_lbl = QLabel("")
        self.url_world_lbl.setStyleSheet("color:#7755ff;font-weight:bold;font-size:13px;")
        url_layout.addWidget(self.url_world_lbl)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "https://finalbastion.com/wizard101-guides/w101-quest-guides/…"
        )
        url_row.addWidget(self.url_input, stretch=1)
        save_url_btn = QPushButton("💾 Save URL")
        save_url_btn.setObjectName("btnFetch")
        save_url_btn.clicked.connect(self._save_url)
        url_row.addWidget(save_url_btn)
        clear_url_btn = QPushButton("✕ Clear")
        clear_url_btn.setObjectName("btnDanger")
        clear_url_btn.clicked.connect(self._clear_url)
        url_row.addWidget(clear_url_btn)
        url_layout.addLayout(url_row)
        layout.addWidget(self.url_frame)
        self.url_frame.hide()

        # Bottom buttons
        btn_row = QHBoxLayout()
        add_btn = QPushButton("＋ Add World")
        add_btn.setObjectName("btnFetch")
        add_btn.clicked.connect(self._add_world)
        btn_row.addWidget(add_btn)

        self.del_btn = QPushButton("🗑 Delete Selected")
        self.del_btn.setObjectName("btnDanger")
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(self._delete_world)
        btn_row.addWidget(self.del_btn)

        if _EXPORTER_AVAILABLE:
            self.exp_world_btn = QPushButton("📤 Export Selected")
            self.exp_world_btn.setObjectName("btnUpdate")
            self.exp_world_btn.setEnabled(False)
            self.exp_world_btn.setToolTip("Export this world's quests to JSON")
            self.exp_world_btn.clicked.connect(self._export_world)
            btn_row.addWidget(self.exp_world_btn)
        else:
            self.exp_world_btn = None

        sort_btn = QPushButton("↕ Sort Chronologically")
        sort_btn.setObjectName("btnUpdate")
        sort_btn.setToolTip("Re-sort worlds by the canonical story order")
        sort_btn.clicked.connect(self._sort_chronologically)
        btn_row.addWidget(sort_btn)

        btn_row.addStretch()
        close_btn = QPushButton("✓ Done")
        close_btn.clicked.connect(self._save_order_and_close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _load(self):
        self.list_widget.clear()
        worlds = dq.get_all_worlds(self.conn)
        for w in worlds:
            item = QListWidgetItem()
            url = w.get("source_url", "")
            url_indicator = "🔗" if url else "○"
            item.setText(f"{url_indicator}  {w['name']}")
            item.setData(Qt.UserRole, w)
            self.list_widget.addItem(item)

    def _current_world(self):
        items = self.list_widget.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _on_selection_changed(self):
        world = self._current_world()
        if world:
            self.url_world_lbl.setText(f"URL for: {world['name']}")
            self.url_input.setText(world.get("source_url", "") or "")
            self.url_frame.show()
            self.del_btn.setEnabled(True)
            if self.exp_world_btn:
                self.exp_world_btn.setEnabled(True)
        else:
            self.url_frame.hide()
            self.del_btn.setEnabled(False)
            if self.exp_world_btn:
                self.exp_world_btn.setEnabled(False)

    def _save_url(self):
        world = self._current_world()
        if not world:
            return
        url = self.url_input.text().strip()
        # Update in DB
        dq.upsert_world(self.conn, {**world, "source_url": url})
        self.conn.commit()
        # Refresh item text
        self._refresh_item(world["name"])
        # Update UserRole data
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            d = item.data(Qt.UserRole)
            if d and d["name"] == world["name"]:
                d["source_url"] = url
                item.setData(Qt.UserRole, d)
                break

    def _clear_url(self):
        self.url_input.clear()
        self._save_url()

    def _refresh_item(self, world_name: str):
        world = dq.get_world_by_name(self.conn, world_name)
        if not world:
            return
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            d = item.data(Qt.UserRole)
            if d and d["name"] == world_name:
                url = world.get("source_url", "")
                url_indicator = "🔗" if url else "○"
                item.setText(f"{url_indicator}  {world_name}")
                item.setData(Qt.UserRole, world)
                break

    def _add_world(self):
        name, ok = QInputDialog.getText(
            self, "Add World", "World name:",
            text=""
        )
        name = name.strip()
        if not ok or not name:
            return
        # Check duplicate
        existing = dq.get_world_by_name(self.conn, name)
        if existing:
            QMessageBox.information(self, "Exists", f"'{name}' already exists.")
            return
        wid = dq.upsert_world(self.conn, {
            "name": name,
            "display_order": self.list_widget.count() + 100,
        })
        self.conn.commit()
        self._load()

    def _rename_world(self, item: QListWidgetItem):
        world = item.data(Qt.UserRole)
        if not world:
            return
        old_name = world["name"]
        new_name, ok = QInputDialog.getText(
            self, "Rename World", "New name:", text=old_name
        )
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        # Check duplicate
        if dq.get_world_by_name(self.conn, new_name):
            QMessageBox.warning(self, "Duplicate", f"'{new_name}' already exists.")
            return
        self.conn.execute(
            "UPDATE quest_worlds SET name=? WHERE id=?", (new_name, world["id"])
        )
        self.conn.commit()
        self._load()

    def _delete_world(self):
        world = self._current_world()
        if not world:
            return
        # Two-step confirm, no OS sounds
        box = QMessageBox(self); box.setWindowTitle("Delete World")
        box.setText(f"Delete <b>{world['name']}</b> and all its quest data?")
        box.setInformativeText("All quests, areas and markers for this world will be removed.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No); box.setIcon(QMessageBox.NoIcon)
        if box.exec_() == QMessageBox.Yes:
            dq.delete_world_data(self.conn, world["id"])
            self._load()
            self.url_frame.hide()
            self.del_btn.setEnabled(False)

    def _export_world(self):
        """Export the selected quest world to JSON."""
        world = self._current_world()
        if world and _EXPORTER_AVAILABLE:
            _exp.export_quest_world(self.conn, world["id"], self)

    def _sort_chronologically(self):
        """Re-sort the list by the canonical world order from world_order.json."""
        import json as _json, os as _os
        _order_file = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "world_order.json")
        canonical = dq.WORLD_DISPLAY_ORDER  # fallback
        if _os.path.exists(_order_file):
            try:
                canonical = _json.loads(open(_order_file, encoding="utf-8").read())
            except Exception:
                pass
        
        # Pull all items
        items_data = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            items_data.append(item.data(Qt.UserRole))
        
        def sort_key(w):
            name = w["name"]
            try:
                return canonical.index(name)
            except ValueError:
                return 9999 + ord(name[0]) if name else 9999
        
        items_data.sort(key=sort_key)
        self.list_widget.clear()
        for w in items_data:
            item = QListWidgetItem()
            url = w.get("source_url", "")
            url_indicator = "🔗" if url else "○"
            item.setText(f"{url_indicator}  {w['name']}")
            item.setData(Qt.UserRole, w)
            self.list_widget.addItem(item)

    def _save_order_and_close(self):
        """Persist the current list order to display_order column."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            world = item.data(Qt.UserRole)
            if world:
                self.conn.execute(
                    "UPDATE quest_worlds SET display_order=? WHERE id=?",
                    (i, world["id"])
                )
        self.conn.commit()
        self.accept()


# ─────────────────────────────────────────────────────────────────────────────
# MARKER DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class MarkerDialog(QDialog):
    """
    Popup to set/clear a marker on a quest.
    The note is optional — saving with an empty note creates a bookmark (📌 with no text).
    """

    def __init__(self, quest: dict, existing_marker: Optional[dict], parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.quest    = quest
        self.existing = existing_marker or {}
        self.setMinimumWidth(340)
        self.setStyleSheet("""
            QDialog { background:#1a1a2e; border:1px solid #2a4a8a; border-radius:8px; }
            QLabel  { color:#e0e0e0; }
            QLineEdit { background:#12121f; color:#e0e0e0; border:1px solid #2a3a5a;
                        border-radius:5px; padding:6px 10px; font-size:13px; }
            QLineEdit:focus { border-color:#4d96ff; }
            QPushButton { background:#1a2a4a; color:#e0e0e0; border:none;
                          border-radius:5px; padding:6px 14px; font-size:12px; font-weight:bold; }
            QPushButton:hover { background:#2a3a6a; }
            QPushButton#saveBtn { background:#1b5c38; }
            QPushButton#saveBtn:hover { background:#27ae60; }
            QPushButton#removeBtn { background:#5c1b1b; }
            QPushButton#removeBtn:hover { background:#e94560; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        has_marker = existing_marker is not None
        title_text = "📌 <b style='color:#4d96ff'>Edit Pin</b>" if has_marker else "📌 <b style='color:#4d96ff'>Add Pin</b>"
        title = QLabel(title_text)
        title.setStyleSheet("font-size:13px;")
        layout.addWidget(title)

        num = quest.get('quest_number', '?')
        ql = QLabel(f"<span style='color:#888'>#{num}</span>  <b>{quest['name']}</b>")
        ql.setWordWrap(True)
        layout.addWidget(ql)

        self.note_input = QLineEdit(self.existing.get("note", ""))
        self.note_input.setPlaceholderText("Optional note (leave empty for a simple bookmark)…")
        self.note_input.returnPressed.connect(self.accept)
        layout.addWidget(self.note_input)

        hint = QLabel("<span style='color:#555;font-size:10px'>Leave note empty to save a blank bookmark pin.</span>")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        # Show Remove whenever a marker already exists (even with empty note)
        if has_marker:
            rb = QPushButton("🗑 Remove Pin")
            rb.setObjectName("removeBtn")
            rb.clicked.connect(self._remove)
            btn_row.addWidget(rb)
        btn_row.addStretch()
        cb = QPushButton("Cancel")
        cb.clicked.connect(self.reject)
        btn_row.addWidget(cb)
        sb = QPushButton("📌 Save")
        sb.setObjectName("saveBtn")
        sb.clicked.connect(self.accept)
        btn_row.addWidget(sb)
        layout.addLayout(btn_row)
        self._remove_requested = False

        self.note_input.setFocus()
        self.note_input.selectAll()

    def _remove(self):
        self._remove_requested = True
        self.accept()

    def get_note(self)  -> str:  return self.note_input.text().strip()
    def is_remove(self) -> bool: return self._remove_requested


# ── Helper: build coloured HTML for a quest line ─────────────────────────────

def _format_type_label(label: str) -> str:
    """Convert a lowercase type label to its display form."""
    label = label.lower().strip()
    special = {
        "d&c":              "D&C",
        "solo minor cheat": "Solo Minor Cheat",
        "solo major cheat": "Solo Major Cheat",
        "minor cheat":      "Minor Cheat",
        "major cheat":      "Major Cheat",
        "quadruple cheat":  "Quadruple Cheat",
    }
    return special.get(label, label.title())


def _quest_line_html(number: Optional[int], name: str, types: list,
                     has_note: bool) -> str:
    """
    Build an HTML string matching the FinalBastion inline format:
      "2. Ghost Hunters – <red>Mob</red> + Talk + Talk"
    """
    type_parts = []
    for t in types:
        raw_label = t.get("label", "")
        label = _format_type_label(raw_label)
        color = t.get("color") or QUEST_TYPE_COLORS.get(raw_label.lower(), "#c8c8c8")
        type_parts.append(f"<b style='color:{color}'>{label}</b>")

    type_str = " + ".join(type_parts) if type_parts else ""
    pin      = " 📌" if has_note else ""
    num_str  = f"{number}." if number is not None else ""
    dash     = " – " if type_str else ""

    return (
        f"<span style='color:#555;font-size:11px'>{num_str}</span>"
        f"<span style='color:#e0e0e0;font-size:13px'> {name}</span>"
        f"<span style='color:#666;font-size:13px'>{dash}</span>"
        f"<span style='font-size:13px'>{type_str}</span>"
        f"<span style='color:#ffcc44;font-size:12px'>{pin}</span>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# QUEST ROW WIDGET
# ─────────────────────────────────────────────────────────────────────────────

class QuestRowWidget(QFrame):
    marker_changed = pyqtSignal(int)

    def __init__(self, quest: dict, marker: Optional[dict], conn, parent=None):
        super().__init__(parent)
        self.quest  = quest
        self.marker = marker
        self.conn   = conn
        self.setObjectName("questRow")
        self._flash_count     = 0
        self._flash_on        = False
        self._build()

    def _build(self):
        # Clear any existing children without destroying the layout itself.
        # This avoids the Qt "widget already has a layout" double-layout bug
        # that makes the row invisible after the first rebuild.
        existing = self.layout()
        if existing is not None:
            while existing.count():
                item = existing.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()
            layout = existing
        else:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(8, 4, 8, 4)
            layout.setSpacing(0)

        # Always make sure margins/spacing are correct
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        # A pin exists if the marker record exists at all — note may be empty (bookmark)
        has_marker = self.marker is not None
        has_note   = has_marker and bool(self.marker.get("note", "").strip())
        html = _quest_line_html(
            self.quest.get("quest_number"),
            self.quest["name"],
            self.quest.get("types", []),
            has_marker,   # show pin icon if ANY marker exists
        )

        lbl = QLabel(html)
        lbl.setWordWrap(True)
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("background:transparent; padding: 2px 0;")
        if has_note:
            lbl.setToolTip(self.marker["note"])
        layout.addWidget(lbl, stretch=1)

        # Pin button — always visible, changes state
        pin_btn = QToolButton()
        pin_btn.setFixedSize(24, 24)
        if has_marker:
            pin_btn.setText("📌")
            tooltip = self.marker["note"] if has_note else "Bookmarked (no note)\n(click to edit)"
            pin_btn.setToolTip(tooltip)
            pin_btn.setStyleSheet(
                "QToolButton{background:transparent;border:none;font-size:13px;}"
                "QToolButton:hover{background:#2a3a5a;border-radius:3px;}"
            )
        else:
            pin_btn.setText("·")
            pin_btn.setToolTip("Add pin or bookmark")
            pin_btn.setStyleSheet(
                "QToolButton{background:transparent;color:#333;border:none;"
                "font-size:16px;font-weight:bold;}"
                "QToolButton:hover{color:#4d96ff;background:#1a2a3a;border-radius:3px;}"
            )
        pin_btn.clicked.connect(self._open_marker)
        layout.addWidget(pin_btn)

    def _open_marker(self):
        dlg = MarkerDialog(self.quest, self.marker, self)
        dlg.move(QCursor.pos())
        if dlg.exec_() == QDialog.Accepted:
            qid = self.quest["id"]
            if dlg.is_remove():
                dq.remove_quest_marker(self.conn, qid)
                self.marker = None
            else:
                # Save with or without a note — empty note = bookmark
                note = dlg.get_note()
                dq.set_quest_marker(self.conn, qid, note, False)
                self.marker = dq.get_quest_marker(self.conn, qid)
            self._build()
            self.marker_changed.emit(self.quest["id"])

    # ── Highlight API (search bar / pin bar jump) ────────────────

    def highlight_search(self, flash_count: int = 3):
        """Flash yellow 3 times slowly, then clear. Used by search bar and pin-bar jumps."""
        self._flash_count = flash_count * 2
        self._flash_on = False
        self._flash_search()

    def _flash_search(self):
        if self._flash_count <= 0:
            self.setStyleSheet("")   # fully clear — no lingering tint
            return
        self._flash_on = not self._flash_on
        if self._flash_on:
            self.setStyleSheet(
                "QFrame#questRow { background: #3a3a00; border-bottom: 1px solid #ffee44; }"
            )
        else:
            self.setStyleSheet("")
        self._flash_count -= 1
        QTimer.singleShot(350, self._flash_search)   # 350ms per half-cycle = ~700ms per blink


# ─────────────────────────────────────────────────────────────────────────────
# WORLD QUEST VIEW
# ─────────────────────────────────────────────────────────────────────────────

class WorldQuestView(QWidget):
    back_requested   = pyqtSignal()
    update_requested = pyqtSignal(str, str)   # world_name, url
    reparse_requested = pyqtSignal(str)        # world_name

    def __init__(self, world: dict, conn, parent=None):
        super().__init__(parent)
        self.world = world
        self.conn  = conn
        self._quest_widgets: Dict[int, QuestRowWidget] = {}
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top_bar = QFrame()
        top_bar.setStyleSheet("QFrame{background:#0f1830;border-bottom:1px solid #1f3060;}")
        tl = QHBoxLayout(top_bar)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setSpacing(10)

        back = QPushButton("← Worlds"); back.setObjectName("btnBack")
        back.setFixedWidth(100); back.clicked.connect(self.back_requested.emit)
        tl.addWidget(back)

        wc = WORLD_COLORS.get(self.world["name"], "#4d96ff")
        tl.addWidget(QLabel(
            f"<b style='color:{wc};font-size:16px'>🌍 {self.world['name']}</b>"
        ))
        tl.addStretch()

        debug_btn = QPushButton("🔍 Debug")
        debug_btn.setObjectName("btnManage")
        debug_btn.setToolTip("View plain text, parse log, and re-parse from cache")
        debug_btn.clicked.connect(self._open_debug)
        tl.addWidget(debug_btn)

        reparse_btn = QPushButton("♻ Re-parse Cache")
        reparse_btn.setObjectName("btnUpdate")
        reparse_btn.setToolTip("Re-parse the saved plain text without fetching again")
        reparse_btn.clicked.connect(lambda: self.reparse_requested.emit(self.world["name"]))
        tl.addWidget(reparse_btn)

        upd = QPushButton("🔄 Fetch from FinalBastion")
        upd.setObjectName("btnFetch")
        src = self.world.get("source_url", "")
        upd.clicked.connect(lambda: self.update_requested.emit(self.world["name"], src))
        tl.addWidget(upd)

        layout.addWidget(top_bar)
        layout.addWidget(self._build_stats_bar())

        # ── Pinned quests shortcut bar ──────────────────────────
        self.pin_bar = self._build_pin_bar()
        layout.addWidget(self.pin_bar)

        # Quest scroll
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background:#12121f;border:none;}")
        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background:#12121f;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(12, 8, 12, 24)
        self.content_layout.setSpacing(0)
        self.scroll.setWidget(self.content_widget)
        layout.addWidget(self.scroll, stretch=1)

        self._populate_quests()

    def _build_stats_bar(self) -> QFrame:
        """
        Compact stats bar showing live encounter-type counts for this world.
        Replaces the old intro-text block.
        Color-coded chips for each type that has count > 0.
        """
        counts = dq.get_world_encounter_counts(self.conn, self.world["id"])
        wc     = WORLD_COLORS.get(self.world["name"], "#4d96ff")

        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background:#0d1522; border-bottom:1px solid #1a2a40; }"
        )
        lo = QHBoxLayout(frame)
        lo.setContentsMargins(16, 10, 16, 10)
        lo.setSpacing(8)

        total = counts.get("total", 0)
        if total:
            tl = QLabel(
                f"<b style='color:{wc}'>{self.world['name']}</b>"
                f"<span style='color:#555'> — </span>"
                f"<b style='color:{wc}'>{total}</b>"
                f"<span style='color:#888'> quests</span>"
            )
            tl.setTextFormat(Qt.RichText)
            tl.setStyleSheet("font-size:13px; background:transparent;")
            lo.addWidget(tl)

            sep = QLabel("|")
            sep.setStyleSheet("color:#2a3a5a; background:transparent;")
            lo.addWidget(sep)

        # Ordered display with colours matching QUEST_TYPE_COLORS
        DISPLAY_ORDER = [
            "talk", "explore", "interact", "collect",
            "mob", "elite", "d&c",
            "boss", "solo", "instance", "puzzle",
            "minor cheat", "major cheat", "cheat",
            "quadruple cheat", "solo minor cheat", "solo major cheat",
        ]
        DISPLAY_LABELS = {
            "d&c": "D&C", "talk": "Talk", "mob": "Mob", "boss": "Boss",
            "cheat": "Cheat", "minor cheat": "Minor Cheat",
            "major cheat": "Major Cheat", "quadruple cheat": "Quad Cheat",
            "solo minor cheat": "Solo Cheat", "solo major cheat": "Solo Maj Cheat",
            "instance": "Instance", "puzzle": "Puzzle", "solo": "Solo",
            "explore": "Explore", "interact": "Interact", "collect": "Collect",
            "elite": "Elite",
        }

        shown = set()
        for key in DISPLAY_ORDER:
            n = counts.get(key, 0)
            if n:
                shown.add(key)
                color = QUEST_TYPE_COLORS.get(key, "#888888")
                lbl_text = DISPLAY_LABELS.get(key, key.title())
                chip = QLabel(
                    f"<b style='color:{color}'>{n}</b>"
                    f"<span style='color:#888;font-size:11px'> {lbl_text}</span>"
                )
                chip.setTextFormat(Qt.RichText)
                chip.setStyleSheet("background:transparent;")
                lo.addWidget(chip)

        # Any extra types not in the display order
        for key, n in sorted(counts.items()):
            if key == "total" or key in shown or n == 0:
                continue
            color = QUEST_TYPE_COLORS.get(key, "#888888")
            chip = QLabel(
                f"<b style='color:{color}'>{n}</b>"
                f"<span style='color:#888;font-size:11px'> {key.title()}</span>"
            )
            chip.setTextFormat(Qt.RichText)
            chip.setStyleSheet("background:transparent;")
            lo.addWidget(chip)

        lo.addStretch()

        src = self.world.get("source_url", "")
        if src:
            url_lbl = QLabel(
                f"<a style='color:#2a4a7a;font-size:10px' href='{src}'>source</a>"
            )
            url_lbl.setTextFormat(Qt.RichText)
            url_lbl.setOpenExternalLinks(True)
            url_lbl.setStyleSheet("background:transparent;")
            lo.addWidget(url_lbl)

        return frame

    def _populate_quests(self):
        areas   = dq.get_areas_for_world(self.conn, self.world["id"])
        markers = dq.get_all_markers_for_world(self.conn, self.world["id"])
        wc      = WORLD_COLORS.get(self.world["name"], "#4d96ff")

        if not areas:
            src = self.world.get("source_url", "")
            msg = (
                "No quests loaded yet.\nClick '🔄 Fetch from FinalBastion' to fetch quest data."
                if src else
                "No quests loaded yet.\nSet a source URL in World Management, "
                "then click '🔄 Fetch from FinalBastion'."
            )
            empty = QLabel(msg)
            empty.setStyleSheet("color:#555;font-size:13px;padding:40px;")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            self.content_layout.addWidget(empty)
            return

        for area in areas:
            # ── Area header — large, coloured, matching FinalBastion style ──
            ah_lbl = QLabel(
                f"<span style='color:{wc};font-size:17px;font-weight:bold;'>"
                f"{area['name']}</span>"
            )
            ah_lbl.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 #0d1a2e, stop:1 #0a0f1a);"
                f"border-left: 4px solid {wc};"
                f"padding: 10px 14px 10px 16px;"
                f"margin-top: 16px;"
                f"margin-bottom: 4px;"
            )
            ah_lbl.setWordWrap(False)
            self.content_layout.addWidget(ah_lbl)

            # ── Quest rows ──
            quests = dq.get_quests_for_area(self.conn, area["id"])
            for quest in quests:
                marker = markers.get(quest["id"])
                row = QuestRowWidget(quest, marker, self.conn, self.content_widget)
                row.marker_changed.connect(self._on_marker_changed)
                self._quest_widgets[quest["id"]] = row
                self.content_layout.addWidget(row)

        self.content_layout.addStretch()

    def _build_pin_bar(self) -> QFrame:
        """
        A compact horizontal strip that lists all pinned quests as clickable
        chips. Clicking a chip scrolls immediately to that quest row.
        Hidden when there are no pins.
        """
        frame = QFrame()
        frame.setObjectName("pinBar")
        frame.setStyleSheet(
            "QFrame#pinBar { background:#16140a; border-bottom:1px solid #3a3000; }"
        )
        lo = QHBoxLayout(frame)
        lo.setContentsMargins(14, 6, 14, 6)
        lo.setSpacing(6)

        pinned = self._get_pinned_quests()
        if not pinned:
            frame.hide()
            return frame

        lbl = QLabel("📌")
        lbl.setStyleSheet("color:#ffdd44; font-size:13px; background:transparent;")
        lbl.setToolTip("Pinned quests — click to jump")
        lo.addWidget(lbl)

        for quest_id, quest_name, note in pinned:
            label = f"📌 {quest_name}" if not note else quest_name
            btn = QPushButton(label)
            btn.setToolTip(note if note else f"Bookmarked: {quest_name}")
            btn.setStyleSheet(
                "QPushButton { background:#2a2200; color:#ffdd44; border:1px solid #3a3000;"
                " border-radius:10px; padding:2px 10px; font-size:11px; font-weight:bold; }"
                "QPushButton:hover { background:#3a3200; border-color:#ffdd44; }"
            )
            # Capture quest_id in closure
            def _make_jump(qid):
                def _jump():
                    w = self._quest_widgets.get(qid)
                    if w:
                        self.scroll.ensureWidgetVisible(w)
                        w.highlight_search(flash_count=2)
                return _jump
            btn.clicked.connect(_make_jump(quest_id))
            lo.addWidget(btn)
        return frame

    def _get_pinned_quests(self):
        """Return list of (quest_id, quest_name, note) for all pinned quests in this world."""
        try:
            cur = self.conn.execute("""
                SELECT q.id, q.name, COALESCE(m.note, '') as note
                FROM quests q
                JOIN quest_markers m ON m.quest_id = q.id
                WHERE q.world_id = ?
                ORDER BY q.sort_order
            """, (self.world["id"],))
            return cur.fetchall()
        except Exception:
            return []

    def _refresh_pin_bar(self):
        """Replace the pin bar widget in-place after a pin is added/removed."""
        lo = self.layout()
        # Pin bar is at index 2 (0=top_bar, 1=intro_block, 2=pin_bar)
        old_item = lo.itemAt(2)
        if old_item and old_item.widget():
            old = old_item.widget()
            new = self._build_pin_bar()
            lo.replaceWidget(old, new)
            old.deleteLater()

    def _on_marker_changed(self, quest_id: int):
        """Refresh stats bar and pin bar after a pin change."""
        lo = self.layout()
        old_stats = lo.itemAt(1).widget()
        new_stats = self._build_stats_bar()
        lo.replaceWidget(old_stats, new_stats)
        old_stats.deleteLater()
        self._refresh_pin_bar()

    def _open_debug(self):
        dlg = DebugViewDialog(self.world["name"], self)
        dlg.reparse_requested.connect(self.reparse_requested.emit)
        dlg.show()

    def refresh(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._quest_widgets.clear()
        # Refresh world data from DB (might have new stats/URL)
        refreshed = dq.get_world_by_name(self.conn, self.world["name"])
        if refreshed:
            self.world = refreshed
        self._populate_quests()
        # Refresh pin bar
        self._refresh_pin_bar()

    def search_and_flash_quest(self, quest_name: str) -> bool:
        """
        Flash-highlight matching quest row (search bar).
        Scrolls immediately to the match.
        Returns True if a match was found.
        """
        target = quest_name.lower().strip()
        # Exact match first
        for qid, w in self._quest_widgets.items():
            if w.quest["name"].lower() == target:
                w.highlight_search()
                self.scroll.ensureWidgetVisible(w)
                return True
        # Substring / fuzzy fallback
        best_w = None
        best_score = 0.0
        for qid, w in self._quest_widgets.items():
            name_lower = w.quest["name"].lower()
            if target in name_lower or name_lower in target:
                score = min(len(target), len(name_lower)) / max(len(target), len(name_lower))
                if score > best_score:
                    best_score = score
                    best_w = w
        if best_w and best_score >= 0.4:
            best_w.highlight_search()
            self.scroll.ensureWidgetVisible(best_w)
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# WORLD CARD (landing page)
# ─────────────────────────────────────────────────────────────────────────────

class WorldCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, world: dict, conn, parent=None):
        super().__init__(parent)
        self.world_name = world["name"]
        self.world      = world
        self.conn       = conn
        self.setObjectName("worldCard")
        self.setFixedSize(210, 160)
        self.setCursor(Qt.PointingHandCursor)
        self._build()

    def _build(self):
        color = WORLD_COLORS.get(self.world_name, "#4d96ff")

        counts    = dq.get_world_encounter_counts(self.conn, self.world["id"])
        pin_count = dq.get_world_pin_count(self.conn, self.world["id"])

        lo = QVBoxLayout(self)
        lo.setContentsMargins(12, 10, 12, 10)
        lo.setSpacing(5)

        # ── Title ──
        title_lbl = QLabel(
            f"<b style='color:{color};font-size:15px'>{self.world_name}</b>"
        )
        title_lbl.setWordWrap(False)
        lo.addWidget(title_lbl)

        # ── Level badge (if set) ──
        lv_min = self.world.get("level_min")
        lv_max = self.world.get("level_max")
        if lv_min or lv_max:
            lv_badge = QLabel(f"Lv {lv_min or '?'}–{lv_max or '?'}")
            lv_badge.setStyleSheet(
                f"background:#1a2a4a; color:{color}; border-radius:3px;"
                "padding:1px 6px; font-size:12px; font-weight:bold;"
            )
            lv_badge.setFixedHeight(18)
            lo.addWidget(lv_badge)

        total = counts.get("total", 0)

        if total:
            # Build icon+count items matching the world stats bar icons/colors
            CHEAT_KEYS = {"cheat", "minor cheat", "major cheat",
                          "quadruple cheat", "solo minor cheat", "solo major cheat"}
            BOSS_KEYS  = {"boss", "solo"} | CHEAT_KEYS
            MOB_KEYS   = {"mob", "elite"}

            solo_only = counts.get("solo", 0)
            bosses   = sum(counts.get(k, 0) for k in BOSS_KEYS)  # includes solo
            cheaters = sum(counts.get(k, 0) for k in CHEAT_KEYS)
            mobs     = sum(counts.get(k, 0) for k in MOB_KEYS)
            dc       = counts.get("d&c", 0)
            talk     = sum(counts.get(k, 0) for k in ("talk", "explore", "interact", "collect"))
            instance = counts.get("instance", 0)
            puzzle   = counts.get("puzzle", 0)

            # Each item: (icon, count, color)  — only shown if count > 0
            stat_items = [
                ("📋", total,     "#888888"),
                ("⚔",  bosses,    "#ff99cc"),
                ("🔴", cheaters,  "#ff4444"),
                ("🧍", solo_only, "#ffcc44"),
                ("👾", mobs,      "#99cc00"),
                ("🔄", dc,        "#00ccff"),
                ("💬", talk,      "#c8c8c8"),
                ("🏛", instance,  "#cc99ff"),
                ("🧩", puzzle,    "#3366ff"),
                ("📌", pin_count, "#ffcc44"),
            ]

            # Render in rows of 3 items each
            shown = [(ic, ct, cl) for ic, ct, cl in stat_items if ct]
            rows_html = []
            for i in range(0, len(shown), 3):
                row_items = shown[i:i+3]
                parts = [
                    f"<span style='font-size:12px'>{ic}</span>"
                    f"<b style='color:{cl};font-size:13px'> {ct}</b>"
                    for ic, ct, cl in row_items
                ]
                rows_html.append("  ".join(parts))

            stat_lbl = QLabel("<br>".join(rows_html))
            stat_lbl.setTextFormat(Qt.RichText)
            stat_lbl.setStyleSheet("background:transparent; padding:1px 0;")
            lo.addWidget(stat_lbl)
        else:
            lo.addWidget(QLabel(
                "<i style='color:#333;font-size:12px'>No quest data</i>"
            ))

        lo.addStretch()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.world_name)
        super().mousePressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# LANDING PAGE
# ─────────────────────────────────────────────────────────────────────────────

class LandingPage(QWidget):
    world_selected          = pyqtSignal(str)
    fetch_all_requested     = pyqtSignal()
    reparse_all_requested   = pyqtSignal()

    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self._build()

    def _build(self):
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        # Top bar
        top = QFrame()
        top.setStyleSheet("QFrame{background:#0a0a1a;border-bottom:1px solid #1f3060;}")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 14, 20, 14)

        tl.addWidget(QLabel(
            "<b style='color:#4d96ff;font-size:20px'>🗺 Quest Tracker</b>"
            "<span style='color:#555;font-size:12px;margin-left:12px'>"
            "  Powered by FinalBastion</span>"
        ))
        tl.addStretch()

        reparse_btn = QPushButton("♻ Re-parse All Cached")
        reparse_btn.setObjectName("btnUpdate")
        reparse_btn.setToolTip("Re-parse all worlds from saved plain text files (no network)")
        reparse_btn.clicked.connect(self.reparse_all_requested.emit)
        tl.addWidget(reparse_btn)

        fetch_btn = QPushButton("🌐 Fetch All Worlds")
        fetch_btn.setObjectName("btnFetchAll")
        fetch_btn.setToolTip("Scrape all world quest guides that have a URL set")
        fetch_btn.clicked.connect(self.fetch_all_requested.emit)
        tl.addWidget(fetch_btn)

        lo.addWidget(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:#12121f;border:none;}")
        content = QWidget(); content.setStyleSheet("background:#12121f;")
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(20, 20, 20, 20)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(content)
        lo.addWidget(scroll, stretch=1)

        # ── Legend strip ──
        legend_bar = QFrame()
        legend_bar.setStyleSheet(
            "QFrame { background:#0a0a14; border-top:1px solid #1a1a2e; }"
        )
        ll = QHBoxLayout(legend_bar)
        ll.setContentsMargins(20, 7, 20, 7)
        ll.setSpacing(16)

        legend_title = QLabel("Legend:")
        legend_title.setStyleSheet("color:#333; font-size:12px; font-weight:bold;")
        ll.addWidget(legend_title)

        LEGEND_ITEMS = [
            ("📋", "Total quests",   "#888888"),
            ("⚔",  "Bosses",        "#ff99cc"),
            ("🔴", "Cheater bosses", "#ff4444"),
            ("🧍", "Solo quests",    "#ffcc44"),
            ("👾", "Mob fights",     "#99cc00"),
            ("🔄", "D&C quests",     "#00ccff"),
            ("💬", "Talk/Explore",   "#c8c8c8"),
            ("🏛", "Instance",       "#cc99ff"),
            ("🧩", "Puzzle",         "#3366ff"),
            ("📌", "Pinned quests",  "#ffcc44"),
        ]
        for icon, label, color in LEGEND_ITEMS:
            item = QLabel(
                f"<span style='font-size:13px'>{icon}</span>"
                f"<span style='color:{color};font-size:12px'> {label}</span>"
            )
            item.setTextFormat(Qt.RichText)
            item.setStyleSheet("background:transparent;")
            ll.addWidget(item)

        ll.addStretch()
        lo.addWidget(legend_bar)

        self._populate()

    def _populate(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        worlds = dq.get_all_worlds(self.conn)

        # If no worlds in DB at all, seed with defaults
        if not worlds:
            for i, wname in enumerate(DEFAULT_WORLD_ORDER):
                known_url = ""
                if qs:
                    known_url = qs.KNOWN_WORLD_URLS.get(wname, "")
                dq.upsert_world(self.conn, {
                    "name": wname,
                    "display_order": i,
                    "source_url": known_url,
                })
            self.conn.commit()
            worlds = dq.get_all_worlds(self.conn)

        COLS = 5
        for idx, world in enumerate(worlds):
            card = WorldCard(world, self.conn, self)
            card.clicked.connect(self.world_selected.emit)
            self.grid.addWidget(card, idx // COLS, idx % COLS)

    def refresh(self):
        self._populate()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN QUEST TRACKER WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class QuestTrackerWindow(QDialog):
    def __init__(self, conn, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.setWindowTitle("Wizard101 Companion — Quest Tracker")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 800)
        self.setStyleSheet(QUEST_STYLE)

        self._db_path = str(db.DB_PATH)
        dq.init_quest_tables(self.conn)

        self._worker: Optional[ScrapeWorker] = None
        self._current_world_view: Optional[WorldQuestView] = None

        self._build_ui()

    def _build_ui(self):
        lo = QVBoxLayout(self)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)

        self.stack = QStackedWidget()
        lo.addWidget(self.stack, stretch=1)

        self.landing = LandingPage(self.conn, self)
        self.landing.world_selected.connect(self._show_world)
        self.landing.fetch_all_requested.connect(self._fetch_all)

        self.landing.reparse_all_requested.connect(self._reparse_all_cached)
        self.stack.addWidget(self.landing)

        # ── Global quest search bar ──────────────────────────────
        search_frame = QFrame()
        search_frame.setStyleSheet(
            "QFrame { background:#0a0f1a; border-top:1px solid #1a2a40; }"
        )
        search_frame.setFixedHeight(44)
        sl = QHBoxLayout(search_frame)
        sl.setContentsMargins(14, 6, 14, 6)
        sl.setSpacing(8)

        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size:14px; background:transparent;")
        sl.addWidget(search_icon)

        self.quest_search_input = QLineEdit()
        self.quest_search_input.setPlaceholderText("Search quests across all worlds…")
        self.quest_search_input.setStyleSheet(
            "QLineEdit { background:#12121f; color:#e0e0e0; border:1px solid #2a3a5a;"
            " border-radius:5px; padding:4px 10px; font-size:12px; }"
            "QLineEdit:focus { border-color:#ffdd44; }"
        )
        self.quest_search_input.returnPressed.connect(self._do_quest_search)
        sl.addWidget(self.quest_search_input, stretch=1)

        self._search_btn = QPushButton("Find")
        self._search_btn.setFixedWidth(54)
        self._search_btn.setStyleSheet(
            "QPushButton { background:#1a3060; color:#e0e0e0; border:none;"
            " border-radius:5px; padding:4px 10px; font-size:12px; font-weight:bold; }"
            "QPushButton:hover { background:#ffdd44; color:#0a0a1a; }"
        )
        self._search_btn.clicked.connect(self._do_quest_search)
        sl.addWidget(self._search_btn)

        self._search_status = QLabel("")
        self._search_status.setStyleSheet("color:#666; font-size:11px; background:transparent;")
        self._search_status.setFixedWidth(180)
        sl.addWidget(self._search_status)

        lo.addWidget(search_frame)

        # Progress bar
        self.prog_frame = QFrame()
        self.prog_frame.setStyleSheet(
            "QFrame{background:#0f1830;border-top:1px solid #1f3060;}"
        )
        self.prog_frame.setFixedHeight(52)
        pl = QHBoxLayout(self.prog_frame)
        pl.setContentsMargins(16, 8, 16, 8); pl.setSpacing(12)
        self.prog_lbl = QLabel("Ready")
        self.prog_lbl.setStyleSheet("color:#888;font-size:12px;")
        pl.addWidget(self.prog_lbl, stretch=1)
        self.cancel_btn = QPushButton("✕ Cancel")
        self.cancel_btn.setFixedWidth(90)
        self.cancel_btn.setStyleSheet(
            "background:#5c1b1b;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:5px 10px;font-size:11px;"
        )
        self.cancel_btn.clicked.connect(self._cancel_scrape)
        self.cancel_btn.hide()
        pl.addWidget(self.cancel_btn)
        lo.addWidget(self.prog_frame)

    # ── Quest search (global, cross-world) ────────────────────────

    def _do_quest_search(self):
        query = self.quest_search_input.text().strip()
        if not query:
            return
        # Find all quests matching the query in the DB
        results = dq.search_quests(self.conn, query)
        if not results:
            self._search_status.setText("No results found")
            self._search_status.setStyleSheet("color:#e94560; font-size:11px; background:transparent;")
            return

        # Take the best match
        best = results[0]
        world_name = best.get("world_name", "")
        quest_name = best["name"]

        self._search_status.setText(f"→ {world_name}")
        self._search_status.setStyleSheet("color:#ffdd44; font-size:11px; background:transparent;")
        QTimer.singleShot(3000, lambda: self._search_status.setText(""))

        # Navigate to the world, then flash-highlight the quest
        self._show_world(world_name)

        # Give the view a moment to render before scrolling + flashing
        def _do_flash():
            if self._current_world_view:
                found = self._current_world_view.search_and_flash_quest(quest_name)
                if not found:
                    self._search_status.setText("Quest not in view")
        QTimer.singleShot(150, _do_flash)

    # ── Navigation ────────────────────────────────────────────────

    def _show_world(self, world_name: str):
        # Commit any open read transaction so background thread writes are visible
        try:
            self.conn.commit()
        except Exception:
            pass
        world = dq.get_world_by_name(self.conn, world_name)
        if not world:
            return
        if self.stack.count() > 1:
            old = self.stack.widget(1)
            self.stack.removeWidget(old)
            old.deleteLater()
        view = WorldQuestView(world, self.conn, self)
        view.back_requested.connect(self._show_landing)
        view.update_requested.connect(self._fetch_single_world)
        view.reparse_requested.connect(self._reparse_single_world)
        self.stack.addWidget(view)
        self.stack.setCurrentIndex(1)
        self._current_world_view = view

    def _show_landing(self):
        self.stack.setCurrentIndex(0)
        self.landing.refresh()
        self._current_world_view = None



    # ── Re-parse from cache ───────────────────────────────────────

    def _reparse_single_world(self, world_name: str):
        """Re-parse a world from its saved plain text (no network)."""
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A fetch/parse is already running.")
            return
        if qs is None:
            QMessageBox.warning(self, "Unavailable", "quest_scraper not loaded.")
            return
        self.cancel_btn.show()
        self._worker = ReparseWorker(self._db_path, [world_name], self)
        self._worker.progress.connect(self._on_progress)
        self._worker.world_done.connect(self._on_world_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _reparse_all_cached(self):
        """Re-parse all worlds that have cached plain text files (no network)."""
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A fetch/parse is already running.")
            return
        if qs is None:
            QMessageBox.warning(self, "Unavailable", "quest_scraper not loaded.")
            return
        cached = qs.get_cached_worlds()
        if not cached:
            QMessageBox.information(
                self, "No Cache",
                "No cached plain text files found.\n"
                "Fetch worlds first using '🌐 Fetch All Worlds'."
            )
            return
        reply = QMessageBox.question(
            self, "Re-parse All Cached",
            f"Re-parse {len(cached)} cached world(s) from disk?\n"
            "No network requests will be made.\n\n"
            + "\n".join(f"  • {w}" for w in cached[:10])
            + ("\n  ..." if len(cached) > 10 else ""),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self.cancel_btn.show()
        self._worker = ReparseWorker(self._db_path, cached, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.world_done.connect(self._on_world_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    # ── Scraping ──────────────────────────────────────────────────

    def _fetch_all(self):
        if self._worker and self._worker.isRunning():
            return
        if not SCRAPER_AVAILABLE:
            QMessageBox.warning(self, "Not Available",
                "requests and beautifulsoup4 must be installed.\n"
                "Run: pip install requests beautifulsoup4")
            return

        # Collect worlds that have a URL set
        worlds_in_db = dq.get_all_worlds(self.conn)
        worlds_with_url = [
            {"world": w["name"], "url": w.get("source_url", "")}
            for w in worlds_in_db
            if w.get("source_url", "").strip()
        ]

        if not worlds_with_url:
            QMessageBox.information(
                self, "No URLs Set",
                "No worlds have a source URL configured.\n"
                "Use '⚙ Manage Worlds' to set URLs, or they will be\n"
                "auto-populated from FinalBastion's master guide on first fetch."
            )
            # Auto-populate from known URLs and try again
            for wname, url in (qs.KNOWN_WORLD_URLS if qs else {}).items():
                w = dq.get_world_by_name(self.conn, wname)
                if w and not w.get("source_url"):
                    dq.upsert_world(self.conn, {**w, "source_url": url})
            self.conn.commit()
            worlds_in_db = dq.get_all_worlds(self.conn)
            worlds_with_url = [
                {"world": w["name"], "url": w.get("source_url", "")}
                for w in worlds_in_db if w.get("source_url", "").strip()
            ]
            if not worlds_with_url:
                return

        reply = QMessageBox.question(
            self, "Fetch All Worlds",
            f"Scrape {len(worlds_with_url)} world quest guide(s) from FinalBastion?\n"
            "This takes a few minutes.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        self._start_worker(worlds_with_url)

    def _fetch_single_world(self, world_name: str, url: str):
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A fetch is already in progress.")
            return
        if not SCRAPER_AVAILABLE:
            QMessageBox.warning(self, "Not Available",
                "requests and beautifulsoup4 must be installed.\n"
                "Run: pip install requests beautifulsoup4")
            return
        if not url:
            # Check DB
            world = dq.get_world_by_name(self.conn, world_name)
            url = (world or {}).get("source_url", "")
        if not url:
            QMessageBox.warning(
                self, "No URL",
                f"No source URL set for '{world_name}'.\n"
                "Use '← Worlds' → '⚙ Manage Worlds' to set one."
            )
            return
        self._start_worker([{"world": world_name, "url": url}])

    def _start_worker(self, worlds: list):
        self.cancel_btn.show()
        self._worker = ScrapeWorker(self._db_path, worlds, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.world_done.connect(self._on_world_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()

    def _on_progress(self, msg: str):
        self.prog_lbl.setText(msg)

    def _on_world_done(self, world_name: str, success: bool, debug_info: str):
        if not success and debug_info:
            logger.warning(f"World '{world_name}' debug: {debug_info}")
        # Commit + reconnect main connection so thread's writes are visible
        try:
            self.conn.commit()
        except Exception:
            pass
        if self._current_world_view and \
                self._current_world_view.world["name"] == world_name:
            world = dq.get_world_by_name(self.conn, world_name)
            if world:
                self._current_world_view.world = world
                self._current_world_view.refresh()

    def _on_all_done(self):
        self.prog_lbl.setText("✅ All done!")
        self.cancel_btn.hide()
        self.landing.refresh()
        QTimer.singleShot(3000, lambda: self.prog_lbl.setText("Ready"))

    def _cancel_scrape(self):
        if self._worker:
            self._worker.abort()
        self.cancel_btn.hide()
        self.prog_lbl.setText("Cancelled.")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.abort()
            self._worker.wait(2000)
        event.accept()


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    conn = db.get_connection()
    db.init_db(conn)
    dq.init_quest_tables(conn)
    win = QuestTrackerWindow(conn)
    win.show()
    sys.exit(app.exec_())
