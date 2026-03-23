"""
exporter.py  —  Wizard101 Companion  —  JSON Export Engine
════════════════════════════════════════════════════════════
Provides export functions for every data category:

  • Bosses  — single / by world or area / all
  • Round Counters — single / all  (with linked boss URLs)
  • Strategy Guides — single / all (with linked boss URLs)
  • Gear Loadouts — single / all
  • Quest worlds — single / all

All exports produce a clean, self-describing JSON envelope:
  {
    "app": "Wizard101 Companion",
    "version": 1,
    "export_type": "...",
    "exported_at": "ISO-8601 timestamp",
    "data": { ... }
  }

Usage (from boss_wiki.py):
    import exporter
    exporter.export_boss(conn, "Malistaire the Undying", parent_widget)
    exporter.export_all_bosses(conn, parent_widget)
    ...
"""

import json
import time
import os
from datetime import datetime, timezone
from typing import Optional

from PyQt5.QtWidgets import QFileDialog, QMessageBox


# ── Wizard101 Central wiki base URL for boss pages ────────────────────────
_WIKI_BASE = "https://wiki.wizard101central.com"


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════

def _ts() -> str:
    """ISO-8601 UTC timestamp."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(export_type: str, data) -> dict:
    return {
        "app": "Wizard101 Companion",
        "version": 1,
        "export_type": export_type,
        "exported_at": _ts(),
        "data": data,
    }


def _save_json(payload: dict, suggested_name: str, parent=None) -> bool:
    """Open a Save-As dialog and write JSON.  Returns True on success."""
    path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export — Choose file location",
        suggested_name,
        "JSON Files (*.json);;All Files (*)",
    )
    if not path:
        return False
    if not path.lower().endswith(".json"):
        path += ".json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        QMessageBox.information(
            parent,
            "Export complete",
            f"Saved to:\n{path}",
        )
        return True
    except Exception as e:
        QMessageBox.critical(parent, "Export failed", str(e))
        return False


def _boss_row_to_dict(row: dict) -> dict:
    """Convert a raw boss DB row (already dict) to a clean export dict."""
    # Parse JSON fields that may still be raw strings
    for field in ("cheats_json", "battle_stats_json", "spells_json",
                  "drops_json", "minions_json", "resistances_json"):
        if field in row and isinstance(row[field], str):
            parsed_key = field.replace("_json", "")
            try:
                row[parsed_key] = json.loads(row[field]) if row[field] else (
                    [] if field != "battle_stats_json" else {}
                )
            except Exception:
                row[parsed_key] = [] if field != "battle_stats_json" else {}
            del row[field]

    # Remove raw HTML — not useful in exports and huge
    row.pop("raw_html", None)
    row.pop("has_raw_html", None)

    # Attach wiki URL
    wiki_path = row.get("wiki_path") or ""
    if wiki_path:
        row["wiki_url"] = _WIKI_BASE + wiki_path if wiki_path.startswith("/") else wiki_path
    elif row.get("url"):
        row["wiki_url"] = row["url"]
    else:
        row["wiki_url"] = ""

    return row


def _get_boss_full(conn, name: str) -> Optional[dict]:
    """Fetch a full boss row from DB and return as cleaned dict."""
    import database as db
    row = conn.execute(
        "SELECT * FROM bosses WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row is None:
        return None
    return _boss_row_to_dict(dict(row))


def _get_bosses_by_prefix(conn, location_prefix: str) -> list:
    """Fetch all bosses whose location matches the prefix."""
    rows = conn.execute(
        "SELECT * FROM bosses WHERE (location = ? OR location LIKE ?) AND is_active = 1",
        (location_prefix, location_prefix + " >%"),
    ).fetchall()
    return [_boss_row_to_dict(dict(r)) for r in rows]


def _get_all_bosses(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM bosses WHERE is_active = 1 ORDER BY location, name"
    ).fetchall()
    return [_boss_row_to_dict(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════
# BOSS EXPORTS
# ═══════════════════════════════════════════════════════════════

def export_boss(conn, boss_name: str, parent=None) -> bool:
    """Export a single boss to JSON."""
    data = _get_boss_full(conn, boss_name)
    if data is None:
        QMessageBox.warning(parent, "Export", f"Boss '{boss_name}' not found in database.")
        return False
    payload = _envelope("boss_single", data)
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in boss_name)
    return _save_json(payload, f"boss_{safe_name}.json", parent)


def export_bosses_by_location(conn, location_key: str, location_type: str = "area",
                               parent=None) -> bool:
    """Export all bosses in a world or area."""
    bosses = _get_bosses_by_prefix(conn, location_key)
    if not bosses:
        QMessageBox.warning(parent, "Export", f"No bosses found in '{location_key}'.")
        return False
    label = "world" if location_type == "world" else "area"
    payload = _envelope(f"bosses_{label}", {
        "location": location_key,
        "boss_count": len(bosses),
        "bosses": bosses,
    })
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in location_key)
    return _save_json(payload, f"bosses_{safe}.json", parent)


def export_all_bosses(conn, parent=None) -> bool:
    """Export every boss in the database."""
    bosses = _get_all_bosses(conn)
    if not bosses:
        QMessageBox.warning(parent, "Export", "No bosses in database to export.")
        return False
    payload = _envelope("bosses_all", {
        "boss_count": len(bosses),
        "bosses": bosses,
    })
    return _save_json(payload, "bosses_all.json", parent)


# ═══════════════════════════════════════════════════════════════
# ROUND COUNTER EXPORTS
# ═══════════════════════════════════════════════════════════════

def _enrich_counter(conn, counter: dict) -> dict:
    """Add wiki_url for each linked boss."""
    enriched = dict(counter)
    boss_links = []
    for boss_name in enriched.get("linked_bosses", []):
        row = conn.execute(
            "SELECT wiki_path, url FROM bosses WHERE name = ? COLLATE NOCASE", (boss_name,)
        ).fetchone()
        wiki_url = ""
        if row:
            wp = row["wiki_path"] or ""
            wiki_url = (_WIKI_BASE + wp if wp.startswith("/") else wp) or row["url"] or ""
        boss_links.append({"name": boss_name, "wiki_url": wiki_url})
    enriched["linked_bosses"] = boss_links
    return enriched


def export_round_counter(conn, counter_id: int, parent=None) -> bool:
    """Export a single round counter."""
    import database as db
    counter = db.get_round_counter(conn, counter_id)
    if counter is None:
        QMessageBox.warning(parent, "Export", "Round counter not found.")
        return False
    data = _enrich_counter(conn, counter)
    payload = _envelope("round_counter_single", data)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in counter["name"])
    return _save_json(payload, f"counter_{safe}.json", parent)


def export_all_round_counters(conn, parent=None) -> bool:
    """Export all round counters."""
    import database as db
    counters = db.list_round_counters(conn)
    if not counters:
        QMessageBox.warning(parent, "Export", "No round counters to export.")
        return False
    enriched = [_enrich_counter(conn, c) for c in counters]
    payload = _envelope("round_counters_all", {
        "counter_count": len(enriched),
        "counters": enriched,
    })
    return _save_json(payload, "round_counters_all.json", parent)


# ═══════════════════════════════════════════════════════════════
# STRATEGY GUIDE EXPORTS
# ═══════════════════════════════════════════════════════════════

def _enrich_guide(conn, guide: dict) -> dict:
    """Add wiki_url for each linked boss."""
    enriched = dict(guide)
    boss_links = []
    for boss_name in enriched.get("linked_bosses", []):
        row = conn.execute(
            "SELECT wiki_path, url FROM bosses WHERE name = ? COLLATE NOCASE", (boss_name,)
        ).fetchone()
        wiki_url = ""
        if row:
            wp = row["wiki_path"] or ""
            wiki_url = (_WIKI_BASE + wp if wp.startswith("/") else wp) or row["url"] or ""
        boss_links.append({"name": boss_name, "wiki_url": wiki_url})
    enriched["linked_bosses"] = boss_links
    return enriched


def export_guide(conn, guide_id: int, parent=None) -> bool:
    """Export a single strategy guide."""
    import database as db
    guide = db.get_guide(conn, guide_id)
    if guide is None:
        QMessageBox.warning(parent, "Export", "Guide not found.")
        return False
    data = _enrich_guide(conn, guide)
    payload = _envelope("strategy_guide_single", data)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in guide["name"])
    return _save_json(payload, f"guide_{safe}.json", parent)


def export_all_guides(conn, parent=None) -> bool:
    """Export all strategy guides."""
    import database as db
    guides = db.list_guides(conn)
    if not guides:
        QMessageBox.warning(parent, "Export", "No strategy guides to export.")
        return False
    enriched = [_enrich_guide(conn, g) for g in guides]
    payload = _envelope("strategy_guides_all", {
        "guide_count": len(enriched),
        "guides": enriched,
    })
    return _save_json(payload, "strategy_guides_all.json", parent)


# ═══════════════════════════════════════════════════════════════
# GEAR LOADOUT EXPORTS
# ═══════════════════════════════════════════════════════════════

def export_gear_loadout(conn, loadout_id: int, parent=None) -> bool:
    """Export a single gear loadout."""
    import database_gear as dg
    loadout = dg.get_loadout_full(conn, loadout_id)
    if loadout is None:
        QMessageBox.warning(parent, "Export", "Gear loadout not found.")
        return False
    payload = _envelope("gear_loadout_single", loadout)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in loadout["name"])
    return _save_json(payload, f"gear_{safe}.json", parent)


def export_all_gear_loadouts(conn, parent=None) -> bool:
    """Export all gear loadouts."""
    import database_gear as dg
    loadouts_meta = dg.list_loadouts(conn)
    if not loadouts_meta:
        QMessageBox.warning(parent, "Export", "No gear loadouts to export.")
        return False
    full = [dg.get_loadout_full(conn, m["id"]) for m in loadouts_meta]
    full = [l for l in full if l]
    payload = _envelope("gear_loadouts_all", {
        "loadout_count": len(full),
        "loadouts": full,
    })
    return _save_json(payload, "gear_loadouts_all.json", parent)


# ═══════════════════════════════════════════════════════════════
# QUEST WORLD EXPORTS
# ═══════════════════════════════════════════════════════════════

def _world_full(conn, world_id: int) -> Optional[dict]:
    """Fetch a full quest world with its areas and quests."""
    import database_quests as dq
    world_row = conn.execute(
        "SELECT * FROM quest_worlds WHERE id = ?", (world_id,)
    ).fetchone()
    if world_row is None:
        return None
    world = dict(world_row)

    areas = conn.execute(
        "SELECT * FROM quest_areas WHERE world_id = ? ORDER BY sort_order, id",
        (world_id,)
    ).fetchall()
    area_list = []
    for area in areas:
        ad = dict(area)
        quests = conn.execute(
            "SELECT * FROM quests WHERE area_id = ? ORDER BY sort_order, quest_number, id",
            (area["id"],)
        ).fetchall()
        quest_list = []
        for q in quests:
            qd = dict(q)
            # Parse types_json
            try:
                qd["types"] = json.loads(qd.get("types_json") or "[]")
            except Exception:
                qd["types"] = []
            qd.pop("types_json", None)
            qd.pop("raw_html", None)
            # Include marker if present
            marker = conn.execute(
                "SELECT note, completed FROM quest_markers WHERE quest_id = ?",
                (q["id"],)
            ).fetchone()
            if marker:
                qd["marker"] = dict(marker)
            quest_list.append(qd)
        ad["quests"] = quest_list
        area_list.append(ad)

    # Quests not assigned to any area
    orphan_quests = conn.execute(
        "SELECT * FROM quests WHERE world_id = ? AND area_id IS NULL "
        "ORDER BY sort_order, quest_number, id",
        (world_id,)
    ).fetchall()
    orphan_list = []
    for q in orphan_quests:
        qd = dict(q)
        try:
            qd["types"] = json.loads(qd.get("types_json") or "[]")
        except Exception:
            qd["types"] = []
        qd.pop("types_json", None)
        qd.pop("raw_html", None)
        orphan_list.append(qd)

    world["areas"] = area_list
    world["unassigned_quests"] = orphan_list
    return world


def export_quest_world(conn, world_id: int, parent=None) -> bool:
    """Export a single quest world with all its quests."""
    world = _world_full(conn, world_id)
    if world is None:
        QMessageBox.warning(parent, "Export", "Quest world not found.")
        return False
    payload = _envelope("quest_world_single", world)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in world.get("name", "world"))
    return _save_json(payload, f"quests_{safe}.json", parent)


def export_all_quest_worlds(conn, parent=None) -> bool:
    """Export all quest worlds."""
    world_rows = conn.execute(
        "SELECT id, name FROM quest_worlds ORDER BY display_order, name"
    ).fetchall()
    if not world_rows:
        QMessageBox.warning(parent, "Export", "No quest worlds to export.")
        return False
    worlds = [_world_full(conn, r["id"]) for r in world_rows]
    worlds = [w for w in worlds if w]
    payload = _envelope("quest_worlds_all", {
        "world_count": len(worlds),
        "worlds": worlds,
    })
    return _save_json(payload, "quest_worlds_all.json", parent)


# ═══════════════════════════════════════════════════════════════
# FULL EXPORT (everything in one file)
# ═══════════════════════════════════════════════════════════════

def export_everything(conn, parent=None) -> bool:
    """Export all data categories into a single JSON file."""
    import database as db
    import database_gear as dg

    world_rows = conn.execute(
        "SELECT id FROM quest_worlds ORDER BY display_order, name"
    ).fetchall()

    import database as db_mod
    counters = db_mod.list_round_counters(conn)
    guides = db_mod.list_guides(conn)

    payload = _envelope("full_export", {
        "bosses": _get_all_bosses(conn),
        "round_counters": [_enrich_counter(conn, c) for c in counters],
        "strategy_guides": [_enrich_guide(conn, g) for g in guides],
        "gear_loadouts": [
            dg.get_loadout_full(conn, m["id"])
            for m in dg.list_loadouts(conn)
        ],
        "quest_worlds": [
            _world_full(conn, r["id"]) for r in world_rows
        ],
    })
    return _save_json(payload, "wizard101_companion_full_export.json", parent)
