"""
database_gear.py
════════════════
SQLite schema and helpers for the Gear Guide feature.

Structure:
  loadouts      — top-level named loadouts (school, level range, world, category)
  gear_slots    — one row per gear slot (Hat, Robe, Boots, …) per loadout
  slot_options  — one or more options per slot (optimal, pay2win, farm, craft, …)
  pet_stats     — free-text pet stat entries per loadout
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(__file__).parent / "boss_wiki.db"


def get_connection(db_path: str = None) -> sqlite3.Connection:
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── SCHEMA ─────────────────────────────────────────────────────

def init_gear_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gear_loadouts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            school      TEXT NOT NULL DEFAULT 'Universal',
            level_min   INTEGER NOT NULL DEFAULT 1,
            level_max   INTEGER NOT NULL DEFAULT 170,
            world       TEXT NOT NULL DEFAULT '',
            category    TEXT NOT NULL DEFAULT '',
            notes       TEXT NOT NULL DEFAULT '',
            created_at  REAL,
            updated_at  REAL
        );

        CREATE INDEX IF NOT EXISTS idx_gear_school   ON gear_loadouts(school);
        CREATE INDEX IF NOT EXISTS idx_gear_lvl      ON gear_loadouts(level_min, level_max);

        -- Each slot in a loadout (Hat, Robe, Boots, Wand, …)
        CREATE TABLE IF NOT EXISTS gear_slots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            loadout_id  INTEGER NOT NULL REFERENCES gear_loadouts(id) ON DELETE CASCADE,
            slot_name   TEXT NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_gear_slots_loadout ON gear_slots(loadout_id);

        -- Options inside a slot (can have several: optimal, farm, craft, …)
        CREATE TABLE IF NOT EXISTS slot_options (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slot_id     INTEGER NOT NULL REFERENCES gear_slots(id) ON DELETE CASCADE,
            label       TEXT NOT NULL DEFAULT 'optimal',
            item_name   TEXT NOT NULL DEFAULT '',
            stats_notes TEXT NOT NULL DEFAULT '',
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_slot_options_slot ON slot_options(slot_id);

        -- Free-text pet stat rows per loadout
        CREATE TABLE IF NOT EXISTS gear_pet_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            loadout_id  INTEGER NOT NULL REFERENCES gear_loadouts(id) ON DELETE CASCADE,
            stat_name   TEXT NOT NULL DEFAULT '',
            stat_value  TEXT NOT NULL DEFAULT '',
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_pet_stats_loadout ON gear_pet_stats(loadout_id);
    """)
    conn.commit()


# ─── LOADOUT CRUD ───────────────────────────────────────────────

def list_loadouts(conn, school: str = None, level_min: int = None,
                  level_max: int = None) -> List[dict]:
    """Return loadouts optionally filtered by school and level overlap."""
    q = "SELECT * FROM gear_loadouts WHERE 1=1"
    params: list = []
    if school and school != "All":
        q += " AND school = ?"
        params.append(school)
    if level_min is not None:
        q += " AND level_max >= ?"
        params.append(level_min)
    if level_max is not None:
        q += " AND level_min <= ?"
        params.append(level_max)
    q += " ORDER BY name COLLATE NOCASE"
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def get_loadout_full(conn, loadout_id: int) -> Optional[dict]:
    """Return a loadout with all its slots, options and pet stats."""
    row = conn.execute(
        "SELECT * FROM gear_loadouts WHERE id = ?", (loadout_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)

    slots_raw = conn.execute(
        "SELECT * FROM gear_slots WHERE loadout_id = ? ORDER BY sort_order, id",
        (loadout_id,)
    ).fetchall()

    slots = []
    for s in slots_raw:
        sd = dict(s)
        opts = conn.execute(
            "SELECT * FROM slot_options WHERE slot_id = ? ORDER BY sort_order, id",
            (s['id'],)
        ).fetchall()
        sd['options'] = [dict(o) for o in opts]
        slots.append(sd)

    d['slots'] = slots

    pet_rows = conn.execute(
        "SELECT * FROM gear_pet_stats WHERE loadout_id = ? ORDER BY sort_order, id",
        (loadout_id,)
    ).fetchall()
    d['pet_stats'] = [dict(p) for p in pet_rows]

    return d


def upsert_loadout(conn, data: dict) -> int:
    """
    Insert or update a loadout.
    data keys: id (optional), name, school, level_min, level_max, world,
               category, notes, slots (list), pet_stats (list)

    Returns the loadout id.
    """
    now = time.time()
    lid = data.get('id')

    if lid:
        conn.execute("""
            UPDATE gear_loadouts SET
                name=?, school=?, level_min=?, level_max=?,
                world=?, category=?, notes=?, updated_at=?
            WHERE id=?
        """, (
            data.get('name', ''), data.get('school', 'Universal'),
            data.get('level_min', 1), data.get('level_max', 170),
            data.get('world', ''), data.get('category', ''),
            data.get('notes', ''), now, lid
        ))
    else:
        cur = conn.execute("""
            INSERT INTO gear_loadouts
                (name, school, level_min, level_max, world, category, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            data.get('name', ''), data.get('school', 'Universal'),
            data.get('level_min', 1), data.get('level_max', 170),
            data.get('world', ''), data.get('category', ''),
            data.get('notes', ''), now, now
        ))
        lid = cur.lastrowid

    # Replace slots
    conn.execute("DELETE FROM gear_slots WHERE loadout_id=?", (lid,))
    for si, slot in enumerate(data.get('slots', [])):
        cur2 = conn.execute(
            "INSERT INTO gear_slots (loadout_id, slot_name, sort_order) VALUES (?,?,?)",
            (lid, slot.get('slot_name', ''), si)
        )
        slot_id = cur2.lastrowid
        for oi, opt in enumerate(slot.get('options', [])):
            conn.execute(
                "INSERT INTO slot_options (slot_id, label, item_name, stats_notes, sort_order) "
                "VALUES (?,?,?,?,?)",
                (slot_id, opt.get('label', 'optimal'),
                 opt.get('item_name', ''), opt.get('stats_notes', ''), oi)
            )

    # Replace pet stats
    conn.execute("DELETE FROM gear_pet_stats WHERE loadout_id=?", (lid,))
    for pi, ps in enumerate(data.get('pet_stats', [])):
        conn.execute(
            "INSERT INTO gear_pet_stats (loadout_id, stat_name, stat_value, sort_order) "
            "VALUES (?,?,?,?)",
            (lid, ps.get('stat_name', ''), ps.get('stat_value', ''), pi)
        )

    conn.commit()
    return lid


def delete_loadout(conn, loadout_id: int):
    conn.execute("DELETE FROM gear_loadouts WHERE id=?", (loadout_id,))
    conn.commit()


def delete_all_gear(conn) -> int:
    """Delete every loadout (cascades to slots, options, pet stats).
    Returns the number of loadouts removed."""
    count = conn.execute("SELECT COUNT(*) FROM gear_loadouts").fetchone()[0]
    conn.execute("DELETE FROM gear_loadouts")
    conn.commit()
    return count
