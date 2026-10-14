"""
W101 Boss Wiki - Database Builder (v3)
══════════════════════════════════════
Opens real Chrome -> user passes Cloudflare -> fetches from INSIDE the
browser (wiki_client.py). Source is the Settings toggle or --source:
  api    MediaWiki api.php (fast; currently answers 444 "Access Blocked")
  pages  normal wiki pages + action=raw, slowly paced (~5 s per request)

Chrome stays open during the entire build, then closes when done.
This avoids:
  - nodriver's cookie parsing bug (sameParty KeyError)
  - TLS fingerprint mismatch between Chrome and Python requests
  - cf_clearance cookie extraction entirely

Usage:
  python db_builder.py --test Satharilith
  python db_builder.py                      # Full build
  python db_builder.py --offline ./pages    # Parse saved files
"""

import sys
import os
import re
import json
import time
import asyncio
import logging
import argparse
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

# ── Windows UTF-8 console fix ────────────────────────────────────────────────
# The default Windows terminal uses cp1252 which can't encode box-drawing chars,
# em-dashes, checkmarks, etc.  Reconfigure stdout/stderr to UTF-8 if possible.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        # Python < 3.7 fallback
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Install beautifulsoup4: pip install beautifulsoup4")

try:
    import nodriver as uc
    NODRIVER_AVAILABLE = True
except ImportError:
    NODRIVER_AVAILABLE = False

import database as db
import wiki_client
import wiki_errors as we
from wiki_errors import WikiFetchError, WikiStop

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('db_builder.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

WIKI_BASE = wiki_client.WIKI_BASE


# ═══════════════════════════════════════════════════════════════
# WIKI CLIENT  (shared with spell_scraper.py — see wiki_client.py)
# Source (API vs slow page fetch) comes from the Settings toggle or
# --source. Blocks raise WikiStop → the run stops with an exit code.
# ═══════════════════════════════════════════════════════════════

class BossWikiClient(wiki_client.WikiClient):
    """Boss-page helpers on top of the shared client."""

    async def fetch_boss_wikitext(self, wiki_path: str, name: str = "") -> Optional[str]:
        """Boss wikitext; in page fetch, rebuilt from the page if action=raw is challenged."""
        return await self.fetch_page_wikitext(
            wiki_path, lambda html: html_to_creature_wikitext(html, name))

    async def list_all_bosses(self) -> List[Dict]:
        """List all boss pages from Category:Boss + its Boss subcategories."""
        all_bosses: List[Dict] = []
        seen = set()

        def add(titles):
            for t in titles:
                if t not in seen:
                    seen.add(t)
                    all_bosses.append({
                        'name': t.replace('Creature:', '').replace('_', ' '),
                        'wiki_path': t,
                    })

        pages, subcats = await self.list_category('Category:Boss', 'Creature:', subcats=True)
        add(pages)
        print(f"    Category:Boss: {len(all_bosses)} total bosses")
        for sub in subcats:
            if 'Boss' in sub:
                print(f"  Scanning subcategory: {sub}")
                pages, _ = await self.list_category(sub, 'Creature:')
                add(pages)
                print(f"    {sub}: {len(all_bosses)} total bosses")
        return all_bosses


# ═══════════════════════════════════════════════════════════════
# WIKITEXT PARSER (CreatureInfobox templates)
# ═══════════════════════════════════════════════════════════════

class WikitextParser:
    @staticmethod
    def parse_boss(wikitext: str, boss_name: str) -> Dict:
        data = {
            'name': boss_name,
            'health': 'Unknown', 'rank': 'Unknown', 'school': 'Unknown',
            'location': 'Unknown', 'description': '',
            'cheats': [], 'battle_stats': {}, 'spells': [], 'drops': [],
            'minions': [], 'resistances': {},
            'raw_html': wikitext,
            'url': f"{WIKI_BASE}Creature:{boss_name.replace(' ', '_')}",
            'wiki_path': f"Creature:{boss_name.replace(' ', '_')}",
        }
        infobox = WikitextParser._extract_template(wikitext, 'CreatureInfobox')
        if infobox:
            fields = WikitextParser._parse_template_fields(infobox)
            data.update(WikitextParser._map_infobox_fields(fields))

            # Cheats come from |cheatnotes= field (NOT section headers)
            if 'cheatnotes' in fields and fields['cheatnotes'].strip():
                data['cheats'] = WikitextParser._parse_cheatnotes(fields['cheatnotes'])

            # Spells come from |casts= field (semicolon separated)
            if 'casts' in fields and fields['casts'].strip():
                data['spells'] = [s.strip() for s in fields['casts'].split(';') if s.strip()]

            # Spell notes from |spellnotes=
            if 'spellnotes' in fields and fields['spellnotes'].strip():
                notes = WikitextParser._clean(fields['spellnotes'])
                if notes:
                    data['battle_stats']['Spell Notes'] = notes

            # Drops from various item fields
            data['drops'] = WikitextParser._collect_drops(fields)

        return data

    @staticmethod
    def _extract_template(wikitext, name):
        match = re.search(r'\{\{' + re.escape(name), wikitext, re.I)
        if not match: return None
        start, depth, i = match.start(), 0, match.start()
        while i < len(wikitext):
            if wikitext[i:i+2] == '{{': depth += 1; i += 2
            elif wikitext[i:i+2] == '}}':
                depth -= 1
                if depth == 0: return wikitext[start:i+2]
                i += 2
            else: i += 1
        return None

    @staticmethod
    def _parse_template_fields(template_text):
        fields = {}
        inner = re.sub(r'^\{\{[^|]+\|?', '', template_text)
        inner = re.sub(r'\}\}\s*$', '', inner)
        current_key, current_value = None, []
        db_, db2_ = 0, 0
        for char in inner + '|':
            if char == '{': db_ += 1; current_value.append(char)
            elif char == '}': db_ -= 1; current_value.append(char)
            elif char == '[': db2_ += 1; current_value.append(char)
            elif char == ']': db2_ -= 1; current_value.append(char)
            elif char == '|' and db_ <= 0 and db2_ <= 0:
                val = ''.join(current_value).strip()
                if current_key:
                    fields[current_key.strip().lower()] = val
                elif '=' in val:
                    k, _, v = val.partition('=')
                    fields[k.strip().lower()] = v.strip()
                current_key, current_value = None, []
            elif char == '=' and current_key is None and db_ <= 0 and db2_ <= 0:
                current_key = ''.join(current_value).strip()
                current_value = []
            else: current_value.append(char)
        return fields

    @staticmethod
    def _map_infobox_fields(fields):
        data = {}
        data['school'] = fields.get('school', 'Unknown')
        data['rank'] = fields.get('rank', 'Unknown')
        data['health'] = fields.get('heal', fields.get('health', 'Unknown'))
        data['description'] = WikitextParser._clean(fields.get('descrip', ''))

        # Location
        parts = [fields.get(k, '') for k in ('world', 'location', 'subloc1') if fields.get(k)]
        data['location'] = ' > '.join(parts) if parts else 'Unknown'

        # Battle stats - map actual wikitext field names
        stats = {}
        stat_map = {
            'startpips': 'Starting Pips', 'powerpips': 'Power Pips',
            'shadowslots': 'Shadow Pip Slots', 'stunable': 'Stunable',
            'beguilable': 'Beguilable', 'cheats': 'Has Cheats',
            'critical': 'Critical Rating', 'criticalblock': 'Critical Block',
            'outpierce': 'Outgoing Pierce', 'outboost': 'Outgoing Boost',
            'incboost': 'Incoming Boost', 'outhealing': 'Outgoing Healing',
            'inchealing': 'Incoming Healing', 'stunresist': 'Stun Resist',
            'naturalattack': 'Natural Attack', 'crecla': 'Classification',
        }
        for wk, dn in stat_map.items():
            val = fields.get(wk, '').strip()
            if val:
                # Clean multi-line stat values (e.g. "173 Any;\n")
                val = re.sub(r'\s+', ' ', val).strip().rstrip(';')
                stats[dn] = val
        data['battle_stats'] = stats

        # Resistances & boosts
        data['resistances'] = {}
        if 'incresist' in fields and fields['incresist'].strip():
            data['resistances'].update(WikitextParser._parse_pct(fields['incresist'], 'Resist'))
        if 'incboost' in fields and fields['incboost'].strip() and fields['incboost'].strip().lower() != 'none':
            data['resistances'].update(WikitextParser._parse_pct(fields['incboost'], 'Boost'))

        # Minions
        minions = []
        for field in ('minions', 'summons'):
            if field in fields and fields[field].strip():
                for m in fields[field].split(';'):
                    m = m.strip()
                    if m and m not in [x['name'] for x in minions]:
                        minions.append({'name': m, 'health': 'Unknown', 'school': 'Unknown'})
        data['minions'] = minions

        return data

    @staticmethod
    def _parse_pct(text, label):
        result = {}
        for part in text.split(';'):
            part = part.strip()
            if not part: continue
            m = re.match(r'(\d+%?)\s+(.+)', part)
            if m:
                pct = m.group(1) if m.group(1).endswith('%') else m.group(1) + '%'
                result[f"{m.group(2).strip()} {label}"] = pct
        return result

    @staticmethod
    def _parse_cheatnotes(cheatnotes_text):
        """
        Parse the |cheatnotes= field.  Handles ALL wiki markup patterns:

        1. Standard bullets  (*  / **)  — regular cheats & sub-points
        2. Single-colon bullets  (:*)   — sub-points of previous cheat
        3. Double-colon bullets  (::*)  — cycle cheat entries
        4. Colon-only headers   (:'''Cycle Name''')  — cycle section titles
        5. Double-colon text    (::text) — cycle descriptive info
        6. Plain bold text      ('''Interrupt Cycles:''') — cycle intro
        7. Bare template lines  ({{Icon|X}} "quote" - text) — top-level cheats
           with no bullet prefix (e.g. Yevgeny NightCreeper)
        """
        cheats = []
        current = None
        in_cycle = False  # True once we enter a cycle section

        for line in cheatnotes_text.split('\n'):
            line = line.strip()
            if not line:
                continue

            # ── Strip leading colons to determine indent depth ───────
            colon_depth = 0
            tmp = line
            while tmp.startswith(':'):
                colon_depth += 1
                tmp = tmp[1:]
            # tmp is now the line without leading colons

            # ── Double-colon bullet  (::*) — cycle cheat entry ──────
            # e.g.  ::*''"I wrap you in armor!"'' - At the beginning ...
            if colon_depth >= 2 and tmp.startswith('*'):
                stripped = re.sub(r'^\*+\s*', '', tmp)
                text = WikitextParser._clean(stripped)
                if text:
                    current = {'text': text, 'type': WikitextParser._ctype(text), 'sub_points': []}
                    cheats.append(current)
                continue

            # ── Single-colon bullet  (:*) — context-dependent ─────
            # Inside a cycle section → standalone cycle cheat entry
            # Outside a cycle section → sub-point of previous cheat
            if colon_depth == 1 and tmp.startswith('*'):
                stripped = re.sub(r'^\*+\s*', '', tmp)
                text = WikitextParser._clean(stripped)
                if not text:
                    continue
                if in_cycle:
                    # Inside a cycle: :* is always a standalone cheat
                    current = {'text': text, 'type': WikitextParser._ctype(text), 'sub_points': []}
                    cheats.append(current)
                elif current:
                    # Outside a cycle: :* is a sub-point of the previous cheat
                    current['sub_points'].append(text)
                else:
                    # No parent at all — promote to top-level
                    current = {'text': text, 'type': WikitextParser._ctype(text), 'sub_points': []}
                    cheats.append(current)
                continue

            # ── Double-colon text (::text, no bullet) — cycle info ───
            # e.g.  ::The primary interrupt cycle consists of ...
            if colon_depth >= 2 and not tmp.startswith('*'):
                text = WikitextParser._clean(tmp)
                if text:
                    in_cycle = True
                    current = {'text': text, 'type': 'cycle_info', 'sub_points': []}
                    cheats.append(current)
                continue

            # ── Single-colon text (:text, no bullet) — cycle header ──
            # e.g.  :{{Icon|Death}} '''Death Interrupt Cycle''' (First Cycle):
            #       :'''Primary Interrupt Cycle:'''
            #       :This battle revolves around ...
            if colon_depth == 1:
                text = WikitextParser._clean(tmp)
                if not text:
                    continue
                # Determine if this is a TITLE header or descriptive info.
                # Headers are short, have bold markup ('''), school icons, or
                # end with a colon — they name a cycle, not describe it.
                has_bold = bool(re.search(r"'''", tmp))
                has_school_icon = bool(re.match(r'\[(?:Ice|Fire|Storm|Myth|Life|Death|Balance|Star|Sun|Moon|Shadow)\]', text))
                is_short_cycle_label = ('cycle' in text.lower() and len(text) < 60)
                is_header = has_bold or has_school_icon or is_short_cycle_label
                if is_header:
                    in_cycle = True
                    current = {'text': text, 'type': 'cycle_header', 'sub_points': []}
                else:
                    in_cycle = True
                    current = {'text': text, 'type': 'cycle_info', 'sub_points': []}
                cheats.append(current)
                continue

            # ── No colons: standard wiki bullet patterns ─────────────

            # Standard top-level bullet (* but not **)
            if re.match(r'^\*(?!\*)', line):
                text = re.sub(r'^\*\s*', '', line)
                text = WikitextParser._clean(text)
                if text and len(text) > 3:
                    current = {'text': text, 'type': WikitextParser._ctype(text), 'sub_points': []}
                    cheats.append(current)
                continue

            # Standard sub-bullet (** or ***)
            if re.match(r'^\*{2,}', line) and current:
                sub = re.sub(r'^\*+\s*', '', line)
                sub = WikitextParser._clean(sub)
                if sub:
                    current['sub_points'].append(sub)
                continue

            # ── Plain text (no bullet, no colon) ─────────────────────
            # Could be:
            #   a) '''Interrupt Cycles:''' — bold section heading → cycle_info
            #   b) {{Icon|Blade}} "quote" - cheat text → bare top-level cheat
            #   c) Other descriptive text → cycle_info
            text_cleaned = WikitextParser._clean(line)
            if not text_cleaned or len(text_cleaned) <= 3:
                continue

            # Check if this looks like a conditional/cheat line (has a
            # quote + dash pattern, or starts with an Icon reference)
            has_icon = re.match(r'\{\{Icon\|', line)
            has_quote_dash = re.search(r"''\"[^\"]+\"''\s*-", line)
            if has_icon or has_quote_dash:
                current = {'text': text_cleaned, 'type': WikitextParser._ctype(text_cleaned), 'sub_points': []}
                cheats.append(current)
            else:
                in_cycle = True
                current = {'text': text_cleaned, 'type': 'cycle_info', 'sub_points': []}
                cheats.append(current)

        return cheats

    @staticmethod
    def _collect_drops(fields):
        """Collect drops from all the various item fields in the template."""
        drops = []
        drop_fields = [
            'hats', 'robes', 'boots', 'athames', 'amulets', 'rings',
            'wands', 'decks', 'items', 'cards', 'reagents', 'spellements',
            'snacks', 'jewels', 'pins', 'pets', 'seeds', 'mounts', 'spells',
        ]
        for field in drop_fields:
            val = fields.get(field, '').strip()
            if val:
                for item in val.split(';'):
                    item = item.strip()
                    if item:
                        drops.append(f"[{field.title()}] {item}")
        return drops

    @staticmethod
    def _extract_cheats(wikitext):
        """Fallback: extract cheats from == Cheats == section headers (for non-template pages)."""
        cheats = []
        m = re.search(r'={2,4}\s*Cheats?\s*={2,4}\s*\n(.*?)(?=\n={2,4}\s|\Z)', wikitext, re.I | re.S)
        if not m:
            return cheats
        current = None
        for line in m.group(1).split('\n'):
            line = line.strip()
            if not line: continue
            if re.match(r'^\*(?!\*)', line):
                text = WikitextParser._clean(re.sub(r'^\*\s*', '', line))
                if text and len(text) > 3:
                    current = {'text': text, 'type': WikitextParser._ctype(text), 'sub_points': []}
                    cheats.append(current)
            elif re.match(r'^\*{2,}', line) and current:
                sub = WikitextParser._clean(re.sub(r'^\*+\s*', '', line))
                if sub: current['sub_points'].append(sub)
        return cheats

    @staticmethod
    def _ctype(text):
        tl = text.lower()
        # Match based on {{Icon|Type}} patterns (cleaned to just the keyword)
        if any(w in tl for w in ['at the start', 'beginning', 'round 1', 'first round', 'start of the battle']): return 'start_of_battle'
        if any(w in tl for w in ['interrupt', 'whenever', 'each time', 'every round']): return 'interrupt'
        if any(w in tl for w in ['if ', 'when ', 'after ', 'late to combat', 'is defeated', 'feint']): return 'conditional'
        if any(w in tl for w in ['cheat', 'always', 'minion', 'summon']): return 'passive'
        return 'unknown'

    @staticmethod
    def _clean(text):
        if not text: return ''
        # {{Icon|Late}} -> [Late], {{Icon|Round}} -> [Round]
        text = re.sub(r'\{\{Icon\|([^}]+)\}\}', r'[\1]', text, flags=re.I)
        # {{Link|Type|Display}} -> Display, {{link|Creature|Name}} -> Name
        text = re.sub(r'\{\{[Ll]ink\|[^|]+\|([^}]+)\}\}', r'\1', text)
        # Other templates -> remove
        text = re.sub(r'\{\{[^}]*\}\}', '', text)
        # [[Link|Display]] -> Display
        text = re.sub(r'\[\[[^|\]]+\|([^\]]+)\]\]', r'\1', text)
        # [[Link]] -> Link
        text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
        # Bold/italic wiki markup
        text = re.sub(r"'{2,5}", '', text)
        # HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Indent colons at start of line
        text = re.sub(r'^:+', '', text)
        # Whitespace cleanup
        return re.sub(r'\s+', ' ', text).strip()


# ═══════════════════════════════════════════════════════════════
# PAGE HTML → CreatureInfobox  (page-fetch mode)
# The wiki challenges action=raw on every request, so in page-fetch mode
# the infobox is rebuilt from the rendered page and handed to the
# unchanged WikitextParser — including the cheat-notes logic, which works
# on wiki markup. Rendered lists map back to that markup 1:1:
#   <ul><li> → *   <dl><dd> → :   <b> → '''   <i> → ''   icon <img> → {{Icon|X}}
# ═══════════════════════════════════════════════════════════════

FROM_HTML_MARKER = "<!-- w101c: rebuilt from page HTML (action=raw unavailable) -->"

_BLOCK_TAGS = {'p', 'div', 'ul', 'ol', 'dl', 'hr', 'table', 'h2', 'h3', 'h4'}

# Icons that only decorate a stat row ("195 [Any][Critical]" → "195 Any;").
_DECOR_ICONS = {
    'resistance', 'damage', 'armor piercing', 'critical', 'critical block',
    'incoming', 'outgoing', 'healing', 'shadow pip', 'pip', 'power pip',
    'accuracy', 'stun resist', 'pierce', 'boost',
}

# Infobox row label → CreatureInfobox field (single-value rows).
_ROW_FIELDS = {
    'health': 'heal', 'stunable': 'stunable', 'beguilable': 'beguilable',
    'cheats': 'cheats', 'stun resist': 'stunresist',
}
# Rows written as "<value> <school>;" groups (see _stat_groups).
_GROUP_FIELDS = {
    'critical rating': 'critical', 'critical block rating': 'criticalblock',
    'outgoing pierce': 'outpierce', 'outgoing boost': 'outboost',
    'incoming boost': 'incboost', 'incoming resist': 'incresist',
    'outgoing healing': 'outhealing', 'incoming healing': 'inchealing',
}
# Drop-category heading → CreatureInfobox field (WikitextParser._collect_drops).
_DROP_FIELDS = {
    'hats': 'hats', 'robes': 'robes', 'boots': 'boots', 'athames': 'athames',
    'amulets': 'amulets', 'rings': 'rings', 'wands': 'wands', 'decks': 'decks',
    'housing items': 'items', 'items': 'items', 'treasure cards': 'cards',
    'cards': 'cards', 'reagents': 'reagents', 'spellements': 'spellements',
    'snacks': 'snacks', 'jewels': 'jewels', 'pins': 'pins', 'pets': 'pets',
    'seeds': 'seeds', 'mounts': 'mounts', 'spells': 'spells',
}


def _icon_alt(img) -> str:
    """Icon name from an <img>: "Fire", or "(Icon) Fire.png" → "Fire"."""
    alt = (img.get('alt') or '').strip()
    m = re.match(r'^\(Icon\)\s*(.+?)(?:\.png)?$', alt, re.I)
    return m.group(1).strip() if m else alt


def _inline_wikitext(node) -> str:
    """Inline HTML → wiki markup (text, bold, italic, icons; links → text)."""
    if isinstance(node, str):          # NavigableString
        return str(node)
    name = node.name
    if name == 'img':
        # Only "(Icon) X.png" images were {{Icon|X}}; spell cards etc. were
        # [[File:...]] embeds that WikitextParser._clean drops anyway.
        alt = _icon_alt(node)
        src = node.get('src') or ''
        if alt and ('%28Icon%29' in src or '(Icon)' in src):
            return f"{{{{Icon|{alt}}}}}"
        return ''
    if name == 'br':
        return ' '
    if name in ('style', 'script'):
        return ''
    inner = ''.join(_inline_wikitext(c) for c in node.children)
    if name in ('b', 'strong') and inner.strip():
        return f"'''{inner}'''"
    if name in ('i', 'em') and inner.strip():
        return f"''{inner}''"
    return inner


def _html_block_to_wikitext(container, skip_heading: bool = True) -> str:
    """Rendered block (paragraphs + nested lists) → wikitext lines."""
    lines: List[str] = []
    buf: List[str] = []

    def emit(prefix: str, parts: List[str]):
        text = ''.join(parts)
        for ln in text.split('\n'):
            ln = re.sub(r'[ \t]+', ' ', ln).strip()
            if ln:
                lines.append(prefix + ln)

    def walk_item(item, prefix: str):
        parts: List[str] = []
        for c in item.children:
            if getattr(c, 'name', None) in ('ul', 'ol', 'dl'):
                emit(prefix, parts)
                parts = []
                walk_list(c, prefix)
            else:
                parts.append(_inline_wikitext(c).replace('\n', ' '))
        emit(prefix, parts)

    def walk_list(lst, prefix: str):
        mark = {'ul': '*', 'ol': '#', 'dl': ':'}[lst.name]
        for li in lst.find_all(['li', 'dd', 'dt'], recursive=False):
            walk_item(li, prefix + (';' if li.name == 'dt' else mark))

    def walk(node):
        nonlocal buf
        for c in node.children:
            name = getattr(c, 'name', None)
            if name is None or name not in _BLOCK_TAGS:
                buf.append(_inline_wikitext(c))
                continue
            emit('', buf)
            buf = []
            if skip_heading and 'infobox-plain-heading' in (c.get('class') or []):
                continue
            if name in ('ul', 'ol', 'dl'):
                walk_list(c, '')
            elif name == 'p':
                emit('', [_inline_wikitext(x) for x in c.children])
            elif name == 'div':
                walk(c)
        emit('', buf)
        buf = []

    walk(container)
    return '\n'.join(lines)


def _stat_groups(td) -> str:
    """
    "25% to [Fire][Ice] 75% to [Storm] [Resistance]" →
    "25% Fire, Ice;\\n75% Storm;" — the CreatureInfobox list format that
    WikitextParser._parse_pct and the battle-stat display expect.
    """
    groups: List[list] = []
    for c in td.descendants:
        if isinstance(c, str):
            for num in re.findall(r'-?\d[\d,]*%?|None', str(c)):
                groups.append([num, []])
        elif c.name == 'img':
            alt = _icon_alt(c)
            if alt and alt.lower() not in _DECOR_ICONS and groups:
                groups[-1][1].append(alt)
    out = []
    for num, schools in groups:
        out.append(f"{num} {', '.join(schools)};" if schools else f"{num};")
    return '\n'.join(out)


def _container_by_heading(soup, heading: str):
    for p in soup.find_all(class_='infobox-plain-heading'):
        if p.get_text(' ', strip=True).lower() == heading:
            return p.parent
    return None


def html_to_creature_wikitext(html: str, name: str = '') -> Optional[str]:
    """
    Rebuild a ``{{CreatureInfobox}}`` from a rendered Creature: page so
    WikitextParser.parse_boss can read it unchanged. None when the page
    has no creature infobox.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, 'html.parser')
    box = soup.find('table', class_='infobox')
    if box is None:
        return None

    f: Dict[str, str] = {}
    section = ''
    for tr in box.find_all('tr'):
        tds = tr.find_all(['td', 'th'], recursive=False)
        if len(tds) == 1:
            if 'infobox-header' in (tr.get('class') or []):
                section = tds[0].get_text(' ', strip=True).lower()
            continue
        if len(tds) != 2:
            continue
        label = tds[0].get_text(' ', strip=True)
        low = label.lower()
        td = tds[1]
        text = td.get_text(' ', strip=True)
        alts = [_icon_alt(i) for i in td.find_all('img')]
        links = [a.get_text(' ', strip=True) for a in td.find_all('a') if a.get_text(strip=True)]

        if section == 'location':
            f['world'] = label
            parts = [p.strip() for p in text.split(' - ') if p.strip()]
            for key, val in zip(('location', 'subloc1', 'subloc2', 'subloc3'), parts):
                f[key] = val
        elif section == 'allies' and low in ('minion', 'minions', 'summon', 'summons'):
            key = 'minions' if low.startswith('minion') else 'summons'
            f[key] = ''.join(f"{n};" for n in (links or [text]))
        elif low == 'rank':
            m = re.match(r'(\d+)\s*(.*)', text)
            f['rank'] = m.group(1) if m else text
            if m and m.group(2):
                f['cretype'] = m.group(2)
        elif low == 'classification':
            for key, val in zip(('crecla', 'crecla2'), [p.strip() for p in text.split('/')]):
                f[key] = val
        elif low == 'school':
            f['school'] = alts[0] if alts else text
        elif low == 'masteries':
            f['masteries'] = ''.join(f"{a};" for a in alts)
        elif low == 'shadow pips':
            f['shadowslots'] = text.split()[0] if text else ''
        elif low == 'starting pips':
            f['startpips'] = text.split()[0] if text else ''
            f['powerpips'] = 'Yes' if any('power pip' in a.lower() for a in alts) else 'No'
        elif low == 'school pips':
            f['schoolpips'] = '\n'.join(f"{re.sub(r' School Pip$', '', a)};" for a in alts)
        elif low == 'natural attack':
            f['naturalattack'] = ', '.join(a for a in alts if a) or text
        elif low in _GROUP_FIELDS:
            f[_GROUP_FIELDS[low]] = _stat_groups(td)
        elif low in _ROW_FIELDS:
            f[_ROW_FIELDS[low]] = text

    # Description (Speech lives in the same box but is its own field)
    desc = _container_by_heading(soup, 'description')
    if desc is not None:
        text = _html_block_to_wikitext(desc)
        f['descrip'] = re.split(r"^'''Speech:'''", text, flags=re.M)[0].strip()
    cheats = _container_by_heading(soup, 'cheats')
    if cheats is not None:
        f['cheatnotes'] = _html_block_to_wikitext(cheats)
    notes = _container_by_heading(soup, 'spell notes')
    if notes is not None:
        f['spellnotes'] = _html_block_to_wikitext(notes)

    casts = soup.find(class_='cast-list')
    if casts is not None:
        names = [a.get('title') or (a.img.get('alt') if a.img else '') for a in casts.find_all('a')]
        f['casts'] = '\n'.join(f"{n};" for n in names if n)

    for cat in soup.select('div.drop-category'):
        head = cat.find(class_='infobox-plain-heading')
        field = _DROP_FIELDS.get(head.get_text(' ', strip=True).lower()) if head else None
        if not field:
            continue
        items = [a.get_text(' ', strip=True) for a in cat.find_all('a')
                 if a.get_text(strip=True) and not (a.get('href') or '').startswith('/wiki/Category:')]
        if items:
            f[field] = '\n'.join(f"{i};" for i in items)

    if not (f.get('rank') or f.get('heal') or f.get('school')):
        return None
    # A literal "|" in page text would split the template field; the only
    # pipes allowed through are the ones inside our own {{Icon|X}}.
    body = '\n'.join(f"| {k} = {re.sub(r'(?<!{{Icon)[|]', '/', v)}" for k, v in f.items())
    return f"{FROM_HTML_MARKER}\n{{{{CreatureInfobox\n{body}\n}}}}\n"


# ═══════════════════════════════════════════════════════════════
# OFFLINE MODE
# ═══════════════════════════════════════════════════════════════

def build_offline(html_dir, conn):
    html_path = Path(html_dir)
    if not html_path.exists():
        sys.exit(f"Directory not found: {html_dir}")
    files = list(html_path.rglob('*.html')) + list(html_path.rglob('*.htm'))
    for f in html_path.rglob('*'):
        if f.is_file() and 'Creature' in f.name and f.suffix in ('', '.txt'):
            files.append(f)
    print(f"\n  Found {len(files)} files in {html_dir}")
    success = errors = 0
    for i, fp in enumerate(files):
        try:
            name = fp.stem.replace('Creature_', '').replace('Creature:', '').replace('_', ' ').strip()
            if not name or len(name) < 2: continue
            content = fp.read_text(encoding='utf-8', errors='ignore')
            if '{{CreatureInfobox' in content:
                data = WikitextParser.parse_boss(content, name)
            else:
                data = {'name': name, 'health': 'Unknown', 'rank': 'Unknown',
                        'school': 'Unknown', 'location': 'Unknown', 'description': '',
                        'cheats': [], 'battle_stats': {}, 'spells': [], 'drops': [],
                        'minions': [], 'resistances': {}, 'raw_html': content, 'url': ''}
            db.upsert_boss(conn, data)
            success += 1
            if (i+1) % 100 == 0:
                conn.commit()
                print(f"  [{i+1}/{len(files)}] {success} OK, {errors} errors")
        except Exception as e:
            errors += 1
            logger.error(f"Error parsing {fp.name}: {e}")
    conn.commit()
    print(f"\n  [OK] Parsed {success} bosses ({errors} errors)")



# ═══════════════════════════════════════════════════════════════
# MAIN ASYNC BUILD
# ═══════════════════════════════════════════════════════════════

def _safe_print(*args, **kwargs):
    """print() wrapper that survives non-UTF-8 Windows consoles."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        safe = text.encode("cp1252", errors="replace").decode("cp1252")
        print(safe, **{k: v for k, v in kwargs.items() if k != "end"})


async def async_build(conn, test_boss=None, resume=False, source=None) -> int:
    """Returns a process exit code (wiki_errors.EXIT_*)."""
    client = BossWikiClient(source)
    cache_dir = Path("wikitext_cache")
    cache_dir.mkdir(exist_ok=True)

    try:
        # Step 1: Launch browser, user passes Cloudflare, check the source
        print("=" * 60)
        print("  STEP 1: Opening the wiki in Chrome")
        print("=" * 60)
        await client.start()

        # Test mode
        if test_boss:
            _safe_print(f"\n  Fetching: {test_boss}")
            try:
                wt = await client.fetch_boss_wikitext(f"Creature:{test_boss.replace(' ', '_')}",
                                                      test_boss)
            except WikiStop:
                raise
            except WikiFetchError as e:
                _safe_print(f"  [FAIL:{e.code}] {test_boss}  ({e})")
                return we.EXIT_PARTIAL
            if wt:
                dump_path = cache_dir / f"{test_boss.replace(' ', '_')}.txt"
                dump_path.write_text(wt, encoding='utf-8')
                src = "infobox rebuilt from page HTML" if wt.startswith(FROM_HTML_MARKER)                     else "raw wikitext"
                print(f"  ({src} saved to {dump_path})")

                data = WikitextParser.parse_boss(wt, test_boss)
                db.upsert_boss(conn, data)
                conn.commit()
                _safe_print(f"  [OK] {test_boss}")
                return we.EXIT_OK
            _safe_print(f"  [FAIL] {test_boss}")
            return we.EXIT_PARTIAL

        # Full build
        print("\n" + "=" * 60)
        print("  STEP 2: Discovering all boss pages")
        print("=" * 60)

        boss_list = await client.list_all_bosses()
        print(f"\n  Found {len(boss_list)} bosses\n")

        if not boss_list:
            print("  No bosses found - something went wrong.")
            return we.EXIT_PARTIAL

        print("=" * 60)
        print(f"  STEP 3: Fetching {len(boss_list)} boss pages")
        print("=" * 60)
        print("  (Chrome stays open during this - do not close it!)\n")

        # Resume: preload existing boss names (lowercased) so we can skip in O(1)
        # everything already fetched, letting an interrupted build continue from
        # where it stopped instead of grinding back through the whole list.
        existing_names = set()
        if resume:
            existing_names = {n.lower() for n in db.get_boss_names(conn)}
            print(f"  Resume ON — {len(existing_names)} bosses already in DB "
                  f"will be skipped.\n")

        success = errors = skipped = cached = 0
        start_time = time.time()
        progress = wiki_client.Progress(len(boss_list))

        for i, info in enumerate(boss_list):
            name = info['name']
            wiki_path = info['wiki_path']

            # Resume skip: already in DB (any age)
            if resume and name.lower() in existing_names:
                skipped += 1
                continue

            # Skip if already recent in DB (non-resume freshness guard)
            existing = db.get_boss(conn, name)
            if existing and existing.get('last_updated_at', 0) > time.time() - 86400:
                skipped += 1
                continue

            safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
            cache_file = cache_dir / f"{safe_name}.txt"
            try:
                # Page fetch is slow, so it is cache-first: anything already in
                # wikitext_cache/ is parsed from disk, never fetched again.
                if client.is_pages and cache_file.exists():
                    wt = cache_file.read_text(encoding='utf-8', errors='replace')
                    cached += 1
                else:
                    wt = await client.fetch_boss_wikitext(wiki_path, name)
                if wt:
                    data = WikitextParser.parse_boss(wt, name)
                    data['wiki_path'] = wiki_path
                    db.upsert_boss(conn, data)
                    cache_file.write_text(wt, encoding='utf-8')
                    success += 1
                    _safe_print(f"  [OK]   {name}")
                else:
                    errors += 1
                    _safe_print(f"  [FAIL] {name}")
            except WikiStop:
                raise
            except WikiFetchError as e:
                errors += 1
                logger.error(f"{e.code}: {name}: {e}")
                _safe_print(f"  [FAIL:{e.code}] {name}  ({e})")
            except Exception as e:
                errors += 1
                logger.error(f"Error: {name}: {e}")
                _safe_print(f"  [ERR]  {name}  ({e})")

            if client.is_pages:
                conn.commit()  # each page is expensive — never lose one
                _safe_print(progress.line(skipped))
            elif (i + 1) % 25 == 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                _safe_print(f"\n  -- {success} OK  {errors} failed  {skipped} skipped  ({rate:.1f}/sec) --\n")

        conn.commit()
        elapsed = time.time() - start_time

        print(f"\n  [DONE] Finished in {elapsed/60:.1f} minutes")
        print(f"    Succeeded : {success}" + (f" ({cached} from wikitext_cache)" if cached else ""))
        print(f"    Failed    : {errors}")
        print(f"    Skipped   : {skipped} (already up to date)")
        print(f"    Total DB  : {len(db.get_boss_names(conn))} bosses")
        print(f"    Requests  : {client.requests}")
        return wiki_client.run_exit_code(errors)

    except WikiStop as e:
        conn.commit()  # keep everything fetched before the stop
        return we.announce_stop(e)
    finally:
        await client.stop()


def build_via_browser(conn, test_boss=None, resume=False, source=None) -> int:
    """Sync wrapper for the async build. Returns a process exit code."""
    if not NODRIVER_AVAILABLE:
        print("\n  nodriver NOT INSTALLED")
        print("  Install: pip install nodriver")
        print("  You also need Chrome installed.")
        print("\n  Alternative: python db_builder.py --offline ./saved_pages")
        return we.EXIT_NODRIVER_MISSING
    # nodriver needs its own event loop management
    return uc.loop().run_until_complete(
        async_build(conn, test_boss, resume=resume, source=source))


def main() -> int:
    parser = argparse.ArgumentParser(
        description='W101 Boss Wiki - Database Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python db_builder.py --test Satharilith
  python db_builder.py --test "Lord Nightshade"
  python db_builder.py                      # Full build (~1850 bosses)
  python db_builder.py --source pages       # Full build via slow page fetch
  python db_builder.py --offline ./pages    # Parse saved files

Exit codes (wiki_errors.py): 0 OK, 1 some items failed, 3 nodriver missing,
10 checkbox not clicked, 11 blocked, 12 rate-limited, 13 no response,
14 robots.txt, 15 API unavailable, 16 unexpected page,
17 challenged on every request, 130 cancelled
        """
    )
    parser.add_argument('--offline', type=str, help='Path to folder with saved HTML files')
    parser.add_argument('--test', type=str, help='Test fetch a single boss by name')
    parser.add_argument('--resume', action='store_true',
                        help='Skip bosses already in the database (resume an interrupted build)')
    wiki_client.add_source_argument(parser)
    args = parser.parse_args()

    conn = db.get_connection()
    db.init_db(conn)

    code = we.EXIT_OK
    try:
        if args.offline:
            build_offline(args.offline, conn)
        else:
            code = build_via_browser(conn, test_boss=args.test, resume=args.resume,
                                     source=args.source)
    except KeyboardInterrupt:
        code = we.EXIT_CANCELLED
    finally:
        conn.close()
    return code


if __name__ == '__main__':
    sys.exit(main())
