"""
database_calc.py
════════════════
SQLite schema, CRUD helpers, default seeds and the (PyQt-free) damage
engine for the Damage Calculator + Character Manager features.

Lives inside the shared boss_wiki.db (same pattern as database_gear.py) so
the main app connection sees everything without split-connection / WAL
checkpoint problems.

Two tables:
  calc_presets — predefined modifier entries (blades, traps, shields,
                 armor, enchants, battlecircles).  label + value + category.
  wizards      — saved characters: name, school, health/mana and the full
                 per-school stat grids from the in-game character sheet,
                 stored as JSON keyed by school.

The compute_damage() function is deliberately free of any Qt import so it
can be reused by hud_overlays.py and unit-tested in isolation.
"""

import sqlite3
import json
import re
import time
from pathlib import Path
from typing import List, Optional

DB_PATH = Path(__file__).parent / "boss_wiki.db"

# Wizard's own school (no Shadow — wizards don't have a Shadow primary school)
WIZARD_SCHOOLS = ["Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance"]

# Stat-grid columns shown on the character sheet (8 columns incl. Shadow)
STAT_SCHOOLS = ["Fire", "Ice", "Storm", "Myth", "Life", "Death", "Balance", "Shadow"]

# Categories for calc presets
PRESET_CATEGORIES = ["Enchant", "Aura", "Blade", "Trap", "Shield", "Armor", "Battlecircle"]

SCHOOL_COLORS = {
    "Fire": "#e05a00", "Ice": "#4db8ff", "Storm": "#9b59b6",
    "Myth": "#d4ac0d", "Life": "#27ae60", "Death": "#8e44ad",
    "Balance": "#c8a000", "Shadow": "#5d6d9e",
}


def preset_unit(category: str) -> str:
    """Flat categories show no % sign; percentage categories do."""
    return "" if category in ("Enchant", "Armor") else "%"


def preset_value_str(category: str, value) -> str:
    """'+40%' / '-70%' / '+100' depending on the category."""
    unit = preset_unit(category)
    return f"{value:+g}{unit}"


def get_connection(db_path: str = None) -> sqlite3.Connection:
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── SCHEMA ─────────────────────────────────────────────────────

def init_calc_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS calc_presets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category    TEXT NOT NULL DEFAULT 'Blade',
            label       TEXT NOT NULL DEFAULT '',
            value       REAL NOT NULL DEFAULT 0,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  REAL,
            updated_at  REAL
        );
        CREATE INDEX IF NOT EXISTS idx_calc_presets_cat ON calc_presets(category);

        CREATE TABLE IF NOT EXISTS wizards (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            school        TEXT NOT NULL DEFAULT 'Storm',
            school2       TEXT NOT NULL DEFAULT '',
            level         INTEGER NOT NULL DEFAULT 1,
            health        INTEGER NOT NULL DEFAULT 0,
            mana          INTEGER NOT NULL DEFAULT 0,
            damage_json   TEXT NOT NULL DEFAULT '{}',
            damage_flat_json TEXT NOT NULL DEFAULT '{}',
            resist_json   TEXT NOT NULL DEFAULT '{}',
            accuracy_json TEXT NOT NULL DEFAULT '{}',
            critical_json TEXT NOT NULL DEFAULT '{}',
            block_json    TEXT NOT NULL DEFAULT '{}',
            pierce_json   TEXT NOT NULL DEFAULT '{}',
            stun_resist   REAL NOT NULL DEFAULT 0,
            heal_in       REAL NOT NULL DEFAULT 0,
            heal_out      REAL NOT NULL DEFAULT 0,
            created_at    REAL,
            updated_at    REAL
        );
    """)
    conn.commit()
    _migrate_wizard_schema(conn)
    _seed_default_presets(conn)


def _migrate_wizard_schema(conn):
    """Add columns introduced after the first release to existing DBs."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wizards)").fetchall()}
    if "school2" not in cols:
        conn.execute("ALTER TABLE wizards ADD COLUMN school2 TEXT NOT NULL DEFAULT ''")
        conn.commit()


# ─── DEFAULT PRESETS (kept simple, as requested) ────────────────

# (category, label, value)   value is a percentage for %-based categories,
# a flat number for Enchant/Armor.  Labels are names only — the value is shown
# beside the name in the dropdowns.
DEFAULT_PRESETS = [
    ("Enchant", "Strong",      100),
    ("Enchant", "Giant",       225),
    ("Enchant", "Monstrous",   250),
    ("Enchant", "Gargantuan",  225),
    ("Enchant", "Colossal",    275),

    ("Aura", "Amplify",         25),
    ("Aura", "Empowerment",     30),
    ("Aura", "Punishment",     -25),
    ("Aura", "Infallible",      15),

    ("Blade", "Blade", 20),
    ("Blade", "Blade", 25),
    ("Blade", "Blade", 30),
    ("Blade", "Blade", 35),
    ("Blade", "Blade", 40),

    ("Trap", "Trap", 20),
    ("Trap", "Trap", 25),
    ("Trap", "Trap", 30),
    ("Trap", "Trap", 35),

    ("Shield", "Shield",       -50),
    ("Shield", "Shield",       -60),
    ("Shield", "Shield",       -70),
    ("Shield", "Shield",       -80),
    ("Shield", "Tower Shield",  -50),

    ("Armor", "Absorb",  300),
    ("Armor", "Absorb",  500),
    ("Armor", "Absorb", 1000),

    ("Battlecircle", "Damage Circle",    25),
    ("Battlecircle", "Damage Circle",    30),
    ("Battlecircle", "Weakness Circle", -25),
]


def _seed_default_presets(conn: sqlite3.Connection):
    """Insert default presets only if the table is completely empty."""
    count = conn.execute("SELECT COUNT(*) FROM calc_presets").fetchone()[0]
    if count > 0:
        return
    now = time.time()
    for i, (cat, label, val) in enumerate(DEFAULT_PRESETS):
        conn.execute(
            "INSERT INTO calc_presets (category, label, value, sort_order, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (cat, label, val, i, now, now)
        )
    conn.commit()


# ─── PRESET CRUD ────────────────────────────────────────────────

def list_presets(conn, category: str = None) -> List[dict]:
    if category and category != "All":
        rows = conn.execute(
            "SELECT * FROM calc_presets WHERE category = ? ORDER BY sort_order, id",
            (category,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM calc_presets ORDER BY category, sort_order, id"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_preset(conn, data: dict) -> int:
    now = time.time()
    pid = data.get("id")
    if pid:
        conn.execute(
            "UPDATE calc_presets SET category=?, label=?, value=?, sort_order=?, updated_at=? WHERE id=?",
            (data.get("category", "Blade"), data.get("label", ""),
             float(data.get("value", 0)), int(data.get("sort_order", 0)), now, pid)
        )
    else:
        cur = conn.execute(
            "INSERT INTO calc_presets (category, label, value, sort_order, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (data.get("category", "Blade"), data.get("label", ""),
             float(data.get("value", 0)), int(data.get("sort_order", 0)), now, now)
        )
        pid = cur.lastrowid
    conn.commit()
    return pid


def delete_preset(conn, preset_id: int):
    conn.execute("DELETE FROM calc_presets WHERE id=?", (preset_id,))
    conn.commit()


def delete_all_presets(conn) -> int:
    count = conn.execute("SELECT COUNT(*) FROM calc_presets").fetchone()[0]
    conn.execute("DELETE FROM calc_presets")
    conn.commit()
    return count


# ─── WIZARD CRUD ────────────────────────────────────────────────

_WIZARD_GRID_FIELDS = [
    "damage_json", "damage_flat_json", "resist_json",
    "accuracy_json", "critical_json", "block_json", "pierce_json",
]


def list_wizards(conn) -> List[dict]:
    rows = conn.execute("SELECT * FROM wizards ORDER BY name COLLATE NOCASE").fetchall()
    return [_wizard_row_to_dict(r) for r in rows]


def get_wizard(conn, wizard_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM wizards WHERE id=?", (wizard_id,)).fetchone()
    return _wizard_row_to_dict(row) if row else None


def _wizard_row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    for f in _WIZARD_GRID_FIELDS:
        parsed_key = f.replace("_json", "")
        try:
            d[parsed_key] = json.loads(d.get(f) or "{}")
        except (json.JSONDecodeError, TypeError):
            d[parsed_key] = {}
    return d


def upsert_wizard(conn, data: dict) -> int:
    now = time.time()
    wid = data.get("id")

    def _grid(key):
        val = data.get(key)
        if isinstance(val, str):
            return val
        return json.dumps(val or {}, ensure_ascii=False)

    fields = (
        data.get("name", ""), data.get("school", "Storm"),
        data.get("school2", ""),
        int(data.get("level", 1) or 0), int(data.get("health", 0) or 0),
        int(data.get("mana", 0) or 0),
        _grid("damage"), _grid("damage_flat"), _grid("resist"),
        _grid("accuracy"), _grid("critical"), _grid("block"), _grid("pierce"),
        float(data.get("stun_resist", 0) or 0), float(data.get("heal_in", 0) or 0),
        float(data.get("heal_out", 0) or 0),
    )

    if wid:
        conn.execute("""
            UPDATE wizards SET
                name=?, school=?, school2=?, level=?, health=?, mana=?,
                damage_json=?, damage_flat_json=?, resist_json=?,
                accuracy_json=?, critical_json=?, block_json=?, pierce_json=?,
                stun_resist=?, heal_in=?, heal_out=?, updated_at=?
            WHERE id=?
        """, fields + (now, wid))
    else:
        cur = conn.execute("""
            INSERT INTO wizards
                (name, school, school2, level, health, mana,
                 damage_json, damage_flat_json, resist_json,
                 accuracy_json, critical_json, block_json, pierce_json,
                 stun_resist, heal_in, heal_out, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, fields + (now, now))
        wid = cur.lastrowid
    conn.commit()
    return wid


def delete_wizard(conn, wizard_id: int):
    conn.execute("DELETE FROM wizards WHERE id=?", (wizard_id,))
    conn.commit()


def delete_all_wizards(conn) -> int:
    count = conn.execute("SELECT COUNT(*) FROM wizards").fetchone()[0]
    conn.execute("DELETE FROM wizards")
    conn.commit()
    return count


# ─── BOSS DATA HELPERS (parse the messy text the wiki gives us) ──

def parse_health(health_str) -> Optional[int]:
    """Extract a sensible integer HP from a boss health string.
    Handles '5,945', '5000', '5,000 - 7,000' (takes the largest)."""
    if health_str is None:
        return None
    if isinstance(health_str, (int, float)):
        return int(health_str)
    nums = re.findall(r"[\d,]+", str(health_str))
    vals = []
    for n in nums:
        try:
            vals.append(int(n.replace(",", "")))
        except ValueError:
            pass
    return max(vals) if vals else None


def _parse_pct_value(text) -> Optional[float]:
    m = re.search(r"-?\d+(\.\d+)?", str(text))
    return float(m.group(0)) if m else None


def _schools_in_text(text) -> list:
    """All school names mentioned in a string, e.g.
    'Fire, Ice, Storm, Myth, Death, Shadow Resist' → those six schools."""
    low = str(text).lower()
    return [s for s in STAT_SCHOOLS if re.search(r"\b" + s.lower() + r"\b", low)]


def _school_in_text(text):
    found = _schools_in_text(text)
    return found[0] if found else None


def resist_map(boss: dict) -> dict:
    """Return {school: effective resist%} for every stat school.

    Positive = boss takes less damage; negative = boss takes MORE (a weakness /
    incoming boost).  Handles the wiki's combined entries, e.g.
        'Fire, Ice, Storm, Myth, Death, Shadow Resist' : '30%'   → +30 to each
        'Life, Balance Boost'                          : '15%'   → −15 to each
    plus Universal, bare school keys, and a battle_stats 'Incoming Boost'
    like '15% Life, Balance'.
    """
    out = {s: 0.0 for s in STAT_SCHOOLS}
    if not isinstance(boss, dict):
        return out

    resistances = boss.get("resistances") or {}
    for key, raw in resistances.items():
        k = str(key).strip().lower()
        val = _parse_pct_value(raw)
        if val is None:
            continue
        is_boost = "boost" in k          # boost = the boss takes MORE (weakness)
        signed = -val if is_boost else val
        if "universal" in k:
            for s in out:
                out[s] += signed
            continue
        schools = _schools_in_text(k)
        if schools:
            for s in schools:
                out[s] += signed
        # a key with neither a school nor 'universal' is ignored

    # battle_stats: 'Incoming Boost' = extra damage the boss takes of a school.
    # Only apply where the resistances table didn't already record that weakness,
    # so a boss listing it in both places isn't double-counted.
    battle_stats = boss.get("battle_stats") or {}
    for key, raw in battle_stats.items():
        kl = str(key).strip().lower()
        if "incoming boost" in kl:
            val = _parse_pct_value(raw)
            if val is None:
                continue
            for s in _schools_in_text(raw):
                if out.get(s, 0.0) >= 0:
                    out[s] -= val
        elif "incoming resist" in kl or kl == "incoming":
            val = _parse_pct_value(raw)
            if val is None:
                continue
            for s in _schools_in_text(raw):
                if out.get(s, 0.0) <= 0:
                    out[s] += val
    return out


def boss_resist_for_school(boss, school: str) -> float:
    """Effective incoming resist% the boss has vs `school`.
    Accepts either a full boss dict or a bare resistances dict."""
    if not school:
        return 0.0
    if isinstance(boss, dict) and ("resistances" in boss or "battle_stats" in boss
                                   or "name" in boss):
        return resist_map(boss).get(school, 0.0)
    # legacy: a bare resistances dict was passed
    return resist_map({"resistances": boss or {}}).get(school, 0.0)


def boss_all_resists(boss) -> dict:
    """Return {school: effective resist%} for every stat school.
    Accepts a full boss dict or a bare resistances dict."""
    if isinstance(boss, dict) and ("resistances" in boss or "battle_stats" in boss
                                   or "name" in boss):
        return resist_map(boss)
    return resist_map({"resistances": boss or {}})


def boss_critical_block(battle_stats: dict) -> float:
    """Return the boss critical block rating if present, else 0."""
    if not battle_stats:
        return 0.0
    for key, raw in battle_stats.items():
        if "critical block" in str(key).strip().lower():
            v = _parse_pct_value(raw)
            if v is not None:
                return v
    return 0.0


# ─── DAMAGE ENGINE (pure, no Qt) ────────────────────────────────

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def compute_damage(p: dict) -> dict:
    """Modern-W101 damage model.

    Order of operations (each step also recorded in result['steps']):
      1. effective base = attack base + fist enchant (flat)
      2. × damage multiplier  (wizard damage% + battlecircle%, added together)
      3. + flat '+' gear damage           (optional)
      4. × each blade        (multiplicative)
      5. × each trap         (multiplicative)
      6. × each shield       (multiplicative, value is negative)
      7. × (1 - boss resist%)             (armor piercing reduces resist first)
      8. - flat armor absorb
      9. critical variant: step-7 result × crit multiplier, then - armor
     10. damage-over-time total added on top of the direct hit

    Expected params (all optional, sensible defaults):
      base, enchant, damage_pct, gear_flat, battlecircle_pct,
      blades [%...], traps [%...], shields [%neg...], armor (flat),
      resist_pct, pierce_pct, crit_rating, block_rating,
      use_crit, use_pierce, use_flat,
      boss_hp, dot_per_round, dot_rounds
    """
    steps = []

    base = float(p.get("base", 0) or 0)
    per_pip = float(p.get("per_pip", 0) or 0)
    pips = float(p.get("pips", 0) or 0)
    base = base + per_pip * pips
    enchant = float(p.get("enchant", 0) or 0)
    damage_pct = float(p.get("damage_pct", 0) or 0)
    bc_pct = float(p.get("battlecircle_pct", 0) or 0)
    gear_flat = float(p.get("gear_flat", 0) or 0)
    armor = float(p.get("armor", 0) or 0)
    resist_pct = float(p.get("resist_pct", 0) or 0)
    pierce_pct = float(p.get("pierce_pct", 0) or 0)
    crit_rating = float(p.get("crit_rating", 0) or 0)
    block_rating = float(p.get("block_rating", 0) or 0)

    use_crit = bool(p.get("use_crit", False))
    use_pierce = bool(p.get("use_pierce", False))
    use_flat = bool(p.get("use_flat", False))

    blades = [float(x) for x in p.get("blades", []) if x is not None]
    traps = [float(x) for x in p.get("traps", []) if x is not None]
    shields = [float(x) for x in p.get("shields", []) if x is not None]

    boss_hp = p.get("boss_hp")
    boss_hp = int(boss_hp) if boss_hp not in (None, "") else None

    dot_per_round = float(p.get("dot_per_round", 0) or 0)
    dot_rounds = int(p.get("dot_rounds", 0) or 0)

    targets = int(p.get("targets", 1) or 1)
    targets = max(1, min(4, targets))

    # 1 ─ base + enchant
    base_eff = base + enchant
    if per_pip and pips:
        steps.append(f"Per-pip {per_pip:g} × {pips:g} pips = {per_pip*pips:g}")
    if enchant:
        steps.append(f"Base {base:g} + enchant {enchant:g} = {base_eff:g}")
    else:
        steps.append(f"Base damage = {base_eff:g}")

    # 2 ─ damage boost (additive group)
    boost = damage_pct + bc_pct
    after_boost = base_eff * (1 + boost / 100.0)
    if boost:
        circ = f" + circle {bc_pct:g}%" if bc_pct else ""
        steps.append(f"× damage boost ({damage_pct:g}%{circ} = {boost:g}%) → {after_boost:.0f}")

    # 3 ─ flat gear damage
    after_flat = after_boost
    if use_flat and gear_flat:
        after_flat = after_boost + gear_flat
        steps.append(f"+ flat gear damage {gear_flat:g} → {after_flat:.0f}")

    # 4-6 ─ multiplicative blades / traps / shields
    after_mods = after_flat
    for b in blades:
        after_mods *= (1 + b / 100.0)
    if blades:
        steps.append("× blades " + " ".join(f"({b:+g}%)" for b in blades) + f" → {after_mods:.0f}")
    for t in traps:
        after_mods *= (1 + t / 100.0)
    if traps:
        steps.append("× traps " + " ".join(f"({t:+g}%)" for t in traps) + f" → {after_mods:.0f}")
    for s in shields:
        after_mods *= (1 + s / 100.0)
    if shields:
        steps.append("× shields " + " ".join(f"({s:+g}%)" for s in shields) + f" → {after_mods:.0f}")

    # 7 ─ resist (pierce reduces positive resist only)
    eff_resist = resist_pct
    if use_pierce and resist_pct > 0:
        eff_resist = max(0.0, resist_pct - pierce_pct)
    after_resist = after_mods * (1 - eff_resist / 100.0)
    if resist_pct or eff_resist:
        pnote = f" (pierced {pierce_pct:g}% off {resist_pct:g}%)" if (use_pierce and pierce_pct and resist_pct > 0) else ""
        steps.append(f"× resist (1 − {eff_resist:g}%){pnote} → {after_resist:.0f}")

    # 8 ─ armor absorb
    normal = max(0.0, after_resist - armor)
    if armor:
        steps.append(f"− armor absorb {armor:g} → {normal:.0f}")

    # 9 ─ critical
    crit = None
    crit_mult = None
    if use_crit and crit_rating > 0:
        crit_mult = _clamp(2.0 - (block_rating / crit_rating), 1.25, 2.0)
        crit_raw = after_resist * crit_mult
        crit = max(0.0, crit_raw - armor)
        steps.append(
            f"Critical: × {crit_mult:.2f} (crit {crit_rating:g} vs block {block_rating:g}) → {crit:.0f}"
        )

    # 10 ─ damage over time
    dot_round_dmg = 0.0
    dot_total = 0.0
    if dot_per_round > 0 and dot_rounds > 0:
        dot_round_dmg = dot_per_round * (1 + boost / 100.0)
        if use_flat and gear_flat:
            dot_round_dmg += gear_flat
        for b in blades:
            dot_round_dmg *= (1 + b / 100.0)
        for t in traps:
            dot_round_dmg *= (1 + t / 100.0)
        for s in shields:
            dot_round_dmg *= (1 + s / 100.0)
        dot_round_dmg *= (1 - eff_resist / 100.0)
        dot_round_dmg = max(0.0, dot_round_dmg)
        dot_total = dot_round_dmg * dot_rounds
        steps.append(
            f"DoT: {dot_round_dmg:.0f}/round × {dot_rounds} = {dot_total:.0f}"
        )

    total_normal = normal + dot_total
    total_crit = (crit + dot_total) if crit is not None else None

    # Split between multiple enemies (AoE that divides its damage)
    if targets > 1:
        normal /= targets
        if crit is not None:
            crit /= targets
        dot_total /= targets
        dot_round_dmg /= targets
        total_normal /= targets
        if total_crit is not None:
            total_crit /= targets
        steps.append(f"Split between {targets} enemies → {total_normal:.0f} each")

    result = {
        "steps": steps,
        "base_eff": round(base_eff),
        "damage_boost_pct": boost,
        "effective_resist_pct": eff_resist,
        "targets": targets,
        "normal": round(normal),
        "crit": round(crit) if crit is not None else None,
        "crit_mult": round(crit_mult, 2) if crit_mult is not None else None,
        "dot_round": round(dot_round_dmg),
        "dot_total": round(dot_total),
        "total_normal": round(total_normal),
        "total_crit": round(total_crit) if total_crit is not None else None,
        "boss_hp": boss_hp,
    }

    if boss_hp is not None:
        result["hp_after_normal"] = round(boss_hp - total_normal)
        result["killed_normal"] = total_normal >= boss_hp
        if total_crit is not None:
            result["hp_after_crit"] = round(boss_hp - total_crit)
            result["killed_crit"] = total_crit >= boss_hp

    return result
