"""
exporter.py  —  Wizard101 Companion  —  Full Backup Engine
════════════════════════════════════════════════════════════
Creates a single self-describing backup file containing every piece of
user data in the app. This replaces the old per-category JSON exports;
individual items are now shared via compact base64 codes instead
(see database.py, database_gear.py, database_spells.py).

Backup envelope
───────────────
  {
    "app": "Wizard101 Companion",
    "format": "backup",
    "version": 1,
    "created_at":  <epoch float>,
    "created_iso": "YYYY-MM-DD HH:MM",
    "counts": { "<category>": <int>, ... },
    "data":   { "<category>": [ ... ], ... }
  }

Public API
──────────
  create_backup(conn, parent=None) -> bool       (pick categories + save file)
  gather_backup(conn, selected=None) -> dict      (envelope, no file I/O)
  count_categories(conn) -> dict                  (cheap per-category counts)
"""

import json
import time

from PyQt5.QtWidgets import QFileDialog, QMessageBox


# ═══════════════════════════════════════════════════════════════
# CATEGORY GATHERERS  — each returns a list; failures degrade to []
# ═══════════════════════════════════════════════════════════════

def _gather_bosses(conn) -> list:
    try:
        rows = conn.execute(
            "SELECT * FROM bosses WHERE is_active = 1 ORDER BY name"
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        # Parse JSON columns into rich values (importer accepts either form)
        for col in ("cheats_json", "battle_stats_json", "spells_json",
                    "drops_json", "minions_json", "resistances_json"):
            key = col.replace("_json", "")
            try:
                d[key] = json.loads(d.pop(col, "") or ("{}" if "stats" in col or "resist" in col else "[]"))
            except Exception:
                d[key] = {} if ("stats" in col or "resist" in col) else []
                d.pop(col, None)
        # Strip heavy / re-fetchable columns
        for heavy in ("raw_html", "raw_wikitext", "id",
                      "first_scraped_at", "last_updated_at", "is_active"):
            d.pop(heavy, None)
        out.append(d)
    return out


def _gather_round_counters(conn) -> list:
    try:
        import database as db
        return db.list_round_counters(conn)
    except Exception:
        return []


def _gather_strategy_guides(conn) -> list:
    try:
        import database as db
        return db.list_guides(conn)
    except Exception:
        return []


def _gather_gear_loadouts(conn) -> list:
    try:
        import database_gear as dg
        rows = conn.execute("SELECT id FROM gear_loadouts ORDER BY name").fetchall()
        out = []
        for r in rows:
            full = dg.get_loadout_full(conn, r["id"])
            if full:
                full.pop("id", None)
                out.append(full)
        return out
    except Exception:
        return []


def _gather_quest_worlds(conn) -> list:
    try:
        import database_quests as dq
    except Exception:
        return []
    out = []
    try:
        worlds = dq.get_all_worlds(conn)
    except Exception:
        return []
    for w in worlds:
        wid = w["id"]
        try:
            markers = dq.get_all_markers_for_world(conn, wid)
        except Exception:
            markers = {}
        try:
            quests = dq.get_quests_for_world(conn, wid)
        except Exception:
            quests = []
        try:
            areas = dq.get_areas_for_world(conn, wid)
        except Exception:
            areas = []

        area_map = {a["id"]: {"name": a["name"],
                              "sort_order": a.get("sort_order", 0),
                              "quests": []} for a in areas}
        unassigned = []

        for q in quests:
            entry = {
                "name":         q.get("name", ""),
                "quest_number": q.get("quest_number"),
                "types":        q.get("types", []),
                "sort_order":   q.get("sort_order", 0),
            }
            m = markers.get(q["id"])
            if m:
                entry["marker"] = {"note": m.get("note", ""),
                                   "completed": int(m.get("completed", 0))}
            aid = q.get("area_id")
            if aid in area_map:
                area_map[aid]["quests"].append(entry)
            else:
                unassigned.append(entry)

        wd = {k: v for k, v in dict(w).items() if k != "id"}
        wd["areas"] = list(area_map.values())
        wd["unassigned_quests"] = unassigned
        out.append(wd)
    return out


def _gather_decks(conn) -> list:
    try:
        import database_spells as ds
        decks = ds.list_decks(conn)
        for d in decks:
            d.pop("id", None)
            for c in d.get("cards", []):
                c.pop("id", None)
                c.pop("deck_id", None)
        return decks
    except Exception:
        return []


def _gather_calc_presets(conn) -> list:
    try:
        import database_calc as dcalc
        rows = dcalc.list_presets(conn)
        for r in rows:
            r.pop("id", None)
        return rows
    except Exception:
        return []


def _gather_characters(conn) -> list:
    try:
        import database_calc as dcalc
        rows = dcalc.list_wizards(conn)
        for r in rows:
            r.pop("id", None)
        return rows
    except Exception:
        return []


def _gather_spells(conn) -> list:
    try:
        import database_spells as ds
    except Exception:
        return []
    try:
        names = [r["name"] for r in
                 conn.execute("SELECT name FROM spells ORDER BY name").fetchall()]
    except Exception:
        return []
    out = []
    for nm in names:
        try:
            sp = ds.get_spell(conn, nm)
        except Exception:
            sp = None
        if not sp:
            continue
        # Drop heavy / re-derivable columns to keep the backup lean.
        for heavy in ("id", "raw_wikitext", "ocr_raw",
                      "first_scraped_at", "last_updated_at"):
            sp.pop(heavy, None)
        out.append(sp)
    return out


# Ordered category → (label, gatherer, count-sql) map.
# Drives the backup selection dialog, the gathered data and the counts.
_CATEGORIES = [
    ("bosses",          "Bosses",             _gather_bosses,
     "SELECT COUNT(*) FROM bosses WHERE is_active = 1"),
    ("round_counters",  "Round Counters",     _gather_round_counters,
     "SELECT COUNT(*) FROM round_counters"),
    ("strategy_guides", "Strategy Guides",    _gather_strategy_guides,
     "SELECT COUNT(*) FROM guides"),
    ("gear_loadouts",   "Gear Loadouts",      _gather_gear_loadouts,
     "SELECT COUNT(*) FROM gear_loadouts"),
    ("quest_worlds",    "Quest Worlds",       _gather_quest_worlds,
     "SELECT COUNT(*) FROM quest_worlds"),
    ("decks",           "Decks",              _gather_decks,
     "SELECT COUNT(*) FROM decks"),
    ("spells",          "Spells",             _gather_spells,
     "SELECT COUNT(*) FROM spells"),
    ("calc_presets",    "Calculator Presets", _gather_calc_presets,
     "SELECT COUNT(*) FROM calc_presets"),
    ("characters",      "Characters",         _gather_characters,
     "SELECT COUNT(*) FROM wizards"),
]


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def count_categories(conn) -> dict:
    """Cheap per-category counts (no full gather)."""
    counts = {}
    for key, _label, _fn, sql in _CATEGORIES:
        try:
            counts[key] = int(conn.execute(sql).fetchone()[0])
        except Exception:
            counts[key] = 0
    return counts


def gather_backup(conn, selected=None) -> dict:
    """
    Build the backup envelope. If ``selected`` is given (a set of category
    keys), only those categories are gathered; the rest are omitted entirely.
    """
    now = time.time()
    data, counts = {}, {}
    for key, _label, fn, _sql in _CATEGORIES:
        if selected is not None and key not in selected:
            continue
        items = fn(conn) or []
        data[key] = items
        counts[key] = len(items)
    return {
        "app":         "Wizard101 Companion",
        "format":      "backup",
        "version":     1,
        "created_at":  now,
        "created_iso": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "counts":      counts,
        "data":        data,
    }


def create_backup(conn, parent=None) -> bool:
    """Let the user pick categories, gather them and save to a .json file."""
    import share_codes

    counts = count_categories(conn)
    categories = [(key, label, counts.get(key, 0))
                  for key, label, _fn, _sql in _CATEGORIES]

    dlg = share_codes.CategorySelectDialog(
        "Create Backup",
        "Choose what to include in this backup.",
        categories,
        action_label="💾 Create Backup",
        note="Spells are large and re-fetchable, so leave them off unless you "
             "want them included.",
        parent=parent,
    )
    if dlg.exec_() != dlg.Accepted or not dlg.selected:
        return False

    try:
        payload = gather_backup(conn, selected=dlg.selected)
    except Exception as e:
        QMessageBox.critical(parent, "Backup failed", f"Could not gather data:\n{e}")
        return False

    default_name = time.strftime("wizard101_backup_%Y%m%d_%H%M.json")
    path, _ = QFileDialog.getSaveFileName(
        parent, "Save Backup", default_name, "JSON Files (*.json);;All Files (*)"
    )
    if not path:
        return False
    if not path.lower().endswith(".json"):
        path += ".json"

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except Exception as e:
        QMessageBox.critical(parent, "Backup failed", f"Could not write file:\n{e}")
        return False

    total = sum(payload["counts"].values())
    QMessageBox.information(
        parent, "Backup complete",
        f"Saved a backup of <b>{total}</b> item(s) to:\n{path}"
    )
    return True
