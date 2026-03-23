"""
Database Quest Module
══════════════════════
Quest tracker tables and CRUD operations.
Designed to be imported alongside database.py without conflicts.
"""

import sqlite3
import json
import time
from typing import Dict, List, Optional


def init_quest_tables(conn: sqlite3.Connection):
    """Create quest tracker tables if they don't exist."""
    conn.executescript("""
        -- World metadata (stats scraped from FinalBastion)
        CREATE TABLE IF NOT EXISTS quest_worlds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            source_url TEXT DEFAULT '',
            total_quests INTEGER,
            mob_fights INTEGER,
            dc_quests INTEGER,
            boss_fights INTEGER,
            cheater_bosses INTEGER,
            solo_quests INTEGER,
            description TEXT DEFAULT '',
            intro_text TEXT DEFAULT '',
            scraped_at REAL,
            display_order INTEGER DEFAULT 999
        );

        -- Areas within a world
        CREATE TABLE IF NOT EXISTS quest_areas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            world_id INTEGER NOT NULL REFERENCES quest_worlds(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        -- Individual quests
        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            world_id INTEGER NOT NULL REFERENCES quest_worlds(id) ON DELETE CASCADE,
            area_id INTEGER REFERENCES quest_areas(id) ON DELETE SET NULL,
            quest_number INTEGER,
            name TEXT NOT NULL,
            types_json TEXT DEFAULT '[]',   -- [{"label":"cheat","color":"#ff4444"}, ...]
            raw_html TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_quests_world ON quests(world_id);
        CREATE INDEX IF NOT EXISTS idx_quests_area ON quests(area_id);
        CREATE INDEX IF NOT EXISTS idx_quests_number ON quests(quest_number);

        -- User markers on quests (free-text notes + completion flag)
        CREATE TABLE IF NOT EXISTS quest_markers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quest_id INTEGER NOT NULL REFERENCES quests(id) ON DELETE CASCADE,
            note TEXT DEFAULT '',
            completed INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL,
            UNIQUE(quest_id)
        );
    """)
    # Migration: add intro_text column to existing databases
    try:
        conn.execute("ALTER TABLE quest_worlds ADD COLUMN intro_text TEXT DEFAULT ''")
    except Exception:
        pass  # Column already exists

    # Migration: add level range columns
    for col, default in [("level_min", "NULL"), ("level_max", "NULL")]:
        try:
            conn.execute(f"ALTER TABLE quest_worlds ADD COLUMN {col} INTEGER DEFAULT {default}")
        except Exception:
            pass  # Column already exists

    # Migration: remove UNIQUE(world_id, name) constraint from quest_areas.
    # Same area name can appear multiple times (e.g. "Last Wood" in Khrysalis).
    # SQLite can't DROP CONSTRAINTS; detect via schema text and recreate if needed.
    try:
        schema_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='quest_areas'"
        ).fetchone()
        schema_sql = schema_row[0] if schema_row else ""
        needs_migration = "UNIQUE" in schema_sql.upper()

        if needs_migration:
            conn.executescript("""
                CREATE TABLE quest_areas_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    world_id INTEGER NOT NULL REFERENCES quest_worlds(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    sort_order INTEGER DEFAULT 0
                );
                INSERT INTO quest_areas_new SELECT id, world_id, name, sort_order FROM quest_areas;
                DROP TABLE quest_areas;
                ALTER TABLE quest_areas_new RENAME TO quest_areas;
                CREATE INDEX IF NOT EXISTS idx_areas_world ON quest_areas(world_id);
            """)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(f"quest_areas migration warning: {e}")

    conn.commit()


def get_world_has_pins(conn: sqlite3.Connection, world_id: int) -> bool:
    """Returns True if any quest in this world has a marker (pin), with or without a note."""
    return get_world_pin_count(conn, world_id) > 0


def get_world_pin_count(conn: sqlite3.Connection, world_id: int) -> int:
    """Returns the number of pinned quests in this world (any marker, with or without note)."""
    row = conn.execute("""
        SELECT COUNT(*) as c FROM quest_markers qm
        JOIN quests q ON qm.quest_id = q.id
        WHERE q.world_id = ?
    """, (world_id,)).fetchone()
    return row["c"] if row else 0


# ── World CRUD ─────────────────────────────────────────────────────────────────

def upsert_world(conn: sqlite3.Connection, data: dict) -> int:
    """Insert or update a world record. Returns world_id."""
    existing = conn.execute(
        "SELECT id FROM quest_worlds WHERE name = ? COLLATE NOCASE", (data["name"],)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE quest_worlds SET
                source_url=?, total_quests=?, mob_fights=?, dc_quests=?,
                boss_fights=?, cheater_bosses=?, solo_quests=?,
                description=?, intro_text=?, scraped_at=?,
                level_min=?, level_max=?,
                display_order=COALESCE(?, display_order)
            WHERE id=?
        """, (
            data.get("source_url", ""),
            data.get("total_quests"),
            data.get("mob_fights"),
            data.get("dc_quests"),
            data.get("boss_fights"),
            data.get("cheater_bosses"),
            data.get("solo_quests"),
            data.get("description", ""),
            data.get("intro_text", ""),
            data.get("scraped_at", time.time()),
            data.get("level_min"),
            data.get("level_max"),
            data.get("display_order"),
            existing["id"],
        ))
        return existing["id"]
    else:
        cur = conn.execute("""
            INSERT INTO quest_worlds
                (name, source_url, total_quests, mob_fights, dc_quests,
                 boss_fights, cheater_bosses, solo_quests, description,
                 intro_text, scraped_at, display_order, level_min, level_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["name"],
            data.get("source_url", ""),
            data.get("total_quests"),
            data.get("mob_fights"),
            data.get("dc_quests"),
            data.get("boss_fights"),
            data.get("cheater_bosses"),
            data.get("solo_quests"),
            data.get("description", ""),
            data.get("intro_text", ""),
            data.get("scraped_at", time.time()),
            data.get("display_order", 999),
            data.get("level_min"),
            data.get("level_max"),
        ))
        return cur.lastrowid


def get_all_worlds(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute(
        "SELECT * FROM quest_worlds ORDER BY display_order ASC, name ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_world_by_name(conn: sqlite3.Connection, name: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM quest_worlds WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return dict(row) if row else None


def delete_world_data(conn: sqlite3.Connection, world_id: int):
    """Delete all quest data for a world (cascades to areas, quests, markers)."""
    conn.execute("DELETE FROM quest_worlds WHERE id=?", (world_id,))
    conn.commit()


# ── Area CRUD ──────────────────────────────────────────────────────────────────

def get_or_create_area(conn: sqlite3.Connection, world_id: int, name: str,
                       sort_order: int = 0, allow_duplicate_names: bool = True) -> int:
    """
    Insert a new area row. Areas with the same name can appear multiple times
    (e.g. 'Last Wood' appears twice in Khrysalis). We always create a new row
    unless allow_duplicate_names=False, in which case we reuse an existing one.
    """
    if not allow_duplicate_names:
        existing = conn.execute(
            "SELECT id FROM quest_areas WHERE world_id=? AND name=? COLLATE NOCASE",
            (world_id, name)
        ).fetchone()
        if existing:
            return existing["id"]
    cur = conn.execute(
        "INSERT INTO quest_areas (world_id, name, sort_order) VALUES (?, ?, ?)",
        (world_id, name, sort_order)
    )
    return cur.lastrowid


def get_areas_for_world(conn: sqlite3.Connection, world_id: int) -> List[Dict]:
    rows = conn.execute(
        "SELECT * FROM quest_areas WHERE world_id=? ORDER BY sort_order ASC, id ASC",
        (world_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── Quest CRUD ─────────────────────────────────────────────────────────────────

def insert_quest(conn: sqlite3.Connection, world_id: int, area_id: Optional[int],
                 quest_number: Optional[int], name: str,
                 types: list, raw_html: str = "", sort_order: int = 0) -> int:
    cur = conn.execute("""
        INSERT INTO quests (world_id, area_id, quest_number, name, types_json, raw_html, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (world_id, area_id, quest_number, name, json.dumps(types), raw_html, sort_order))
    return cur.lastrowid


def get_quests_for_world(conn: sqlite3.Connection, world_id: int) -> List[Dict]:
    rows = conn.execute("""
        SELECT q.*, qa.name as area_name
        FROM quests q
        LEFT JOIN quest_areas qa ON q.area_id = qa.id
        WHERE q.world_id = ?
        ORDER BY q.sort_order ASC, q.id ASC
    """, (world_id,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["types"] = json.loads(d.get("types_json") or "[]")
        del d["types_json"]
        result.append(d)
    return result


def get_quests_for_area(conn: sqlite3.Connection, area_id: int) -> List[Dict]:
    rows = conn.execute("""
        SELECT q.*, qa.name as area_name
        FROM quests q
        LEFT JOIN quest_areas qa ON q.area_id = qa.id
        WHERE q.area_id = ?
        ORDER BY q.sort_order ASC, q.id ASC
    """, (area_id,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["types"] = json.loads(d.get("types_json") or "[]")
        del d["types_json"]
        result.append(d)
    return result


def clear_quests_for_world(conn: sqlite3.Connection, world_id: int):
    """Remove all quests and areas for a world (used before re-scraping)."""
    conn.execute("DELETE FROM quests WHERE world_id=?", (world_id,))
    conn.execute("DELETE FROM quest_areas WHERE world_id=?", (world_id,))
    conn.commit()


# ── Marker CRUD ────────────────────────────────────────────────────────────────

def set_quest_marker(conn: sqlite3.Connection, quest_id: int, note: str, completed: bool = False):
    """Set or update a marker on a quest. Note may be empty (bookmark with no text)."""
    now = time.time()
    existing = conn.execute(
        "SELECT id FROM quest_markers WHERE quest_id=?", (quest_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE quest_markers SET note=?, completed=?, updated_at=? WHERE quest_id=?",
            (note, 1 if completed else 0, now, quest_id)
        )
    else:
        conn.execute(
            "INSERT INTO quest_markers (quest_id, note, completed, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (quest_id, note, 1 if completed else 0, now, now)
        )
    conn.commit()


def remove_quest_marker(conn: sqlite3.Connection, quest_id: int):
    conn.execute("DELETE FROM quest_markers WHERE quest_id=?", (quest_id,))
    conn.commit()


def get_quest_marker(conn: sqlite3.Connection, quest_id: int) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM quest_markers WHERE quest_id=?", (quest_id,)
    ).fetchone()
    return dict(row) if row else None


def get_all_markers_for_world(conn: sqlite3.Connection, world_id: int) -> Dict[int, Dict]:
    """Returns {quest_id: marker_dict} for a world."""
    rows = conn.execute("""
        SELECT qm.* FROM quest_markers qm
        JOIN quests q ON qm.quest_id = q.id
        WHERE q.world_id = ?
    """, (world_id,)).fetchall()
    return {row["quest_id"]: dict(row) for row in rows}


def get_world_completion_stats(conn: sqlite3.Connection, world_id: int) -> Dict:
    """Returns total_quests, completed_quests, marked_quests for a world."""
    total = conn.execute(
        "SELECT COUNT(*) as c FROM quests WHERE world_id=?", (world_id,)
    ).fetchone()["c"]
    completed = conn.execute("""
        SELECT COUNT(*) as c FROM quest_markers qm
        JOIN quests q ON qm.quest_id = q.id
        WHERE q.world_id=? AND qm.completed=1
    """, (world_id,)).fetchone()["c"]
    marked = conn.execute("""
        SELECT COUNT(*) as c FROM quest_markers qm
        JOIN quests q ON qm.quest_id = q.id
        WHERE q.world_id=?
    """, (world_id,)).fetchone()["c"]
    return {"total": total, "completed": completed, "marked": marked}


# ── Bulk import from scraper result ───────────────────────────────────────────

WORLD_DISPLAY_ORDER = [
    "Wizard City", "Krokotopia", "Grizzleheim", "Marleybone", "MooShu",
    "Dragonspyre", "Celestia", "Zafaria", "Wysteria", "Avalon", "Azteca",
    "Aquila", "Khrysalis", "Polaris", "Arcanum", "Mirage", "Empyrea",
    "Karamelle", "Lemuria", "Novus", "Wallaru", "Selenopolis", "Darkmoor",
]


def import_world_data(conn: sqlite3.Connection, scraped: dict) -> int:
    """
    Import a scraped world dict (from quest_scraper.scrape_world_guide) into DB.
    Clears existing quest/area data for the world first (full refresh),
    but PRESERVES any user markers by re-attaching them to the new quest rows
    using quest name matching.
    Returns world_id.
    """
    world_name = scraped["world"]
    stats = scraped.get("stats", {})

    display_order = 999
    if world_name in WORLD_DISPLAY_ORDER:
        display_order = WORLD_DISPLAY_ORDER.index(world_name)

    world_data = {
        "name": world_name,
        "source_url": scraped.get("source_url", ""),
        "total_quests": stats.get("total_quests"),
        "mob_fights": stats.get("mob_fights"),
        "dc_quests": stats.get("dc_quests"),
        "boss_fights": stats.get("boss_fights"),
        "cheater_bosses": stats.get("cheater_bosses"),
        "solo_quests": stats.get("solo_quests"),
        "description": stats.get("description", ""),
        "intro_text": scraped.get("intro_text", ""),
        "scraped_at": scraped.get("scraped_at", time.time()),
        "display_order": display_order,
    }

    world_id = upsert_world(conn, world_data)

    # ── Save markers keyed by quest name BEFORE clearing quests ──────────────
    # quest_markers rows will be cascade-deleted when quests are deleted, so we
    # snapshot them here and restore them after the new quests are inserted.
    saved_markers: dict = {}   # {quest_name_lower: marker_dict}
    try:
        rows = conn.execute("""
            SELECT q.name, qm.note, qm.completed, qm.created_at, qm.updated_at
            FROM quest_markers qm
            JOIN quests q ON qm.quest_id = q.id
            WHERE q.world_id = ?
        """, (world_id,)).fetchall()
        for row in rows:
            saved_markers[row[0].lower()] = {
                "note":       row[1] or "",
                "completed":  row[2],
                "created_at": row[3],
                "updated_at": row[4],
            }
    except Exception:
        pass   # no markers yet — no problem

    # Clear old quest data (markers will be cascade-deleted here)
    clear_quests_for_world(conn, world_id)

    areas = scraped.get("areas", [])
    global_sort = 0

    for area_idx, area in enumerate(areas):
        area_name = area.get("name", "General")
        area_id = get_or_create_area(conn, world_id, area_name, area_idx)

        for quest in area.get("quests", []):
            insert_quest(
                conn,
                world_id=world_id,
                area_id=area_id,
                quest_number=quest.get("number"),
                name=quest.get("name", ""),
                types=quest.get("types", []),
                raw_html=quest.get("raw_html", ""),
                sort_order=global_sort,
            )
            global_sort += 1

    conn.commit()

    # ── Re-attach saved markers to new quest rows by name ────────────────────
    if saved_markers:
        try:
            new_quests = conn.execute(
                "SELECT id, name FROM quests WHERE world_id = ?", (world_id,)
            ).fetchall()
            now = time.time()
            for qrow in new_quests:
                key = qrow[1].lower()
                if key in saved_markers:
                    m = saved_markers[key]
                    conn.execute("""
                        INSERT OR REPLACE INTO quest_markers
                            (quest_id, note, completed, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (qrow[0], m["note"], m["completed"],
                          m["created_at"] or now, m["updated_at"] or now))
            conn.commit()
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning(f"Marker restore failed: {e}")

    return world_id


def search_quests(conn: sqlite3.Connection, query: str, limit: int = 10) -> List[Dict]:
    """
    Full-text search across all quest names (and types) in the DB.
    Returns a list of matching quest dicts, each augmented with
    'world_name' and 'area_name' for context.
    Falls back to LIKE if FTS is not available.

    Result dicts include at minimum:
        id, name, quest_number, types, world_name, area_name
    """
    query_clean = query.strip()
    if not query_clean:
        return []

    rows = []

    # ── Try FTS first ────────────────────────────────────────────
    try:
        cur = conn.execute("""
            SELECT
                q.id, q.name, q.quest_number, q.types_json,
                w.name  AS world_name,
                a.name  AS area_name,
                w.display_order
            FROM quests q
            JOIN quest_areas   a ON a.id = q.area_id
            JOIN quest_worlds  w ON w.id = q.world_id
            WHERE q.name LIKE ? COLLATE NOCASE
            ORDER BY w.display_order, q.sort_order
            LIMIT ?
        """, (f"%{query_clean}%", limit))
        rows = [dict(r) for r in cur.fetchall()]
    except Exception:
        pass

    # ── Exact/prefix bonus: sort exact matches first ─────────────
    ql = query_clean.lower()
    rows.sort(key=lambda r: (
        0 if r["name"].lower() == ql else
        1 if r["name"].lower().startswith(ql) else
        2
    ))

    # ── Parse types_json ────────────────────────────────────────
    for r in rows:
        try:
            r["types"] = json.loads(r.get("types_json") or "[]")
        except Exception:
            r["types"] = []

    return rows


def get_all_quest_names(conn: sqlite3.Connection) -> List[str]:
    """
    Return a flat list of every quest name in the DB.
    Used to populate the QuestOCRScanner's known-names list.
    """
    try:
        cur = conn.execute("SELECT DISTINCT name FROM quests ORDER BY name")
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def get_world_encounter_counts(conn: sqlite3.Connection, world_id: int) -> dict:
    """
    Count every encounter type for a world by reading types_json from the quests table.
    Returns a dict like:
      {
        "total": 148,
        "talk": 60, "mob": 30, "d&c": 19, "boss": 18, "cheat": 6,
        "instance": 4, "explore": 22, "interact": 14, "collect": 3,
        "puzzle": 1, "solo": 2, "elite": 1, ...
      }
    Only keys with count > 0 are included (except "total").
    """
    try:
        rows = conn.execute(
            "SELECT types_json FROM quests WHERE world_id = ?", (world_id,)
        ).fetchall()
    except Exception:
        return {"total": 0}

    if not rows:
        return {"total": 0}

    counts: dict = {}
    total = len(rows)

    for (types_json,) in rows:
        try:
            types = json.loads(types_json or "[]")
        except Exception:
            types = []
        seen_in_quest: set = set()
        for t in types:
            if isinstance(t, dict):
                label = t.get("label", "").lower().strip()
                if label and label not in seen_in_quest:
                    counts[label] = counts.get(label, 0) + 1
                    seen_in_quest.add(label)

    counts["total"] = total
    return counts
