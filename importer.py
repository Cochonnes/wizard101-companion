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

from PyQt5.QtWidgets import QFileDialog, QMessageBox


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
