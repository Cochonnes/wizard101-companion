"""
Database Module - Local Boss Storage
Inspired by Kleinanzeigen approach: scrape once, store locally, serve from DB.
Uses SQLite with FTS5 for fast full-text search across all boss data.
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DB_PATH = Path(__file__).parent / "boss_wiki.db"


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """Get a database connection with WAL mode for performance."""
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection):
    """Initialize database schema with FTS5 search."""
    conn.executescript("""
        -- Main boss table
        CREATE TABLE IF NOT EXISTS bosses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            wiki_path TEXT,
            url TEXT,
            
            -- Basic stats (stored as-is for quick display)
            health TEXT DEFAULT 'Unknown',
            rank TEXT DEFAULT 'Unknown',
            school TEXT DEFAULT 'Unknown',
            location TEXT DEFAULT 'Unknown',
            description TEXT DEFAULT '',
            
            -- Complex data stored as JSON
            cheats_json TEXT DEFAULT '[]',
            battle_stats_json TEXT DEFAULT '{}',
            spells_json TEXT DEFAULT '[]',
            drops_json TEXT DEFAULT '[]',
            minions_json TEXT DEFAULT '[]',
            resistances_json TEXT DEFAULT '{}',
            
            -- Raw HTML for debugging / re-parsing
            raw_html TEXT DEFAULT '',
            
            -- Metadata
            first_scraped_at REAL,
            last_updated_at REAL,
            last_checked_at REAL,
            is_active INTEGER DEFAULT 1,
            scrape_error TEXT DEFAULT ''
        );
        
        CREATE INDEX IF NOT EXISTS idx_bosses_name ON bosses(name);
        CREATE INDEX IF NOT EXISTS idx_bosses_school ON bosses(school);
        CREATE INDEX IF NOT EXISTS idx_bosses_location ON bosses(location);
    """)
    
    # Create FTS5 virtual table for full-text search
    # Check if FTS table already exists
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bosses_fts'"
    ).fetchone()
    
    if not existing:
        conn.execute("""
            CREATE VIRTUAL TABLE bosses_fts USING fts5(
                name,
                school,
                location,
                cheats_text,
                description,
                content='bosses',
                content_rowid='id',
                tokenize='porter unicode61'
            )
        """)
        
        # Triggers to keep FTS in sync
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS bosses_ai AFTER INSERT ON bosses BEGIN
                INSERT INTO bosses_fts(rowid, name, school, location, cheats_text, description)
                VALUES (new.id, new.name, new.school, new.location, new.cheats_json, new.description);
            END;
            
            CREATE TRIGGER IF NOT EXISTS bosses_ad AFTER DELETE ON bosses BEGIN
                INSERT INTO bosses_fts(bosses_fts, rowid, name, school, location, cheats_text, description)
                VALUES ('delete', old.id, old.name, old.school, old.location, old.cheats_json, old.description);
            END;
            
            CREATE TRIGGER IF NOT EXISTS bosses_au AFTER UPDATE ON bosses BEGIN
                INSERT INTO bosses_fts(bosses_fts, rowid, name, school, location, cheats_text, description)
                VALUES ('delete', old.id, old.name, old.school, old.location, old.cheats_json, old.description);
                INSERT INTO bosses_fts(rowid, name, school, location, cheats_text, description)
                VALUES (new.id, new.name, new.school, new.location, new.cheats_json, new.description);
            END;
        """)
    
    conn.commit()
    
    init_round_counters(conn)


def upsert_boss(conn: sqlite3.Connection, data: Dict) -> int:
    """Insert or update a boss record. Returns the row id."""
    now = time.time()
    
    # Check if boss already exists
    existing = conn.execute(
        "SELECT id, first_scraped_at FROM bosses WHERE name = ? COLLATE NOCASE",
        (data['name'],)
    ).fetchone()
    
    if existing:
        conn.execute("""
            UPDATE bosses SET
                wiki_path = ?, url = ?,
                health = ?, rank = ?, school = ?, location = ?, description = ?,
                cheats_json = ?, battle_stats_json = ?, spells_json = ?,
                drops_json = ?, minions_json = ?, resistances_json = ?,
                raw_html = ?,
                last_updated_at = ?, last_checked_at = ?,
                is_active = 1, scrape_error = ''
            WHERE id = ?
        """, (
            data.get('wiki_path', ''),
            data.get('url', ''),
            data.get('health', 'Unknown'),
            data.get('rank', 'Unknown'),
            data.get('school', 'Unknown'),
            data.get('location', 'Unknown'),
            data.get('description', ''),
            json.dumps(data.get('cheats', []), ensure_ascii=False),
            json.dumps(data.get('battle_stats', {}), ensure_ascii=False),
            json.dumps(data.get('spells', []), ensure_ascii=False),
            json.dumps(data.get('drops', []), ensure_ascii=False),
            json.dumps(data.get('minions', []), ensure_ascii=False),
            json.dumps(data.get('resistances', {}), ensure_ascii=False),
            data.get('raw_html', ''),
            now, now,
            existing['id']
        ))
        return existing['id']
    else:
        cursor = conn.execute("""
            INSERT INTO bosses (
                name, wiki_path, url,
                health, rank, school, location, description,
                cheats_json, battle_stats_json, spells_json,
                drops_json, minions_json, resistances_json,
                raw_html,
                first_scraped_at, last_updated_at, last_checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['name'],
            data.get('wiki_path', ''),
            data.get('url', ''),
            data.get('health', 'Unknown'),
            data.get('rank', 'Unknown'),
            data.get('school', 'Unknown'),
            data.get('location', 'Unknown'),
            data.get('description', ''),
            json.dumps(data.get('cheats', []), ensure_ascii=False),
            json.dumps(data.get('battle_stats', {}), ensure_ascii=False),
            json.dumps(data.get('spells', []), ensure_ascii=False),
            json.dumps(data.get('drops', []), ensure_ascii=False),
            json.dumps(data.get('minions', []), ensure_ascii=False),
            json.dumps(data.get('resistances', {}), ensure_ascii=False),
            data.get('raw_html', ''),
            now, now, now
        ))
        return cursor.lastrowid


def mark_inactive(conn: sqlite3.Connection, boss_name: str):
    """Mark a boss as inactive (wiki page gone)."""
    conn.execute(
        "UPDATE bosses SET is_active = 0, last_checked_at = ? WHERE name = ? COLLATE NOCASE",
        (time.time(), boss_name)
    )
    conn.commit()


def mark_error(conn: sqlite3.Connection, boss_name: str, error: str):
    """Record a scrape error for a boss."""
    conn.execute(
        "UPDATE bosses SET scrape_error = ?, last_checked_at = ? WHERE name = ? COLLATE NOCASE",
        (error, time.time(), boss_name)
    )
    conn.commit()


def get_boss(conn: sqlite3.Connection, name: str) -> Optional[Dict]:
    """Get a single boss by exact name."""
    row = conn.execute(
        "SELECT * FROM bosses WHERE name = ? COLLATE NOCASE AND is_active = 1",
        (name,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def search_bosses(conn: sqlite3.Connection, query: str, limit: int = 50) -> List[Dict]:
    """Full-text search across boss names, schools, locations, cheats."""
    if not query or not query.strip():
        return list_all_bosses(conn, limit)
    
    # Try FTS first (name, school, location only — no cheat text)
    try:
        fts_query = ' OR '.join(f'"{word}"*' for word in query.split() if word.strip())
        rows = conn.execute("""
            SELECT b.* FROM bosses b
            JOIN bosses_fts fts ON b.id = fts.rowid
            WHERE bosses_fts MATCH ? AND b.is_active = 1
            ORDER BY rank
            LIMIT ?
        """, (fts_query, limit)).fetchall()
    except Exception:
        rows = []
    
    # Fallback to LIKE search if FTS returns nothing (name, school, location only)
    if not rows:
        like_pattern = f"%{query}%"
        rows = conn.execute("""
            SELECT * FROM bosses 
            WHERE is_active = 1 AND (
                name LIKE ? OR school LIKE ? OR location LIKE ?
            )
            ORDER BY name
            LIMIT ?
        """, (like_pattern, like_pattern, like_pattern, limit)).fetchall()
    
    return [_row_to_dict(r) for r in rows]


def list_all_bosses(conn: sqlite3.Connection, limit: int = 500) -> List[Dict]:
    """List all active bosses (lightweight - no raw_html)."""
    rows = conn.execute("""
        SELECT id, name, health, rank, school, location, last_updated_at
        FROM bosses WHERE is_active = 1
        ORDER BY name
        LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_boss_names(conn: sqlite3.Connection) -> List[str]:
    """Get all boss names for autocomplete / OCR matching."""
    rows = conn.execute(
        "SELECT name FROM bosses WHERE is_active = 1 ORDER BY name"
    ).fetchall()
    return [r['name'] for r in rows]


def get_stats(conn: sqlite3.Connection) -> Dict:
    """Get database statistics."""
    total = conn.execute("SELECT COUNT(*) as c FROM bosses").fetchone()['c']
    active = conn.execute("SELECT COUNT(*) as c FROM bosses WHERE is_active = 1").fetchone()['c']
    errors = conn.execute("SELECT COUNT(*) as c FROM bosses WHERE scrape_error != ''").fetchone()['c']
    
    schools = conn.execute("""
        SELECT school, COUNT(*) as c FROM bosses 
        WHERE is_active = 1 GROUP BY school ORDER BY c DESC
    """).fetchall()
    
    return {
        'total': total,
        'active': active,
        'errors': errors,
        'schools': {r['school']: r['c'] for r in schools}
    }


def get_stale_bosses(conn: sqlite3.Connection, max_age_hours: int = 24) -> List[Dict]:
    """Get bosses that haven't been checked recently."""
    cutoff = time.time() - (max_age_hours * 3600)
    rows = conn.execute("""
        SELECT id, name, wiki_path, url, last_checked_at
        FROM bosses WHERE is_active = 1 AND last_checked_at < ?
        ORDER BY last_checked_at ASC
        LIMIT 100
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def delete_boss(conn: sqlite3.Connection, boss_name: str):
    """Permanently delete a boss from the database."""
    conn.execute("DELETE FROM bosses WHERE name = ? COLLATE NOCASE", (boss_name,))
    conn.commit()


def get_boss_raw_wikitext(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Get the raw wikitext (stored in raw_html column) for a single boss."""
    row = conn.execute(
        "SELECT raw_html FROM bosses WHERE name = ? COLLATE NOCASE AND is_active = 1",
        (name,)
    ).fetchone()
    return row['raw_html'] if row and row['raw_html'] else None


def get_all_boss_raw_wikitext(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Get (name, raw_html) for all active bosses that have stored wikitext."""
    rows = conn.execute(
        "SELECT name, raw_html FROM bosses WHERE is_active = 1 AND raw_html != '' ORDER BY name"
    ).fetchall()
    return [(r['name'], r['raw_html']) for r in rows]


def get_boss_raw_wikitext_by_location(conn: sqlite3.Connection, prefix: str) -> List[Tuple[str, str]]:
    """Get (name, raw_html) for bosses matching a location prefix."""
    rows = conn.execute(
        "SELECT name, raw_html FROM bosses WHERE is_active = 1 AND raw_html != '' "
        "AND (location = ? OR location LIKE ?) ORDER BY name",
        (prefix, prefix + " >%")
    ).fetchall()
    return [(r['name'], r['raw_html']) for r in rows]


def delete_bosses_by_location_prefix(conn: sqlite3.Connection, prefix: str) -> int:
    """
    Delete all bosses whose location starts with `prefix`.
    Used for deleting all bosses in a world or area subtree.
    e.g. prefix="Wizard City" removes all WC bosses,
         prefix="Wizard City > Unicorn Way" removes only that area.
    Returns the number of deleted bosses.
    """
    # Match exact prefix or prefix followed by " >"
    cur = conn.execute(
        "DELETE FROM bosses WHERE location = ? OR location LIKE ?",
        (prefix, prefix + " >%")
    )
    deleted = cur.rowcount
    conn.commit()
    return deleted


def get_boss_names_by_location_prefix(conn: sqlite3.Connection, prefix: str) -> List[str]:
    """Return names of all bosses whose location matches the given prefix."""
    rows = conn.execute(
        "SELECT name FROM bosses WHERE location = ? OR location LIKE ? ORDER BY name",
        (prefix, prefix + " >%")
    ).fetchall()
    return [r['name'] for r in rows]


def list_bosses_by_location(conn: sqlite3.Connection) -> List[Dict]:
    """
    Return all active bosses with parsed location parts and cheats flag.
    Each entry: {name, school, health, rank, has_cheats, loc_parts: [world, area, subarea, ...]}
    """
    rows = conn.execute("""
        SELECT name, health, rank, school, location, cheats_json
        FROM bosses WHERE is_active = 1
        ORDER BY location, name
    """).fetchall()

    results = []
    for r in rows:
        loc = r['location'] or 'Unknown'
        parts = [p.strip() for p in loc.split('>') if p.strip()]
        if not parts:
            parts = ['Unknown']

        has_cheats = False
        cj = r['cheats_json']
        if cj:
            try:
                has_cheats = len(json.loads(cj)) > 0
            except Exception:
                pass

        results.append({
            'name': r['name'],
            'school': r['school'],
            'health': r['health'],
            'rank': r['rank'],
            'has_cheats': has_cheats,
            'loc_parts': parts,
        })

    return results


# ═══════════════════════════════════════════════════════════════
# ROUND COUNTER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def init_round_counters(conn: sqlite3.Connection):
    """Create round counter tables if not present."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS round_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            rounds_json TEXT DEFAULT '[]',
            created_at REAL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS round_counter_bosses (
            counter_id INTEGER NOT NULL REFERENCES round_counters(id) ON DELETE CASCADE,
            boss_name TEXT NOT NULL COLLATE NOCASE,
            PRIMARY KEY (counter_id, boss_name)
        );
    """)
    conn.commit()
    init_guides(conn)


def upsert_round_counter(conn: sqlite3.Connection, data: Dict) -> int:
    """Insert or update a round counter. Returns counter id."""
    now = time.time()
    rounds_json = json.dumps(data.get('rounds', []), ensure_ascii=False)

    if data.get('id'):
        conn.execute("""
            UPDATE round_counters SET name=?, description=?, rounds_json=?, updated_at=?
            WHERE id=?
        """, (data['name'], data.get('description', ''), rounds_json, now, data['id']))
        cid = data['id']
    else:
        cur = conn.execute("""
            INSERT INTO round_counters (name, description, rounds_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (data['name'], data.get('description', ''), rounds_json, now, now))
        cid = cur.lastrowid

    # Update boss links
    conn.execute("DELETE FROM round_counter_bosses WHERE counter_id=?", (cid,))
    for boss_name in data.get('linked_bosses', []):
        conn.execute(
            "INSERT OR IGNORE INTO round_counter_bosses (counter_id, boss_name) VALUES (?, ?)",
            (cid, boss_name)
        )
    conn.commit()
    return cid


def get_round_counter(conn: sqlite3.Connection, counter_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM round_counters WHERE id=?", (counter_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['rounds'] = json.loads(d.get('rounds_json') or '[]')
    del d['rounds_json']
    linked = conn.execute(
        "SELECT boss_name FROM round_counter_bosses WHERE counter_id=?", (counter_id,)
    ).fetchall()
    d['linked_bosses'] = [r['boss_name'] for r in linked]
    return d


def list_round_counters(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute("SELECT * FROM round_counters ORDER BY updated_at DESC").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d['rounds'] = json.loads(d.get('rounds_json') or '[]')
        del d['rounds_json']
        linked = conn.execute(
            "SELECT boss_name FROM round_counter_bosses WHERE counter_id=?", (d['id'],)
        ).fetchall()
        d['linked_bosses'] = [r['boss_name'] for r in linked]
        result.append(d)
    return result


def get_counters_for_boss(conn: sqlite3.Connection, boss_name: str) -> List[Dict]:
    rows = conn.execute("""
        SELECT rc.* FROM round_counters rc
        JOIN round_counter_bosses rcb ON rc.id = rcb.counter_id
        WHERE rcb.boss_name = ? COLLATE NOCASE
        ORDER BY rc.updated_at DESC
    """, (boss_name,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d['rounds'] = json.loads(d.get('rounds_json') or '[]')
        del d['rounds_json']
        linked = conn.execute(
            "SELECT boss_name FROM round_counter_bosses WHERE counter_id=?", (d['id'],)
        ).fetchall()
        d['linked_bosses'] = [r['boss_name'] for r in linked]
        result.append(d)
    return result


def delete_round_counter(conn: sqlite3.Connection, counter_id: int):
    conn.execute("DELETE FROM round_counters WHERE id=?", (counter_id,))
    conn.commit()


# ═══════════════════════════════════════════════════════════════
# GUIDE FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def init_guides(conn: sqlite3.Connection):
    """Create guides tables if not present."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            free_text TEXT DEFAULT '',
            schools_json TEXT DEFAULT '["Fire","Ice","Storm","Myth"]',
            table_data_json TEXT DEFAULT '{}',
            num_rounds INTEGER DEFAULT 3,
            created_at REAL,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS guide_bosses (
            guide_id INTEGER NOT NULL REFERENCES guides(id) ON DELETE CASCADE,
            boss_name TEXT NOT NULL COLLATE NOCASE,
            PRIMARY KEY (guide_id, boss_name)
        );
    """)
    conn.commit()


def upsert_guide(conn: sqlite3.Connection, data: Dict) -> int:
    now = time.time()
    schools_json = json.dumps(data.get('schools', ['Fire', 'Ice', 'Storm', 'Myth']), ensure_ascii=False)
    table_data_json = json.dumps(data.get('table_data', {}), ensure_ascii=False)
    num_rounds = data.get('num_rounds', 3)

    if data.get('id'):
        conn.execute("""
            UPDATE guides SET name=?, free_text=?, schools_json=?, table_data_json=?,
                              num_rounds=?, updated_at=?
            WHERE id=?
        """, (data['name'], data.get('free_text', ''), schools_json, table_data_json,
              num_rounds, now, data['id']))
        gid = data['id']
    else:
        cur = conn.execute("""
            INSERT INTO guides (name, free_text, schools_json, table_data_json, num_rounds, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (data['name'], data.get('free_text', ''), schools_json, table_data_json, num_rounds, now, now))
        gid = cur.lastrowid

    conn.execute("DELETE FROM guide_bosses WHERE guide_id=?", (gid,))
    for boss_name in data.get('linked_bosses', []):
        conn.execute(
            "INSERT OR IGNORE INTO guide_bosses (guide_id, boss_name) VALUES (?, ?)",
            (gid, boss_name)
        )
    conn.commit()
    return gid


def get_guide(conn: sqlite3.Connection, guide_id: int) -> Optional[Dict]:
    row = conn.execute("SELECT * FROM guides WHERE id=?", (guide_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['schools'] = json.loads(d.get('schools_json') or '[]')
    d['table_data'] = json.loads(d.get('table_data_json') or '{}')
    del d['schools_json'], d['table_data_json']
    linked = conn.execute("SELECT boss_name FROM guide_bosses WHERE guide_id=?", (guide_id,)).fetchall()
    d['linked_bosses'] = [r['boss_name'] for r in linked]
    return d


def list_guides(conn: sqlite3.Connection) -> List[Dict]:
    rows = conn.execute("SELECT * FROM guides ORDER BY updated_at DESC").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d['schools'] = json.loads(d.get('schools_json') or '[]')
        d['table_data'] = json.loads(d.get('table_data_json') or '{}')
        del d['schools_json'], d['table_data_json']
        linked = conn.execute("SELECT boss_name FROM guide_bosses WHERE guide_id=?", (d['id'],)).fetchall()
        d['linked_bosses'] = [r['boss_name'] for r in linked]
        result.append(d)
    return result


def get_guides_for_boss(conn: sqlite3.Connection, boss_name: str) -> List[Dict]:
    rows = conn.execute("""
        SELECT g.* FROM guides g
        JOIN guide_bosses gb ON g.id = gb.guide_id
        WHERE gb.boss_name = ? COLLATE NOCASE
        ORDER BY g.updated_at DESC
    """, (boss_name,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d['schools'] = json.loads(d.get('schools_json') or '[]')
        d['table_data'] = json.loads(d.get('table_data_json') or '{}')
        del d['schools_json'], d['table_data_json']
        linked = conn.execute("SELECT boss_name FROM guide_bosses WHERE guide_id=?", (d['id'],)).fetchall()
        d['linked_bosses'] = [r['boss_name'] for r in linked]
        result.append(d)
    return result


def delete_guide(conn: sqlite3.Connection, guide_id: int):
    conn.execute("DELETE FROM guides WHERE id=?", (guide_id,))
    conn.commit()


def _row_to_dict(row) -> Dict:
    """Convert a database row to a rich dictionary with parsed JSON."""
    if row is None:
        return None
    d = dict(row)
    # Parse JSON fields
    for key in ['cheats_json', 'battle_stats_json', 'spells_json', 
                'drops_json', 'minions_json', 'resistances_json']:
        if key in d and d[key]:
            try:
                parsed_key = key.replace('_json', '')
                d[parsed_key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key.replace('_json', '')] = [] if 'json' in key and key != 'battle_stats_json' else {}
    # Remove raw HTML from search results for memory efficiency
    if 'raw_html' in d:
        d['has_raw_html'] = bool(d['raw_html'])
        del d['raw_html']
    return d
