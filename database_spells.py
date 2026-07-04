"""
database_spells.py
══════════════════
SQLite schema, CRUD helpers for:
  • spells            — scraped spell data + OCR fields + images
  • spell_spellement_paths — per-spell spellement upgrade tiers
  • decks             — named deck presets (school + tag)
  • deck_cards        — cards inside each deck
  • character_deck_links  — N:M wizard ↔ deck
  • character_gear_links  — N:M wizard ↔ gear_loadout

All tables live inside the shared boss_wiki.db so one connection
sees everything (same WAL-checkpoint-safe pattern as database_calc.py).
"""

import sqlite3
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

DB_PATH = Path(__file__).parent / "boss_wiki.db"

# ── Schools ──────────────────────────────────────────────────────────
SPELL_SCHOOLS = [
    "Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance",
    "Star", "Moon", "Sun", "Shadow",
]

DECK_TAGS = [
    "Boss Deck", "Mob Deck", "Tank", "Healing",
    "Farming", "PvP", "Solo", "Jade", "Other",
]

SCHOOL_COLORS = {
    "Fire":    "#e05a00",
    "Ice":     "#4db8ff",
    "Storm":   "#9b59b6",
    "Myth":    "#d4ac0d",
    "Life":    "#27ae60",
    "Death":   "#8e44ad",
    "Balance": "#c8a000",
    "Star":    "#f0c040",
    "Moon":    "#a0a0d0",
    "Sun":     "#ffaa00",
    "Shadow":  "#5d6d9e",
}

# ── Pip sort key ──────────────────────────────────────────────────────
def pip_sort_key(pip_str: str) -> int:
    """Sort order: 0 → X → 1 → 2 → 3 …"""
    s = str(pip_str).strip().lower().split()[0] if pip_str else "0"
    if s == "0":
        return 0
    if s in ("x", "?"):
        return 1
    try:
        return int(s) + 2
    except ValueError:
        return 998


def get_connection(db_path: str = None) -> sqlite3.Connection:
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ═══════════════════════════════════════════════════════════════════════
# SCHEMA INIT
# ═══════════════════════════════════════════════════════════════════════

def init_spell_tables(conn: sqlite3.Connection):
    """Create all spell + deck tables if they don't exist. Idempotent."""
    conn.executescript("""
        -- ── Spells ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS spells (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
            wiki_path           TEXT DEFAULT '',
            school              TEXT NOT NULL DEFAULT 'Unknown',
            pip_cost            TEXT DEFAULT '0',
            school_pip_cost     INTEGER DEFAULT 0,
            accuracy            INTEGER DEFAULT 0,
            spell_type          TEXT DEFAULT '',
            pvp                 INTEGER DEFAULT 0,
            description         TEXT DEFAULT '',
            where_to_train      TEXT DEFAULT '',
            training_sources_json TEXT DEFAULT '[]',
            spellement_paths_json TEXT DEFAULT '[]',
            -- OCR extracted values
            ocr_raw             TEXT DEFAULT '',
            ocr_damage          TEXT DEFAULT '',
            ocr_effect          TEXT DEFAULT '',
            -- Image
            image_path          TEXT DEFAULT '',
            -- Raw wikitext for re-parsing
            raw_wikitext        TEXT DEFAULT '',
            -- Metadata
            first_scraped_at    REAL,
            last_updated_at     REAL
        );
        CREATE INDEX IF NOT EXISTS idx_spells_name   ON spells(name);
        CREATE INDEX IF NOT EXISTS idx_spells_school ON spells(school);
        CREATE INDEX IF NOT EXISTS idx_spells_pip    ON spells(pip_cost);

        -- ── Spellement Paths ──────────────────────────────────────
        CREATE TABLE IF NOT EXISTS spell_spellement_paths (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            spell_id   INTEGER NOT NULL REFERENCES spells(id) ON DELETE CASCADE,
            tier       INTEGER NOT NULL DEFAULT 1,
            description TEXT DEFAULT '',
            damage     TEXT DEFAULT '',
            effect     TEXT DEFAULT '',
            image_path TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ssp_spell ON spell_spellement_paths(spell_id);

        -- ── Decks ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS decks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            school      TEXT NOT NULL DEFAULT 'Fire',
            tag         TEXT NOT NULL DEFAULT '',
            description TEXT DEFAULT '',
            created_at  REAL,
            updated_at  REAL
        );
        CREATE INDEX IF NOT EXISTS idx_decks_school ON decks(school);

        -- ── Deck Cards ────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS deck_cards (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            deck_id     INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            spell_name  TEXT NOT NULL,
            spell_school TEXT DEFAULT '',
            quantity    INTEGER NOT NULL DEFAULT 1,
            is_side_deck INTEGER NOT NULL DEFAULT 0,
            sort_order  INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_dc_deck ON deck_cards(deck_id);

        -- ── Character ↔ Deck links ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS character_deck_links (
            wizard_id  INTEGER NOT NULL,
            deck_id    INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
            PRIMARY KEY (wizard_id, deck_id)
        );

        -- ── Character ↔ Gear links ─────────────────────────────────
        CREATE TABLE IF NOT EXISTS character_gear_links (
            wizard_id   INTEGER NOT NULL,
            loadout_id  INTEGER NOT NULL,
            PRIMARY KEY (wizard_id, loadout_id)
        );

        -- ── Icon-description presets (the manageable icon legend) ───
        CREATE TABLE IF NOT EXISTS icon_presets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
            description TEXT NOT NULL DEFAULT '',
            image_path  TEXT DEFAULT '',
            sort_order  INTEGER DEFAULT 0
        );

        -- ── Per-spell attached icon descriptions ─────────────────────
        CREATE TABLE IF NOT EXISTS spell_icon_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            spell_id    INTEGER NOT NULL REFERENCES spells(id) ON DELETE CASCADE,
            icon_preset_id INTEGER NOT NULL REFERENCES icon_presets(id) ON DELETE CASCADE,
            sort_order  INTEGER DEFAULT 0,
            auto        INTEGER DEFAULT 0,
            UNIQUE(spell_id, icon_preset_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sil_spell ON spell_icon_links(spell_id);
    """)
    conn.commit()
    _migrate_spell_schema(conn)


def _migrate_spell_schema(conn: sqlite3.Connection):
    """Add any columns added after the initial release."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(spells)").fetchall()}
    if not cols:
        return
    # Structured OCR fields (Gambit / DoT separation), added after the
    # initial OCR pass proved too coarse (bare numbers, no context).
    new_cols = {
        "ocr_dot_damage": "TEXT DEFAULT ''",
        "ocr_dot_rounds": "TEXT DEFAULT ''",
        "ocr_gambit":     "TEXT DEFAULT ''",
        "ocr_keywords":   "TEXT DEFAULT ''",
        "ocr_heal":        "TEXT DEFAULT ''",
        "ocr_heal_rounds": "TEXT DEFAULT ''",
        "ocr_divided":     "TEXT DEFAULT ''",
        "ocr_conditional": "TEXT DEFAULT ''",
        "ocr_clear_effect": "TEXT DEFAULT ''",
        "ocr_uncertain":    "INTEGER DEFAULT 0",
        # Was mapped in SpellParser._FIELD_MAP but had no storage column
        # or handler at all — silently dropped on every scrape. Added
        # when this was found during a "what can fetch actually give
        # us" audit.
        "shadow_pip_cost": "INTEGER DEFAULT 0",
        # Mapped in SpellParser (pvplevel → pvp_level) but likewise had no
        # storage column — the PvP level ("170+", "40+") was being dropped.
        "pvp_level": "TEXT DEFAULT ''",
        # Rendered-HTML-derived acquisition data (Semantic-MediaWiki blocks
        # that never appear in raw wikitext). Stored as JSON:
        #   training_info_json → {"sections":[{"title","categories":[…]}]}
        #   fusion_json        → [{"components":[…],"result":{…}}]
        "training_info_json": "TEXT DEFAULT ''",
        "fusion_json":        "TEXT DEFAULT ''",
    }
    for col, decl in new_cols.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE spells ADD COLUMN {col} {decl}")
    conn.commit()

    # ── spell_icon_links.auto ────────────────────────────────────────
    # Distinguishes auto-detected icon links (auto=1) from ones the user
    # added by hand (auto=0). Lets Reparse rebuild ONLY the auto set,
    # leaving manual picks alone. On first migration, existing links are
    # backfilled to auto=1: the detection feature is what created them, so
    # a subsequent Reparse can correct any that the old, looser logic
    # mis-linked. (Anything added via "+ Add" AFTER this update is stored
    # as manual and is preserved across future Reparses.)
    sil_cols = {r[1] for r in
                conn.execute("PRAGMA table_info(spell_icon_links)").fetchall()}
    if sil_cols and "auto" not in sil_cols:
        conn.execute("ALTER TABLE spell_icon_links ADD COLUMN auto INTEGER DEFAULT 0")
        conn.execute("UPDATE spell_icon_links SET auto=1")
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# SPELL CRUD
# ═══════════════════════════════════════════════════════════════════════

def upsert_spell(conn: sqlite3.Connection, data: dict) -> int:
    """Insert or update a spell. Returns the spell id."""
    now = time.time()
    existing = conn.execute(
        "SELECT id, first_scraped_at FROM spells WHERE name=? COLLATE NOCASE",
        (data["name"],),
    ).fetchone()

    training = data.get("training_sources", [])
    spellements = data.get("spellement_paths", [])

    # Rendered-HTML acquisition data. When the caller didn't supply these
    # (e.g. a --reparse of a spell whose HTML wasn't cached), pass None so the
    # COALESCE below preserves whatever is already stored instead of wiping it.
    train_info_json = (json.dumps(data["training_info"], ensure_ascii=False)
                       if "training_info" in data else None)
    fusion_json_val = (json.dumps(data["fusion_formulae"], ensure_ascii=False)
                       if "fusion_formulae" in data else None)

    if existing:
        conn.execute("""
            UPDATE spells SET
                wiki_path=?, school=?, pip_cost=?, school_pip_cost=?, shadow_pip_cost=?,
                accuracy=?, spell_type=?, pvp=?, pvp_level=?, description=?,
                where_to_train=?, training_sources_json=?,
                spellement_paths_json=?,
                ocr_raw=?, ocr_damage=?, ocr_effect=?,
                ocr_dot_damage=?, ocr_dot_rounds=?, ocr_gambit=?, ocr_keywords=?,
                ocr_heal=?, ocr_heal_rounds=?, ocr_divided=?, ocr_conditional=?,
                ocr_clear_effect=?, ocr_uncertain=?,
                image_path=?, raw_wikitext=?,
                training_info_json=COALESCE(?, training_info_json),
                fusion_json=COALESCE(?, fusion_json),
                last_updated_at=?
            WHERE id=?
        """, (
            data.get("wiki_path", ""),
            data.get("school", "Unknown"),
            str(data.get("pip_cost", "0")),
            int(data.get("school_pip_cost", 0)),
            int(data.get("shadow_pip_cost", 0)),
            int(data.get("accuracy", 0)),
            data.get("spell_type", ""),
            1 if data.get("pvp") else 0,
            str(data.get("pvp_level", "") or ""),
            data.get("description", ""),
            data.get("where_to_train", ""),
            json.dumps(training, ensure_ascii=False),
            json.dumps(spellements, ensure_ascii=False),
            data.get("ocr_raw", ""),
            data.get("ocr_damage", ""),
            data.get("ocr_effect", ""),
            data.get("ocr_dot_damage", ""),
            data.get("ocr_dot_rounds", ""),
            data.get("ocr_gambit", ""),
            data.get("ocr_keywords", ""),
            data.get("ocr_heal", ""),
            data.get("ocr_heal_rounds", ""),
            data.get("ocr_divided", ""),
            data.get("ocr_conditional", ""),
            data.get("ocr_clear_effect", ""),
            1 if data.get("ocr_uncertain") else 0,
            data.get("image_path", ""),
            data.get("raw_wikitext", ""),
            train_info_json,
            fusion_json_val,
            now,
            existing["id"],
        ))
        spell_id = existing["id"]
    else:
        cur = conn.execute("""
            INSERT INTO spells
                (name, wiki_path, school, pip_cost, school_pip_cost, shadow_pip_cost,
                 accuracy, spell_type, pvp, pvp_level, description, where_to_train,
                 training_sources_json, spellement_paths_json,
                 ocr_raw, ocr_damage, ocr_effect,
                 ocr_dot_damage, ocr_dot_rounds, ocr_gambit, ocr_keywords,
                 ocr_heal, ocr_heal_rounds, ocr_divided, ocr_conditional,
                 ocr_clear_effect, ocr_uncertain,
                 image_path, raw_wikitext, training_info_json, fusion_json,
                 first_scraped_at, last_updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["name"],
            data.get("wiki_path", ""),
            data.get("school", "Unknown"),
            str(data.get("pip_cost", "0")),
            int(data.get("school_pip_cost", 0)),
            int(data.get("shadow_pip_cost", 0)),
            int(data.get("accuracy", 0)),
            data.get("spell_type", ""),
            1 if data.get("pvp") else 0,
            str(data.get("pvp_level", "") or ""),
            data.get("description", ""),
            data.get("where_to_train", ""),
            json.dumps(training, ensure_ascii=False),
            json.dumps(spellements, ensure_ascii=False),
            data.get("ocr_raw", ""),
            data.get("ocr_damage", ""),
            data.get("ocr_effect", ""),
            data.get("ocr_dot_damage", ""),
            data.get("ocr_dot_rounds", ""),
            data.get("ocr_gambit", ""),
            data.get("ocr_keywords", ""),
            data.get("ocr_heal", ""),
            data.get("ocr_heal_rounds", ""),
            data.get("ocr_divided", ""),
            data.get("ocr_conditional", ""),
            data.get("ocr_clear_effect", ""),
            1 if data.get("ocr_uncertain") else 0,
            data.get("image_path", ""),
            data.get("raw_wikitext", ""),
            train_info_json or "",
            fusion_json_val or "",
            now,
            now,
        ))
        spell_id = cur.lastrowid

    # Re-insert spellement path rows
    conn.execute("DELETE FROM spell_spellement_paths WHERE spell_id=?", (spell_id,))
    for i, path in enumerate(spellements):
        if isinstance(path, dict):
            conn.execute("""
                INSERT INTO spell_spellement_paths
                    (spell_id, tier, description, damage, effect, image_path, sort_order)
                VALUES (?,?,?,?,?,?,?)
            """, (
                spell_id,
                path.get("tier", i + 1),
                path.get("description", ""),
                path.get("damage", ""),
                path.get("effect", ""),
                path.get("image_path", ""),
                i,
            ))

    conn.commit()
    return spell_id


def get_spell(conn: sqlite3.Connection, name: str) -> Optional[dict]:
    """Fetch a spell by name (case-insensitive). Returns dict or None."""
    row = conn.execute(
        "SELECT * FROM spells WHERE name=? COLLATE NOCASE", (name,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["training_sources"] = _load_json(d.pop("training_sources_json", "[]"), [])
    d["spellement_paths"] = _load_json(d.pop("spellement_paths_json", "[]"), [])
    # Rendered-HTML acquisition data (may be absent on older rows).
    d["training_info"]   = _load_json(d.pop("training_info_json", "") or "", {"sections": []})
    d["fusion_formulae"] = _load_json(d.pop("fusion_json", "") or "", [])
    # Attach spellement path rows from the dedicated table
    path_rows = conn.execute(
        "SELECT * FROM spell_spellement_paths WHERE spell_id=? ORDER BY sort_order",
        (d["id"],),
    ).fetchall()
    if path_rows:
        d["spellement_paths"] = [dict(r) for r in path_rows]
    return d


def list_spells(
    conn: sqlite3.Connection,
    school: str = None,
    search: str = None,
) -> List[dict]:
    """Return spells ordered by pip_cost (0,X,1,2…) then name."""
    q = "SELECT * FROM spells WHERE 1=1"
    params: list = []
    if school and school != "All":
        q += " AND school=?"
        params.append(school)
    if search:
        q += " AND name LIKE ?"
        params.append(f"%{search}%")
    q += " ORDER BY name"
    rows = conn.execute(q, params).fetchall()
    result = [dict(r) for r in rows]
    for d in result:
        d["training_sources"] = _load_json(d.pop("training_sources_json", "[]"), [])
        d["spellement_paths"] = _load_json(d.pop("spellement_paths_json", "[]"), [])
        # Decoded here too (not just get_spell) so the browser's filter panel
        # can filter on training type / fusion without a per-row DB round-trip.
        d["training_info"]   = _load_json(d.pop("training_info_json", "") or "", {"sections": []})
        d["fusion_formulae"] = _load_json(d.pop("fusion_json", "") or "", [])
    # Sort by pip_cost
    result.sort(key=lambda s: (pip_sort_key(s.get("pip_cost", "0")), s["name"].lower()))
    return result


def get_spell_names(conn: sqlite3.Connection, school: str = None) -> List[str]:
    if school and school != "All":
        rows = conn.execute(
            "SELECT name FROM spells WHERE school=? ORDER BY name", (school,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT name FROM spells ORDER BY name").fetchall()
    return [r[0] for r in rows]


def delete_spell(conn: sqlite3.Connection, name: str):
    conn.execute("DELETE FROM spells WHERE name=? COLLATE NOCASE", (name,))
    conn.commit()


def get_spell_count(conn: sqlite3.Connection, school: str = None) -> int:
    if school and school != "All":
        return conn.execute(
            "SELECT COUNT(*) FROM spells WHERE school=?", (school,)
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM spells").fetchone()[0]


def get_spell_raw_wikitext(conn: sqlite3.Connection, name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT raw_wikitext FROM spells WHERE name=? COLLATE NOCASE", (name,)
    ).fetchone()
    return row[0] if row else None


# ═══════════════════════════════════════════════════════════════════════
# DECK CRUD
# ═══════════════════════════════════════════════════════════════════════

def upsert_deck(conn: sqlite3.Connection, data: dict) -> int:
    """Insert or update a deck. Returns deck id."""
    now = time.time()
    did = data.get("id")
    if did:
        conn.execute("""
            UPDATE decks SET name=?, school=?, tag=?, description=?, updated_at=?
            WHERE id=?
        """, (
            data.get("name", ""),
            data.get("school", "Fire"),
            data.get("tag", ""),
            data.get("description", ""),
            now, did,
        ))
    else:
        cur = conn.execute("""
            INSERT INTO decks (name, school, tag, description, created_at, updated_at)
            VALUES (?,?,?,?,?,?)
        """, (
            data.get("name", "Unnamed Deck"),
            data.get("school", "Fire"),
            data.get("tag", ""),
            data.get("description", ""),
            now, now,
        ))
        did = cur.lastrowid

    # Replace cards
    if "cards" in data:
        conn.execute("DELETE FROM deck_cards WHERE deck_id=?", (did,))
        for i, card in enumerate(data["cards"]):
            conn.execute("""
                INSERT INTO deck_cards
                    (deck_id, spell_name, spell_school, quantity, is_side_deck, sort_order)
                VALUES (?,?,?,?,?,?)
            """, (
                did,
                card.get("spell_name", ""),
                card.get("spell_school", ""),
                max(1, int(card.get("quantity", 1))),
                1 if card.get("is_side_deck") else 0,
                i,
            ))
    conn.commit()
    return did


def get_deck(conn: sqlite3.Connection, deck_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    cards = conn.execute(
        "SELECT * FROM deck_cards WHERE deck_id=? ORDER BY is_side_deck, sort_order",
        (deck_id,),
    ).fetchall()
    d["cards"] = [dict(c) for c in cards]
    return d


def list_decks(conn: sqlite3.Connection, school: str = None) -> List[dict]:
    if school and school != "All":
        rows = conn.execute(
            "SELECT * FROM decks WHERE school=? ORDER BY name", (school,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM decks ORDER BY name").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        cards = conn.execute(
            "SELECT * FROM deck_cards WHERE deck_id=? ORDER BY is_side_deck, sort_order",
            (d["id"],),
        ).fetchall()
        d["cards"] = [dict(c) for c in cards]
        result.append(d)
    return result


def delete_deck(conn: sqlite3.Connection, deck_id: int):
    conn.execute("DELETE FROM decks WHERE id=?", (deck_id,))
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# CHARACTER LINKS
# ═══════════════════════════════════════════════════════════════════════

def link_deck_to_wizard(conn: sqlite3.Connection, wizard_id: int, deck_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO character_deck_links (wizard_id, deck_id) VALUES (?,?)",
        (wizard_id, deck_id),
    )
    conn.commit()


def unlink_deck_from_wizard(conn: sqlite3.Connection, wizard_id: int, deck_id: int):
    conn.execute(
        "DELETE FROM character_deck_links WHERE wizard_id=? AND deck_id=?",
        (wizard_id, deck_id),
    )
    conn.commit()


def get_decks_for_wizard(conn: sqlite3.Connection, wizard_id: int) -> List[dict]:
    rows = conn.execute("""
        SELECT d.* FROM decks d
        JOIN character_deck_links l ON l.deck_id=d.id
        WHERE l.wizard_id=?
        ORDER BY d.name
    """, (wizard_id,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        cards = conn.execute(
            "SELECT * FROM deck_cards WHERE deck_id=? ORDER BY is_side_deck, sort_order",
            (d["id"],),
        ).fetchall()
        d["cards"] = [dict(c) for c in cards]
        result.append(d)
    return result


def link_gear_to_wizard(conn: sqlite3.Connection, wizard_id: int, loadout_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO character_gear_links (wizard_id, loadout_id) VALUES (?,?)",
        (wizard_id, loadout_id),
    )
    conn.commit()


def unlink_gear_from_wizard(conn: sqlite3.Connection, wizard_id: int, loadout_id: int):
    conn.execute(
        "DELETE FROM character_gear_links WHERE wizard_id=? AND loadout_id=?",
        (wizard_id, loadout_id),
    )
    conn.commit()


def get_gear_for_wizard(conn: sqlite3.Connection, wizard_id: int) -> List[dict]:
    """Return gear loadout dicts linked to this wizard."""
    rows = conn.execute("""
        SELECT g.* FROM gear_loadouts g
        JOIN character_gear_links l ON l.loadout_id=g.id
        WHERE l.wizard_id=?
        ORDER BY g.name
    """, (wizard_id,)).fetchall()
    return [dict(r) for r in rows]


def get_wizards_for_deck(conn: sqlite3.Connection, deck_id: int) -> List[dict]:
    rows = conn.execute("""
        SELECT w.* FROM wizards w
        JOIN character_deck_links l ON l.wizard_id=w.id
        WHERE l.deck_id=?
        ORDER BY w.name
    """, (deck_id,)).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# DECK SHARE CODE  (base64 encode/decode)
# ═══════════════════════════════════════════════════════════════════════

def export_deck_code(deck: dict) -> str:
    """Encode a deck as a compact base64 share code."""
    import base64
    payload = json.dumps({
        "n": deck.get("name", ""),
        "s": deck.get("school", ""),
        "t": deck.get("tag", ""),
        "d": deck.get("description", ""),
        "c": [
            (c["spell_name"], c.get("spell_school", ""),
             c.get("quantity", 1), 1 if c.get("is_side_deck") else 0)
            for c in deck.get("cards", [])
        ],
    }, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def import_deck_code(code: str) -> Optional[dict]:
    """Decode a base64 share code into a deck dict. Returns None on error."""
    import base64
    try:
        payload = json.loads(base64.b64decode(code.strip().encode("ascii")).decode("utf-8"))
        return {
            "name":        payload.get("n", "Imported Deck"),
            "school":      payload.get("s", "Fire"),
            "tag":         payload.get("t", ""),
            "description": payload.get("d", ""),
            "cards": [
                {
                    "spell_name":  c[0],
                    "spell_school": c[1] if len(c) > 1 else "",
                    "quantity":    c[2] if len(c) > 2 else 1,
                    "is_side_deck": bool(c[3]) if len(c) > 3 else False,
                }
                for c in payload.get("c", [])
            ],
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _load_json(text: str, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════
# TIER VARIANT HELPERS
# ═══════════════════════════════════════════════════════════════════════

import re as _re_mod

def is_tier_variant(name: str):
    """
    Returns (base_name, tier_label) if name matches 'Base (Tier Xa)' pattern.
    Returns (None, None) if it is not a tier variant.
    """
    m = _re_mod.match(r'^(.+?)\s+\(Tier\s+(\d+[a-zA-Z]?)\)\s*$', name, _re_mod.IGNORECASE)
    return (m.group(1).strip(), m.group(2)) if m else (None, None)


def list_spells_base_only(
    conn,
    school: str = None,
    search: str = None,
) -> List[dict]:
    """Like list_spells() but excludes tier variants (e.g. 'Spell (Tier 2a)')."""
    all_spells = list_spells(conn, school=school, search=search)
    return [s for s in all_spells if not is_tier_variant(s["name"])[0]]


def count_spells_by_school(conn) -> dict:
    """
    Base-spell counts per school (tier variants excluded), plus an "All" total.
    Used by the browser sidebar to show how many spells each school has.
    """
    rows = conn.execute("SELECT name, school FROM spells").fetchall()
    counts: dict = {}
    total = 0
    for r in rows:
        if is_tier_variant(r["name"])[0]:
            continue
        counts[r["school"]] = counts.get(r["school"], 0) + 1
        total += 1
    counts["All"] = total
    return counts


def get_all_spell_icon_links(conn) -> dict:
    """Map spell_id -> set of linked icon-preset names (for the icon filter)."""
    rows = conn.execute(
        "SELECT l.spell_id AS sid, p.name AS name "
        "FROM spell_icon_links l JOIN icon_presets p ON p.id = l.icon_preset_id"
    ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["sid"], set()).add(r["name"])
    return out


def get_tier_base_names(conn) -> set:
    """Set of base spell names that have at least one tier variant."""
    bases = set()
    for r in conn.execute("SELECT name FROM spells").fetchall():
        base, _tier = is_tier_variant(r["name"])
        if base:
            bases.add(base)
    return bases


def get_tier_variants(conn, base_name: str) -> List[dict]:
    """Return all tier variant spells for a given base spell name, sorted by name."""
    rows = conn.execute(
        "SELECT * FROM spells WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
        (f"{base_name} (Tier%",),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["training_sources"] = _load_json(d.pop("training_sources_json", "[]"), [])
        d["spellement_paths"] = _load_json(d.pop("spellement_paths_json", "[]"), [])
        result.append(d)
    return result


# ═══════════════════════════════════════════════════════════════════════
# BULK DELETE
# ═══════════════════════════════════════════════════════════════════════

def delete_spells_by_school(conn, school: str) -> int:
    """Delete all spells for a given school. Returns count deleted."""
    cur = conn.execute("DELETE FROM spells WHERE school=?", (school,))
    conn.commit()
    return cur.rowcount


def delete_all_spells(conn) -> int:
    """Delete every spell in the database. Returns count deleted."""
    cur = conn.execute("DELETE FROM spells")
    conn.commit()
    return cur.rowcount


# ═══════════════════════════════════════════════════════════════════════
# SPELL TEXT UPDATE (for OCR correction)
# ═══════════════════════════════════════════════════════════════════════

def update_spell_text(conn, spell_id: int, description: str = None,
                      ocr_damage: str = None, ocr_effect: str = None,
                      where_to_train: str = None, ocr_dot_damage: str = None,
                      ocr_dot_rounds: str = None, ocr_gambit: str = None,
                      ocr_heal: str = None, ocr_heal_rounds: str = None,
                      ocr_divided: str = None, ocr_conditional: str = None,
                      ocr_clear_effect: str = None):
    """Surgically update editable text fields on a spell row."""
    import time as _time
    updates = []
    params  = []
    if description is not None:
        updates.append("description=?")
        params.append(description)
    if ocr_damage is not None:
        updates.append("ocr_damage=?")
        params.append(ocr_damage)
    if ocr_dot_damage is not None:
        updates.append("ocr_dot_damage=?")
        params.append(ocr_dot_damage)
    if ocr_dot_rounds is not None:
        updates.append("ocr_dot_rounds=?")
        params.append(ocr_dot_rounds)
    if ocr_gambit is not None:
        updates.append("ocr_gambit=?")
        params.append(ocr_gambit)
    if ocr_heal is not None:
        updates.append("ocr_heal=?")
        params.append(ocr_heal)
    if ocr_heal_rounds is not None:
        updates.append("ocr_heal_rounds=?")
        params.append(ocr_heal_rounds)
    if ocr_divided is not None:
        updates.append("ocr_divided=?")
        params.append(ocr_divided)
    if ocr_conditional is not None:
        updates.append("ocr_conditional=?")
        params.append(ocr_conditional)
    if ocr_clear_effect is not None:
        updates.append("ocr_clear_effect=?")
        params.append(ocr_clear_effect)
        updates.append("ocr_uncertain=?")
        params.append(0)  # manual edit clears the "uncertain" flag
    if ocr_effect is not None:
        updates.append("ocr_effect=?")
        params.append(ocr_effect)
    if where_to_train is not None:
        updates.append("where_to_train=?")
        params.append(where_to_train)
    if not updates:
        return
    updates.append("last_updated_at=?")
    params.append(_time.time())
    params.append(spell_id)
    conn.execute(f"UPDATE spells SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()

# ═══════════════════════════════════════════════════════════════════════
# CORE FETCH-ONLY FIELD EDITOR  (right-side panel of Spell Detail)
# ═══════════════════════════════════════════════════════════════════════

def update_spell_core_fields(conn, spell_id: int, **fields):
    """
    Edit the fetch-only fields shown in the right-side panel of the
    Spell Detail view: school, pip_cost, school_pip_cost,
    shadow_pip_cost, accuracy, spell_type, pvp, description.

    Pass only the fields you want to change as keyword args, e.g.:
        update_spell_core_fields(conn, 5, accuracy=80, pvp=True)
    """
    import time as _time
    ALLOWED = {
        "school": "school", "pip_cost": "pip_cost",
        "school_pip_cost": "school_pip_cost",
        "shadow_pip_cost": "shadow_pip_cost",
        "accuracy": "accuracy", "spell_type": "spell_type",
        "pvp": "pvp", "pvp_level": "pvp_level", "description": "description",
    }
    updates, params = [], []
    for key, val in fields.items():
        if key not in ALLOWED:
            continue
        col = ALLOWED[key]
        if col == "pvp":
            val = 1 if val else 0
        elif col in ("school_pip_cost", "shadow_pip_cost", "accuracy"):
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = 0
        updates.append(f"{col}=?")
        params.append(val)
    if not updates:
        return
    updates.append("last_updated_at=?")
    params.append(_time.time())
    params.append(spell_id)
    conn.execute(f"UPDATE spells SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# ICON PRESETS  (the manageable icon → description legend)
# ═══════════════════════════════════════════════════════════════════════

def upsert_icon_preset(conn, data: dict) -> int:
    """Insert or update an icon preset. Returns its id."""
    existing = None
    if data.get("id"):
        existing = conn.execute(
            "SELECT id FROM icon_presets WHERE id=?", (data["id"],)
        ).fetchone()
    elif data.get("name"):
        existing = conn.execute(
            "SELECT id FROM icon_presets WHERE name=? COLLATE NOCASE", (data["name"],)
        ).fetchone()

    if existing:
        conn.execute("""
            UPDATE icon_presets SET name=?, description=?, image_path=?, sort_order=?
            WHERE id=?
        """, (
            data.get("name", ""), data.get("description", ""),
            data.get("image_path", ""), data.get("sort_order", 0),
            existing["id"],
        ))
        pid = existing["id"]
    else:
        cur = conn.execute("""
            INSERT INTO icon_presets (name, description, image_path, sort_order)
            VALUES (?,?,?,?)
        """, (
            data.get("name", ""), data.get("description", ""),
            data.get("image_path", ""), data.get("sort_order", 0),
        ))
        pid = cur.lastrowid
    conn.commit()
    return pid


def list_icon_presets(conn) -> List[dict]:
    rows = conn.execute(
        "SELECT * FROM icon_presets ORDER BY sort_order, name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_icon_preset(conn, preset_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM icon_presets WHERE id=?", (preset_id,)).fetchone()
    return dict(row) if row else None


def delete_icon_preset(conn, preset_id: int):
    conn.execute("DELETE FROM icon_presets WHERE id=?", (preset_id,))
    conn.commit()


def reorder_icon_presets(conn, ordered_ids: List[int]):
    """Persist a new display order for icon presets (drag-reorder)."""
    for i, pid in enumerate(ordered_ids):
        conn.execute("UPDATE icon_presets SET sort_order=? WHERE id=?", (i, pid))
    conn.commit()


def seed_icon_presets_if_empty(conn, presets: List[dict]):
    """
    Populate icon_presets from a bundled list (name, description,
    image_path) only if the table is currently empty — never overwrites
    user customizations on subsequent app starts.
    """
    count = conn.execute("SELECT COUNT(*) FROM icon_presets").fetchone()[0]
    if count > 0:
        return
    for i, p in enumerate(presets):
        upsert_icon_preset(conn, {
            "name": p["name"], "description": p["description"],
            "image_path": p.get("image_path", ""), "sort_order": i,
        })


# ═══════════════════════════════════════════════════════════════════════
# SPELL ↔ ICON LINKS  (which presets are attached to which spell)
# ═══════════════════════════════════════════════════════════════════════

def link_icon_to_spell(conn, spell_id: int, icon_preset_id: int, auto: bool = False):
    """
    Attach an icon preset to a spell. `auto=True` marks it as
    auto-detected (rebuilt by Reparse); `auto=False` (default, e.g. the
    "+ Add" button) marks it as a manual pick that Reparse leaves alone.
    If the link already exists its auto flag is left unchanged, so a
    manual pick is never silently downgraded to auto by a later
    auto-link pass.
    """
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM spell_icon_links WHERE spell_id=?",
        (spell_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO spell_icon_links (spell_id, icon_preset_id, sort_order, auto) "
        "VALUES (?,?,?,?)",
        (spell_id, icon_preset_id, next_order, 1 if auto else 0),
    )
    conn.commit()


def clear_auto_icon_links(conn, spell_id: int) -> int:
    """
    Remove only the auto-detected icon links for a spell (auto=1),
    leaving manual picks (auto=0) intact. Returns the number removed.
    Used by Reparse to rebuild the detected set from scratch.
    """
    cur = conn.execute(
        "DELETE FROM spell_icon_links WHERE spell_id=? AND auto=1", (spell_id,)
    )
    conn.commit()
    return cur.rowcount


def unlink_icon_from_spell(conn, spell_id: int, icon_preset_id: int):
    conn.execute(
        "DELETE FROM spell_icon_links WHERE spell_id=? AND icon_preset_id=?",
        (spell_id, icon_preset_id),
    )
    conn.commit()


def get_icons_for_spell(conn, spell_id: int) -> List[dict]:
    """Return the icon presets attached to a spell, in display order."""
    rows = conn.execute("""
        SELECT ip.*, sil.sort_order AS link_order
        FROM icon_presets ip
        JOIN spell_icon_links sil ON sil.icon_preset_id = ip.id
        WHERE sil.spell_id = ?
        ORDER BY sil.sort_order
    """, (spell_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Text → icon-preset keyword extraction ──────────────────────────────
# Maps an icon-preset DISPLAY NAME (must match icon_preset_seed exactly,
# case-insensitively) to a regex that identifies it in clean spell text
# (the wiki description is the best source; card OCR is a noisy fallback).
#
# This is the fix for "only one or no icons get detected": the wiki
# description is grammatical prose that names the mechanics directly
# ("... applies a -50% Damage Ward on caster"), but the old pipeline only
# scanned it for damage/heal NUMBERS and a tiny keyword set. Scanning it
# against these patterns lets a single spell resolve to several icons
# (Damage + Ward + Caster (Self), etc.) the way its card actually shows.
ICON_KEYWORD_PATTERNS = {
    # Order matters: more specific entries come first so suppression
    # (below) can drop the generic duplicate they imply.
    "Harmful Aura":         r"harmful\s+aura",
    "Heal Over Time":       r"heal[s]?\s+\d[\d,]*\s+over\s+\d|heal over time|\bhot\b",
    "Damage Over Time":     r"\d[\d,]*\s+(?:\w+\s+)?damage over\s+\d|damage over time|\bdot\b",
    "Power Pip":            r"power\s+pip",
    "Shadow Pip":           r"shadow\s+pip",
    "Stun Resistance":      r"stun\s+resist",
    "Drain / Steal Health": r"steal[s]?\b[^.]{0,30}\bhealth\b|drain[s]?\b[^.]{0,30}\bhealth\b|convert[s]?\b[^.]{0,30}\bhealth\b|steals?\s+health",
    "Damage":               r"\bdeal(?:s|t)?\b|damage\s+to\b|damage\s+over\b",
    "Heal":                 r"\bheal(?:s|ed|ing)?\b|restore[sd]?\s+health",
    # Ward also fires on the wiki-prose form "-N% to the next incoming
    # damage spell" and on any "…shield" wording, not just the word "ward".
    "Ward":                 r"\bward\b|\bshield\b|-\s*\d+\s*%[^.]{0,50}next[^.]{0,30}incoming",
    "Absorb":               r"\babsorb",
    # Blade also fires on the wiki-prose form "+N% to your next outgoing
    # damage spell" — the mechanic that never literally says "blade".
    "Blade":                r"\bblade\b|\+\s*\d+\s*%[^.]{0,50}(?:to\s+your\s+next|next[^.]{0,30}outgoing)",
    "Charm":                r"\bcharm\b",
    # Trap also fires on "+N% to the next incoming damage spell".
    "Trap":                 r"\btrap\b|\+\s*\d+\s*%[^.]{0,50}next[^.]{0,30}incoming",
    "Jinx":                 r"\bjinx\b",
    # Curse also covers the common "weakness" wording (a damage curse) and
    # the prose "-N% to next outgoing damage".
    "Curse":                r"\bcurse\b|\bweakness\b|\bweaken(?:s|ed)?\b|-\s*\d+\s*%[^.]{0,50}next[^.]{0,30}outgoing",
    "Dispel":               r"\bdispel\b|remove[sd]?\s+(?:a\s+)?(?:harmful\s+)?"
                            r"(?:charm|ward|aura|hex|trap|jinx|curse|blade|effect|shield)",
    "Aura":                 r"\baura\b",
    "Stun":                 r"\bstun(?:s|ned|ning)?\b",
    "Minion":               r"\bminion\b|\bsummon",
    "Polymorph":            r"\bpolymorph\b",
    "Critical":             r"\bcritical\b",
    "Block":                r"\bblock\b",
    "Armor Piercing":       r"\bpierc",
    "Enchantment":          r"\benchant",
    "Afterlife":            r"\bafterlife\b",
    "Threat":               r"\bthreat\b",
    "Resistance":           r"\bresist(?:ance)?\b",
    "Global":               r"\bglobal\b",
    "Incoming":             r"\bincoming\b",
    "Outgoing":             r"\boutgoing\b",
    "Rounds":               r"\bfor\s+\d+\s+rounds?\b|over\s+the\s+next\s+\d+\s+rounds?|each\s+round\b|per\s+round\b",
    "Caster (Self)":        r"\bcaster\b|\byourself\b|\bto\s+self\b|\bon\s+self\b",
    "All Enemies":          r"all\s+enem|every\s+enem",
    "All Friends":          r"all\s+(?:friend|allies|ally)|every\s+(?:friend|all)",
    # "Pip" only when the EFFECT manipulates pips (gain/steal/lose/give a
    # pip) — NOT for the pip COST, which every spell has and which shows as
    # a number, not a pip icon in the effect area.
    "Pip":                  r"(?:gain|give|grant|steal|lose|remove|add)[s]?\b[^.]{0,25}\bpips?\b|\bextra\s+pips?\b|\bpip\s+back\b",
}

# When the key on the left is detected, drop the generic icons on the
# right that it already implies (avoids e.g. both "Aura" and "Harmful
# Aura" on the same card).
_ICON_KEYWORD_SUPPRESS = {
    "Harmful Aura": ["Aura"],
    "Power Pip":    ["Pip"],
    "Shadow Pip":   ["Pip"],
}


def extract_icon_keyword_labels(text: str) -> List[str]:
    """
    Scan free text (wiki description and/or card OCR) and return the list
    of icon-preset display names it implies, in a stable order and with
    generic/specific duplicates suppressed. Safe to call on empty text.
    """
    if not text:
        return []
    low = " " + text.lower() + " "
    found = []
    for name, pat in ICON_KEYWORD_PATTERNS.items():
        if re.search(pat, low):
            found.append(name)
    for trigger, victims in _ICON_KEYWORD_SUPPRESS.items():
        if trigger in found:
            found = [f for f in found if f not in victims]
    return found


# ── Structured-field → icon-preset derivation ───────────────────────────
# The spell's own fields (name, type, school, pip costs, pvp flag) are far
# more reliable than free-text OCR. Mining them means a card like "Aegis
# Deathblade" (Charm type, Death school, 1 pip) auto-populates Blade +
# Charm + Death School + Pip even when the description never says "blade".

_SCHOOL_TO_PRESET = {
    "fire": "Fire School", "ice": "Ice School", "storm": "Storm School",
    "myth": "Myth School", "life": "Life School", "death": "Death School",
    "balance": "Balance School", "sun": "Sun School", "moon": "Moon School",
    "star": "Star School",
    # "shadow" has no dedicated School preset — intentionally omitted.
}

_TYPE_TO_PRESET = {
    "charm": "Charm", "ward": "Ward", "aura": "Aura", "global": "Global",
    "enchantment": "Enchantment", "polymorph": "Polymorph",
    "curse": "Curse", "jinx": "Jinx", "trap": "Trap", "minion": "Minion",
    "damage": "Damage", "heal": "Heal", "manipulation": "Manipulation",
    "mutate": "Enchantment",
}

# Whole-or-suffix name cues — deliberately NOT anchored with a leading
# \b so "Deathblade" / "Tower Shield" / "Feint" resolve. Each entry is
# (regex, preset name).
_NAME_PATTERNS = [
    (r"blade\b",        "Blade"),
    (r"trap\b",         "Trap"),
    (r"shield\b",       "Ward"),
    (r"\bcharm\b",      "Charm"),
    (r"\bfeint\b",      "Trap"),
    (r"\baura\b",       "Aura"),
    (r"polymorph\b",    "Polymorph"),
    (r"\bdispel\b",     "Dispel"),
    (r"weakness\b",     "Curse"),
    (r"\bbane\b",       "Jinx"),
    (r"\bminion\b",     "Minion"),
    (r"\bprism\b",      "Ward"),
    (r"\bhex\b",        "Curse"),
]


def _labels_from_name(name: str) -> List[str]:
    if not name:
        return []
    low = name.lower()
    out = []
    for pat, label in _NAME_PATTERNS:
        if re.search(pat, low) and label not in out:
            out.append(label)
    return out


def _labels_from_type(spell_type: str) -> List[str]:
    if not spell_type:
        return []
    low = spell_type.strip().lower()
    out = []
    for key, label in _TYPE_TO_PRESET.items():
        if key in low and label not in out:
            out.append(label)
    return out


def _labels_from_school(school: str) -> List[str]:
    if not school:
        return []
    label = _SCHOOL_TO_PRESET.get(school.strip().lower())
    return [label] if label else []


def _to_int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0


def _labels_from_pvp(pvp) -> List[str]:
    # pvp is the "PvP legal" flag. A falsy value means the spell can't be
    # used in PvP → surface the "No PvP" icon. (There's no reliable
    # "PvP Only" signal in the stored fields, so it isn't inferred here.)
    return [] if pvp is None else ([] if _to_int(pvp) else ["No PvP"])


def derive_all_icon_labels(spell: dict) -> List[str]:
    """
    Build the fullest reasonable set of icon-preset labels for a spell by
    combining every available signal: description/OCR text mechanics, the
    spell name, the Type field, the School field, pip costs, and the PvP
    flag. Order-stable, de-duplicated, with the generic/specific
    suppression rules applied across the merged set.

    Philosophy is "a bit more is better": it favours recall so the user
    sees candidate icons they can remove, rather than missing ones they'd
    have to add by hand. Everything is still matched to a real preset by
    auto_link_ocr_icons, so labels with no matching preset are dropped.
    """
    if not spell:
        return []
    name    = spell.get("name", "") or ""
    desc    = spell.get("description", "") or ""
    ocr_raw = spell.get("ocr_raw", "") or ""
    stype   = spell.get("spell_type", "") or ""
    school  = spell.get("school", "") or ""

    labels: List[str] = []
    labels += extract_icon_keyword_labels(" ".join([desc, ocr_raw]))
    labels += _labels_from_name(name)
    labels += _labels_from_type(stype)
    labels += _labels_from_school(school)
    # NOTE: the PvP flag is deliberately NOT turned into a "No PvP" label
    # here. Deriving it from the parsed field attached "No PvP" to almost
    # every spell (any spell not explicitly marked PvP-legal), even ones
    # whose card shows no such icon. "No PvP" should behave like every other
    # detected icon — surfaced only when actually found via OCR / visual
    # template matching (extract_icon_keyword_labels above) — or added by
    # hand via "＋ Add". See _labels_from_pvp (now unused) for history.

    seen, out = set(), []
    for lb in labels:
        if lb not in seen:
            seen.add(lb)
            out.append(lb)
    for trigger, victims in _ICON_KEYWORD_SUPPRESS.items():
        if trigger in out:
            out = [x for x in out if x not in victims]
    return out


# ── Fuzzy alias resolution for auto-linking ─────────────────────────────
# Maps common OCR/wiki wordings that don't equal a preset name onto the
# right preset. Applied after an exact lookup fails, before the difflib
# near-match fallback.
_ICON_ALIASES = {
    "shield": "Ward", "shields": "Ward",
    "weakness": "Curse", "weaken": "Curse",
    "steal health": "Drain / Steal Health", "steal": "Drain / Steal Health",
    "drain": "Drain / Steal Health",
    "dot": "Damage Over Time", "damage over time": "Damage Over Time",
    "hot": "Heal Over Time", "heal over time": "Heal Over Time",
    "self": "Caster (Self)", "caster": "Caster (Self)",
    "caster self": "Caster (Self)",
    "pierce": "Armor Piercing", "piercing": "Armor Piercing",
    "fire": "Fire School", "ice": "Ice School", "storm": "Storm School",
    "myth": "Myth School", "life": "Life School", "death": "Death School",
    "balance": "Balance School",
    "power pip": "Power Pip", "shadow pip": "Shadow Pip",
    "no pvp": "No PvP", "pvp only": "PvP Only",
}


def _resolve_preset_name(candidate: str, presets: dict) -> Optional[int]:
    """
    Resolve a free-form label to a preset id: exact (case-insensitive)
    first, then alias table, then a conservative difflib near-match.
    `presets` is a {lowercased name: id} map. Returns id or None.
    """
    if not candidate:
        return None
    low = candidate.strip().lower()
    pid = presets.get(low)
    if pid is not None:
        return pid
    alias = _ICON_ALIASES.get(low)
    if alias and alias.lower() in presets:
        return presets[alias.lower()]
    # Conservative fuzzy fallback — only for reasonably long tokens and a
    # high similarity cutoff, so we never link a wildly different preset.
    if len(low) >= 5:
        import difflib
        match = difflib.get_close_matches(low, list(presets.keys()),
                                          n=1, cutoff=0.86)
        if match:
            return presets[match[0]]
    return None


def auto_link_ocr_icons(conn: sqlite3.Connection, spell_id: int,
                         ocr_keywords_str: str, replace_auto: bool = False):
    """
    Parse an OCR keyword string and auto-link matching icon presets to
    a spell. Called during fetch_spell and reparse_from_cache so the
    icon legend is populated immediately after scraping — not only when
    the user first opens the spell's detail dialog.

    Links are created with auto=1 (auto-detected), so a later Reparse can
    rebuild them without disturbing manual "+ Add" picks (auto=0).

    replace_auto=False (default, e.g. first fetch): only ADDS missing
    icons; never removes. Existing links (manual or auto) are left alone.

    replace_auto=True (Reparse): first clears this spell's auto-detected
    links, then re-adds the freshly detected set. Manual picks survive.
    This is what lets Reparse correct icons the older, looser detection
    mis-linked.

    Visual match entries ("Damage Over Time (visual, 82%)") and plain
    text keywords ("Trap", "All Enemies") are both handled.
    The embedded comma inside "(visual, 82%)" is handled by re-joining
    any fragment that looks like a dangling percentage back onto the
    preceding token before matching.
    """
    if not ocr_keywords_str:
        # Even with nothing to add, a Reparse should still wipe stale
        # auto links so a spell that now detects nothing ends up empty
        # (rather than keeping icons from a previous, wronger pass).
        if replace_auto:
            clear_auto_icon_links(conn, spell_id)
        return

    # Check if presets exist at all — skip silently if the table is empty
    # (scraper may run before SpellBrowserWidget seeds the library).
    count = conn.execute("SELECT COUNT(*) FROM icon_presets").fetchone()[0]
    if count == 0:
        return

    # Reparse rebuild: drop the previous auto set FIRST, so the fresh
    # detection fully replaces it (manual picks, auto=0, are untouched).
    if replace_auto:
        clear_auto_icon_links(conn, spell_id)

    # Build a case-insensitive name → id map of all available presets
    presets = {r["name"].lower(): r["id"]
               for r in conn.execute("SELECT id, name FROM icon_presets").fetchall()}

    # Fetch already-linked ids so we never duplicate (this now reflects the
    # post-clear state when replace_auto is on: only surviving manual links).
    linked_ids = {r[0] for r in
                  conn.execute("SELECT icon_preset_id FROM spell_icon_links WHERE spell_id=?",
                               (spell_id,)).fetchall()}

    # Naive comma-split first, then re-join fragments that are dangling
    # confidence suffixes: "82%)" should rejoin onto "Damage Over Time (visual"
    import re as _re
    raw_tokens = [t.strip() for t in ocr_keywords_str.split(",") if t.strip()]
    merged: list = []
    for tok in raw_tokens:
        if _re.match(r'^\d+\.?\d*%\)$', tok) and merged:
            merged[-1] = merged[-1] + ", " + tok
        else:
            merged.append(tok)

    next_order_row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM spell_icon_links WHERE spell_id=?",
        (spell_id,),
    ).fetchone()
    next_order = next_order_row[0] if next_order_row else 0

    for kw in merged:
        # Strip the visual-confidence suffix
        name_candidate = kw
        if "(visual," in kw:
            name_candidate = kw.split("(visual,")[0].strip()
        name_candidate = name_candidate.replace("_", " ").strip()

        pid = _resolve_preset_name(name_candidate, presets)
        if pid is None:
            continue
        if pid in linked_ids:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO spell_icon_links (spell_id, icon_preset_id, sort_order, auto) "
            "VALUES (?,?,?,1)",
            (spell_id, pid, next_order),
        )
        linked_ids.add(pid)
        next_order += 1

    conn.commit()
