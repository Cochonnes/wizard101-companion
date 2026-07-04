"""
icon_preset_seed.py
════════════════════
Default icon → description library, seeded into the icon_presets table
on first run. Names and descriptions come directly from the in-game
Icon Dictionary (Schools / Combat / Loot pages) — the same source the
icon_templates/*.png reference images were cropped from.

Only used once: database_spells.seed_icon_presets_if_empty() skips
seeding entirely if the table already has rows, so any user edits,
additions, or deletions made via HUD & Settings → Icon Presets are
never overwritten on a later app start.
"""

import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(APP_DIR, "icon_templates")


def _img(name: str) -> str:
    path = os.path.join(ICON_DIR, f"{name}.png")
    return path if os.path.exists(path) else ""


# (template filename stem, display name, description — verbatim from
#  the in-game Icon Dictionary where possible)
ICON_SEED_DATA = [
    # ── Combat: core mechanics ──────────────────────────────────────
    ("Damage",              "Damage",              "Deals direct damage to the target."),
    ("Heal",                "Heal",                "Restores health to the target."),
    ("Accuracy",             "Accuracy",            "Affects the chance the spell successfully hits."),
    ("Manipulation",        "Manipulation",        "Manipulates pips, cards, or other game mechanics rather than dealing damage or healing."),
    ("Drain_Steal_Health",  "Drain / Steal Health","Steals health from the target and gives it to the caster."),
    ("All_Spells",          "All Spells",          "Affects every spell, treasure cards included."),
    ("Treasure_Card",       "Treasure Card",       "This spell is granted by or behaves as a Treasure Card."),
    ("Pip",                 "Pip",                 "A standard combat pip."),
    ("Power_Pip",           "Power Pip",           "A power pip — counts as two pips for spells of your own school."),
    ("Shadow_Pip",          "Shadow Pip",          "A Shadow Pip, used to cast Shadow Magic spells."),

    # ── Combat: charms, wards, traps ────────────────────────────────
    ("Global",              "Global",              "Affects the entire battle, not just one target."),
    ("Charm",                "Charm",               "A positive modifier applied to the caster, boosting their own future spells."),
    ("Curse",                "Curse",               "A negative modifier applied to an enemy, weakening their future spells."),
    ("Ward",                 "Ward",                "A defensive shield that reduces incoming damage."),
    ("Jinx",                 "Jinx",                "A negative modifier that weakens an enemy's defenses."),
    ("Resistance",           "Resistance",          "Increases resistance to incoming damage of a school."),
    ("Stun_Resistance",      "Stun Resistance",     "Increases resistance to being stunned."),
    ("Armor_Piercing",       "Armor Piercing",      "Reduces the effectiveness of the target's wards/shields."),
    ("Critical",             "Critical",            "Affects the chance of landing a critical hit for bonus damage."),
    ("Block",                "Block",               "Affects the chance of blocking an incoming critical hit."),

    # ── Combat: damage timing & special effects ─────────────────────
    ("Deferred_Damage",      "Deferred Damage",     "Damage that is delayed and triggers later in the battle."),
    ("Dispel",               "Dispel",              "Removes a charm, ward, or other beneficial/harmful effect from a target."),
    ("Rounds",               "Rounds",              "Indicates how many rounds an effect lasts."),
    ("Enchantment",          "Enchantment",         "Modifies a card in hand before it is cast."),
    ("Caster_Self",          "Caster (Self)",       "The effect targets the caster rather than an enemy or ally."),
    ("Blade",                "Blade",               "A positive damage-boosting charm specifically called a Blade."),
    ("Aura",                 "Aura",                "A beneficial persistent effect placed on a target."),
    ("Harmful_Aura",         "Harmful Aura",        "A persistent negative effect placed on a target, dealing damage or otherwise harming them each round."),
    ("Afterlife",            "Afterlife",           "An effect that triggers after the affected creature is defeated."),
    ("Polymorph",            "Polymorph",           "Transforms the caster into a different creature with a new spell deck."),

    # ── Combat: targeting & threat ──────────────────────────────────
    ("Minion",               "Minion",              "Summons a minion to fight alongside the caster."),
    ("Threat",               "Threat",              "Affects how likely an enemy AI is to target the caster."),
    ("Stun",                 "Stun",                "Prevents the target from casting a spell next round."),
    ("Absorb",               "Absorb",              "Absorbs a fixed amount of incoming damage."),
    ("Trap",                 "Trap",                "A negative modifier placed on an enemy, boosting damage dealt to them."),
    ("Damage_or_Drain",      "Damage or Drain",     "Deals damage or drains health, depending on the spell."),
    ("Flat_Damage",          "Flat Damage",         "A fixed (non-percentage) amount of bonus damage."),
    ("Flat_Resist",          "Flat Resist",         "A fixed (non-percentage) amount of damage resistance."),
    ("Incoming",             "Incoming",            "Applies to damage coming into the target."),
    ("Outgoing",             "Outgoing",            "Applies to damage going out from the caster."),

    # ── Combat: damage/heal-over-time and grouped schools ───────────
    ("Fire_Ice_Storm_Damage","Fire, Ice and Storm Damage", "Affects damage from the Fire, Ice, and Storm schools collectively."),
    ("Life_Death_Myth_Damage","Life, Death and Myth Damage","Affects damage from the Life, Death, and Myth schools collectively."),
    ("Heal_Over_Time",       "Heal Over Time",      "Heals the target a set amount each round for several rounds."),
    ("Damage_Over_Time",     "Damage Over Time",    "Damages the target a set amount each round for several rounds."),

    # ── Combat: targeting groups ─────────────────────────────────────
    ("Enemy",                "Enemy",               "Targets a single enemy."),
    ("Friend",                "Friend",              "Targets a single friend/ally."),
    ("All_Enemies",          "All Enemies",         "Targets every enemy in the battle."),
    ("All_Friends",          "All Friends",         "Targets every friend/ally in the battle."),
    ("PvP_Only",             "PvP Only",            "This spell or effect is only usable/active in PvP combat."),
    ("No_PvP",               "No PvP",              "This spell cannot be used in PvP combat."),

    # ── Schools ────────────────────────────────────────────────────
    ("School_Fire",          "Fire School",         "Fire school or Fire damage type."),
    ("School_Ice",           "Ice School",          "Ice school or Ice damage type."),
    ("School_Storm",         "Storm School",        "Storm school or Storm damage type."),
    ("School_Myth",          "Myth School",         "Myth school or Myth damage type."),
    ("School_Life",          "Life School",         "Life school or Life damage type."),
    ("School_Death",         "Death School",        "Death school or Death damage type."),
    ("School_Balance",       "Balance School",      "Balance school or Balance damage type."),
    ("School_Sun",           "Sun School",          "Sun school (player-versus-self enhancement magic)."),
    ("School_Moon",          "Moon School",         "Moon school (Polymorph and creature-transformation magic)."),
    ("School_Star",          "Star School",         "Star school (universal buffs and utility magic)."),
]


def get_seed_presets():
    """Returns a list of {name, description, image_path} dicts ready
    for database_spells.seed_icon_presets_if_empty()."""
    result = []
    for stem, name, desc in ICON_SEED_DATA:
        result.append({
            "name": name,
            "description": desc,
            "image_path": _img(stem),
        })
    return result
