"""
W101 Boss Wiki - Database Builder (v3)
══════════════════════════════════════
Opens real Chrome -> passes Cloudflare -> makes ALL API calls
from INSIDE the browser via fetch(). No cookie extraction needed.

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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('db_builder.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

API_URL = "https://wiki.wizard101central.com/wiki/api.php"
WIKI_BASE = "https://wiki.wizard101central.com/wiki/"


# ═══════════════════════════════════════════════════════════════
# BROWSER-BASED API CLIENT
# All API calls happen inside Chrome via fetch() - same session,
# same cookies, same TLS fingerprint. Cloudflare never notices.
# ═══════════════════════════════════════════════════════════════

class BrowserAPIClient:
    """
    Uses a real Chrome browser (via nodriver) to make MediaWiki API calls.
    The browser passes Cloudflare, then we call fetch() from JS.
    """

    def __init__(self):
        self.browser = None
        self.page = None
        self._request_count = 0

    async def start(self):
        """Launch Chrome and pass Cloudflare."""
        print("\n  Opening Chrome to solve Cloudflare challenge...")
        print("  (a Chrome window will appear - this is normal)\n")

        self.browser = await uc.start(headless=False)
        self.page = await self.browser.get(WIKI_BASE + "Wizard101_Wiki")

        # Wait for Cloudflare to resolve
        print("  Waiting for Cloudflare challenge to resolve...")
        for i in range(60):
            await asyncio.sleep(1)
            try:
                title = await self.page.evaluate("document.title")
                if "wizard101" in title.lower() or "wiki" in title.lower():
                    print(f"  [OK] Cloudflare passed! Page: {title[:60]}")
                    break
                if i > 0 and i % 5 == 0:
                    print(f"  ... still waiting ({i}s)")
            except Exception:
                pass
        else:
            print("  [WARN] Timeout - trying anyway...")

        await asyncio.sleep(2)  # Let cookies settle
        print("  [OK] Browser ready for API calls\n")

    async def stop(self):
        """Close browser."""
        if self.browser:
            self.browser.stop()
            self.browser = None
            self.page = None

    async def api_get(self, params: dict, _retry: int = 0) -> Optional[dict]:
        """
        Make an API call using synchronous XMLHttpRequest inside the browser.
        If we get 403, re-visit the wiki to refresh Cloudflare session, then retry.
        """
        if not self.page:
            return None

        self._request_count += 1
        params['format'] = 'json'
        query_string = urlencode(params)
        url = f"{API_URL}?{query_string}"

        await asyncio.sleep(0.3)

        try:
            result = await self.page.evaluate("""
                (() => {
                    try {
                        const xhr = new XMLHttpRequest();
                        xhr.open("GET", "%s", false);
                        xhr.send();
                        if (xhr.status === 200) {
                            return xhr.responseText;
                        } else {
                            return JSON.stringify({error: xhr.status});
                        }
                    } catch(e) {
                        return JSON.stringify({error: e.message});
                    }
                })()
            """ % url.replace('"', '\\"'))

            if not result:
                return None

            data = json.loads(result)
            if 'error' in data and isinstance(data['error'], (int, str)):
                error_val = data['error']
                # 403 = Cloudflare session expired, try to refresh
                if error_val == 403 and _retry < 2:
                    logger.warning(f"Got 403 - refreshing Cloudflare session (attempt {_retry + 1})...")
                    print(f"  [WARN] 403 detected - refreshing session...")
                    await self._refresh_session()
                    return await self.api_get(params, _retry=_retry + 1)
                logger.warning(f"API error: {error_val}")
                return None
            return data

        except Exception as e:
            logger.debug(f"XHR failed ({e}), trying navigation fallback...")
            try:
                await self.page.get(url)
                await asyncio.sleep(0.8)
                body_text = await self.page.evaluate("""
                    (() => {
                        const pre = document.querySelector('pre');
                        if (pre) return pre.textContent;
                        return document.body ? document.body.innerText : '';
                    })()
                """)
                if body_text:
                    return json.loads(body_text.strip())
            except Exception as e2:
                logger.error(f"Navigation fallback also failed: {e2}")
            return None

    async def _refresh_session(self):
        """Re-visit the wiki to refresh the Cloudflare cf_clearance cookie."""
        try:
            await self.page.get(WIKI_BASE + "Wizard101_Wiki")
            # Wait for Cloudflare to resolve again
            for i in range(30):
                await asyncio.sleep(1)
                try:
                    title = await self.page.evaluate("document.title")
                    if "wizard101" in title.lower() or "wiki" in title.lower():
                        print(f"  [OK] Session refreshed!")
                        await asyncio.sleep(2)
                        return
                except Exception:
                    pass
            print("  [WARN] Session refresh timed out")
        except Exception as e:
            logger.error(f"Session refresh failed: {e}")

    async def test_connection(self) -> bool:
        """Verify the API works from inside the browser."""
        print("  Testing API call...")
        result = await self.api_get({'action': 'query', 'meta': 'siteinfo', 'siprop': 'general'})
        if result and 'query' in result:
            sitename = result['query']['general'].get('sitename', '?')
            print(f"  [OK] API accessible! Site: {sitename}")
            return True
        if result:
            print(f"  [FAIL] API returned unexpected data: {str(result)[:200]}")
        else:
            print("  [FAIL] API returned nothing")
        return False

    async def list_all_bosses(self) -> List[Dict]:
        """List all boss pages from Category:Boss + subcategories."""
        all_bosses = []
        seen = set()

        # Direct members
        await self._list_category(all_bosses, seen, 'Category:Boss')

        # Subcategories
        result = await self.api_get({
            'action': 'query', 'list': 'categorymembers',
            'cmtitle': 'Category:Boss', 'cmlimit': '500', 'cmtype': 'subcat',
        })
        if result and 'query' in result:
            for subcat in result['query'].get('categorymembers', []):
                title = subcat.get('title', '')
                if 'Boss' in title:
                    print(f"  Scanning subcategory: {title}")
                    await self._list_category(all_bosses, seen, title)

        return all_bosses

    async def _list_category(self, results, seen, category):
        params = {
            'action': 'query', 'list': 'categorymembers',
            'cmtitle': category, 'cmlimit': '500',
            'cmtype': 'page', 'cmprop': 'title|ids',
        }
        while True:
            result = await self.api_get(params)
            if not result or 'query' not in result:
                break
            for m in result['query'].get('categorymembers', []):
                title = m.get('title', '')
                if title.startswith('Creature:') and title not in seen:
                    seen.add(title)
                    results.append({
                        'name': title.replace('Creature:', '').replace('_', ' '),
                        'wiki_path': title,
                    })
            print(f"    {category}: {len(results)} total bosses")
            if 'continue' in result:
                params['cmcontinue'] = result['continue']['cmcontinue']
            else:
                break

    async def fetch_wikitext(self, wiki_path: str) -> Optional[str]:
        """Get raw wikitext for a page."""
        result = await self.api_get({
            'action': 'parse', 'page': wiki_path, 'prop': 'wikitext',
        })
        if result and 'parse' in result:
            return result['parse']['wikitext'].get('*', '')
        return None


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


async def async_build(conn, test_boss=None):
    client = BrowserAPIClient()

    try:
        # Step 1: Launch browser, pass Cloudflare
        print("=" * 60)
        print("  STEP 1: Bypassing Cloudflare")
        print("=" * 60)
        await client.start()

        # Step 2: Test API
        print("=" * 60)
        print("  STEP 2: Testing MediaWiki API (via browser fetch)")
        print("=" * 60)
        if not await client.test_connection():
            print("\n  [FAIL] API not working. Try running again.")
            return

        # Test mode
        if test_boss:
            _safe_print(f"\n  Fetching: {test_boss}")
            wt = await client.fetch_wikitext(f"Creature:{test_boss.replace(' ', '_')}")
            if wt:
                cache_dir = Path("wikitext_cache")
                cache_dir.mkdir(exist_ok=True)
                dump_path = cache_dir / f"{test_boss.replace(' ', '_')}.txt"
                dump_path.write_text(wt, encoding='utf-8')
                print(f"  (raw wikitext saved to {dump_path})")

                data = WikitextParser.parse_boss(wt, test_boss)
                db.upsert_boss(conn, data)
                conn.commit()
                _safe_print(f"  [OK] {test_boss}")
            else:
                _safe_print(f"  [FAIL] {test_boss}")
            return

        # Full build
        print("\n" + "=" * 60)
        print("  STEP 3: Discovering all boss pages")
        print("=" * 60)

        boss_list = await client.list_all_bosses()
        print(f"\n  Found {len(boss_list)} bosses\n")

        if not boss_list:
            print("  No bosses found - something went wrong.")
            return

        print("=" * 60)
        print(f"  STEP 4: Fetching {len(boss_list)} boss pages")
        print("=" * 60)
        print("  (Chrome stays open during this - don't close it!)\n")

        cache_dir = Path("wikitext_cache")
        cache_dir.mkdir(exist_ok=True)

        success = errors = skipped = 0
        start_time = time.time()

        for i, info in enumerate(boss_list):
            name = info['name']
            wiki_path = info['wiki_path']

            # Skip if already recent in DB
            existing = db.get_boss(conn, name)
            if existing and existing.get('last_updated_at', 0) > time.time() - 86400:
                skipped += 1
                continue

            try:
                wt = await client.fetch_wikitext(wiki_path)
                if wt:
                    data = WikitextParser.parse_boss(wt, name)
                    data['wiki_path'] = wiki_path
                    db.upsert_boss(conn, data)
                    safe_name = re.sub(r'[<>:"/\\|?*]', '_', name)
                    (cache_dir / f"{safe_name}.txt").write_text(wt, encoding='utf-8')
                    success += 1
                    _safe_print(f"  [OK]   {name}")
                else:
                    errors += 1
                    _safe_print(f"  [FAIL] {name}")
            except Exception as e:
                errors += 1
                logger.error(f"Error: {name}: {e}")
                _safe_print(f"  [ERR]  {name}  ({e})")

            if (i + 1) % 25 == 0:
                conn.commit()
                elapsed = time.time() - start_time
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                _safe_print(f"\n  -- {success} OK  {errors} failed  {skipped} skipped  ({rate:.1f}/sec) --\n")

        conn.commit()
        elapsed = time.time() - start_time

        print(f"\n  [DONE] Finished in {elapsed/60:.1f} minutes")
        print(f"    Succeeded : {success}")
        print(f"    Failed    : {errors}")
        print(f"    Skipped   : {skipped} (already up to date)")
        print(f"    Total DB  : {len(db.get_boss_names(conn))} bosses")

    finally:
        await client.stop()


def build_via_browser(conn, test_boss=None):
    """Sync wrapper for the async build."""
    if not NODRIVER_AVAILABLE:
        print("\n  nodriver NOT INSTALLED")
        print("  Install: pip install nodriver")
        print("  You also need Chrome installed.")
        print("\n  Alternative: python db_builder.py --offline ./saved_pages")
        return
    # nodriver needs its own event loop management
    uc.loop().run_until_complete(async_build(conn, test_boss))


def main():
    parser = argparse.ArgumentParser(
        description='W101 Boss Wiki - Database Builder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python db_builder.py --test Satharilith
  python db_builder.py --test "Lord Nightshade"
  python db_builder.py                      # Full build (~1850 bosses)
  python db_builder.py --offline ./pages    # Parse saved files
        """
    )
    parser.add_argument('--offline', type=str, help='Path to folder with saved HTML files')
    parser.add_argument('--test', type=str, help='Test fetch a single boss by name')
    args = parser.parse_args()

    conn = db.get_connection()
    db.init_db(conn)

    if args.offline:
        build_offline(args.offline, conn)
    else:
        build_via_browser(conn, test_boss=args.test)

    conn.close()


if __name__ == '__main__':
    main()
