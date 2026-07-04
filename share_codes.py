"""
share_codes.py  —  Wizard101 Companion
════════════════════════════════════════
Tiny shared UI helpers for base64 "share code" workflows used by the
Gear Guide, Strategy Guides and Round Counters (mirrors the Deck Builder).

  show_share_dialog(parent, title, code)   → read-only copyable code popup
  prompt_import_code(parent, title)        → returns the pasted code or None
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QApplication, QInputDialog, QCheckBox, QGridLayout,
    QFrame, QMessageBox,
)


def show_share_dialog(parent, title: str, code: str,
                      subtitle: str = "Share this code with other players:"):
    """Show a read-only code field with a copy-to-clipboard button."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
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
    dv.addWidget(QLabel(subtitle))
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


def prompt_import_code(parent, title: str,
                       prompt: str = "Paste the base64 share code here:"):
    """Prompt for a share code. Returns the trimmed code, or None if cancelled/empty."""
    code, ok = QInputDialog.getText(parent, title, prompt)
    if not ok or not code.strip():
        return None
    return code.strip()


class CategorySelectDialog(QDialog):
    """
    Reusable checkbox picker for choosing data categories, used by both the
    Backup (choose what to save) and Import (choose what to restore) flows.

    categories : list of (key, label, count)
    Returns the chosen set of keys in .selected (None if cancelled).
    """

    def __init__(self, title: str, subtitle: str, categories,
                 action_label: str = "OK", note: str = "",
                 meta_line: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 0)
        self._checks = {}
        self.selected = None
        self.setStyleSheet(
            "QDialog,QWidget{background:#1a1a2e;color:#e0e0e0;}"
            "QCheckBox{font-size:12px;padding:2px;}"
            "QCheckBox:disabled{color:#555;}"
            "QPushButton{background:#0f3460;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 14px;font-size:12px;}"
            "QPushButton:hover{background:#4d96ff;}"
        )

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        head = QLabel(subtitle)
        head.setStyleSheet("font-size:13px;font-weight:bold;color:#e94560;")
        head.setWordWrap(True)
        v.addWidget(head)

        if meta_line:
            info = QLabel(meta_line)
            info.setStyleSheet("color:#999;font-size:11px;")
            v.addWidget(info)

        if note:
            n = QLabel(note)
            n.setStyleSheet("color:#666;font-size:11px;")
            n.setWordWrap(True)
            v.addWidget(n)

        div = QFrame(); div.setFrameShape(QFrame.HLine)
        div.setStyleSheet("color:#0f3460;background:#0f3460;max-height:1px;")
        v.addWidget(div)

        grid = QGridLayout(); grid.setSpacing(6)
        any_present = False
        for row, (key, label, count) in enumerate(categories):
            n = int(count or 0)
            cb = QCheckBox(f"{label}  ·  {n}")
            cb.setChecked(n > 0)
            cb.setEnabled(n > 0)
            if n > 0:
                any_present = True
            self._checks[key] = cb
            grid.addWidget(cb, row // 2, row % 2)
        v.addLayout(grid)

        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select All")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Select None")
        none_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        sel_row.addStretch()
        v.addLayout(sel_row)

        div2 = QFrame(); div2.setFrameShape(QFrame.HLine)
        div2.setStyleSheet("color:#0f3460;background:#0f3460;max-height:1px;")
        v.addWidget(div2)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        go = QPushButton(action_label)
        go.setStyleSheet(
            "QPushButton{background:#1b5c38;color:#e0e0e0;border:none;"
            "border-radius:5px;padding:6px 16px;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#27ae60;}"
        )
        go.setEnabled(any_present)
        go.clicked.connect(self._accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(go)
        v.addLayout(btn_row)

    def _set_all(self, state: bool):
        for cb in self._checks.values():
            if cb.isEnabled():
                cb.setChecked(state)

    def _accept(self):
        self.selected = {k for k, cb in self._checks.items()
                         if cb.isEnabled() and cb.isChecked()}
        if not self.selected:
            QMessageBox.warning(self, "Nothing selected",
                                "Tick at least one category.")
            return
        self.accept()
