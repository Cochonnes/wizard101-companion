"""
importer.py  —  Wizard101 Companion  —  JSON Import Engine
════════════════════════════════════════════════════════════
Reads any JSON file previously exported by exporter.py and
routes it to the correct import handler based on export_type.

Supported export_type values
─────────────────────────────
  boss_single            → upsert one boss
  bosses_world           → upsert all bosses in a world
  bosses_area            → upsert all bosses in an area
  bosses_all             → upsert all bosses
  round_counter_single   → upsert one round counter
  round_counters_all     → upsert all round counters
  strategy_guide_single  → upsert one strategy guide
  strategy_guides_all    → upsert all strategy guides
  gear_loadout_single    → upsert one gear loadout
  gear_loadouts_all      → upsert all gear loadouts
  quest_world_single     → upsert one quest world + its quests
  quest_worlds_all       → upsert all quest worlds
  full_export            → import everything

Public API
──────────
  import_file(conn, parent_widget=None)
"""

import json
import time
from typing import Optional

from PyQt5.QtWidgets import (
    QFileDialog, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QCheckBox, QPushButton, QGridLayout, QFrame,
)
from PyQt5.QtCore import Qt


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def import_file(conn, parent=None) -> bool:
    """Open a file dialog, detect the export type, and import."""
    path, _ = QFileDialog.getOpenFileName(
        parent,
        "Import — Choose exported JSON file",
        "",
        "JSON Files (*.json);;All Files (*)",
    )
    if not path:
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        QMessageBox.critical(parent, "Import failed", f"Could not read file:\n{e}")
        return False

    if not isinstance(payload, dict) or payload.get("app") != "Wizard101 Companion":
        QMessageBox.warning(
            parent, "Import failed",
            "This does not look like a Wizard101 Companion export file.\n"
            "Only files created by the built-in export function are supported."
        )
        return False

    export_type = payload.get("export_type", "")
    data        = payload.get("data", {})

    try:
        count = _dispatch(conn, export_type, data)
    except Exception as e:
        QMessageBox.critical(parent, "Import error", str(e))
        return False

    QMessageBox.information(
        parent, "Import complete",
        f"Successfully imported <b>{count}</b> item(s) from:\n{path}"
    )
    return True


# ═══════════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════════

def _dispatch(conn, export_type: str, data) -> int:
    """Route to the correct import function. Returns count of items imported."""

    # ── Bosses ──────────────────────────────────────────────────
    if export_type == "boss_single":
        return _import_boss(conn, data)

    if export_type in ("bosses_world", "bosses_area", "bosses_all"):
        bosses = data.get("bosses", []) if isinstance(data, dict) else data
        n = 0
        for b in bosses:
            n += _import_boss(conn, b)
        return n

    # ── Round Counters ──────────────────────────────────────────
    if export_type == "round_counter_single":
        return _import_counter(conn, data)

    if export_type == "round_counters_all":
        counters = data.get("counters", []) if isinstance(data, dict) else data
        n = 0
        for c in counters:
            n += _import_counter(conn, c)
        return n

    # ── Strategy Guides ─────────────────────────────────────────
    if export_type == "strategy_guide_single":
        return _import_guide(conn, data)

    if export_type == "strategy_guides_all":
        guides = data.get("guides", []) if isinstance(data, dict) else data
        n = 0
        for g in guides:
            n += _import_guide(conn, g)
        return n

    # ── Gear Loadouts ───────────────────────────────────────────
    if export_type == "gear_loadout_single":
        return _import_loadout(conn, data)

    if export_type == "gear_loadouts_all":
        loadouts = data.get("loadouts", []) if isinstance(data, dict) else data
        n = 0
        for lo in loadouts:
            n += _import_loadout(conn, lo)
        return n

    # ── Quest Worlds ────────────────────────────────────────────
    if export_type == "quest_world_single":
        return _import_quest_world(conn, data)

    if export_type == "quest_worlds_all":
        worlds = data.get("worlds", []) if isinstance(data, dict) else data
        n = 0
        for w in worlds:
            n += _import_quest_world(conn, w)
        return n

    # ── Damage Calculator presets ───────────────────────────────
    if export_type == "calc_presets_all":
        presets = data.get("presets", []) if isinstance(data, dict) else data
        n = 0
        for p in presets:
            n += _import_calc_preset(conn, p)
        return n

    # ── Characters / wizards ─────────────────────────────────────
    if export_type == "characters_all":
        wizards = data.get("wizards", []) if isinstance(data, dict) else data
        n = 0
        for w in wizards:
            n += _import_wizard(conn, w)
        return n

    # ── Full Export ─────────────────────────────────────────────
    if export_type == "full_export":
        total = 0
        for b in data.get("bosses", []):
            total += _import_boss(conn, b)
        for c in data.get("round_counters", []):
            total += _import_counter(conn, c)
        for g in data.get("strategy_guides", []):
            total += _import_guide(conn, g)
        for lo in data.get("gear_loadouts", []):
            total += _import_loadout(conn, lo)
        for w in data.get("quest_worlds", []):
            total += _import_quest_world(conn, w)
        for p in data.get("calc_presets", []):
            total += _import_calc_preset(conn, p)
        for wz in data.get("characters", []):
            total += _import_wizard(conn, wz)
        return total

    raise ValueError(
        f"Unknown export_type: '{export_type}'\n"
        "This file may have been created by a different version of the app."
    )


# ═══════════════════════════════════════════════════════════════
# INDIVIDUAL IMPORTERS
# ═══════════════════════════════════════════════════════════════

def _import_boss(conn, data: dict) -> int:
    """Upsert a single boss record."""
    if not data or not data.get("name"):
        return 0

    now = time.time()

    # Re-serialise sub-fields that are stored as JSON strings in the DB
    def _js(val, default):
        if val is None:
            return json.dumps(default)
        if isinstance(val, str):
            return val   # already serialised
        return json.dumps(val, ensure_ascii=False)

    cheats_json      = _js(data.get("cheats"),       [])
    battle_stats     = _js(data.get("battle_stats"), {})
    spells_json      = _js(data.get("spells"),       [])
    drops_json       = _js(data.get("drops"),        [])
    minions_json     = _js(data.get("minions"),      [])
    resistances_json = _js(data.get("resistances"),  {})

    existing = conn.execute(
        "SELECT id FROM bosses WHERE name = ? COLLATE NOCASE", (data["name"],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE bosses SET
                wiki_path=?, url=?,
                health=?, rank=?, school=?, location=?, description=?,
                cheats_json=?, battle_stats_json=?, spells_json=?,
                drops_json=?, minions_json=?, resistances_json=?,
                last_updated_at=?, is_active=1
            WHERE id=?
        """, (
            data.get("wiki_path", ""), data.get("wiki_url", data.get("url", "")),
            data.get("health", "Unknown"), data.get("rank", "Unknown"),
            data.get("school", "Unknown"), data.get("location", "Unknown"),
            data.get("description", ""),
            cheats_json, battle_stats, spells_json,
            drops_json, minions_json, resistances_json,
            now, existing["id"]
        ))
    else:
        conn.execute("""
            INSERT INTO bosses
                (name, wiki_path, url, health, rank, school, location, description,
                 cheats_json, battle_stats_json, spells_json, drops_json,
                 minions_json, resistances_json, first_scraped_at, last_updated_at, is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (
            data["name"],
            data.get("wiki_path", ""), data.get("wiki_url", data.get("url", "")),
            data.get("health", "Unknown"), data.get("rank", "Unknown"),
            data.get("school", "Unknown"), data.get("location", "Unknown"),
            data.get("description", ""),
            cheats_json, battle_stats, spells_json,
            drops_json, minions_json, resistances_json,
            now, now,
        ))
    conn.commit()
    return 1


def _import_counter(conn, data: dict) -> int:
    """Upsert a round counter. Ignores the exported id to avoid collisions."""
    if not data or not data.get("name"):
        return 0

    # Normalise linked_bosses: export stores list of {name, wiki_url}
    linked = []
    for item in data.get("linked_bosses", []):
        if isinstance(item, dict):
            linked.append(item.get("name", ""))
        elif isinstance(item, str):
            linked.append(item)
    linked = [b for b in linked if b]

    import database as db
    # Check if a counter with this name already exists → update it
    existing = conn.execute(
        "SELECT id FROM round_counters WHERE name = ? COLLATE NOCASE",
        (data["name"],)
    ).fetchone()

    record = {
        "name":          data["name"],
        "description":   data.get("description", ""),
        "rounds":        data.get("rounds", []),
        "linked_bosses": linked,
    }
    if existing:
        record["id"] = existing["id"]

    db.upsert_round_counter(conn, record)
    return 1


def _import_guide(conn, data: dict) -> int:
    """Upsert a strategy guide."""
    if not data or not data.get("name"):
        return 0

    linked = []
    for item in data.get("linked_bosses", []):
        if isinstance(item, dict):
            linked.append(item.get("name", ""))
        elif isinstance(item, str):
            linked.append(item)
    linked = [b for b in linked if b]

    import database as db
    existing = conn.execute(
        "SELECT id FROM guides WHERE name = ? COLLATE NOCASE",
        (data["name"],)
    ).fetchone()

    record = {
        "name":          data["name"],
        "free_text":     data.get("free_text", ""),
        "schools":       data.get("schools", ["Fire", "Ice", "Storm", "Myth"]),
        "table_data":    data.get("table_data", {}),
        "num_rounds":    data.get("num_rounds", 3),
        "linked_bosses": linked,
    }
    if existing:
        record["id"] = existing["id"]

    db.upsert_guide(conn, record)
    return 1


def _import_loadout(conn, data: dict) -> int:
    """Upsert a gear loadout."""
    if not data or not data.get("name"):
        return 0

    import database_gear as dg
    existing = conn.execute(
        "SELECT id FROM gear_loadouts WHERE name = ? COLLATE NOCASE",
        (data["name"],)
    ).fetchone()

    record = {
        "name":      data["name"],
        "school":    data.get("school", "Universal"),
        "level_min": data.get("level_min", 1),
        "level_max": data.get("level_max", 170),
        "world":     data.get("world", ""),
        "category":  data.get("category", ""),
        "notes":     data.get("notes", ""),
        "slots":     data.get("slots", []),
        "pet_stats": data.get("pet_stats", []),
    }
    if existing:
        record["id"] = existing["id"]

    dg.upsert_loadout(conn, record)
    return 1


def _import_quest_world(conn, data: dict) -> int:
    """Upsert a quest world with all its areas and quests."""
    if not data or not data.get("name"):
        return 0

    import database_quests as dq
    now = time.time()

    # Upsert world row
    world_base = {
        "name":            data["name"],
        "source_url":      data.get("source_url", ""),
        "total_quests":    data.get("total_quests"),
        "mob_fights":      data.get("mob_fights"),
        "dc_quests":       data.get("dc_quests"),
        "boss_fights":     data.get("boss_fights"),
        "cheater_bosses":  data.get("cheater_bosses"),
        "solo_quests":     data.get("solo_quests"),
        "description":     data.get("description", ""),
        "intro_text":      data.get("intro_text", ""),
        "display_order":   data.get("display_order", 999),
    }
    world_id = dq.upsert_world(conn, world_base)

    total = 1

    def _upsert_quest(qdata: dict, area_id: Optional[int]):
        types_json = json.dumps(qdata.get("types", []), ensure_ascii=False)
        existing_q = conn.execute(
            "SELECT id FROM quests WHERE world_id=? AND name=? COLLATE NOCASE AND "
            "(area_id IS ? OR area_id=?)",
            (world_id, qdata.get("name", ""), area_id, area_id)
        ).fetchone()
        if existing_q:
            conn.execute("""
                UPDATE quests SET quest_number=?, types_json=?, sort_order=? WHERE id=?
            """, (qdata.get("quest_number"), types_json,
                  qdata.get("sort_order", 0), existing_q["id"]))
            qid = existing_q["id"]
        else:
            cur = conn.execute("""
                INSERT INTO quests (world_id, area_id, quest_number, name, types_json, sort_order)
                VALUES (?,?,?,?,?,?)
            """, (world_id, area_id, qdata.get("quest_number"),
                  qdata.get("name", "Unnamed Quest"), types_json,
                  qdata.get("sort_order", 0)))
            qid = cur.lastrowid
        # Restore marker if present
        marker = qdata.get("marker")
        if marker:
            conn.execute("""
                INSERT INTO quest_markers (quest_id, note, completed, created_at, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(quest_id) DO UPDATE SET
                    note=excluded.note, completed=excluded.completed,
                    updated_at=excluded.updated_at
            """, (qid, marker.get("note", ""), int(marker.get("completed", 0)),
                  now, now))

    # Import areas + their quests
    for area in data.get("areas", []):
        existing_a = conn.execute(
            "SELECT id FROM quest_areas WHERE world_id=? AND name=? COLLATE NOCASE",
            (world_id, area["name"])
        ).fetchone()
        if existing_a:
            area_id = existing_a["id"]
        else:
            cur = conn.execute(
                "INSERT INTO quest_areas (world_id, name, sort_order) VALUES (?,?,?)",
                (world_id, area["name"], area.get("sort_order", 0))
            )
            area_id = cur.lastrowid
        for q in area.get("quests", []):
            _upsert_quest(q, area_id)
            total += 1

    # Unassigned quests
    for q in data.get("unassigned_quests", []):
        _upsert_quest(q, None)
        total += 1

    conn.commit()
    return total


# ═══════════════════════════════════════════════════════════════
# DAMAGE CALCULATOR IMPORTERS
# ═══════════════════════════════════════════════════════════════

def _import_calc_preset(conn, data: dict) -> int:
    """Upsert a calculator modifier preset (matched by category + label)."""
    if not data or not data.get("label"):
        return 0
    import database_calc as dcalc
    existing = conn.execute(
        "SELECT id FROM calc_presets WHERE category = ? AND label = ? COLLATE NOCASE",
        (data.get("category", "Blade"), data["label"])
    ).fetchone()
    record = {
        "category":   data.get("category", "Blade"),
        "label":      data["label"],
        "value":      data.get("value", 0),
        "sort_order": data.get("sort_order", 0),
    }
    if existing:
        record["id"] = existing["id"]
    dcalc.upsert_preset(conn, record)
    return 1


def _import_spell(conn, data: dict) -> int:
    """Upsert a single spell (matched by name via upsert_spell)."""
    if not data or not data.get("name"):
        return 0
    import database_spells as ds
    try:
        ds.upsert_spell(conn, data)
        return 1
    except Exception:
        return 0


def _import_deck(conn, data: dict) -> int:
    """Upsert a saved deck (matched by name)."""
    if not data or not data.get("name"):
        return 0
    import database_spells as ds
    existing = conn.execute(
        "SELECT id FROM decks WHERE name = ? COLLATE NOCASE", (data["name"],)
    ).fetchone()
    record = {
        "name":        data["name"],
        "school":      data.get("school", "Fire"),
        "tag":         data.get("tag", ""),
        "description": data.get("description", ""),
        "cards":       data.get("cards", []),
    }
    if existing:
        record["id"] = existing["id"]
    ds.upsert_deck(conn, record)
    return 1


def _import_wizard(conn, data: dict) -> int:
    """Upsert a saved wizard / character (matched by name)."""
    if not data or not data.get("name"):
        return 0
    import database_calc as dcalc
    existing = conn.execute(
        "SELECT id FROM wizards WHERE name = ? COLLATE NOCASE",
        (data["name"],)
    ).fetchone()
    record = {
        "name":        data["name"],
        "school":      data.get("school", "Storm"),
        "school2":     data.get("school2", ""),
        "level":       data.get("level", 1),
        "health":      data.get("health", 0),
        "mana":        data.get("mana", 0),
        "damage":      data.get("damage", {}),
        "damage_flat": data.get("damage_flat", {}),
        "resist":      data.get("resist", {}),
        "accuracy":    data.get("accuracy", {}),
        "critical":    data.get("critical", {}),
        "block":       data.get("block", {}),
        "pierce":      data.get("pierce", {}),
        "stun_resist": data.get("stun_resist", 0),
        "heal_in":     data.get("heal_in", 0),
        "heal_out":    data.get("heal_out", 0),
    }
    if existing:
        record["id"] = existing["id"]
    dcalc.upsert_wizard(conn, record)
    return 1


# ═══════════════════════════════════════════════════════════════
# BACKUP IMPORT  (selective restore with GUI category picker)
# ═══════════════════════════════════════════════════════════════

# category key → (nice label, per-item importer)
_BACKUP_CATEGORIES = [
    ("bosses",          "Bosses",             _import_boss),
    ("round_counters",  "Round Counters",     _import_counter),
    ("strategy_guides", "Strategy Guides",    _import_guide),
    ("gear_loadouts",   "Gear Loadouts",      _import_loadout),
    ("quest_worlds",    "Quest Worlds",       _import_quest_world),
    ("decks",           "Decks",              _import_deck),
    ("spells",          "Spells",             _import_spell),
    ("calc_presets",    "Calculator Presets", _import_calc_preset),
    ("characters",      "Characters",         _import_wizard),
]


def import_backup(conn, parent=None) -> bool:
    """Open a backup file, let the user pick categories, and restore them."""
    path, _ = QFileDialog.getOpenFileName(
        parent, "Import from Backup — choose a backup file",
        "", "JSON Files (*.json);;All Files (*)"
    )
    if not path:
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        QMessageBox.critical(parent, "Import failed", f"Could not read file:\n{e}")
        return False

    if not isinstance(payload, dict) or payload.get("app") != "Wizard101 Companion":
        QMessageBox.warning(
            parent, "Import failed",
            "This does not look like a Wizard101 Companion backup file."
        )
        return False

    # Accept the new backup format and the legacy full_export format.
    if payload.get("format") == "backup" or payload.get("export_type") == "full_export":
        data = payload.get("data", {}) or {}
    else:
        QMessageBox.warning(
            parent, "Not a backup",
            "This file isn't a full backup. Use the Backup button to create one, "
            "or share individual items with their base64 codes."
        )
        return False

    if not isinstance(data, dict):
        QMessageBox.warning(parent, "Import failed", "Backup data is malformed.")
        return False

    counts = payload.get("counts") or {k: len(data.get(k, []) or [])
                                        for k, _l, _f in _BACKUP_CATEGORIES}
    meta_iso = payload.get("created_iso", "")

    import share_codes
    categories = [(key, label, counts.get(key, 0))
                  for key, label, _fn in _BACKUP_CATEGORIES]
    dlg = share_codes.CategorySelectDialog(
        "Import from Backup",
        "Choose what to restore from this backup.",
        categories,
        action_label="📥 Import Selected",
        note="Items are merged into your data — matching names are updated, "
             "nothing existing is deleted.",
        meta_line=f"📅 Backup created: {meta_iso or 'unknown'}",
        parent=parent,
    )
    if dlg.exec_() != QDialog.Accepted or not dlg.selected:
        return False

    total = 0
    per_cat = []
    for key, label, fn in _BACKUP_CATEGORIES:
        if key not in dlg.selected:
            continue
        n = 0
        for item in data.get(key, []) or []:
            try:
                n += fn(conn, item)
            except Exception:
                pass
        if n:
            per_cat.append(f"{label}: {n}")
        total += n

    try:
        conn.commit()
    except Exception:
        pass

    summary = "<br>".join(per_cat) if per_cat else "No items imported."
    QMessageBox.information(
        parent, "Import complete",
        f"Restored <b>{total}</b> item(s):<br>{summary}"
    )
    return True
