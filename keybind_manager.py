"""
keybind_manager.py  —  Wizard101 Companion  —  Global Keybinds
═══════════════════════════════════════════════════════════════
Global hotkeys that fire even when Wizard101 is focused, in any
window mode (fullscreen, windowed, borderless, borderless fullscreen).

Strategy
────────
Primary:  `keyboard` library (pip install keyboard)
          Hooks Windows low-level keyboard API — works regardless of
          which window/game has focus.

          Thread safety: the keyboard lib fires callbacks from its own
          hook thread. We use a Qt signal (thread-safe by design) to
          marshal the action back to the main thread event loop.

Fallback: Qt application-level event filter.
          Only fires when a Qt window has focus.

Bindings
────────
  boss / quest / counter / guide — toggle HUD overlays
  ocr                            — toggle Boss OCR

Persisted in keybinds.json next to the script.
"""

import json
import os
import logging
from typing import Dict, Optional, Callable

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QLineEdit, QMessageBox,
)
from PyQt5.QtCore import Qt, QObject, QEvent, pyqtSignal
from PyQt5.QtGui import QKeySequence

logger = logging.getLogger(__name__)

_KEYBINDS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "keybinds.json"
)

OVERLAY_KEYS  = ["boss", "quest", "counter", "guide"]
ALL_BIND_KEYS = OVERLAY_KEYS + ["ocr"]

BIND_LABELS = {
    "boss":    "👾 Boss Info Overlay",
    "quest":   "🗺 Quest Tracker Overlay",
    "counter": "⏱ Round Counter Overlay",
    "guide":   "📖 Strategy Guide Overlay",
    "ocr":     "👾 Boss OCR Toggle",
}

BIND_COLORS = {
    "boss":    "#e94560",
    "quest":   "#4d96ff",
    "counter": "#ffd93d",
    "guide":   "#c39bd3",
    "ocr":     "#66ff99",
}


# ═══════════════════════════════════════════════════════════════
# GLOBAL KEYBOARD HOOK
# ═══════════════════════════════════════════════════════════════

try:
    import keyboard as _kb
    _KB_AVAILABLE = True
    logger.info("Keybinds: keyboard library loaded — global hotkeys active")
except Exception as _e:
    _kb = None
    _KB_AVAILABLE = False
    logger.warning(
        f"Keybinds: keyboard library unavailable ({_e}) — "
        "falling back to Qt event filter (only works when app is focused)"
    )


class _HotkeySignaller(QObject):
    """
    Lives on the main thread. Its signal is emitted from the keyboard
    library's hook thread — Qt signals are thread-safe, so this is the
    correct way to get work onto the main thread event loop from any thread.
    """
    triggered = pyqtSignal(str)   # bind_key


class _GlobalHook:
    """Wraps keyboard-lib registrations and routes to _HotkeySignaller."""

    def __init__(self, signaller: _HotkeySignaller):
        self._sig = signaller
        self._registered: Dict[str, str] = {}   # bind_key → kb_seq string

    # ── public ──────────────────────────────────────────────────

    def register_all(self, sequences: Dict[str, str]):
        self.unregister_all()
        for bind_key, seq in sequences.items():
            if seq:
                self._register(bind_key, seq)

    def update(self, bind_key: str, seq: str):
        self._unregister(bind_key)
        if seq:
            self._register(bind_key, seq)

    def unregister_all(self):
        for bind_key in list(self._registered):
            self._unregister(bind_key)

    # ── internal ─────────────────────────────────────────────────

    @staticmethod
    def _to_kb_seq(seq: str) -> str:
        """
        'Ctrl+F1' → 'ctrl+f1'  (keyboard-lib format)
        'Alt+Shift+B' → 'alt+shift+b'
        """
        return seq.lower().replace(" ", "")

    def _register(self, bind_key: str, seq: str):
        if not _KB_AVAILABLE:
            return
        kb_seq = self._to_kb_seq(seq)
        try:
            # trigger_on_release=False → fire on key-down (default, explicit here)
            # suppress=False          → game still receives the keypress
            _kb.add_hotkey(
                kb_seq,
                self._make_cb(bind_key),
                suppress=False,
                trigger_on_release=False,
            )
            self._registered[bind_key] = kb_seq
            logger.info(f"Keybinds: registered '{seq}' → {bind_key}")
        except Exception as e:
            logger.warning(f"Keybinds: could not register '{seq}' → {bind_key}: {e}")

    def _unregister(self, bind_key: str):
        if not _KB_AVAILABLE:
            return
        kb_seq = self._registered.pop(bind_key, None)
        if kb_seq:
            try:
                _kb.remove_hotkey(kb_seq)
                logger.debug(f"Keybinds: unregistered '{kb_seq}'")
            except Exception:
                pass

    def _make_cb(self, bind_key: str):
        """
        The callback runs on the keyboard library's hook thread.
        Emitting a Qt signal is thread-safe — Qt queues it to the
        receiver's thread (main thread) automatically.
        """
        sig = self._sig
        def _cb():
            sig.triggered.emit(bind_key)
        return _cb


# ═══════════════════════════════════════════════════════════════
# QT EVENT FILTER  (fallback / app-focused)
# ═══════════════════════════════════════════════════════════════

def _parse_sequence(text: str):
    text = text.strip()
    if not text:
        return None
    ks = QKeySequence(text, QKeySequence.PortableText)
    if ks.isEmpty():
        ks = QKeySequence(text)
    if ks.isEmpty():
        return None
    combined  = ks[0]
    modifiers = Qt.KeyboardModifiers(combined & Qt.KeyboardModifierMask)
    key       = combined & ~Qt.KeyboardModifierMask
    if key == 0:
        return None
    return (modifiers, key)


def _normalise(text: str) -> str:
    parsed = _parse_sequence(text)
    if parsed is None:
        return ""
    modifiers, key = parsed
    parts = []
    if modifiers & Qt.ControlModifier:  parts.append("Ctrl")
    if modifiers & Qt.AltModifier:      parts.append("Alt")
    if modifiers & Qt.ShiftModifier:    parts.append("Shift")
    key_name = QKeySequence(key).toString()
    if key_name:
        parts.append(key_name)
    return "+".join(parts)


class _QtKeyFilter(QObject):
    """Catches key presses when a Qt window has focus (always-on fallback)."""

    def __init__(self, manager: "KeybindManager", parent=None):
        super().__init__(parent)
        self._mgr = manager

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            modifiers = event.modifiers() & Qt.KeyboardModifierMask
            key = event.key()
            if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
                return False
            for bind_key, (b_mod, b_key) in self._mgr._qt_bindings.items():
                if modifiers == b_mod and key == b_key:
                    self._mgr._fire(bind_key)
                    return True
        return False


# ═══════════════════════════════════════════════════════════════
# MANAGER
# ═══════════════════════════════════════════════════════════════

class KeybindManager(QObject):
    keybind_changed = pyqtSignal(str, str)   # (bind_key, new_sequence_str)

    def __init__(self, overlay_manager=None, parent=None):
        super().__init__(parent)
        self._overlay_manager = overlay_manager
        self._ocr_callback: Optional[Callable] = None
        self._ocr_on: bool = False

        self._sequences:   Dict[str, str]   = {k: "" for k in ALL_BIND_KEYS}
        self._qt_bindings: Dict[str, tuple] = {}

        # Signal bridge — lives on main thread, safe to emit from any thread
        self._signaller = _HotkeySignaller()
        self._signaller.triggered.connect(self._fire)

        # Debounce: track last fire time per bind_key to prevent double-firing
        # when both the global hook and Qt filter would otherwise both trigger.
        self._last_fire: Dict[str, float] = {}
        self._debounce_ms: float = 150.0

        # Global hook (keyboard lib)
        self._hook = _GlobalHook(self._signaller)

        # Qt fallback filter — only installed when keyboard lib is NOT available.
        # If keyboard IS available its global hook handles everything; installing
        # the Qt filter too causes every hotkey to fire twice when the app has focus.
        self._qt_filter = _QtKeyFilter(self)
        if not _KB_AVAILABLE:
            QApplication.instance().installEventFilter(self._qt_filter)

        self._load()

        if _KB_AVAILABLE:
            self._hook.register_all(self._sequences)
            active = sum(1 for v in self._sequences.values() if v)
            logger.info(f"Keybinds: {active} global hotkey(s) registered")
        else:
            logger.warning("Keybinds: install 'keyboard' package for game-focused hotkeys")

    # ── Wiring ───────────────────────────────────────────────────

    def set_overlay_manager(self, overlay_manager):
        self._overlay_manager = overlay_manager

    def set_ocr_toggle_callback(self, fn: Callable):
        self._ocr_callback = fn

    def sync_ocr_state(self, is_on: bool):
        """Call whenever OCR is toggled externally so manager stays in sync."""
        self._ocr_on = is_on

    # ── Persistence ──────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(_KEYBINDS_FILE):
                with open(_KEYBINDS_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                for k in ALL_BIND_KEYS:
                    self._sequences[k] = saved.get(k, "")
        except Exception:
            pass
        self._rebuild_qt_bindings()

    def _save(self):
        try:
            with open(_KEYBINDS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._sequences, f, indent=2)
        except Exception:
            pass

    def _rebuild_qt_bindings(self):
        self._qt_bindings = {}
        for k, seq in self._sequences.items():
            if seq:
                parsed = _parse_sequence(seq)
                if parsed:
                    self._qt_bindings[k] = parsed

    # ── Public API ───────────────────────────────────────────────

    def get_keybind(self, bind_key: str) -> str:
        return self._sequences.get(bind_key, "")

    def set_keybind(self, bind_key: str, sequence_str: str) -> bool:
        if not sequence_str.strip():
            self._sequences[bind_key] = ""
            self._rebuild_qt_bindings()
            if _KB_AVAILABLE:
                self._hook.update(bind_key, "")
            self._save()
            self.keybind_changed.emit(bind_key, "")
            return True
        normalised = _normalise(sequence_str)
        if not normalised:
            return False
        for other_key, other_seq in self._sequences.items():
            if other_key != bind_key and other_seq == normalised:
                return False   # conflict
        self._sequences[bind_key] = normalised
        self._rebuild_qt_bindings()
        if _KB_AVAILABLE:
            self._hook.update(bind_key, normalised)
        self._save()
        self.keybind_changed.emit(bind_key, normalised)
        return True

    def clear_keybind(self, bind_key: str):
        self.set_keybind(bind_key, "")

    def conflicts_with(self, bind_key: str, sequence_str: str) -> Optional[str]:
        normalised = _normalise(sequence_str)
        if not normalised:
            return None
        for other_key, other_seq in self._sequences.items():
            if other_key != bind_key and other_seq == normalised:
                return other_key
        return None

    def cleanup(self):
        """Unregister all global hooks — call on app exit."""
        if _KB_AVAILABLE:
            self._hook.unregister_all()

    # ── Fire (always on main thread via signal) ───────────────────

    def _fire(self, bind_key: str):
        """Always runs on the main thread — safe to touch Qt objects."""
        import time
        now = time.monotonic() * 1000  # ms
        last = self._last_fire.get(bind_key, 0.0)
        if now - last < self._debounce_ms:
            logger.debug(f"Keybinds: _fire({bind_key!r}) suppressed by debounce ({now-last:.0f}ms)")
            return
        self._last_fire[bind_key] = now
        logger.debug(f"Keybinds: _fire({bind_key!r})")
        if bind_key == "ocr":
            self._toggle_ocr()
        elif bind_key in OVERLAY_KEYS:
            self._toggle_overlay(bind_key)

    def _toggle_overlay(self, overlay_key: str):
        """Toggle overlay and sync all UI buttons."""
        if self._overlay_manager is None:
            logger.warning("Keybinds: _toggle_overlay called but overlay_manager is None")
            return
        try:
            from hud_overlays import overlay_settings, _force_topmost
            current  = overlay_settings.is_enabled(overlay_key)
            new_state = not current
            logger.debug(f"Keybinds: toggling {overlay_key} {current} → {new_state}")

            # toggle() internally calls set_enabled(notify=False) + show/hide
            self._overlay_manager.toggle(overlay_key, new_state)
            # Fire UI-sync callbacks (settings page, toolbar buttons, panels)
            overlay_settings.set_enabled(overlay_key, new_state, notify=True)

            if new_state:
                # Re-assert topmost after OS has re-focused the game window
                from PyQt5.QtCore import QTimer
                def _reassert():
                    ov = self._overlay_manager._overlays.get(overlay_key)
                    if ov and ov.isVisible():
                        _force_topmost(ov)
                        logger.debug(f"Keybinds: re-asserted topmost for {overlay_key}")
                QTimer.singleShot(150, _reassert)
                QTimer.singleShot(500, _reassert)
        except Exception as e:
            logger.error(f"Keybinds: _toggle_overlay({overlay_key!r}) error: {e}", exc_info=True)

    def _toggle_ocr(self):
        if self._ocr_callback is None:
            logger.warning("Keybinds: OCR hotkey fired but no callback registered")
            return
        self._ocr_on = not self._ocr_on
        logger.debug(f"Keybinds: toggling OCR → {self._ocr_on}")
        try:
            self._ocr_callback(self._ocr_on)
        except Exception as e:
            logger.error(f"Keybinds: _toggle_ocr error: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════
# KEY CAPTURE WIDGET
# ═══════════════════════════════════════════════════════════════

class KeyCaptureEdit(QLineEdit):
    """QLineEdit that captures the next key press as a portable sequence."""
    sequence_captured = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Click here, then press your key combination…")
        self.setReadOnly(True)
        self._capturing  = False
        self._last_valid = ""
        self.setStyleSheet("""
            QLineEdit {
                background:#1a1a2e; color:#e0e0e0;
                border:2px solid #0f3460; border-radius:5px;
                padding:6px 10px; font-size:13px;
            }
            QLineEdit:focus { border-color:#e94560; background:#120d20; }
        """)

    def setText(self, text):
        self._last_valid = text
        super().setText(text)

    def mousePressEvent(self, event):
        self._capturing = True
        self.setFocus()
        super().setText("⌨  Press your key combination now…")
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        if self._capturing:
            self._capturing = False
            super().setText(self._last_valid)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self._capturing = False
            super().setText(self._last_valid)
            return
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return
        modifiers  = event.modifiers() & Qt.KeyboardModifierMask
        combined   = int(modifiers) | key
        ks         = QKeySequence(combined)
        seq_str    = ks.toString(QKeySequence.PortableText) or ks.toString()
        normalised = _normalise(seq_str)
        self._capturing = False
        if normalised:
            self._last_valid = normalised
            super().setText(normalised)
            self.sequence_captured.emit(normalised)
        else:
            super().setText(self._last_valid)


# ═══════════════════════════════════════════════════════════════
# SETTINGS WIDGET
# ═══════════════════════════════════════════════════════════════

_CARD_STYLE = "QFrame#kbCard{background:#1a1a2e;border:1px solid #0f3460;border-radius:10px;}"


class KeybindSettingsWidget(QWidget):
    """Settings-page widget — one capture row per binding."""

    def __init__(self, keybind_manager: KeybindManager, parent=None):
        super().__init__(parent)
        self._mgr = keybind_manager
        self._captures:    Dict[str, KeyCaptureEdit] = {}
        self._status_lbls: Dict[str, QLabel]         = {}
        self._build()
        keybind_manager.keybind_changed.connect(self._on_changed)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if _KB_AVAILABLE:
            status_lbl = QLabel("🌐 Global hotkeys active — work even when Wizard101 is focused")
            status_lbl.setStyleSheet(
                "color:#66ff99;font-size:11px;background:transparent;font-weight:bold;"
            )
        else:
            status_lbl = QLabel(
                "⚠ Global hotkeys unavailable.\n"
                "Run:  pip install keyboard\n"
                "Hotkeys currently only work when this app is focused."
            )
            status_lbl.setStyleSheet("color:#ffd93d;font-size:11px;background:transparent;")
        status_lbl.setWordWrap(True)
        layout.addWidget(status_lbl)

        hint = QLabel(
            "Click a capture field, then press your key or combination "
            "(e.g. F1, Ctrl+1, Alt+B).  Each binding must be unique.  Esc to cancel."
        )
        hint.setStyleSheet("color:#666;font-size:11px;background:transparent;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        for bind_key in ALL_BIND_KEYS:
            layout.addWidget(self._make_card(bind_key))

    def _make_card(self, bind_key: str) -> QFrame:
        color   = BIND_COLORS[bind_key]
        label   = BIND_LABELS[bind_key]
        current = self._mgr.get_keybind(bind_key)

        card = QFrame()
        card.setObjectName("kbCard")
        card.setStyleSheet(_CARD_STYLE)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 12)
        cl.setSpacing(6)

        hdr = QHBoxLayout()
        title_lbl = QLabel(label)
        title_lbl.setStyleSheet(
            f"color:{color};font-size:13px;font-weight:bold;background:transparent;"
        )
        hdr.addWidget(title_lbl)
        hdr.addStretch()
        status_lbl = QLabel(f"Keybind: {current or '(none)'}")
        status_lbl.setStyleSheet("color:#888;font-size:11px;background:transparent;")
        hdr.addWidget(status_lbl)
        self._status_lbls[bind_key] = status_lbl
        cl.addLayout(hdr)

        row = QHBoxLayout()
        capture = KeyCaptureEdit()
        capture.setText(current)
        capture.sequence_captured.connect(
            lambda seq, k=bind_key: self._on_captured(k, seq)
        )
        self._captures[bind_key] = capture
        row.addWidget(capture, stretch=1)

        clear_btn = QPushButton("✕ Clear")
        clear_btn.setStyleSheet(
            "QPushButton{background:#3a1a1a;color:#e94560;border:none;"
            "border-radius:5px;padding:5px 10px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#e94560;color:#fff;}"
        )
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(lambda checked=False, k=bind_key: self._clear(k))
        row.addWidget(clear_btn)
        cl.addLayout(row)
        return card

    def _on_captured(self, bind_key: str, seq: str):
        conflict = self._mgr.conflicts_with(bind_key, seq)
        if conflict:
            QMessageBox.warning(
                self, "Keybind conflict",
                f"<b>{seq}</b> is already used by "
                f"<b>{BIND_LABELS.get(conflict, conflict)}</b>.\n"
                "Please choose a different key."
            )
            self._captures[bind_key].setText(self._mgr.get_keybind(bind_key))
            return
        if not self._mgr.set_keybind(bind_key, seq):
            QMessageBox.warning(self, "Invalid key", f"Could not parse '{seq}'.")
            self._captures[bind_key].setText(self._mgr.get_keybind(bind_key))

    def _clear(self, bind_key: str):
        self._mgr.clear_keybind(bind_key)
        self._captures[bind_key].setText("")

    def _on_changed(self, bind_key: str, new_seq: str):
        if bind_key in self._status_lbls:
            self._status_lbls[bind_key].setText(f"Keybind: {new_seq or '(none)'}")
        if bind_key in self._captures:
            if self._captures[bind_key].text() != new_seq:
                self._captures[bind_key].setText(new_seq)
