"""
hud_overlays.py  —  Wizard101 Companion HUD Overlays
"""

import json
import os
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QScrollArea, QFrame, QSizeGrip, QStackedWidget, QCheckBox,
    QTabWidget, QListWidget, QListWidgetItem, QSizePolicy
)
from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen


_SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "hud_settings.json"
)


# ── Win32: force overlay above fullscreen/borderless game windows ─────────
# SWP_NOACTIVATE  — don't steal focus from the game
# SWP_NOMOVE      — keep current position
# SWP_NOSIZE      — keep current size
# HWND_TOPMOST    — place above all non-topmost windows (including game)
_SWP_NOSIZE      = 0x0001
_SWP_NOMOVE      = 0x0002
_SWP_NOACTIVATE  = 0x0010
_HWND_TOPMOST    = -1

try:
    import ctypes
    import ctypes.wintypes
    _user32      = ctypes.windll.user32
    _WIN32_OK    = True
except Exception:
    _user32   = None
    _WIN32_OK = False


def _force_topmost(widget):
    """
    Use SetWindowPos to pin the overlay above the game without stealing focus.
    Falls back to raise_() on non-Windows or if ctypes is unavailable.
    """
    if _WIN32_OK:
        try:
            hwnd = int(widget.winId())
            _user32.SetWindowPos(
                hwnd,
                _HWND_TOPMOST,
                0, 0, 0, 0,
                _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
            )
            return
        except Exception:
            pass
    # Non-Windows fallback
    widget.raise_()

HUD_BG_ALPHA = 51

HUD_PALETTE = {
    "boss":    {"accent": "#e94560", "title": "👾 Boss Wiki"},
    "quest":   {"accent": "#4d96ff", "title": "🗺 Quest Tracker"},
    "counter": {"accent": "#ffd93d", "title": "⏱ Round Counters"},
    "guide":   {"accent": "#c39bd3", "title": "📖 Strategy Guides"},
}

QUEST_TYPE_COLORS = {
    "talk": "#c8c8c8", "mob": "#99cc00", "elite": "#99cc00",
    "d&c": "#00ccff", "boss": "#ff99cc", "minor cheat": "#ff99cc",
    "cheat": "#ff0000", "major cheat": "#ff0000", "quadruple cheat": "#ff0000",
    "solo minor cheat": "#ff99cc", "solo major cheat": "#ff0000",
    "instance": "#cc99ff", "puzzle": "#3366ff", "interact": "#c8c8c8",
    "collect": "#c8c8c8", "explore": "#c8c8c8", "solo": "#ffcc00",
}

WORLD_COLORS = {
    "Wizard City": "#4d96ff", "Krokotopia": "#ffaa22", "Grizzleheim": "#88ccff",
    "Marleybone": "#aaaaaa", "MooShu": "#ff88aa", "Dragonspyre": "#ff4444",
    "Celestia": "#22ddff", "Zafaria": "#44cc66", "Wysteria": "#cc88ff",
    "Avalon": "#4488ff", "Azteca": "#ffcc44", "Aquila": "#88aaff",
    "Khrysalis": "#ff8844", "Polaris": "#aaddff", "Arcanum": "#cc44ff",
    "Mirage": "#ffdd88", "Empyrea": "#44ffcc", "Karamelle": "#ff88cc",
    "Lemuria": "#44ffaa", "Novus": "#88ddff", "Wallaru": "#ffaa66",
    "Selenopolis": "#cc88ff", "Darkmoor": "#8844ff",
}

SCHOOL_COLORS = {
    "Fire": "#e85d04", "Ice": "#48cae4", "Storm": "#9b5de5",
    "Myth": "#f4a261", "Life": "#57cc99", "Death": "#9d4edd",
    "Balance": "#ffd166",
}

CHEAT_TYPE_META = {
    "start_of_battle": {"label": "Start of Battle", "color": "#ff6b6b", "bg": "30,10,10"},
    "interrupt":       {"label": "Interrupt",        "color": "#ffd93d", "bg": "30,25,5"},
    "conditional":     {"label": "Conditional",      "color": "#6bcb77", "bg": "7,26,13"},
    "passive":         {"label": "Passive",           "color": "#4d96ff", "bg": "5,15,31"},
    "cycle_header":    {"label": "Cycle",             "color": "#e0a0ff", "bg": "26,10,42"},
    "cycle_info":      {"label": "Info",              "color": "#aaaaaa", "bg": "17,17,17"},
    "unknown":         {"label": "Unknown",           "color": "#888888", "bg": "17,17,17"},
}


# ════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ════════════════════════════════════════════════════════════════════════════

class OverlaySettings:
    _DEFAULTS = {
        "boss":    {"enabled": False, "clickthrough": False, "x": 40,  "y": 40,  "w": 360, "h": 360, "alpha": -1},
        "quest":   {"enabled": False, "clickthrough": False, "x": 40,  "y": 420, "w": 360, "h": 440, "alpha": -1},
        "counter": {"enabled": False, "clickthrough": False, "x": 420, "y": 40,  "w": 300, "h": 340, "alpha": -1},
        "guide":   {"enabled": False, "clickthrough": False, "x": 420, "y": 400, "w": 320, "h": 340, "alpha": -1},
        "_global": {"alpha": 51},
    }

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._enabled_callbacks: List = []
        self._load()

    def _load(self):
        try:
            if os.path.exists(_SETTINGS_FILE):
                with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for key, defaults in self._DEFAULTS.items():
                    merged = dict(defaults)
                    merged.update(saved.get(key, {}))
                    self._data[key] = merged
                return
        except Exception:
            pass
        self._data = {k: dict(v) for k, v in self._DEFAULTS.items()}

    def save(self):
        try:
            with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get(self, key: str) -> dict:
        return dict(self._data.get(key, self._DEFAULTS.get(key, {})))

    def set_geometry(self, key: str, x: int, y: int, w: int, h: int):
        if key in self._data:
            self._data[key].update({"x": x, "y": y, "w": w, "h": h})
            self.save()

    def set_enabled(self, key: str, v: bool, notify: bool = True):
        if key in self._data:
            self._data[key]["enabled"] = v
            self.save()
            if notify:
                for cb in self._enabled_callbacks:
                    try:
                        cb(key, v)
                    except Exception:
                        pass

    def set_clickthrough(self, key: str, v: bool):
        if key in self._data:
            self._data[key]["clickthrough"] = v
            self.save()

    def is_enabled(self, key: str) -> bool:
        return bool(self._data.get(key, {}).get("enabled", False))

    def is_clickthrough(self, key: str) -> bool:
        return bool(self._data.get(key, {}).get("clickthrough", False))

    def get_alpha(self) -> int:
        return int(self._data.get("_global", {}).get("alpha", HUD_BG_ALPHA))

    def set_alpha(self, alpha: int):
        self._data.setdefault("_global", {})["alpha"] = alpha
        self.save()

    def get_overlay_alpha(self, key: str) -> int:
        """Return per-overlay alpha if set (>= 0), else fall back to global."""
        per = int(self._data.get(key, {}).get("alpha", -1))
        return per if per >= 0 else self.get_alpha()

    def set_overlay_alpha(self, key: str, alpha: int):
        """Set per-overlay alpha. Pass -1 to use global."""
        if key in self._data:
            self._data[key]["alpha"] = alpha
            self.save()

    def add_enabled_changed_callback(self, cb):
        self._enabled_callbacks.append(cb)


overlay_settings = OverlaySettings()


# ════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ════════════════════════════════════════════════════════════════════════════

class _RoundedContainer(QWidget):
    def __init__(self, accent: str, overlay_key: str = "", parent=None):
        super().__init__(parent)
        self._accent      = QColor(accent)
        self._overlay_key = overlay_key
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        alpha = overlay_settings.get_overlay_alpha(self._overlay_key)
        p.setBrush(QColor(10, 12, 30, alpha))
        p.setPen(QPen(self._accent, 1))
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()


def _lbl(text: str, color: str = "#cccccc", bold: bool = False,
         size: int = 11, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color};font-size:{size}px;font-weight:{'bold' if bold else 'normal'};"
        "background:transparent;"
    )
    w.setWordWrap(wrap)
    return w


def _div() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color:rgba(255,255,255,25);background:rgba(255,255,255,25);max-height:1px;")
    return f


class _TinyBtn(QPushButton):
    def __init__(self, text: str, tip: str = "", parent=None):
        super().__init__(text, parent)
        self.setFixedSize(20, 20)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,15);color:#888;border:none;"
            "border-radius:4px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:rgba(233,69,96,180);color:white;}"
            "QPushButton:checked{background:rgba(233,69,96,100);color:#ffd93d;}"
        )


def _back_btn(accent: str) -> QPushButton:
    b = QPushButton("← Back")
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background:rgba(255,255,255,12);color:{accent};"
        f"border:1px solid rgba(255,255,255,30);border-radius:4px;"
        "padding:2px 8px;font-size:13px;font-weight:bold;}"
        f"QPushButton:hover{{background:rgba(255,255,255,25);}}"
    )
    return b


def _clear_layout(lo):
    """Recursively remove and delete all items from a layout, including nested layouts."""
    while lo.count():
        item = lo.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


# ════════════════════════════════════════════════════════════════════════════
# BASE OVERLAY
# ════════════════════════════════════════════════════════════════════════════

class BaseHUDOverlay(QWidget):
    closed = pyqtSignal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.Tool)
        self._key       = key
        self._drag_pos: Optional[QPoint] = None
        self._pal       = HUD_PALETTE.get(key, {"accent": "#e0e0e0", "title": "HUD"})
        self._collapsed = False
        self._saved_h   = 300
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._build_shell()
        self._restore_geometry()
        self._apply_ct(overlay_settings.is_clickthrough(key))

    def _build_shell(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._container = _RoundedContainer(self._pal["accent"], self._key)
        cl = QVBoxLayout(self._container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._make_title_bar())

        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollBar:vertical{background:rgba(10,10,30,100);width:5px;border-radius:2px;}"
            "QScrollBar::handle:vertical{background:rgba(255,255,255,70);"
            "border-radius:2px;min-height:16px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
        )
        self._host = QWidget()
        self._host.setStyleSheet("background:transparent;")
        self._scroll.setWidget(self._host)
        self._build_content(self._host)
        bl.addWidget(self._scroll, stretch=1)

        gr = QHBoxLayout()
        gr.setContentsMargins(0, 0, 3, 1)
        gr.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("QSizeGrip{background:transparent;width:12px;height:12px;}")
        gr.addWidget(grip)
        bl.addLayout(gr)

        cl.addWidget(self._body, stretch=1)
        outer.addWidget(self._container)

    def _make_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(26)
        bar.setStyleSheet("background:transparent;")
        bar.setCursor(Qt.SizeAllCursor)

        lo = QHBoxLayout(bar)
        lo.setContentsMargins(9, 0, 5, 0)
        lo.setSpacing(3)

        lo.addWidget(_lbl(self._pal["title"], self._pal["accent"], bold=True, size=14))
        lo.addStretch()

        self._collapse_btn = _TinyBtn("⊟", "Collapse overlay")
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.toggled.connect(self._on_collapse)
        lo.addWidget(self._collapse_btn)

        self._ct_btn = _TinyBtn("⊘", "Click-through: when ON the window ignores all mouse input")
        self._ct_btn.setCheckable(True)
        self._ct_btn.setChecked(overlay_settings.is_clickthrough(self._key))
        self._ct_btn.toggled.connect(self._on_ct)
        lo.addWidget(self._ct_btn)

        cb = _TinyBtn("✕", "Hide overlay")
        cb.clicked.connect(self._on_close)
        lo.addWidget(cb)

        bar.mousePressEvent   = self._drag_press
        bar.mouseMoveEvent    = self._drag_move
        bar.mouseReleaseEvent = self._drag_release
        return bar

    def _build_content(self, host: QWidget):
        pass

    def enterEvent(self, e):
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        super().leaveEvent(e)

    def _on_collapse(self, checked: bool):
        self._collapsed = checked
        self._body.setVisible(not checked)
        self._collapse_btn.setText("⊞" if checked else "⊟")
        self._collapse_btn.setToolTip("Expand overlay" if checked else "Collapse overlay")
        if checked:
            self._saved_h = self.height()
            self.setFixedHeight(28)
        else:
            self.setMinimumSize(200, 140)
            self.setMaximumSize(16777215, 16777215)
            self.resize(self.width(), self._saved_h)

    def _drag_press(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()

    def _drag_move(self, e):
        if self._drag_pos and e.buttons() == Qt.LeftButton:
            self.move(e.globalPos() - self._drag_pos)

    def _drag_release(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = None
            self._save_geo()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._collapsed:
            self._save_geo()

    def _save_geo(self):
        g = self.geometry()
        overlay_settings.set_geometry(self._key, g.x(), g.y(), g.width(), g.height())

    def _restore_geometry(self):
        s = overlay_settings.get(self._key)
        self.setGeometry(s["x"], s["y"], s["w"], s["h"])
        self.setMinimumSize(200, 140)

    def _on_ct(self, checked: bool):
        overlay_settings.set_clickthrough(self._key, checked)
        self._apply_ct(checked)

    def _apply_ct(self, enabled: bool):
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        self._ct_btn.blockSignals(True)
        self._ct_btn.setChecked(enabled)
        self._ct_btn.blockSignals(False)

    def showEvent(self, event):
        super().showEvent(event)
        _force_topmost(self)

    def _on_close(self):
        overlay_settings.set_enabled(self._key, False, notify=True)
        self.hide()
        self.closed.emit(self._key)

    def refresh(self, data: dict):
        pass


# ════════════════════════════════════════════════════════════════════════════
# LIVE COUNTER WIDGET  (used inside BossHUDOverlay Counters tab)
# ════════════════════════════════════════════════════════════════════════════

class _LiveCounterWidget(QFrame):
    """
    Compact live-play counter card with tick buttons per round and a reset button.
    Mirrors the behaviour of the standalone RoundCounterHUDOverlay detail view.
    """

    def __init__(self, ctr: dict, parent=None):
        super().__init__(parent)
        self._ctr           = ctr
        self._current_round = 0
        self._tick_btns:    list = []   # (num_lbl, text_lbl, tick_btn)
        self._round_frames: list = []
        self.setObjectName("liveCard")
        self.setStyleSheet(
            "QFrame#liveCard{background:rgba(30,25,10,140);border:1px solid #ffd93d44;"
            "border-radius:6px;}"
        )
        self._build()

    def _build(self):
        lo = QVBoxLayout(self)
        lo.setContentsMargins(8, 6, 8, 8); lo.setSpacing(3)

        # Header: name
        lo.addWidget(_lbl(self._ctr.get("name", "?"), "#ffd93d", bold=True, size=16))

        if self._ctr.get("description"):
            lo.addWidget(_lbl(self._ctr["description"], "#aaa", size=14, wrap=True))
        if self._ctr.get("linked_bosses"):
            lo.addWidget(_lbl(
                "  ".join(f"👾 {b}" for b in self._ctr["linked_bosses"]),
                "#4d96ff", size=13
            ))

        lo.addWidget(_div())

        # Round rows
        for i, r in enumerate(self._ctr.get("rounds", [])):
            label = r.get("label", "") or f"Round {i+1}"
            rf = QFrame(); rf.setObjectName("lcRow")
            rf.setStyleSheet(
                "QFrame#lcRow{background:rgba(255,211,61,8);border-radius:3px;border:none;}"
            )
            rl = QHBoxLayout(rf)
            rl.setContentsMargins(6, 3, 6, 3); rl.setSpacing(6)

            num = _lbl(f"R{i+1}", "#ffd93d", bold=True, size=14)
            num.setFixedWidth(28)
            rl.addWidget(num)

            lbl_w = _lbl(label, "#d0d0d0", size=15, wrap=True)
            rl.addWidget(lbl_w, stretch=1)

            tick = QPushButton("○")
            tick.setFixedSize(22, 22)
            tick.setCheckable(True)
            tick.setStyleSheet(
                "QPushButton{background:rgba(10,10,30,160);color:#555;border:1px solid #333;"
                "border-radius:11px;font-size:15px;font-weight:bold;padding:0;}"
                "QPushButton:checked{background:#e94560;color:white;border-color:#e94560;}"
                "QPushButton:hover{border-color:#ffd93d;}"
            )
            tick.toggled.connect(lambda checked, idx=i: self._on_tick(idx, checked))
            rl.addWidget(tick)

            lo.addWidget(rf)
            self._tick_btns.append((num, lbl_w, tick))
            self._round_frames.append(rf)

        # Reset button
        ctrl = QHBoxLayout()
        rst = QPushButton("↺ Reset")
        rst.setCursor(Qt.PointingHandCursor)
        rst.setStyleSheet(
            "QPushButton{background:rgba(20,40,80,160);color:#4d96ff;"
            "border:1px solid rgba(77,150,255,60);border-radius:4px;"
            "padding:3px 10px;font-size:14px;}"
            "QPushButton:hover{background:rgba(30,60,120,200);}"
        )
        rst.clicked.connect(self._reset)
        ctrl.addWidget(rst); ctrl.addStretch()
        lo.addLayout(ctrl)

        self._update_highlight()

    def _on_tick(self, idx: int, checked: bool):
        if checked:
            self._current_round = min(idx + 1, len(self._tick_btns) - 1)
        self._update_highlight()

    def _reset(self):
        for _, _, btn in self._tick_btns:
            btn.blockSignals(True)
            btn.setChecked(False)
            btn.blockSignals(False)
        self._current_round = 0
        self._update_highlight()

    def _update_highlight(self):
        for i, (num, lbl_w, btn) in enumerate(self._tick_btns):
            if btn.isChecked():
                self._round_frames[i].setStyleSheet(
                    "QFrame#lcRow{background:rgba(5,10,20,60);border-radius:3px;border:none;}"
                )
                num.setStyleSheet(
                    "color:#333;font-size:14px;font-weight:bold;background:transparent;"
                )
            elif i == self._current_round:
                self._round_frames[i].setStyleSheet(
                    "QFrame#lcRow{background:rgba(10,40,20,160);"
                    "border-left:3px solid #27ae60;border-radius:3px;border-top:none;"
                    "border-right:none;border-bottom:none;}"
                )
                num.setStyleSheet(
                    "color:#27ae60;font-size:14px;font-weight:bold;background:transparent;"
                )
            else:
                self._round_frames[i].setStyleSheet(
                    "QFrame#lcRow{background:rgba(255,211,61,8);border-radius:3px;border:none;}"
                )
                num.setStyleSheet(
                    "color:#ffd93d;font-size:14px;font-weight:bold;background:transparent;"
                )


# ════════════════════════════════════════════════════════════════════════════
# BOSS HUD
# ════════════════════════════════════════════════════════════════════════════

class BossHUDOverlay(BaseHUDOverlay):
    """
    4 tabs: Boss Info | Cheats | Counters | Guides
    Counters/Guides tabs show data linked to the current boss inside the overlay.
    Search uses plain LIKE — no FTS quirks.
    """
    search_requested = pyqtSignal(str)   # tells main app to navigate to this boss
    ocr_toggled      = pyqtSignal(bool)

    def __init__(self, parent=None):
        self._conn = None
        self._boss_names: List[str] = []
        self._current_boss_name: str = ""
        super().__init__("boss", parent)

    def set_conn(self, conn):
        self._conn = conn

    def set_boss_names(self, names: List[str]):
        self._boss_names = names

    # ── Build content ─────────────────────────────────────────────

    def _build_content(self, host: QWidget):
        lo = QVBoxLayout(host)
        lo.setContentsMargins(8, 6, 8, 8)
        lo.setSpacing(5)

        # Search row
        sr = QHBoxLayout(); sr.setSpacing(4)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search boss…")
        self._search_input.setStyleSheet(
            "QLineEdit{background:rgba(10,20,50,180);color:#e0e0e0;"
            "border:1px solid rgba(233,69,96,80);border-radius:5px;"
            "padding:4px 8px;font-size:14px;}"
            "QLineEdit:focus{border-color:#e94560;}"
        )
        self._search_input.textChanged.connect(self._on_search_changed)
        self._search_input.returnPressed.connect(self._pick_first)
        sr.addWidget(self._search_input, stretch=1)

        clr = QPushButton("\u2715")
        clr.setFixedSize(22, 22)
        clr.setCursor(Qt.PointingHandCursor)
        clr.setStyleSheet(
            "QPushButton{background:rgba(80,20,30,120);color:#e94560;border:none;"
            "border-radius:4px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:white;}"
        )
        clr.clicked.connect(self._clear_search)
        sr.addWidget(clr)
        lo.addLayout(sr)

        # Results dropdown
        self._results_list = QListWidget()
        self._results_list.setMaximumHeight(130)
        self._results_list.setStyleSheet(
            "QListWidget{background:rgba(10,20,50,230);color:#e0e0e0;"
            "border:1px solid rgba(233,69,96,60);border-radius:4px;font-size:14px;}"
            "QListWidget::item{padding:3px 8px;}"
            "QListWidget::item:selected{background:#e94560;color:white;}"
            "QListWidget::item:hover{background:rgba(233,69,96,70);}"
        )
        self._results_list.setVisible(False)
        self._results_list.itemClicked.connect(self._on_result_clicked)
        lo.addWidget(self._results_list)

        # OCR row
        ocr_row = QHBoxLayout(); ocr_row.setSpacing(5)
        self._ocr_check = QCheckBox("\U0001F47E OCR")
        self._ocr_check.setStyleSheet(
            "QCheckBox{color:#e94560;font-size:13px;background:transparent;spacing:5px;}"
            "QCheckBox::indicator{width:13px;height:13px;border:1px solid #e94560;"
            "border-radius:3px;background:rgba(10,20,50,160);}"
            "QCheckBox::indicator:checked{background:#e94560;}"
        )
        self._ocr_check.stateChanged.connect(lambda s: self.ocr_toggled.emit(bool(s)))
        ocr_row.addWidget(self._ocr_check)
        ocr_row.addStretch()
        lo.addLayout(ocr_row)

        lo.addWidget(_div())

        # 4 tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane{background:transparent;border:none;}"
            "QTabBar::tab{background:rgba(10,20,50,120);color:#888;"
            "border:1px solid rgba(233,69,96,40);padding:4px 8px;"
            "margin-right:2px;border-top-left-radius:5px;border-top-right-radius:5px;"
            "font-size:13px;}"
            "QTabBar::tab:selected{background:rgba(233,69,96,60);color:#e94560;"
            "border-bottom-color:transparent;}"
        )

        self._info_w = QWidget(); self._info_w.setStyleSheet("background:transparent;")
        self._info_lo = QVBoxLayout(self._info_w)
        self._info_lo.setContentsMargins(4, 6, 4, 4); self._info_lo.setSpacing(4)
        self._info_lo.addWidget(_lbl("\u2014 No boss selected \u2014", "#e94560", bold=True, size=16))
        self._info_lo.addStretch()

        self._cheats_w = QWidget(); self._cheats_w.setStyleSheet("background:transparent;")
        self._cheats_lo = QVBoxLayout(self._cheats_w)
        self._cheats_lo.setContentsMargins(4, 6, 4, 4); self._cheats_lo.setSpacing(4)
        self._cheats_lo.addWidget(_lbl("No cheats.", "#444", size=13))
        self._cheats_lo.addStretch()

        self._counters_w = QWidget(); self._counters_w.setStyleSheet("background:transparent;")
        self._counters_lo = QVBoxLayout(self._counters_w)
        self._counters_lo.setContentsMargins(4, 6, 4, 4); self._counters_lo.setSpacing(4)
        self._counters_lo.addWidget(_lbl("No counters linked.", "#666", size=13))
        self._counters_lo.addStretch()

        self._guides_w = QWidget(); self._guides_w.setStyleSheet("background:transparent;")
        self._guides_lo = QVBoxLayout(self._guides_w)
        self._guides_lo.setContentsMargins(4, 6, 4, 4); self._guides_lo.setSpacing(4)
        self._guides_lo.addWidget(_lbl("No guides linked.", "#666", size=13))
        self._guides_lo.addStretch()

        self._tabs.addTab(self._info_w,     "\U0001F4CB Info")
        self._tabs.addTab(self._cheats_w,   "\u2694 Cheats")
        self._tabs.addTab(self._counters_w, "\u23F1 Counters")
        self._tabs.addTab(self._guides_w,   "\U0001F4D6 Guides")
        lo.addWidget(self._tabs, stretch=1)

    # ── Search ────────────────────────────────────────────────────

    def _on_search_changed(self, text: str):
        query = text.strip()
        if not query:
            self._results_list.setVisible(False)
            return
        matches = []
        if self._conn:
            try:
                rows = self._conn.execute(
                    "SELECT name FROM bosses WHERE is_active=1 AND name LIKE ?"
                    " ORDER BY name LIMIT 30",
                    (f"%{query}%",)
                ).fetchall()
                matches = [r["name"] for r in rows]
            except Exception:
                pass
        if not matches:
            ql = query.lower()
            matches = [n for n in self._boss_names if ql in n.lower()][:30]
        self._results_list.clear()
        for m in matches:
            self._results_list.addItem(m)
        self._results_list.setVisible(bool(matches))

    def _pick_first(self):
        if self._results_list.count():
            self._select_boss(self._results_list.item(0).text())

    def _on_result_clicked(self, item):
        self._select_boss(item.text())

    def _select_boss(self, name: str):
        self._results_list.setVisible(False)
        self._search_input.blockSignals(True)
        self._search_input.setText(name)
        self._search_input.blockSignals(False)
        self._current_boss_name = name

        # Load into overlay from DB
        if self._conn:
            try:
                import database as db
                data = db.get_boss(self._conn, name)
                if data:
                    self.refresh(data)  # populates overlay tabs — does NOT emit signal
            except Exception:
                pass

        # Always tell main app to navigate — this is one-way, not recursive
        self.search_requested.emit(name)

    def _clear_search(self):
        self._search_input.clear()
        self._results_list.setVisible(False)
        # Also clear the currently loaded boss results
        self._current_boss_name = ""
        self.refresh({})  # resets all tabs to empty state

    # ── Public ───────────────────────────────────────────────────

    def set_ocr_available(self, available: bool):
        self._ocr_check.setEnabled(available)
        if not available:
            self._ocr_check.setText("\U0001F47E OCR (N/A)")

    def set_ocr_checked(self, checked: bool):
        self._ocr_check.blockSignals(True)
        self._ocr_check.setChecked(checked)
        self._ocr_check.blockSignals(False)

    def refresh(self, data: dict):
        _clear_layout(self._info_lo)
        _clear_layout(self._cheats_lo)

        if not data:
            self._info_lo.addWidget(_lbl("\u2014 No boss selected \u2014", "#e94560", bold=True, size=16))
            self._info_lo.addStretch()
            self._cheats_lo.addWidget(_lbl("No boss selected.", "#444", size=13))
            self._cheats_lo.addStretch()
            self._tabs.setCurrentIndex(0)
            self._current_boss_name = ""
            self._refresh_counters_tab()
            self._refresh_guides_tab()
            return

        name = data.get("name", "?")
        self._current_boss_name = name
        self._search_input.blockSignals(True)
        self._search_input.setText(name)
        self._search_input.blockSignals(False)
        self._results_list.setVisible(False)

        # Info tab
        self._info_lo.addWidget(_lbl(name, "#e94560", bold=True, size=16))
        parts = []
        if hp := data.get("health"):   parts.append(f"\u2665 {hp}")
        if rk := data.get("rank"):     parts.append(f"\u2605 Rank {rk}")
        if sc := data.get("school"):   parts.append(f"\U0001F4DA {sc}")
        if lc := data.get("location"): parts.append(f"\U0001F4CD {lc}")
        if parts:
            self._info_lo.addWidget(_lbl("  \u00B7  ".join(parts), "#888", size=13, wrap=True))
        bs = data.get("battle_stats", {}) or {}
        if bs:
            self._info_lo.addWidget(_div())
            for k, v in list(bs.items())[:8]:
                # Use a container widget (not a raw QHBoxLayout) so _clear_layout
                # can fully destroy it when the next boss is loaded
                row_w = QWidget(); row_w.setStyleSheet("background:transparent;")
                row_lo = QHBoxLayout(row_w)
                row_lo.setContentsMargins(0, 0, 0, 0); row_lo.setSpacing(4)
                row_lo.addWidget(_lbl(str(k), "#666", size=13))
                row_lo.addStretch()
                row_lo.addWidget(_lbl(str(v), "#aaa", size=13))
                self._info_lo.addWidget(row_w)
        self._info_lo.addStretch()

        # Cheats tab
        cheats = data.get("cheats", [])
        if cheats:
            for cheat in cheats:
                self._cheats_lo.addWidget(self._make_cheat_card(cheat))
        else:
            self._cheats_lo.addWidget(_lbl("No cheats recorded.", "#444", size=13))
        self._cheats_lo.addStretch()

        self._tabs.setCurrentIndex(1 if cheats else 0)
        self._refresh_counters_tab()
        self._refresh_guides_tab()
        # Note: search_requested is emitted by _select_boss, not here,
        # to prevent recursion when main app calls refresh() via update_boss()

    # ── Counters tab ─────────────────────────────────────────────

    def _refresh_counters_tab(self):
        _clear_layout(self._counters_lo)
        boss = self._current_boss_name
        if not boss or not self._conn:
            self._counters_lo.addWidget(_lbl("No boss selected.", "#666", size=13))
            self._counters_lo.addStretch(); return
        try:
            import database as db
            counters = db.get_counters_for_boss(self._conn, boss)
        except Exception:
            counters = []
        if not counters:
            self._counters_lo.addWidget(_lbl(f"No counters linked to {boss}.", "#666", size=13))
            self._counters_lo.addStretch(); return
        for ctr in counters:
            self._counters_lo.addWidget(self._make_counter_card(ctr))
        self._counters_lo.addStretch()

    def _make_counter_card(self, ctr: dict) -> QWidget:
        """Stateful live counter card with tick buttons and reset."""
        return _LiveCounterWidget(ctr)

    # ── Guides tab ────────────────────────────────────────────────

    def _refresh_guides_tab(self):
        _clear_layout(self._guides_lo)
        boss = self._current_boss_name
        if not boss or not self._conn:
            self._guides_lo.addWidget(_lbl("No boss selected.", "#666", size=13))
            self._guides_lo.addStretch(); return
        try:
            import database as db
            guides = db.get_guides_for_boss(self._conn, boss)
        except Exception:
            guides = []
        if not guides:
            self._guides_lo.addWidget(_lbl(f"No guides linked to {boss}.", "#666", size=13))
            self._guides_lo.addStretch(); return
        for guide in guides:
            self._guides_lo.addWidget(self._make_guide_block(guide))
        self._guides_lo.addStretch()

    def _make_guide_block(self, guide: dict) -> QFrame:
        card = QFrame(); card.setObjectName("guideCard")
        card.setStyleSheet(
            "QFrame#guideCard{background:rgba(20,15,30,120);border:1px solid #c39bd344;"
            "border-radius:6px;}"
        )
        lo = QVBoxLayout(card)
        lo.setContentsMargins(8, 5, 8, 6); lo.setSpacing(3)
        lo.addWidget(_lbl(guide.get("name", "?"), "#c39bd3", bold=True, size=16))
        text = (guide.get("free_text") or "").strip()
        if text:
            lo.addWidget(_div())
            for line in text.split("\n"):
                if line.strip():
                    lo.addWidget(_lbl(line.strip(), "#cccccc", size=15, wrap=True))

        table_data = guide.get("table_data") or {}
        schools    = guide.get("schools") or []
        num_rounds = int(guide.get("num_rounds") or 0)
        num_cols   = 4

        if table_data and num_rounds > 0:
            lo.addWidget(_div())
            # Header
            hdr_lo = QHBoxLayout(); hdr_lo.setSpacing(2)
            rn = _lbl("Rnd", "#555", bold=True, size=13); rn.setFixedWidth(24)
            hdr_lo.addWidget(rn)
            for c in range(num_cols):
                cs    = schools[c] if c < len(schools) else []
                label = (" + ".join(cs) if isinstance(cs, list) and cs
                         else str(cs) if cs else "—")
                first = (cs[0] if isinstance(cs, list) and cs
                         else (cs if isinstance(cs, str) else ""))
                ccol = SCHOOL_COLORS.get(first, "#888")
                h = _lbl(label, ccol, bold=True, size=13); h.setAlignment(Qt.AlignCenter)
                hdr_lo.addWidget(h, stretch=1)
            lo.addLayout(hdr_lo)

            # Data rows — alternating bg, objectName scoped
            for r_idx in range(num_rounds):
                row_bg = "rgba(195,155,211,10)" if r_idx % 2 == 0 else "rgba(255,255,255,4)"
                rf = QFrame(); rf.setObjectName("guideRow")
                rf.setStyleSheet(
                    f"QFrame#guideRow{{background:{row_bg};border-radius:3px;border:none;}}"
                )
                rl = QHBoxLayout(rf)
                rl.setContentsMargins(3, 2, 3, 2); rl.setSpacing(2)
                rn = _lbl(str(r_idx + 1), "#888", size=13); rn.setFixedWidth(24)
                rl.addWidget(rn)
                for c_idx in range(num_cols):
                    cell = table_data.get(f"{r_idx}_c{c_idx}", "")
                    cl = _lbl(str(cell) if cell else "—", "#d0d0d0", size=14, wrap=True)
                    cl.setAlignment(Qt.AlignCenter)
                    rl.addWidget(cl, stretch=1)
                lo.addWidget(rf)
        return card

    # ── Cheat card ────────────────────────────────────────────────

    def _make_cheat_card(self, cheat) -> QFrame:
        if isinstance(cheat, str):
            cheat = {"text": cheat, "type": "unknown"}
        ctype = cheat.get("type", "unknown")
        meta  = CHEAT_TYPE_META.get(ctype, CHEAT_TYPE_META["unknown"])
        text  = cheat.get("text", "")
        subs  = cheat.get("sub_points", [])
        # bg is comma-separated RGB e.g. "30,10,10"
        bg_rgb = meta["bg"]
        alpha = overlay_settings.get_overlay_alpha("boss")
        bg_rgba = f"rgba({bg_rgb},{alpha})"
        bg_solid = f"rgb({bg_rgb})"

        # ── Cycle header: section divider, not a regular card ──
        if ctype == 'cycle_header':
            card = QFrame(); card.setObjectName("cheatCycleHeader")
            card.setStyleSheet(
                f"QFrame#cheatCycleHeader{{background:transparent;"
                f"border:none;border-bottom:2px solid {meta['color']}66;"
                f"border-radius:0;margin-top:6px;}}"
            )
            lo = QVBoxLayout(card)
            lo.setContentsMargins(4, 8, 4, 4); lo.setSpacing(2)
            lo.addWidget(_lbl(text, meta["color"], bold=True, size=14, wrap=True))
            return card

        # ── Cycle info: plain italic text, no card ──
        if ctype == 'cycle_info':
            card = QFrame(); card.setObjectName("cheatCycleInfo")
            card.setStyleSheet(
                "QFrame#cheatCycleInfo{background:transparent;"
                "border:none;border-radius:0;}"
            )
            lo = QVBoxLayout(card)
            lo.setContentsMargins(4, 2, 4, 2); lo.setSpacing(0)
            info_lbl = _lbl(text, "#999999", size=13, wrap=True)
            info_lbl.setStyleSheet(info_lbl.styleSheet() + "font-style:italic;")
            lo.addWidget(info_lbl)
            return card

        # ── Standard cheat card ──
        card = QFrame(); card.setObjectName("cheatCard")
        card.setStyleSheet(
            f"QFrame#cheatCard{{background:{bg_rgba};"
            f"border:1px solid {meta['color']}55;"
            f"border-left:3px solid {meta['color']};"
            f"border-radius:5px;}}"
        )
        lo = QVBoxLayout(card)
        lo.setContentsMargins(8, 5, 8, 5); lo.setSpacing(3)
        badge = QLabel(meta["label"])
        badge.setStyleSheet(
            f"color:{meta['color']};font-size:14px;font-weight:bold;"
            f"background:{bg_solid};border:1px solid {meta['color']}44;"
            "border-radius:3px;padding:1px 5px;"
        )
        badge_row = QHBoxLayout(); badge_row.addWidget(badge); badge_row.addStretch()
        lo.addLayout(badge_row)
        if text:
            lo.addWidget(_lbl(text, "#d0d0d0", size=13, wrap=True))
        for sp in (subs or []):
            lo.addWidget(_lbl(f"  \u2022 {sp}", "#aaaaaa", size=13, wrap=True))
        return card

# ════════════════════════════════════════════════════════════════════════════
# QUEST ROW WIDGET
# ════════════════════════════════════════════════════════════════════════════

class _QuestRowWidget(QWidget):
    _BG_NORMAL = "background:transparent;"

    def _marked_style(self) -> str:
        alpha = overlay_settings.get_overlay_alpha("quest")
        # Use full yellow at the same opacity as the overlay background
        return (f"background:rgba(255,200,50,{alpha});"
                "border-left:3px solid #ffcc44;border-radius:2px;")

    def __init__(self, quest, has_marker, conn, parent=None):
        super().__init__(parent)
        self._quest    = quest
        self._marked   = has_marker
        self._conn     = conn
        self._quest_id = quest.get("id")
        self.setCursor(Qt.PointingHandCursor)
        self._label = None
        self._build()
        self._apply_style()

    def _type_html(self):
        import html as _h
        types = self._quest.get("types") or []
        if not types:
            raw = self._quest.get("types_json", "[]")
            try:
                types = json.loads(raw) if isinstance(raw, str) else []
            except Exception:
                types = []
        parts = []
        for t in (types or []):
            if isinstance(t, dict):
                label = t.get("label", "")
                col   = t.get("color") or QUEST_TYPE_COLORS.get(label.lower(), "#888")
            else:
                label = str(t); col = QUEST_TYPE_COLORS.get(label.lower(), "#888")
            if label:
                parts.append(f"<b style='color:{col}'>{_h.escape(label.title())}</b>")
        sep  = "<span style='color:#aaa'> + </span>"
        dash = "<span style='color:#aaa'> – </span>" if parts else ""
        return dash + sep.join(parts)

    def _full_html(self):
        import html as _h
        name  = _h.escape(self._quest.get("name", ""))
        types = self._type_html()
        pin   = " <span style='color:#ffcc44'>📌</span>" if self._marked else ""
        return (f"<span style='color:#d0d0d0;font-size:14px'>{name}</span>"
                f"<span style='font-size:13px'>{types}</span>{pin}")

    def _build(self):
        lo = QHBoxLayout(self)
        lo.setContentsMargins(2, 2, 4, 2); lo.setSpacing(3)
        num = self._quest.get("quest_number")
        if num:
            nl = _lbl(f"{num}.", "#555", size=13)
            nl.setFixedWidth(22)
            lo.addWidget(nl)
        self._label = QLabel()
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(True)
        self._label.setStyleSheet("background:transparent;")
        self._label.setText(self._full_html())
        lo.addWidget(self._label, stretch=1)

    def _apply_style(self):
        self.setStyleSheet(self._marked_style() if self._marked else self._BG_NORMAL)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._quest_id is not None:
            self._marked = not self._marked
            self._apply_style()
            if self._label:
                self._label.setText(self._full_html())
            if self._conn:
                try:
                    import database_quests as dq
                    if self._marked:
                        dq.set_quest_marker(self._conn, self._quest_id, "", False)
                    else:
                        dq.remove_quest_marker(self._conn, self._quest_id)
                except Exception:
                    pass
        super().mousePressEvent(event)


# ════════════════════════════════════════════════════════════════════════════
# QUEST TRACKER HUD
# ════════════════════════════════════════════════════════════════════════════

class QuestHUDOverlay(BaseHUDOverlay):
    def __init__(self, parent=None):
        self._conn = None
        self._last_world: Optional[str] = None
        super().__init__("quest", parent)

    def set_conn(self, conn):
        self._conn = conn
        self._refresh_overview()

    def _build_content(self, host: QWidget):
        lo = QVBoxLayout(host)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(0)
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
        lo.addWidget(self._stack)

        # Page 0
        self._ov_page = QWidget(); self._ov_page.setStyleSheet("background:transparent;")
        self._ov_lo = QVBoxLayout(self._ov_page)
        self._ov_lo.setContentsMargins(8, 6, 8, 6); self._ov_lo.setSpacing(4)
        self._stack.addWidget(self._ov_page)

        # Page 1
        self._det_page = QWidget(); self._det_page.setStyleSheet("background:transparent;")
        self._det_lo = QVBoxLayout(self._det_page)
        self._det_lo.setContentsMargins(0, 0, 0, 0); self._det_lo.setSpacing(0)
        self._stack.addWidget(self._det_page)

    def _refresh_overview(self):
        _clear_layout(self._ov_lo)
        if not self._conn:
            self._ov_lo.addWidget(_lbl("No database.", "#555", size=13)); return
        try:
            import database_quests as dq
            worlds = dq.get_all_worlds(self._conn)
        except Exception:
            self._ov_lo.addWidget(_lbl("Could not load worlds.", "#666", size=13)); return

        self._ov_lo.addWidget(_lbl("Worlds", "#4d96ff", bold=True, size=14))
        if not worlds:
            self._ov_lo.addWidget(_lbl("No world data yet.", "#555", size=13))
        else:
            for w in worlds:
                self._ov_lo.addWidget(self._make_world_row(w))
        self._ov_lo.addStretch()

    def _make_world_row(self, world: dict) -> QWidget:
        name  = world["name"]
        color = WORLD_COLORS.get(name, "#4d96ff")
        row = QWidget()
        row.setStyleSheet("background:rgba(20,30,60,80);border-radius:4px;")
        row.setCursor(Qt.PointingHandCursor)
        lo = QHBoxLayout(row)
        lo.setContentsMargins(8, 4, 8, 4); lo.setSpacing(0)
        lo.addWidget(_lbl(name, color, size=14))
        lo.addStretch()
        lo.addWidget(_lbl("›", color, bold=True, size=16))
        row.mousePressEvent = lambda e, w=world: self._open_world(w)
        return row

    def _open_world(self, world: dict):
        self._last_world = world["name"]
        _clear_layout(self._det_lo)

        acc   = self._pal["accent"]
        color = WORLD_COLORS.get(world["name"], "#4d96ff")

        hdr = QWidget()
        hdr.setStyleSheet(f"background:rgba(10,15,40,160);border-bottom:1px solid {color}44;")
        hdr_lo = QHBoxLayout(hdr)
        hdr_lo.setContentsMargins(8, 5, 8, 5); hdr_lo.setSpacing(6)
        bb = _back_btn(acc)
        bb.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        hdr_lo.addWidget(bb)
        hdr_lo.addWidget(_lbl(f"🌍 {world['name']}", color, bold=True, size=15))
        hdr_lo.addStretch()
        self._det_lo.addWidget(hdr)

        content = QWidget(); content.setStyleSheet("background:transparent;")
        clo = QVBoxLayout(content)
        clo.setContentsMargins(8, 6, 8, 6); clo.setSpacing(0)

        first_marked_row = None  # we'll scroll to this after layout

        try:
            import database_quests as dq
            areas      = dq.get_areas_for_world(self._conn, world["id"])
            markers    = dq.get_all_markers_for_world(self._conn, world["id"])
            marker_map = markers if isinstance(markers, dict) else {}

            if not areas:
                clo.addWidget(_lbl("No quest data loaded.", "#555", size=13))
            else:
                for area in areas:
                    ah = QLabel(area["name"])
                    ah.setStyleSheet(
                        f"color:{color};font-size:14px;font-weight:bold;"
                        f"background:transparent;border-left:3px solid {color};"
                        "padding:4px 0 4px 8px;margin-top:6px;"
                    )
                    clo.addWidget(ah)
                    for q in dq.get_quests_for_area(self._conn, area["id"]):
                        has_marker = q["id"] in marker_map
                        row_w = _QuestRowWidget(q, has_marker, self._conn)
                        clo.addWidget(row_w)
                        if has_marker and first_marked_row is None:
                            first_marked_row = row_w

        except Exception as ex:
            clo.addWidget(_lbl(f"Error: {ex}", "#e94560", size=13, wrap=True))

        clo.addStretch()
        self._det_lo.addWidget(content, stretch=1)
        self._stack.setCurrentIndex(1)

        # Scroll to first marked quest after layout settles
        if first_marked_row is not None:
            scroll = self._scroll
            QTimer.singleShot(80, lambda: scroll.ensureWidgetVisible(first_marked_row))

    def _make_quest_row(self, quest: dict, marker) -> QWidget:
        """Legacy — kept for compatibility; _open_world now uses _QuestRowWidget directly."""
        return _QuestRowWidget(quest, bool(marker), self._conn)

    def refresh(self, data: dict):
        if not self._conn:
            return
        self._refresh_overview()
        if self._stack.currentIndex() == 1 and self._last_world:
            try:
                import database_quests as dq
                w = dq.get_world_by_name(self._conn, self._last_world)
                if w:
                    self._open_world(w)
            except Exception:
                pass

    def navigate_to_world(self, world_name: str):
        if not self._conn:
            return
        try:
            import database_quests as dq
            w = dq.get_world_by_name(self._conn, world_name)
            if w:
                self._refresh_overview()
                self._open_world(w)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# ROUND COUNTER HUD
# ════════════════════════════════════════════════════════════════════════════

class RoundCounterHUDOverlay(BaseHUDOverlay):
    def __init__(self, parent=None):
        self._conn = None
        self._last_counter_id: Optional[int] = None
        self._tick_btns:    list = []
        self._round_frames: list = []
        self._current_round = 0
        super().__init__("counter", parent)

    def set_conn(self, conn):
        self._conn = conn
        self._refresh_overview()

    def _build_content(self, host: QWidget):
        lo = QVBoxLayout(host)
        lo.setContentsMargins(0, 0, 0, 0); lo.setSpacing(0)
        self._stack = QStackedWidget(); self._stack.setStyleSheet("background:transparent;")
        lo.addWidget(self._stack)

        self._ov_page = QWidget(); self._ov_page.setStyleSheet("background:transparent;")
        self._ov_lo = QVBoxLayout(self._ov_page)
        self._ov_lo.setContentsMargins(8, 6, 8, 6); self._ov_lo.setSpacing(4)
        self._stack.addWidget(self._ov_page)

        self._det_page = QWidget(); self._det_page.setStyleSheet("background:transparent;")
        self._det_lo = QVBoxLayout(self._det_page)
        self._det_lo.setContentsMargins(0, 0, 0, 0); self._det_lo.setSpacing(0)
        self._stack.addWidget(self._det_page)

    def _refresh_overview(self):
        _clear_layout(self._ov_lo)
        if not self._conn:
            self._ov_lo.addWidget(_lbl("No database.", "#555", size=13)); return
        try:
            import database as db
            counters = db.list_round_counters(self._conn)
        except Exception:
            self._ov_lo.addWidget(_lbl("Could not load counters.", "#666", size=13)); return

        self._ov_lo.addWidget(_lbl("Round Counters", "#ffd93d", bold=True, size=14))
        if not counters:
            self._ov_lo.addWidget(_lbl("No counters yet.", "#555", size=13))
        else:
            for ctr in counters:
                self._ov_lo.addWidget(self._make_counter_row(ctr))
        self._ov_lo.addStretch()

    def _make_counter_row(self, ctr: dict) -> QWidget:
        row = QWidget(); row.setStyleSheet("background:rgba(30,25,10,80);border-radius:4px;")
        row.setCursor(Qt.PointingHandCursor)
        lo = QHBoxLayout(row); lo.setContentsMargins(8, 4, 8, 4); lo.setSpacing(0)
        lo.addWidget(_lbl(ctr.get("name", "?"), "#ffd93d", size=14))
        lo.addStretch()
        lo.addWidget(_lbl("›", "#ffd93d", bold=True, size=16))
        row.mousePressEvent = lambda e, c=ctr: self._open_counter(c)
        return row

    def _open_counter(self, ctr: dict):
        self._last_counter_id = ctr.get("id")
        self._current_round   = 0
        self._tick_btns       = []
        self._round_frames    = []
        _clear_layout(self._det_lo)

        acc = self._pal["accent"]
        hdr = QWidget()
        hdr.setStyleSheet("background:rgba(10,15,40,160);border-bottom:1px solid #ffd93d44;")
        hdr_lo = QHBoxLayout(hdr); hdr_lo.setContentsMargins(8, 5, 8, 5); hdr_lo.setSpacing(6)
        bb = _back_btn(acc); bb.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        hdr_lo.addWidget(bb)
        hdr_lo.addWidget(_lbl(ctr.get("name", "?"), "#ffd93d", bold=True, size=15))
        hdr_lo.addStretch()
        self._det_lo.addWidget(hdr)

        body = QWidget(); body.setStyleSheet("background:transparent;")
        blo = QVBoxLayout(body); blo.setContentsMargins(8, 6, 8, 6); blo.setSpacing(3)

        if ctr.get("description"):
            blo.addWidget(_lbl(ctr["description"], "#888", size=13, wrap=True))
        if ctr.get("linked_bosses"):
            blo.addWidget(_lbl("  ".join(f"👾 {b}" for b in ctr["linked_bosses"]), "#4d96ff", size=13))
        blo.addWidget(_div())

        for i, r in enumerate(ctr.get("rounds", [])):
            rf = self._make_round_row(i, r.get("label", "") or f"Round {i+1}")
            blo.addWidget(rf)
            self._round_frames.append(rf)

        ctrl = QHBoxLayout()
        rst = QPushButton("↺ Reset")
        rst.setStyleSheet(
            "QPushButton{background:rgba(20,40,80,160);color:#4d96ff;"
            "border:1px solid rgba(31,58,110,100);border-radius:4px;"
            "padding:3px 8px;font-size:13px;}"
            "QPushButton:hover{background:rgba(30,60,120,200);}"
        )
        rst.clicked.connect(self._reset_ticks)
        ctrl.addWidget(rst); ctrl.addStretch()
        blo.addLayout(ctrl); blo.addStretch()

        self._det_lo.addWidget(body, stretch=1)
        self._update_highlight()
        self._stack.setCurrentIndex(1)

    def _make_round_row(self, idx: int, label: str) -> QFrame:
        rf = QFrame()
        rf.setStyleSheet("QFrame{background:rgba(10,20,40,100);border-radius:4px;}")
        lo = QHBoxLayout(rf); lo.setContentsMargins(6, 3, 6, 3); lo.setSpacing(5)
        num = _lbl(f"R{idx+1}", "#555", bold=True, size=13); num.setFixedWidth(22)
        lo.addWidget(num)
        lw = _lbl(label, "#c0c0c0", size=13, wrap=True)
        lo.addWidget(lw, stretch=1)
        tick = QPushButton("○"); tick.setFixedSize(20, 20); tick.setCheckable(True)
        tick.setStyleSheet(
            "QPushButton{background:rgba(10,10,30,120);color:#555;border:1px solid #333;"
            "border-radius:10px;font-size:13px;font-weight:bold;padding:0;}"
            "QPushButton:checked{background:#e94560;color:white;border-color:#e94560;}"
            "QPushButton:hover{border-color:#e94560;}"
        )
        tick.toggled.connect(lambda checked, i=idx: self._on_tick(i, checked))
        lo.addWidget(tick)
        self._tick_btns.append((num, lw, tick))
        return rf

    def _on_tick(self, idx: int, checked: bool):
        if checked:
            self._current_round = min(idx + 1, len(self._tick_btns) - 1)
        self._update_highlight()

    def _reset_ticks(self):
        for num, lw, btn in self._tick_btns:
            btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
        self._current_round = 0; self._update_highlight()

    def _update_highlight(self):
        for i, (num, lw, btn) in enumerate(self._tick_btns):
            if btn.isChecked():
                self._round_frames[i].setStyleSheet("QFrame{background:rgba(5,10,20,80);border-radius:4px;}")
                num.setStyleSheet("color:#333;font-size:13px;font-weight:bold;background:transparent;")
            elif i == self._current_round:
                self._round_frames[i].setStyleSheet(
                    "QFrame{background:rgba(10,40,20,140);border-left:3px solid #27ae60;border-radius:4px;}")
                num.setStyleSheet("color:#27ae60;font-size:13px;font-weight:bold;background:transparent;")
            else:
                self._round_frames[i].setStyleSheet("QFrame{background:rgba(10,20,40,100);border-radius:4px;}")
                num.setStyleSheet("color:#555;font-size:13px;font-weight:bold;background:transparent;")

    def refresh(self, data: dict):
        if not self._conn: return
        self._refresh_overview()
        if self._stack.currentIndex() == 1 and self._last_counter_id is not None:
            try:
                import database as db
                ctr = db.get_round_counter(self._conn, self._last_counter_id)
                if ctr: self._open_counter(ctr)
            except Exception:
                pass

    def navigate_to_counter(self, counter_id: int):
        if not self._conn: return
        try:
            import database as db
            ctr = db.get_round_counter(self._conn, counter_id)
            if ctr:
                self._refresh_overview(); self._open_counter(ctr)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# STRATEGY GUIDE HUD
# table_data is a dict with keys like "0_c0", "0_c1", "1_c0" ...
# schools is a LIST OF LISTS: [["Fire","Ice"], ["Storm"], [], ["Balance"]]
# ════════════════════════════════════════════════════════════════════════════

class StrategyGuideHUDOverlay(BaseHUDOverlay):
    def __init__(self, parent=None):
        self._conn = None
        self._last_guide_id: Optional[int] = None
        super().__init__("guide", parent)

    def set_conn(self, conn):
        self._conn = conn
        self._refresh_overview()

    def _build_content(self, host: QWidget):
        lo = QVBoxLayout(host)
        lo.setContentsMargins(0, 0, 0, 0); lo.setSpacing(0)
        self._stack = QStackedWidget(); self._stack.setStyleSheet("background:transparent;")
        lo.addWidget(self._stack)

        self._ov_page = QWidget(); self._ov_page.setStyleSheet("background:transparent;")
        self._ov_lo = QVBoxLayout(self._ov_page)
        self._ov_lo.setContentsMargins(8, 6, 8, 6); self._ov_lo.setSpacing(4)
        self._stack.addWidget(self._ov_page)

        self._det_page = QWidget(); self._det_page.setStyleSheet("background:transparent;")
        self._det_lo = QVBoxLayout(self._det_page)
        self._det_lo.setContentsMargins(0, 0, 0, 0); self._det_lo.setSpacing(0)
        self._stack.addWidget(self._det_page)

    def _refresh_overview(self):
        _clear_layout(self._ov_lo)
        if not self._conn:
            self._ov_lo.addWidget(_lbl("No database.", "#555", size=13)); return
        try:
            import database as db
            guides = db.list_guides(self._conn)
        except Exception:
            self._ov_lo.addWidget(_lbl("Could not load guides.", "#666", size=13)); return

        self._ov_lo.addWidget(_lbl("Strategy Guides", "#c39bd3", bold=True, size=14))
        if not guides:
            self._ov_lo.addWidget(_lbl("No guides yet.", "#555", size=13))
        else:
            for g in guides:
                self._ov_lo.addWidget(self._make_guide_row(g))
        self._ov_lo.addStretch()

    def _make_guide_row(self, guide: dict) -> QWidget:
        row = QWidget(); row.setStyleSheet("background:rgba(20,15,30,80);border-radius:4px;")
        row.setCursor(Qt.PointingHandCursor)
        lo = QHBoxLayout(row); lo.setContentsMargins(8, 4, 8, 4); lo.setSpacing(0)
        lo.addWidget(_lbl(guide.get("name", "?"), "#c39bd3", size=14))
        lo.addStretch()
        lo.addWidget(_lbl("›", "#c39bd3", bold=True, size=16))
        row.mousePressEvent = lambda e, g=guide: self._open_guide(g)
        return row

    def _open_guide(self, guide: dict):
        self._last_guide_id = guide.get("id")
        _clear_layout(self._det_lo)

        acc = self._pal["accent"]
        try:
            import database as db
            full = db.get_guide(self._conn, guide["id"]) if guide.get("id") else guide
        except Exception:
            full = guide
        if not full:
            full = guide

        # Header
        hdr = QWidget()
        hdr.setStyleSheet("background:rgba(10,15,40,160);border-bottom:1px solid #c39bd344;")
        hdr_lo = QHBoxLayout(hdr); hdr_lo.setContentsMargins(8, 5, 8, 5); hdr_lo.setSpacing(6)
        bb = _back_btn(acc); bb.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        hdr_lo.addWidget(bb)
        hdr_lo.addWidget(_lbl(full.get("name", "?"), "#c39bd3", bold=True, size=15))
        hdr_lo.addStretch()
        self._det_lo.addWidget(hdr)

        # Body
        body = QWidget(); body.setStyleSheet("background:transparent;")
        blo = QVBoxLayout(body); blo.setContentsMargins(8, 6, 8, 6); blo.setSpacing(5)

        if full.get("linked_bosses"):
            blo.addWidget(_lbl("  ".join(f"👾 {b}" for b in full["linked_bosses"]), "#4d96ff", size=13))

        text = (full.get("free_text") or "").strip()
        if text:
            blo.addWidget(_div())
            blo.addWidget(_lbl("📝 Notes", "#c39bd3", bold=True, size=13))
            for line in text.split("\n"):
                if line.strip():
                    blo.addWidget(_lbl(line.strip(), "#cccccc", size=13, wrap=True))

        # ── Spell table ──
        # table_data: dict with keys "r_c{col}" e.g. "0_c0", "0_c1" ...
        # schools:    list of 4 lists  e.g. [["Fire","Ice"], ["Storm"], [], ["Balance"]]
        table_data = full.get("table_data") or {}
        schools    = full.get("schools") or []
        num_rounds = int(full.get("num_rounds") or 0)
        num_cols   = 4  # always 4 columns

        if table_data and num_rounds > 0:
            blo.addWidget(_div())
            blo.addWidget(_lbl("⚔ Spell Table", "#c39bd3", bold=True, size=13))

            # Build column header labels
            col_labels = []
            for c in range(num_cols):
                col_schools = schools[c] if c < len(schools) else []
                if isinstance(col_schools, list):
                    label = " + ".join(col_schools) if col_schools else "—"
                else:
                    label = str(col_schools) if col_schools else "—"
                col_labels.append(label)

            # Header row
            hdr_row = QHBoxLayout(); hdr_row.setSpacing(3)
            rn = _lbl("Rnd", "#555", bold=True, size=12); rn.setFixedWidth(22)
            hdr_row.addWidget(rn)
            for c, ch in enumerate(col_labels):
                # colour by first school in this column
                col_schools = schools[c] if c < len(schools) else []
                first = (col_schools[0] if isinstance(col_schools, list) and col_schools
                         else (col_schools if isinstance(col_schools, str) else ""))
                ccol = SCHOOL_COLORS.get(first, "#888")
                h = _lbl(ch, ccol, bold=True, size=12); h.setAlignment(Qt.AlignCenter)
                hdr_row.addWidget(h, stretch=1)
            blo.addLayout(hdr_row)

            # Data rows — key format is "{row_index}_c{col_index}", alternating bg
            for r_idx in range(num_rounds):
                row_bg = "rgba(195,155,211,10)" if r_idx % 2 == 0 else "rgba(255,255,255,4)"
                rf = QFrame(); rf.setObjectName("sgRow")
                rf.setStyleSheet(
                    f"QFrame#sgRow{{background:{row_bg};border-radius:3px;border:none;}}"
                )
                row_lo = QHBoxLayout(rf); row_lo.setSpacing(3)
                row_lo.setContentsMargins(3, 2, 3, 2)
                rn = _lbl(str(r_idx + 1), "#888", size=13); rn.setFixedWidth(24)
                row_lo.addWidget(rn)
                for c_idx in range(num_cols):
                    key  = f"{r_idx}_c{c_idx}"
                    cell = table_data.get(key, "")
                    cl   = _lbl(str(cell) if cell else "—", "#d0d0d0", size=13, wrap=True)
                    cl.setAlignment(Qt.AlignCenter)
                    row_lo.addWidget(cl, stretch=1)
                blo.addWidget(rf)

        blo.addStretch()
        self._det_lo.addWidget(body, stretch=1)
        self._stack.setCurrentIndex(1)

    def refresh(self, data: dict):
        if not self._conn: return
        self._refresh_overview()
        if self._stack.currentIndex() == 1 and self._last_guide_id is not None:
            try:
                import database as db
                g = db.get_guide(self._conn, self._last_guide_id)
                if g: self._open_guide(g)
            except Exception:
                pass

    def navigate_to_guide(self, guide_id: int):
        if not self._conn: return
        try:
            import database as db
            g = db.get_guide(self._conn, guide_id)
            if g:
                self._refresh_overview(); self._open_guide(g)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════════════
# OVERLAY MANAGER
# ════════════════════════════════════════════════════════════════════════════

class OverlayManager:
    def __init__(self):
        self._overlays: Dict[str, BaseHUDOverlay] = {}
        self._conn = None
        self._restore_enabled()

    def set_conn(self, conn):
        self._conn = conn
        for ov in self._overlays.values():
            if hasattr(ov, "set_conn"):
                ov.set_conn(conn)

    def _get_or_create(self, key: str) -> BaseHUDOverlay:
        if key not in self._overlays:
            cls = {"boss": BossHUDOverlay, "quest": QuestHUDOverlay,
                   "counter": RoundCounterHUDOverlay, "guide": StrategyGuideHUDOverlay}[key]
            ov = cls()
            ov.closed.connect(lambda k: self._on_closed(k))
            if self._conn and hasattr(ov, "set_conn"):
                ov.set_conn(self._conn)
            self._overlays[key] = ov
        return self._overlays[key]

    def _restore_enabled(self):
        for key in ("boss", "quest", "counter", "guide"):
            if overlay_settings.is_enabled(key):
                self.toggle(key, True)

    def toggle(self, key: str, enabled: bool):
        overlay_settings.set_enabled(key, enabled, notify=False)
        if enabled:
            ov = self._get_or_create(key)
            if self._conn and hasattr(ov, "set_conn") and not getattr(ov, "_conn", None):
                ov.set_conn(self._conn)
            ov.show()
            _force_topmost(ov)
        else:
            if key in self._overlays:
                self._overlays[key].hide()

    def is_visible(self, key: str) -> bool:
        return key in self._overlays and self._overlays[key].isVisible()

    def _on_closed(self, key: str):
        # overlay_settings already updated; fire callbacks for UI sync
        for cb in overlay_settings._enabled_callbacks:
            try: cb(key, False)
            except Exception: pass

    def set_clickthrough(self, key: str, enabled: bool):
        overlay_settings.set_clickthrough(key, enabled)
        if key in self._overlays:
            self._overlays[key]._apply_ct(enabled)

    def update_boss(self, boss_data: Optional[dict]):
        if "boss" in self._overlays:
            self._overlays["boss"].refresh(boss_data or {})

    def update_quests(self, data: Optional[dict] = None):
        if "quest" in self._overlays:
            self._overlays["quest"].refresh(data or {})

    def update_counters(self, data: Optional[dict] = None):
        if "counter" in self._overlays:
            self._overlays["counter"].refresh(data or {})

    def update_guides(self, data: Optional[dict] = None):
        if "guide" in self._overlays:
            self._overlays["guide"].refresh(data or {})

    def navigate_quest_to_world(self, world_name: str):
        if "quest" in self._overlays:
            self._overlays["quest"].navigate_to_world(world_name)

    def get_boss_overlay(self) -> Optional[BossHUDOverlay]:
        return self._overlays.get("boss")

    def close_all(self):
        for ov in self._overlays.values():
            ov.hide()
