"""
spell_deck_integration.py
══════════════════════════
Idempotent integration patcher.

Wires:
  1. boss_wiki.py  — imports, hub cards, stack pages (6 & 7), _nav_to entries
  2. damage_calc.py — CharacterManagerWidget: Linked Decks + Linked Gear sections

Run:
    python spell_deck_integration.py

Output:
    [OK]   <description>
    [SKIP] <description>   (already present — safe to run again)
    [FAIL] <description> — <error>

Creates .bak backup files on first patch.
"""

import os
import re
import shutil
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))

def _path(name):
    return os.path.join(APP_DIR, name)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _backup(path):
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)


def _patch(path, anchor, insertion, description, mode="after"):
    """
    Insert `insertion` directly before or after the first occurrence of `anchor`.
    mode: 'after'  → insert after anchor
          'before' → insert before anchor
          'replace'→ replace anchor with insertion
    """
    content = _read(path)
    if insertion.strip() in content:
        print(f"  [SKIP] {description}")
        return True
    if anchor not in content:
        print(f"  [FAIL] {description} — anchor not found: {anchor[:60]!r}")
        return False
    _backup(path)
    if mode == "after":
        content = content.replace(anchor, anchor + insertion, 1)
    elif mode == "before":
        content = content.replace(anchor, insertion + anchor, 1)
    elif mode == "replace":
        content = content.replace(anchor, insertion, 1)
    _write(path, content)
    print(f"  [OK]   {description}")
    return True


# ═══════════════════════════════════════════════════════════════════════
# 1.  boss_wiki.py patches
# ═══════════════════════════════════════════════════════════════════════

def patch_boss_wiki():
    path = _path("boss_wiki.py")
    if not os.path.exists(path):
        print(f"  [FAIL] boss_wiki.py not found at {path}")
        return False

    ok = True

    # ── 1a. Import blocks ──────────────────────────────────────────────
    ok &= _patch(
        path,
        anchor="# Damage Calculator + Characters\ntry:",
        insertion="""# Spell Browser
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

""",
        description="boss_wiki.py — Spell Browser + Deck Builder imports",
        mode="before",
    )

    # ── 1b. Init DB tables in __init__ ────────────────────────────────
    ok &= _patch(
        path,
        anchor="        if DAMAGE_CALC_AVAILABLE and dccalc:\n            dccalc.init_calc_tables(self.conn)",
        insertion="""
        # Spell + Deck tables
        if SPELL_BROWSER_AVAILABLE or DECK_BUILDER_AVAILABLE:
            try:
                import database_spells as _dspells
                _dspells.init_spell_tables(self.conn)
            except Exception as _e:
                print(f"  [WARN] Could not init spell tables: {_e}")""",
        description="boss_wiki.py — init_spell_tables in __init__",
        mode="after",
    )

    # ── 1c. Hub cards ─────────────────────────────────────────────────
    ok &= _patch(
        path,
        anchor='            {\n                "icon": "⚙",\n                "title": "HUD & Settings",',
        insertion="""            {
                "icon": "✨",
                "title": "Spell Browser",
                "desc": "Browse, search and fetch spell cards by school with OCR data",
                "title_color": "#c39bd3",
                "action": lambda: self._nav_to("spells"),
            },
            {
                "icon": "🃏",
                "title": "Deck Builder",
                "desc": "Build, save and share deck presets linked to your wizards",
                "title_color": "#4d96ff",
                "action": lambda: self._nav_to("decks"),
            },
""",
        description="boss_wiki.py — Spell Browser + Deck Builder hub cards",
        mode="before",
    )

    # ── 1d. Stack pages ───────────────────────────────────────────────
    ok &= _patch(
        path,
        anchor="        # Page 5: Settings / HUD\n        self._settings_page = self._build_settings_page()",
        insertion="""        # Page 6: Spell Browser
        if SPELL_BROWSER_AVAILABLE and SpellBrowserWidget:
            self._spell_browser_page = SpellBrowserWidget(self.conn, self)
            self._spell_browser_page.nav_hub.connect(lambda: self._nav_to("hub"))
            self.stack.addWidget(self._spell_browser_page)
        else:
            self._spell_browser_page = None
            _ph_sp = QLabel("Spell Browser not available.\\nEnsure spell_browser.py is present.")
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
            _ph_dk = QLabel("Deck Builder not available.\\nEnsure deck_builder.py is present.")
            _ph_dk.setAlignment(Qt.AlignCenter)
            _ph_dk.setStyleSheet("color:#555;font-size:14px;background:#1a1a2e;")
            self.stack.addWidget(_ph_dk)

""",
        description="boss_wiki.py — Stack pages 6 (spells) & 7 (decks)",
        mode="before",
    )

    # ── 1e. _nav_to entries ───────────────────────────────────────────
    ok &= _patch(
        path,
        anchor='        PAGE = {"hub": 0, "boss_wiki": 1, "gear_guide": 2,\n                "damage_calc": 3, "characters": 4, "settings": 5}',
        insertion='        PAGE = {"hub": 0, "boss_wiki": 1, "gear_guide": 2,\n                "damage_calc": 3, "characters": 4, "settings": 5,\n                "spells": 6, "decks": 7}',
        description="boss_wiki.py — _nav_to: add spells=6, decks=7",
        mode="replace",
    )

    # ── 1f. Refresh deck builder when navigating to it ────────────────
    ok &= _patch(
        path,
        anchor='        if section == "characters" and getattr(self, "_char_page", None):\n            self._char_page._refresh_list()',
        insertion="""
        if section == "spells" and getattr(self, "_spell_browser_page", None):
            self._spell_browser_page.refresh()
        if section == "decks" and getattr(self, "_deck_builder_page", None):
            self._deck_builder_page.refresh()""",
        description="boss_wiki.py — _nav_to: refresh spell/deck pages on navigate",
        mode="after",
    )

    return ok


# ═══════════════════════════════════════════════════════════════════════
# 2.  damage_calc.py patches — CharacterManagerWidget linking sections
# ═══════════════════════════════════════════════════════════════════════

def patch_damage_calc():
    path = _path("damage_calc.py")
    if not os.path.exists(path):
        print(f"  [FAIL] damage_calc.py not found at {path}")
        return False

    ok = True

    # ── 2a. Import database_spells at top (lazy, try/except) ──────────
    ok &= _patch(
        path,
        anchor="import database_calc as dc",
        insertion="""
# Optional: spell/deck linking for character profiles
try:
    import database_spells as _ds
    import database_gear as _dg
    _CHAR_LINKING = True
except ImportError:
    _CHAR_LINKING = False
    _ds = None
    _dg = None

""",
        description="damage_calc.py — import database_spells/database_gear for char linking",
        mode="after",
    )

    # ── 2b. Replace _open_editor to append linking sections ───────────
    # We inject a helper call right before `self.stack.addWidget(page)` in _open_editor
    ok &= _patch(
        path,
        anchor="        il.addWidget(editor)\n        scroll.setWidget(inner)\n        v.addWidget(scroll, stretch=1)\n\n        self.stack.addWidget(page)",
        insertion="""        # Linked Decks + Gear sections
        if _CHAR_LINKING and wid is not None:
            _lnk = _build_character_links_widget(self.conn, wid)
            il.addWidget(_lnk)
        il.addStretch()
""",
        description="damage_calc.py — inject character linking section into editor",
        mode="before",
    )

    # ── 2c. Add the helper function before class CharacterManagerWidget ──
    HELPER_CODE = '''

# ─── CHARACTER LINKING HELPER (Decks + Gear) ─────────────────────────

def _build_character_links_widget(conn, wizard_id):
    """
    Returns a QWidget showing linked Decks and Gear Loadouts for a wizard,
    with Add-link / Unlink controls.
    Uses database_spells (_ds) and database_gear (_dg) via lazy import.
    """
    import database_spells as _ds_local
    try:
        import database_gear as _dg_local
    except ImportError:
        _dg_local = None

    outer = QWidget()
    outer.setStyleSheet("background:transparent;")
    ov = QVBoxLayout(outer)
    ov.setContentsMargins(0, 8, 0, 0)
    ov.setSpacing(10)

    def _section_header(text, color):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{color};font-size:10px;font-weight:bold;"
            "letter-spacing:1px;background:transparent;"
        )
        return lbl

    # ── Linked Decks ──────────────────────────────────────────────────
    ov.addWidget(_section_header("🃏 LINKED DECKS", "#4d96ff"))

    decks_frame = QWidget()
    decks_frame.setStyleSheet(
        "background:#0d1b2a;border:1px solid #1f3460;border-radius:5px;"
    )
    decks_v = QVBoxLayout(decks_frame)
    decks_v.setContentsMargins(6, 6, 6, 6)
    decks_v.setSpacing(3)

    linked_decks = _ds_local.get_decks_for_wizard(conn, wizard_id)

    def _refresh_decks():
        while decks_v.count() > 0:
            it = decks_v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        ld = _ds_local.get_decks_for_wizard(conn, wizard_id)
        if not ld:
            lbl = QLabel("No decks linked yet.")
            lbl.setStyleSheet("color:#555;font-size:11px;background:transparent;")
            decks_v.addWidget(lbl)
        for dk in ld:
            row = QHBoxLayout()
            name_lbl = QLabel(f"  {dk['name']}  ({dk['school']})")
            name_lbl.setStyleSheet("color:#d0d0d0;font-size:11px;background:transparent;")
            row.addWidget(name_lbl, stretch=1)
            unlink_btn = QPushButton("Unlink")
            unlink_btn.setFixedWidth(54)
            unlink_btn.setStyleSheet(
                "QPushButton{background:#5c1b1b;color:#e0e0e0;border:none;"
                "border-radius:3px;padding:2px 6px;font-size:10px;}"
                "QPushButton:hover{background:#e94560;}"
            )
            _did = dk["id"]
            unlink_btn.clicked.connect(
                lambda _, did=_did: (
                    _ds_local.unlink_deck_from_wizard(conn, wizard_id, did),
                    _refresh_decks(),
                )
            )
            row.addWidget(unlink_btn)
            container = QWidget()
            container.setStyleSheet("background:transparent;")
            container.setLayout(row)
            decks_v.addWidget(container)
        add_deck_btn = QPushButton("＋ Link a Deck")
        add_deck_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#4d96ff;"
            "border:1px dashed #1f3a6e;border-radius:4px;padding:4px;font-size:10px;}"
            "QPushButton:hover{background:#1a3060;}"
        )
        add_deck_btn.clicked.connect(_on_link_deck)
        decks_v.addWidget(add_deck_btn)

    def _on_link_deck():
        all_decks = _ds_local.list_decks(conn)
        if not all_decks:
            QMessageBox.information(
                None, "No Decks",
                "No decks exist yet. Create one in the Deck Builder first."
            )
            return
        names = [f"{d['name']} ({d['school']})" for d in all_decks]
        choice, ok = QInputDialog.getItem(
            None, "Link Deck", "Select a deck to link:", names, 0, False
        )
        if ok and choice:
            idx = names.index(choice)
            _ds_local.link_deck_to_wizard(conn, wizard_id, all_decks[idx]["id"])
            _refresh_decks()

    _refresh_decks()
    ov.addWidget(decks_frame)

    # ── Linked Gear Loadouts ──────────────────────────────────────────
    ov.addWidget(_section_header("🎒 LINKED GEAR SETS", "#4db8ff"))

    gear_frame = QWidget()
    gear_frame.setStyleSheet(
        "background:#0d1b2a;border:1px solid #1f3460;border-radius:5px;"
    )
    gear_v = QVBoxLayout(gear_frame)
    gear_v.setContentsMargins(6, 6, 6, 6)
    gear_v.setSpacing(3)

    def _refresh_gear():
        while gear_v.count() > 0:
            it = gear_v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        lg = _ds_local.get_gear_for_wizard(conn, wizard_id)
        if not lg:
            lbl = QLabel("No gear sets linked yet.")
            lbl.setStyleSheet("color:#555;font-size:11px;background:transparent;")
            gear_v.addWidget(lbl)
        for g in lg:
            row = QHBoxLayout()
            name_lbl = QLabel(f"  {g['name']}")
            name_lbl.setStyleSheet("color:#d0d0d0;font-size:11px;background:transparent;")
            row.addWidget(name_lbl, stretch=1)
            unlink_btn = QPushButton("Unlink")
            unlink_btn.setFixedWidth(54)
            unlink_btn.setStyleSheet(
                "QPushButton{background:#5c1b1b;color:#e0e0e0;border:none;"
                "border-radius:3px;padding:2px 6px;font-size:10px;}"
                "QPushButton:hover{background:#e94560;}"
            )
            _gid = g["id"]
            unlink_btn.clicked.connect(
                lambda _, gid=_gid: (
                    _ds_local.unlink_gear_from_wizard(conn, wizard_id, gid),
                    _refresh_gear(),
                )
            )
            row.addWidget(unlink_btn)
            container = QWidget()
            container.setStyleSheet("background:transparent;")
            container.setLayout(row)
            gear_v.addWidget(container)
        add_gear_btn = QPushButton("＋ Link a Gear Set")
        add_gear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#4db8ff;"
            "border:1px dashed #1f3a6e;border-radius:4px;padding:4px;font-size:10px;}"
            "QPushButton:hover{background:#1a3060;}"
        )
        add_gear_btn.clicked.connect(_on_link_gear)
        gear_v.addWidget(add_gear_btn)

    def _on_link_gear():
        if _dg_local is None:
            QMessageBox.information(None, "Unavailable", "database_gear.py not found.")
            return
        loadouts = _dg_local.list_loadouts(conn)
        if not loadouts:
            QMessageBox.information(
                None, "No Gear Sets",
                "No gear loadouts exist. Create one in Gear Guide first."
            )
            return
        names = [g["name"] for g in loadouts]
        choice, ok = QInputDialog.getItem(
            None, "Link Gear Set", "Select a gear set to link:", names, 0, False
        )
        if ok and choice:
            idx = names.index(choice)
            _ds_local.link_gear_to_wizard(conn, wizard_id, loadouts[idx]["id"])
            _refresh_gear()

    _refresh_gear()
    ov.addWidget(gear_frame)
    ov.addStretch()
    return outer

'''

    ok &= _patch(
        path,
        anchor="class CharacterManagerWidget(QWidget):",
        insertion=HELPER_CODE,
        description="damage_calc.py — _build_character_links_widget helper function",
        mode="before",
    )

    return ok


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Spell + Deck Integration Patcher")
    print("=" * 60)

    print("\n── boss_wiki.py ──────────────────────────────────────")
    bw_ok = patch_boss_wiki()

    print("\n── damage_calc.py ────────────────────────────────────")
    dc_ok = patch_damage_calc()

    print("\n" + "=" * 60)
    if bw_ok and dc_ok:
        print("  All patches applied successfully.")
        print("  Run the app normally: python boss_wiki.py")
    else:
        print("  Some patches failed — check output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
