"""
spell_export_settings_integration.py
══════════════════════════════════════
Idempotent patcher — adds Spell and Deck export buttons to the
Export & Import section of the boss_wiki.py Settings page.

Run:
    python spell_export_settings_integration.py
"""

import os, sys, shutil

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _backup(path):
    bak = path + ".bak3"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f"  Backup -> {os.path.basename(bak)}")


def _patch(path, anchor, insertion, desc, mode="after"):
    content = _read(path)
    if insertion.strip() in content:
        print(f"  [SKIP] {desc}")
        return True
    if anchor not in content:
        print(f"  [FAIL] {desc} -- anchor not found")
        return False
    _backup(path)
    if mode == "after":
        content = content.replace(anchor, anchor + insertion, 1)
    elif mode == "before":
        content = content.replace(anchor, insertion + anchor, 1)
    elif mode == "replace":
        content = content.replace(anchor, insertion, 1)
    _write(path, content)
    print(f"  [OK]   {desc}")
    return True


# The loop that renders regular_buttons into the exp_grid
ANCHOR = ('            for i, (lbl, cb) in enumerate(regular_buttons):\n'
          '                btn = _exp_btn(lbl, "", cb, False)\n'
          '                exp_grid.addWidget(btn, i // 3, i % 3)')

# Insertion: append spell + deck entries to the list just before that loop
INSERTION = """
            # Spell + Deck exports (spell_export_settings_integration.py)
            if hasattr(exp, "export_all_spells"):
                regular_buttons.append(
                    ("📤 All Spells", lambda: exp.export_all_spells(self.conn, self)))
            if hasattr(exp, "export_all_decks"):
                regular_buttons.append(
                    ("📤 All Decks", lambda: exp.export_all_decks(self.conn, self)))
"""


def main():
    path = os.path.join(APP_DIR, "boss_wiki.py")
    if not os.path.exists(path):
        print(f"[FAIL] boss_wiki.py not found at {path}")
        sys.exit(1)

    print("=" * 60)
    print("  Spell Export Settings Integration Patcher")
    print("=" * 60)
    print()

    ok = _patch(
        path, ANCHOR, INSERTION,
        "boss_wiki.py -- All Spells + All Decks export buttons in Settings",
        mode="before",
    )

    print()
    if ok:
        print("  Done -- restart the app to see the new export buttons in Settings.")
    else:
        # Debug: show what's actually near that section
        content = _read(path)
        idx = content.find("enumerate(regular_buttons)")
        if idx >= 0:
            snippet = content[max(0, idx - 300):idx + 200]
            print("  Found enumerate(regular_buttons) -- nearby context:")
            for ln in snippet.splitlines():
                print("   |", ln)
        else:
            print("  Could not find 'enumerate(regular_buttons)' in boss_wiki.py at all.")
        sys.exit(1)


if __name__ == "__main__":
    main()
