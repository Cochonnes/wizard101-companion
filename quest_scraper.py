"""
Quest Scraper — FinalBastion Quest Guide Parser
════════════════════════════════════════════════
Core algorithm (your suggestion, exactly right):
  Every quest starts with a number N followed by a dot/paren.
  Everything from that number until the NEXT number is part of that quest.
  No format detection needed — just collect lines per number block.

After collecting, within each block:
  - First non-number line segment = quest name (strip instance markers)
  - Everything after " – " or inside "(...)" = types
  - Type keywords extracted from the whole block

Duplicate handling:
  The page sometimes lists a quest twice (e.g. Q76 appears before and after Q77).
  We keep the occurrence with the MOST type keywords (most information).

Debug output: quest_debug/<world_slug>/
    <world>_raw.html      full page HTML
    <world>_plain.txt     plain text as fetched
    <world>_parse_log.txt line-by-line decisions
    <world>_ZERO_QUESTS.txt written if 0 quests parsed
"""

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
    SCRAPER_AVAILABLE = True
except ImportError:
    SCRAPER_AVAILABLE = False

logger = logging.getLogger(__name__)

DEBUG_DIR = Path(__file__).parent / "quest_debug"

MASTER_GUIDE_URL = (
    "https://finalbastion.com/wizard101-guides/w101-quest-guides/"
    "main-quest-line-guides-master-guide/"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer":         "https://finalbastion.com/",
}

# ── Quest type colours (exact from FinalBastion HTML span styles) ──────────────
QUEST_TYPE_COLORS = {
    "talk":             "#c8c8c8",
    "mob":              "#99cc00",
    "elite":            "#99cc00",
    "d&c":              "#00ccff",
    "boss":             "#ff99cc",
    "minor cheat":      "#ff99cc",
    "cheat":            "#ff0000",
    "major cheat":      "#ff0000",
    "quadruple cheat":  "#ff0000",
    "double cheat":     "#ff0000",
    "triple boss":      "#ff99cc",
    "double boss":      "#ff99cc",
    "solo minor cheat": "#ff99cc",
    "solo major cheat": "#ff0000",
    "instance":         "#cc99ff",
    "puzzle":           "#3366ff",
    "interact":         "#c8c8c8",
    "collect":          "#c8c8c8",
    "explore":          "#c8c8c8",
    "solo":             "#ffcc00",
}

# Ordered longest-first for matching
QUEST_KEYWORDS = [
    "quadruple cheat", "double cheat", "triple boss", "double boss",
    "major cheat", "minor cheat",
    "solo minor cheat", "solo major cheat",
    "d&c", "instance", "puzzle", "solo",
    "elite", "mob", "boss", "cheat",
    "talk", "explore", "interact", "collect",
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    return " ".join(s.split()).strip()

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower().strip())

def _world_debug_dir(world_name: str) -> Path:
    d = DEBUG_DIR / _slug(world_name)
    d.mkdir(parents=True, exist_ok=True)
    return d

# Regex: line that STARTS a new quest block — begins with a number
_RE_QUEST_NUM = re.compile(r'^(\d+)[.)]\s*(.*)')

# Prose words that appear in comment text but never in real quest names
_PROSE_INDICATORS = {
    "is", "are", "was", "were", "has", "have", "had", "not", "its",
    "its", "it's", "the", "this", "that", "there", "they", "their",
    "would", "could", "should", "will", "can", "did", "does",
    "you", "your", "we", "our", "my", "but", "and", "for", "with",
    "from", "just", "only", "also", "actually", "however", "because",
    "when", "where", "which", "what", "who", "how", "why",
    "very", "really", "too", "so", "more", "than",
    "quest", "boss", "after", "before", "between", "instead",
    "think", "know", "believe", "seems", "looks", "says", "said",
    "reply", "thanks", "thank", "hi", "hello", "hey",
}


def _is_real_quest_line(line: str) -> bool:
    """
    Returns True if a line that matches _RE_QUEST_NUM is actually a quest
    start, not a comment/footer that happens to reference a quest number.

    Key insight: real quest lines always have EITHER:
      - An em-dash: "269. Metastasis – Explore + Boss + Talk"
      - A parenthesis after the name: "15. Free Mouse ( Instance ) – ..."
      - Nothing after the number (bare line): "76."

    Comment lines look like:
      "269. Metastasis is not a boss, its two Rank 14 Elites"
      "253. Way of Gloom, there's a boss fight too"

    They have NO em-dash and NO opening paren — just prose text.
    Additionally they are long (>60 chars typically) or contain many
    prose words that don't appear in quest names.
    """
    m = _RE_QUEST_NUM.match(line.strip())
    if not m:
        return False

    rest = m.group(2).strip()

    # Bare number line like "76." — valid quest start
    if not rest:
        return True

    # Has em-dash → definitely a real quest line
    if "–" in rest or "—" in rest:
        return True

    # Has opening paren → likely "Name (Instance)" or "Name ("  → real quest
    if "(" in rest:
        return True

    # Has a trailing dash → dash format split across lines
    if rest.endswith(" -") or rest.endswith("-"):
        return True

    # No structural markers. Now check if it looks like comment prose.
    # Comments are typically long AND contain multiple prose-only words
    # that would never appear in a quest name.
    _COMMENT_WORDS = {
        "is", "are", "was", "were", "its", "it's", "not", "but",
        "there", "just", "actually", "however", "because", "after",
        "before", "instead", "reply", "thanks", "hi", "hello",
        "should", "would", "could", "that", "this", "have", "has",
        "also", "think", "know", "says", "said", "two", "quest",
        "boss", "mob", "fight", "fights", "battle", "battles",
    }
    words = re.findall(r'[a-z]+', rest.lower())
    comment_count = sum(1 for w in words if w in _COMMENT_WORDS)

    # Require at least 2 comment words AND the line to be longer than a typical name
    if comment_count >= 2 and len(rest) > 30:
        return False

    return True

# Lines that should never be included in quest content
_JUNK_FRAGMENTS = [
    "click here", "looking for a different", "this guide is meant",
    "i'm only listing", "main quest line quests", "storyline quests",
    "between brackets", "i have divided", "quests that require",
    "quests the require", "quests that just require", "quests you need",
    "it has three", "yay for", "i usually", "when i'm in",
    "happy questing", "finished the", "take a look below",
    "all wizard101", "latest posts", "leave a reply", "cancel reply",
    "required fields", "previous post", "next post", "about the author",
    "share your vote", "do you like", "no, thanks",
    "note that some", "only fights where", "maybe i just got lucky",
    "don't hesitate", "i list the quests", "the areas are listed",
    "simplify matters", "area is not always",
    "one hundred percent", "i will not mention",
    "storyline quests,",
]

_KNOWN_WORLDS_LC = {
    "wizard city", "krokotopia", "marleybone", "mooshu", "dragonspyre",
    "celestia", "zafaria", "avalon", "azteca", "khrysalis", "polaris",
    "mirage", "empyrea", "karamelle", "lemuria", "novus", "wallaru",
    "grizzleheim", "wysteria", "catacombs", "aquila", "arcanum",
    "darkmoor", "selenopolis",
}


def _is_junk_line(line: str) -> bool:
    """True for lines that are navigation, footer, comment, or legend content."""
    s = line.strip()
    if not s:
        return True
    lower = s.lower()
    if lower.startswith("→") or lower in ("home", "→", ":", ",", ".", ";", "*"):
        return True
    if re.match(r'^(URL|World|Fetched|Status):\s', s):
        return True
    if re.match(r'^={4,}$', s):
        return True
    if re.match(r'^\w+ \d+, \d{4}$', s):  # dates
        return True
    if s.startswith("[") and s.endswith("]"):
        return True
    for frag in _JUNK_FRAGMENTS:
        if frag in lower:
            return True
    return False


def _is_area_header(line: str, world_name: str = "") -> bool:
    """
    Returns True only for genuine area/section names.

    Real area names look like:
      "Bastion", "Tyrian Gorge", "Last Wood / Bastion / Moon Cliffs",
      "The Commons + Marleybone", "The Zocalo + Three Points",
      "Cenote/Zocalo/Three Points/Mangrove Marsh/Saltmeadow Swamp"

    False positives to reject:
      - Ends with ")" containing a type keyword → quest name suffix
      - Starts with ".", "," → prose fragment
      - Contains ":" → legend or guide title
      - Contains "#" → instance number or comment ref
      - Quest type keywords alone
      - Prose sentences, comments, guide titles
    """
    s = line.strip()
    if not s or len(s) > 100:
        return False
    if _RE_QUEST_NUM.match(s):
        return False
    if _is_junk_line(s):
        return False

    lower = s.lower()

    # ── Hard structural rejections ─────────────────────────────────────────

    # Starts with punctuation → prose fragment like ", aka" or ". Of those" or ")"
    if s[0] in '.,;!?)':
        return False

    # Starts with digit → quest or numbered item
    if s[0].isdigit():
        return False

    # Starts lowercase → prose sentence
    if s[0].islower():
        return False

    # Starts with "(" → parenthetical note
    if s.startswith("("):
        return False

    # Contains "#" → "Instance #1" or "#246 suns shadow..."
    if "#" in s:
        return False

    # Contains ":" → legend line like "Wizard101 Main Quest Line:" or "instance: mob"
    if ":" in s:
        return False

    # Ends with ")" → could be quest name with type suffix OR descriptive area name
    # "Not a Cold Dead Place (explore)" → type keyword in parens → NOT a header
    # "Moon Cliffs (with a few quests in the Bastion)" → descriptive → IS a header
    if s.endswith(")") and "(" in s:
        paren_content = s[s.rfind("(")+1:-1].strip().lower()
        _TYPE_WORDS = {
            "talk", "mob", "d&c", "boss", "cheat", "instance", "puzzle",
            "interact", "collect", "explore", "solo", "minor cheat",
            "major cheat", "elite", "guide", "guide or guide",
        }
        # Reject only if the paren content IS a type keyword (or just type keywords)
        paren_words = re.sub(r'\+', ' ', paren_content).split()
        if paren_words and all(w.strip() in _TYPE_WORDS for w in paren_words if w.strip()):
            return False

    # "+" handling: allowed ONLY as place-name separator like "The Commons + Marleybone"
    # Reject if + appears with type keywords OR if the parts look like type content
    if "+" in s:
        # Always reject if it starts with "+" (continuation fragment)
        if s.startswith("+"):
            return False
        parts = [p.strip().lower() for p in s.split("+")]
        _TYPE_WORDS_SET = {
            "talk", "mob", "d&c", "boss", "cheat", "instance", "puzzle",
            "interact", "collect", "explore", "solo", "elite", "and",
        }
        # Reject if any part is a bare type keyword
        if any(p in _TYPE_WORDS_SET for p in parts):
            return False
        # Reject if any part is short non-capitalized word (type fragment)
        # Real place names start with uppercase
        for part in [p.strip() for p in s.split("+")]:
            if part and not part[0].isupper():
                return False

    # Ends with "+" → partial quest type fragment like "Fangs Reprisal (explore +"
    if s.endswith("+"):
        return False

    # Contains "–" or "—" → quest type separator
    if "–" in s or "—" in s:
        return False

    # ── Content-based rejections ───────────────────────────────────────────

    # Known false positives
    _FALSE_POSITIVES = {
        "world info", "my", "defeat and collect",
        "double boss", "triple boss", "double cheat",
        "bos", "s",
        "interact 4x", "explore 4x", "explore 5x", "talk 4x", "talk 3x",
        "talk 5x", "talk 7x", "talk x3", "talk x4", "talk x5", "talk x6", "talk x7",
        "explore x3", "explore x4", "explore x5", "interact x3", "interact x4",
        "mob x2", "mob x3", "boss x2", "collect x2", "collect x3",
        "minor boss", "minor cheat", "major cheat", "solo minor cheat", "solo major cheat",
        "play as your pet", "photomancy", "investigate",
        "happy", "sad", "angry", "bored", "afraid", "fascinated",
        "reply", "fixed!", "fixed, thanks.",
        "comment", "name", "email", "website",
        "leave this field empty", "current ye@r",
        "wizard101 main quest line", "wizard101 main quest line:",
        "azteca main quest line", "azteca main quest line guide",
        "khrysalis main quest line", "khrysalis main quest line guide",
        "mirage main quest line guide", "mirage main quest line",
        "empyrea main quest line part 1", "empyrea main quest line part 2",
        "empyrea main quest line guide", "wizard101-worlds",
        # Commenter names
        "misthead", "cody raventamer", "matthew", "jennifer soulstone",
        "tyler life", "katie storm", "stormbreaker", "melz", "nova",
        "amber tomb", "dustin", "nataly", "blaze", "talon", "jordan",
        "kyle firesage", "kate", "joseph", "caleb ironstone",
        "tyler ghostrider", "kaitlyn", "kyle emerald eyes", "alia",
        "karim benhamou", "immortalslayer", "anthony", "jason",
        "marcus wildcrafter", "tony", "william", "hunter g",
        "jordan storm", "jam", "will", "hi", "ok", "challenge",
    }
    if lower in _FALSE_POSITIVES:
        return False

    # Bare type keyword (with optional colon)
    if lower.rstrip(":") in {
        "talk", "mob", "d&c", "boss", "cheat", "instance", "puzzle",
        "interact", "collect", "explore", "solo", "minor cheat",
        "major cheat", "quadruple cheat", "elite", "double boss",
        "triple boss", "double cheat",
    }:
        return False

    # Guide titles
    if re.search(r'Main Quest Line Guide', s, re.I):
        return False

    # Multiplier patterns — "Talk x3", "Interact x3", "Explore x4" etc.
    # Match both lowercase and capitalised variants, with or without space before x
    if re.match(r'^(?:talk|explore|interact|mob|boss|collect|d&c|elite|solo|instance|puzzle)\s*x\s*\d+\s*$', lower):
        return False
    # Also catch "Minor Boss", "Minor Cheat", "Double Cheat", "Double Boss" etc. as standalone
    if re.match(r'^(minor|major|double|triple|quadruple|solo)\s+(boss|cheat|mob|elite)\s*$', lower):
        return False
    # Any single-word or short type-keyword line (capitalised or not)
    if re.match(r'^(play as your pet|photomancy|investigate|collect|interact|explore|solo|instance|puzzle)\s*$', lower):
        return False

    # Informal comment expressions
    if re.search(r'\bxD\b|\blol\b|\bhaha\b|:\)|\^_\^|😂|😊|👍|🙂|:p', s, re.I):
        return False

    # World names in footer
    if lower in _KNOWN_WORLDS_LC:
        return False

    # Must not be world name itself
    if lower == world_name.lower():
        return False

    # Prose: 2+ common English sentence words
    # Exception: if the line has a "(" with descriptive text, only check words BEFORE the paren
    check_text = s[:s.index("(")] if "(" in s and s.endswith(")") else s
    check_words = check_text.split()
    _PROSE_WORDS = {
        "the", "is", "are", "was", "were", "has", "have", "had",
        "this", "that", "these", "those", "it", "its", "for", "with",
        "from", "into", "but", "not", "only", "also", "there", "where",
        "when", "how", "what", "why", "which", "can", "will", "would",
        "could", "should", "may", "might", "do", "did", "does", "been",
        "being", "you", "your", "our", "their", "my", "i", "we",
        "they", "he", "she", "of", "in", "at", "to", "an", "a",
    }
    prose_count = sum(1 for w in check_words if w.lower() in _PROSE_WORDS)
    if prose_count >= 2:
        return False

    return True


# ── Core: extract all types from a block of text ──────────────────────────────

def _extract_types(text: str) -> list:
    """
    Find all quest type keywords in a block of text.
    Returns [{'label': str, 'color': str}, ...] deduplicated in order of appearance.
    Longer keywords take priority — 'quadruple cheat' suppresses 'cheat'.
    """
    lower = text.lower()
    types = []
    seen  = set()

    # Find each keyword and its start position in the text
    hits = []
    for kw in QUEST_KEYWORDS:
        idx = 0
        while True:
            pos = lower.find(kw, idx)
            if pos == -1:
                break
            # Whole-word boundary check
            before = lower[pos - 1] if pos > 0 else " "
            after  = lower[pos + len(kw)] if pos + len(kw) < len(lower) else " "
            if (before in " \n\t()+,:-/") and (after in " \n\t()+,:-/"):
                hits.append((pos, pos + len(kw), kw))
            idx = pos + 1

    # Sort by position
    hits.sort(key=lambda x: x[0])

    # Remove hits that are contained within a longer hit
    # e.g. 'cheat' at pos 10-15 inside 'quadruple cheat' at pos 0-15
    filtered = []
    for i, (start, end, kw) in enumerate(hits):
        dominated = False
        for j, (s2, e2, kw2) in enumerate(hits):
            if i == j:
                continue
            # Is this hit entirely within another hit?
            if s2 <= start and end <= e2 and kw2 != kw:
                dominated = True
                break
        if not dominated:
            filtered.append((start, kw))

    # Sort by position again after filtering, then deduplicate
    filtered.sort(key=lambda x: x[0])
    for _, kw in filtered:
        if kw not in seen:
            seen.add(kw)
            types.append({"label": kw, "color": QUEST_TYPE_COLORS.get(kw, "#c8c8c8")})

    return types


# ── Core: parse quest name from block ─────────────────────────────────────────

def _extract_name(block_text: str, number: int) -> str:
    """
    Extract quest name from the full block text.

    Handles all FinalBastion formats:
      "1. First Star I See Tonight (interact + explore + mob + talk)"
      "15. Free Mouse ( Instance ) – Talk + Mob + Explore"
      "68. Eggs on the Side ( Instance ) – Mob + boss + Interact"
      "4. Proof of Life – Mob ( Solo ) + Talk"
      "17. (instance) Return to Avalon (mob + explore + elite + major cheat)"
    """
    # Remove leading number
    text = re.sub(r'^\d+[.)]\s*', '', block_text.strip())

    # Strip leading parenthetical if it contains ONLY type keywords.
    # Handles: "(instance) Return to Avalon ..." or "( Instance ) The Mind Thief ..."
    _TYPE_PATTERN_LEAD = re.compile(
        r'^\(\s*(?:' + '|'.join(re.escape(kw) for kw in QUEST_KEYWORDS) + r')\s*\)\s*',
        re.I
    )
    text = _TYPE_PATTERN_LEAD.sub('', text)

    # Split on em-dash / en-dash first — everything before it is the name
    if " – " in text or " — " in text:
        name = re.split(r'\s*[–—]\s*', text)[0]
    elif re.search(r'\s[-]\s', text):
        name = re.split(r'\s[-]\s', text)[0]
    else:
        name = text

    # Strip ALL trailing parentheticals that contain type keywords.
    # This covers:
    #   "First Star I See Tonight (interact + explore + mob + talk)"
    #   "Free Mouse ( Instance )"
    #   "Enemy to All Azteca Kind ( instance : talk + mob )"
    #   "Not a Cold Dead Place (explore)"
    # We repeatedly strip the last (...) if it contains a type keyword.
    _TYPE_PATTERN = re.compile(
        r'|'.join(re.escape(kw) for kw in QUEST_KEYWORDS),
        re.I
    )
    while True:
        # Find last opening paren
        last_open = name.rfind("(")
        if last_open == -1:
            break
        paren_part = name[last_open:]
        # Check if this paren region contains a type keyword
        if _TYPE_PATTERN.search(paren_part):
            name = name[:last_open].strip()
        else:
            break  # Paren content is not types — keep it (e.g. quest name with parens)

    # Strip trailing bare "("
    name = name.rstrip("(").strip()

    return _clean(name)


# ── Number-boundary preprocessor ──────────────────────────────────────────────

def _collect_quest_blocks(lines: list) -> list:
    """
    Core algorithm: group raw lines into quest blocks by number boundaries.

    Each block: {'number': int, 'raw_lines': [str], 'joined': str}

    A new block starts whenever a line matches the quest-number pattern.
    Everything until the next such line belongs to the current block.
    """
    blocks     = []
    cur_num    = None
    cur_lines  = []

    for line in lines:
        s = line.strip()
        if not s:
            if cur_lines:
                cur_lines.append("")
            continue

        m = _RE_QUEST_NUM.match(s)
        if m and _is_real_quest_line(s):
            # Save previous block
            if cur_num is not None and cur_lines:
                blocks.append({
                    "number":    cur_num,
                    "raw_lines": cur_lines,
                    "joined":    " ".join(l for l in cur_lines if l.strip()),
                })
            cur_num   = int(m.group(1))
            cur_lines = [s]
        else:
            if cur_num is not None:
                cur_lines.append(s)
            # else: before any quest number — area header territory, skip

    # Last block
    if cur_num is not None and cur_lines:
        blocks.append({
            "number":    cur_num,
            "raw_lines": cur_lines,
            "joined":    " ".join(l for l in cur_lines if l.strip()),
        })

    return blocks


def _pick_best_block(blocks: list) -> dict:
    """
    When a quest number appears multiple times, pick the occurrence with
    the most type keywords (most information). Tie-break: last occurrence.
    """
    best = blocks[0]
    best_score = len(_extract_types(best["joined"]))
    for b in blocks[1:]:
        score = len(_extract_types(b["joined"]))
        if score >= best_score:
            best       = b
            best_score = score
    return best


# ── Find area headers in the non-quest lines ──────────────────────────────────

def _find_area_headers(lines: list, world_name: str) -> dict:
    """
    Returns {line_index: area_name} for lines that are area headers.
    We track which line indices come before quest number blocks.
    """
    headers = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or _is_junk_line(s) or _RE_QUEST_NUM.match(s):
            continue
        if _is_area_header(s, world_name):
            headers[i] = s
    return headers


# ── Stats extraction ───────────────────────────────────────────────────────────

def _parse_stats(text: str) -> dict:
    stats = {
        "total_quests": None, "mob_fights": None, "dc_quests": None,
        "boss_fights": None,  "cheater_bosses": None, "solo_quests": None,
        "description": "",
    }
    joined = " ".join(text.splitlines())
    for key, pat in [
        ("total_quests",   r"total of\s+(\d+)\s+quests"),
        ("total_quests",   r"(\d+)\s+quests\b"),
        ("mob_fights",     r"(\d+)\s+regular\s+(?:mobs?|mob\s+fights?)"),
        ("mob_fights",     r"fight\s+(\d+)\s+regular\s+mobs?"),
        ("dc_quests",      r"(\d+)\s+Defeat and Collect"),
        ("boss_fights",    r"total of\s+(\d+)\s+boss"),
        ("boss_fights",    r"(\d+)\s+boss\s+fights?"),
        ("cheater_bosses", r"(\d+)\s+of\s+them\s+containing\s+cheaters?"),
        ("cheater_bosses", r"(\d+)\s+(?:are\s+)?cheaters?"),
        ("solo_quests",    r"(\d+)\s+quests?.{0,40}solo"),
    ]:
        if stats[key] is None:
            m = re.search(pat, joined, re.I)
            if m:
                stats[key] = int(m.group(1))
    return stats


# ── Intro text extraction ──────────────────────────────────────────────────────

def _extract_intro_text(plain_text: str, world_name: str) -> str:
    lines = plain_text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        lower = line.lower().strip()
        if "has a total of" in lower or (
            "total of" in lower and "quest" in lower
        ):
            start_idx = i
            break

    if start_idx == -1:
        return ""

    raw = []
    for line in lines[start_idx:]:
        s = line.strip()
        lower = s.lower()
        if _RE_QUEST_NUM.match(s):
            break
        if any(f in lower for f in [
            "click here", "looking for", "this guide is meant",
            "i'm only listing", "i have divided", "between brackets",
            "quests that require", "all wizard101", "happy questing",
            "finished the", "take a look", "share your vote",
            "about the author", "latest posts", "leave a reply",
        ]):
            if raw:
                break
            continue
        raw.append(s)

    joined_parts = []
    for part in raw:
        if not part:
            if joined_parts and joined_parts[-1] != "\n\n":
                joined_parts.append("\n\n")
            continue
        if part in (",", ".", ":", ";"):
            if joined_parts:
                joined_parts[-1] = joined_parts[-1].rstrip() + part + " "
            continue
        if part.lower() in ("and", "or"):
            if joined_parts:
                joined_parts[-1] = joined_parts[-1].rstrip() + " " + part + " "
            continue
        if part and part[0] in (".", ",", ":", ";", "!"):
            if joined_parts:
                joined_parts[-1] = joined_parts[-1].rstrip() + part
            else:
                joined_parts.append(part)
            continue
        words = part.split()
        is_header_like = (
            len(part) < 50
            and len(words) <= 4
            and all(w[0].isupper() for w in words if w[0].isalpha())
            and "+" not in part and not re.search(r'\d', part)
            and not any(w.lower() in {
                "has", "have", "the", "it", "its", "those", "are",
                "and", "or", "of", "in", "at", "to", "for", "with",
                "that", "this", "there", "when", "a", "an", "can",
            } for w in words)
        )
        if is_header_like:
            break
        joined_parts.append(part)

    text = ""
    for p in joined_parts:
        if p == "\n\n":
            text = text.rstrip() + "\n\n"
        else:
            if text and not text.endswith("\n\n") and not text.endswith(" "):
                text += " "
            text += p

    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    paragraphs = [p.strip() for p in text.split("\n\n")]
    paragraphs = [p for p in paragraphs if p and p.lower() != world_name.lower()]
    return "\n\n".join(paragraphs).strip()


# ── Main parse function ────────────────────────────────────────────────────────

def parse_plain_text(plain_text: str, world_name: str,
                     log_path: Optional[Path] = None) -> tuple:
    """
    Parse saved plain text → (stats, areas, log_text, intro_text).

    Algorithm:
    1. Split into lines
    2. Group lines into blocks by quest number boundaries
    3. For duplicate quest numbers, keep block with most type keywords
    4. Detect area headers from lines that are NOT part of any quest block
    5. Assign each quest to its current area
    """
    stats      = _parse_stats(plain_text)
    intro_text = _extract_intro_text(plain_text, world_name)

    raw_lines = plain_text.splitlines()

    # ── Truncate at footer/comments section ───────────────────────────────────
    # Everything after the comment section start is garbage. Find the cutoff:
    # the first line that is an unambiguous footer/comment sentinel AFTER
    # we've seen at least some quests. We stop collecting there.
    _FOOTER_SENTINELS = [
        "happy questing", "take a look below",
        "all wizard101 main quest line",
        "finished the khrysalis", "finished the mirage", "finished the empyrea",
        "finished the wizard city", "finished the azteca",
        "leave a reply", "cancel reply",
        "share your vote", "about the author", "latest posts",
        "previous post", "next post",
    ]
    cutoff = len(raw_lines)
    for i, line in enumerate(raw_lines):
        lower = line.strip().lower()
        if any(s in lower for s in _FOOTER_SENTINELS):
            cutoff = i
            break
    raw_lines = raw_lines[:cutoff]

    log_lines = [f"PARSE LOG — {world_name}\n{'='*60}\n\n"]

    # Step 1: collect all quest blocks grouped by number
    all_blocks = _collect_quest_blocks(raw_lines)

    # Step 2: group by quest number (used later for event building)
    # best_blocks is no longer needed — replaced by best_quest_events below

    # Step 3: find which raw line indices have area headers

    # Build a set of line indices that belong to quest blocks
    quest_line_indices: set = set()
    line_list = list(enumerate(raw_lines))

    # Re-collect blocks with line numbers, storing start line per block
    cur_num      = None
    cur_start    = 0
    cur_raw      = []
    block_ranges = []  # (start_idx, end_idx, number, raw_lines_list)

    for idx, line in line_list:
        s = line.strip()
        m = _RE_QUEST_NUM.match(s) if s else None
        if m and _is_real_quest_line(s):
            if cur_num is not None:
                block_ranges.append((cur_start, idx - 1, cur_num, list(cur_raw)))
            cur_num   = int(m.group(1))
            cur_start = idx
            cur_raw   = [s]
        else:
            if cur_num is not None:
                cur_raw.append(s)
    if cur_num is not None:
        block_ranges.append((cur_start, len(raw_lines) - 1, cur_num, list(cur_raw)))

    for start, end, num, _ in block_ranges:
        for i in range(start, end + 1):
            quest_line_indices.add(i)

    # For each quest number:
    # - POSITION = first occurrence's line (preserves document order)
    # - CONTENT  = occurrence with most type keywords (best information)
    # This ensures quest order matches the page, while getting full type data.
    from collections import defaultdict
    ranges_by_num = defaultdict(list)
    for start, end, num, rlines in block_ranges:
        ranges_by_num[num].append((start, end, rlines))

    # best_quest_events[num] = (first_line_position, best_rlines)
    best_quest_events = {}
    for num, rlist in ranges_by_num.items():
        first_start = rlist[0][0]   # ALWAYS use first occurrence for position
        if len(rlist) == 1:
            best_quest_events[num] = (first_start, rlist[0][2])
        else:
            # Content: pick occurrence with most type keywords
            best_rlines = rlist[0][2]
            best_score  = len(_extract_types(" ".join(l for l in rlist[0][2] if l)))
            for start, end, rlines in rlist[1:]:
                score = len(_extract_types(" ".join(l for l in rlines if l)))
                if score > best_score:   # strictly greater: first wins on tie
                    best_rlines = rlines
                    best_score  = score
            best_quest_events[num] = (first_start, best_rlines)

    # Build sorted event list
    areas         = []
    current_area  = {"name": "General", "quests": []}
    parsed_quests = 0
    assigned_nums: set = set()
    events = []

    # ── Area header detection via gap scanning ────────────────────────────────
    # Area headers appear in gaps between consecutive LAST occurrences of quests.
    # (Last occurrences are in the structured block which contains the headers.)
    #
    # POSITIONING: Area header events must be placed JUST BEFORE the first
    # occurrence of the quest that follows them — because quest events use
    # first-occurrence positions for ordering. We use (first_line - 0.5) so
    # the area header fires before its quest in the sorted event list.

    last_line_of_quest: dict  = {}
    first_line_of_quest: dict = {}
    for idx, line in enumerate(raw_lines):
        s = line.strip()
        m = _RE_QUEST_NUM.match(s) if s else None
        if m and _is_real_quest_line(s):
            num = int(m.group(1))
            last_line_of_quest[num] = idx
            if num not in first_line_of_quest:
                first_line_of_quest[num] = idx

    sorted_nums_by_last = sorted(last_line_of_quest.keys(),
                                  key=lambda n: last_line_of_quest[n])

    def _scan_gap_for_headers(from_idx: int, to_idx: int):
        for li in range(from_idx, to_idx):
            if li >= len(raw_lines):
                break
            s = raw_lines[li].strip()
            if not s:
                continue
            # Stop scanning if we've hit comment/footer territory
            lower = s.lower()
            if any(f in lower for f in [
                "leave a reply", "cancel reply", "required fields",
                "previous post", "next post", "about the author",
                "latest posts", "happy questing", "share your vote",
                "54 comments", "comments", "reply",
            ]):
                break
            if _is_junk_line(s):
                continue
            if _RE_QUEST_NUM.match(s):
                continue
            if _is_area_header(s, world_name):
                yield s

    # Scan before first last-occurrence (for any leading area headers)
    if sorted_nums_by_last:
        first_num  = sorted_nums_by_last[0]
        first_last = last_line_of_quest[first_num]
        for hdr in _scan_gap_for_headers(0, first_last):
            # Place before the first occurrence of first_num
            pos = first_line_of_quest[first_num] - 0.5
            events.append((pos, "area", hdr))

    # Scan gaps between consecutive last occurrences
    for i in range(1, len(sorted_nums_by_last)):
        prev_num  = sorted_nums_by_last[i - 1]
        this_num  = sorted_nums_by_last[i]
        prev_last = last_line_of_quest[prev_num]
        this_last = last_line_of_quest[this_num]
        headers_found = list(_scan_gap_for_headers(prev_last + 1, this_last))
        for hdr in headers_found:
            # Place just before this_num's FIRST occurrence
            pos = first_line_of_quest[this_num] - 0.5
            events.append((pos, "area", hdr))

    # Quest events at the FIRST occurrence line, with best content
    for num, (line_idx, rlines) in best_quest_events.items():
        events.append((line_idx, "quest", (num, rlines)))

    # Sort events by line index
    events.sort(key=lambda e: e[0])

    # Process events
    for _, etype, data in events:
        if etype == "area":
            area_name = data
            if area_name.lower() == world_name.lower():
                log_lines.append(f"AREA SKIP (world name): {area_name}\n")
                continue
            if current_area["quests"]:
                areas.append({"name": current_area["name"],
                               "quests": list(current_area["quests"])})
            current_area = {"name": area_name, "quests": []}
            log_lines.append(f"AREA    : {area_name}\n")

        elif etype == "quest":
            num, rlines = data
            if num in assigned_nums:
                log_lines.append(f"DUP#{num:>3}: already assigned\n")
                continue
            assigned_nums.add(num)

            joined = " ".join(l for l in rlines if l.strip())
            name   = _extract_name(joined, num)
            types  = _extract_types(joined)

            if not name:
                log_lines.append(f"SKIP#{num:>3}: empty name\n")
                continue

            current_area["quests"].append({
                "number": num,
                "name":   name,
                "types":  types,
                "area":   current_area["name"],
                "world":  world_name,
            })
            parsed_quests += 1
            tl = " + ".join(t["label"] for t in types) or "(none)"
            log_lines.append(
                f"QUEST#{num:>3}: {name[:40]:<40} [{tl}]\n"
            )

    if current_area["quests"]:
        areas.append({"name": current_area["name"],
                      "quests": list(current_area["quests"])})

    # ── Post-processing: clean up area list ───────────────────────────────────
    # 1. If the very first area has quests that don't start at 1, or its name
    #    is clearly garbage (intro text fragment), rename it or merge it forward.
    #    The first area before quest 1 is always junk from the intro paragraph.
    if areas:
        first_area = areas[0]
        first_quest_num = first_area["quests"][0]["number"] if first_area["quests"] else 999
        # If first area name looks like intro text (not a real area name)
        # OR if quest 1 doesn't exist in it but later quests do — it's a leftover
        first_name = first_area["name"]
        if first_name in ("General", world_name) or (
            first_name != "General" and
            not _is_area_header(first_name, world_name) and
            first_quest_num > 1
        ):
            # Drop it — merge its quests into whatever comes next
            # Actually keep the quests but remove the bad header by using no name
            areas[0] = {"name": "", "quests": first_area["quests"]}
        # Remove empty-named areas with no quests
        areas = [a for a in areas if a["quests"]]
        # Rename any empty-name area to world_name so it at least makes sense
        for a in areas:
            if not a["name"]:
                a["name"] = world_name

    log_lines.append(
        f"\nSUMMARY: {parsed_quests} quests in {len(areas)} areas\n"
        f"Stats: {stats}\n"
    )
    log_text = "".join(log_lines)

    if log_path:
        log_path.write_text(log_text, encoding="utf-8")

    logger.info(f"  Parsed {parsed_quests} quests in {len(areas)} areas for {world_name}")
    if parsed_quests == 0:
        logger.warning(f"  ⚠ Zero quests for {world_name}!")

    return stats, areas, log_text, intro_text


# ── Fetch and save ─────────────────────────────────────────────────────────────

def fetch_and_save(url: str, world_name: str) -> tuple:
    resp     = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    raw_html = resp.text
    soup     = BeautifulSoup(raw_html, "html.parser")
    content  = (
        soup.find("div", class_="entry-content")
        or soup.find("div", class_="post-content")
        or soup.find("article") or soup.find("main") or soup.body
    )
    plain = content.get_text(separator="\n", strip=True) if content else ""
    d    = _world_debug_dir(world_name)
    slug = _slug(world_name)
    (d / f"{slug}_raw.html").write_text(raw_html, encoding="utf-8")
    (d / f"{slug}_plain.txt").write_text(
        f"URL: {url}\nWorld: {world_name}\nFetched: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{'='*60}\n\n{plain}",
        encoding="utf-8"
    )
    logger.info(f"  Saved debug files → {d}")
    return raw_html, plain


def scrape_world_guide(url: str, world_name: str = "") -> dict:
    if not SCRAPER_AVAILABLE:
        raise RuntimeError("requests/beautifulsoup4 not installed.\n"
                           "Run: pip install requests beautifulsoup4")
    if not world_name:
        world_name = _guess_world_from_url(url)
    logger.info(f"Scraping: {world_name} → {url}")
    raw_html, plain_text = fetch_and_save(url, world_name)
    d        = _world_debug_dir(world_name)
    log_path = d / f"{_slug(world_name)}_parse_log.txt"
    stats, areas, log_text, intro_text = parse_plain_text(plain_text, world_name, log_path)
    total = sum(len(a["quests"]) for a in areas)
    if total == 0:
        warn = d / f"{_slug(world_name)}_ZERO_QUESTS.txt"
        warn.write_text(
            f"Zero quests for {world_name}.\nURL: {url}\n\n{log_text}",
            encoding="utf-8"
        )
    debug_info = (
        f"URL: {url}\nQuests: {total}  Areas: {len(areas)}\nDebug: {d}\n\n"
        + "\n".join(log_text.splitlines()[:50])
    )
    return {
        "world": world_name, "source_url": url, "stats": stats,
        "intro_text": intro_text, "areas": areas,
        "scraped_at": time.time(), "debug_info": debug_info, "debug_dir": str(d),
    }


def reparse_from_cache(world_name: str) -> Optional[dict]:
    d          = _world_debug_dir(world_name)
    slug       = _slug(world_name)
    plain_path = d / f"{slug}_plain.txt"
    if not plain_path.exists():
        logger.warning(f"No cached plain text for {world_name}: {plain_path}")
        return None
    plain_text = plain_path.read_text(encoding="utf-8", errors="replace")
    log_path   = d / f"{slug}_parse_log.txt"
    stats, areas, log_text, intro_text = parse_plain_text(plain_text, world_name, log_path)
    total = sum(len(a["quests"]) for a in areas)
    logger.info(f"Re-parsed {world_name} from cache: {total} quests")
    return {
        "world": world_name, "source_url": "",
        "stats": stats, "intro_text": intro_text, "areas": areas,
        "scraped_at": plain_path.stat().st_mtime,
        "debug_info": f"Re-parsed from cache: {total} quests in {len(areas)} areas.",
        "debug_dir": str(d),
    }


def get_cached_worlds() -> list:
    if not DEBUG_DIR.exists():
        return []
    result = []
    for subdir in sorted(DEBUG_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        for f in subdir.glob("*_plain.txt"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines()[:6]:
                    m = re.match(r"World:\s*(.+)", line)
                    if m:
                        result.append(m.group(1).strip())
                        break
                else:
                    result.append(subdir.name)
            except Exception:
                result.append(subdir.name)
    return result


# ── World name / URL helpers ───────────────────────────────────────────────────

WORLD_SLUG_MAP = {
    "wizard-city": "Wizard City",  "wizard_city": "Wizard City",
    "krokotopia":  "Krokotopia",   "grizzleheim": "Grizzleheim",
    "marleybone":  "Marleybone",   "mooshu":      "MooShu",
    "moo-shu":     "MooShu",       "dragonspyre": "Dragonspyre",
    "celestia":    "Celestia",     "zafaria":     "Zafaria",
    "wysteria":    "Wysteria",     "avalon":      "Avalon",
    "azteca":      "Azteca",       "aquila":      "Aquila",
    "khrysalis":   "Khrysalis",    "polaris":     "Polaris",
    "arcanum":     "Arcanum",      "mirage":      "Mirage",
    "empyrea":     "Empyrea",      "karamelle":   "Karamelle",
    "lemuria":     "Lemuria",      "novus":       "Novus",
    "wallaru":     "Wallaru",      "selenopolis": "Selenopolis",
    "darkmoor":    "Darkmoor",
}


def _guess_world_from_url(url: str) -> str:
    lower = url.lower()
    for slug, name in WORLD_SLUG_MAP.items():
        if slug in lower:
            return name
    parts = [p for p in lower.split("/") if p]
    if parts:
        last = parts[-1].replace("-guide","").replace("-quest","").replace("-main","")
        for slug, name in WORLD_SLUG_MAP.items():
            if slug in last:
                return name
    return "Unknown"


def scrape_master_guide() -> list:
    if not SCRAPER_AVAILABLE:
        raise RuntimeError("requests/beautifulsoup4 not installed")
    resp = requests.get(MASTER_GUIDE_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup    = BeautifulSoup(resp.text, "html.parser")
    content = (soup.find("div", class_="entry-content") or soup.find("main")
               or soup.find("article") or soup.body)
    links, seen = [], set()
    if content:
        for a in content.find_all("a", href=True):
            href = a["href"]
            if "finalbastion.com" not in href and not href.startswith("/"):
                continue
            if href in seen:
                continue
            world = _guess_world_from_url(href)
            if world != "Unknown":
                seen.add(href)
                links.append({"world": world, "url": href})
    return links


KNOWN_WORLD_URLS = {
    "Wizard City":  "https://finalbastion.com/wizard101-guides/w101-quest-guides/main-quest-line-wizard-city/",
    "Krokotopia":   "https://finalbastion.com/wizard101-guides/w101-quest-guides/main-quest-line-krokotopia/",
    "Grizzleheim":  "https://finalbastion.com/wizard101-guides/w101-quest-guides/grizzleheim-main-quest-line-guide/",
    "Marleybone":   "https://finalbastion.com/wizard101-guides/w101-quest-guides/marleybone-main-quest-line-guide/",
    "MooShu":       "https://finalbastion.com/wizard101-guides/w101-quest-guides/mooshu-main-quest-line-guide/",
    "Dragonspyre":  "https://finalbastion.com/wizard101-guides/w101-quest-guides/dragonspyre-main-quest-line-guide/",
    "Celestia":     "https://finalbastion.com/wizard101-guides/w101-quest-guides/celestia-main-quest-line-guide/",
    "Zafaria":      "https://finalbastion.com/wizard101-guides/w101-quest-guides/zafaria-main-quest-line-guide/",
    "Wysteria":     "https://finalbastion.com/wizard101-guides/w101-quest-guides/wysteria-main-quest-line-guide/",
    "Avalon":       "https://finalbastion.com/wizard101-guides/w101-quest-guides/avalon-main-quest-line-guide/",
    "Azteca":       "https://finalbastion.com/wizard101-guides/w101-quest-guides/azteca-main-quest-line-guide/",
    "Khrysalis":    "https://finalbastion.com/wizard101-guides/w101-quest-guides/khrysalis-main-quest-line-guide/",
    "Polaris":      "https://finalbastion.com/wizard101-guides/w101-quest-guides/polaris-main-quest-line-guide/",
    "Mirage":       "https://finalbastion.com/wizard101-guides/w101-quest-guides/mirage-main-quest-line/",
    "Empyrea":      "https://finalbastion.com/wizard101-guides/w101-quest-guides/empyrea-main-quest-line/",
    "Karamelle":    "https://finalbastion.com/wizard101-guides/w101-quest-guides/karamelle-main-quest-line-guide/",
    "Lemuria":      "https://finalbastion.com/wizard101-guides/w101-quest-guides/lemuria-main-quest-line-guide/",
    "Novus":        "https://finalbastion.com/wizard101-guides/w101-quest-guides/novus-main-quest-line-guide/",
    "Wallaru":      "https://finalbastion.com/wizard101-guides/w101-quest-guides/wallaru-main-quest-line-guide/",
}


def get_all_world_urls() -> list:
    try:
        links = scrape_master_guide()
        if links:
            found = {x["world"] for x in links}
            for w, u in KNOWN_WORLD_URLS.items():
                if w not in found:
                    links.append({"world": w, "url": u})
            return links
    except Exception as e:
        logger.warning(f"Master guide scrape failed: {e}. Using known URLs.")
    return [{"world": w, "url": u} for w, u in KNOWN_WORLD_URLS.items()]
