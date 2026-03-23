"""
Wiki Scraper Module — DrissionPage Edition
═══════════════════════════════════════════
cloudscraper headers alone get 403'd by Cloudflare now.
DrissionPage controls a REAL Chrome browser, so Cloudflare
sees a legitimate browser session and lets it through.

Approach (Kleinanzeigen-style):
  1. Launch real browser once → get past Cloudflare challenge
  2. Bulk scrape all boss pages → store in local SQLite
  3. Periodic lightweight re-checks only for stale entries
  4. Everything served from local DB after initial population
"""

import re
import time
import logging
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

WIKI_BASE = "https://wiki.wizard101central.com/wiki/"

# ─── Try DrissionPage first, fall back to cloudscraper ──────────
DRISSION_AVAILABLE = False
CLOUDSCRAPER_AVAILABLE = False

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
    DRISSION_AVAILABLE = True
    logger.info("DrissionPage available — real browser Cloudflare bypass enabled")
except ImportError:
    pass

try:
    import cloudscraper
    CLOUDSCRAPER_AVAILABLE = True
except ImportError:
    pass

try:
    from bs4 import BeautifulSoup, Tag
except ImportError:
    raise ImportError("beautifulsoup4 is required: pip install beautifulsoup4")


class WikiScraper:
    """Handles all wiki interaction with real browser Cloudflare bypass."""

    def __init__(self):
        self._browser = None
        self._cloudscraper = None
        self._request_count = 0
        self._last_request_time = 0

        if DRISSION_AVAILABLE:
            try:
                self._init_browser()
                logger.info("Browser initialized for Cloudflare bypass")
            except Exception as e:
                logger.warning(f"DrissionPage init failed: {e}. Falling back to cloudscraper.")
                self._browser = None

        if not self._browser and CLOUDSCRAPER_AVAILABLE:
            self._cloudscraper = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
            )
            logger.info("Using cloudscraper fallback (may get 403)")

        if not self._browser and not self._cloudscraper:
            raise RuntimeError(
                "No scraping backend available!\n"
                "Install DrissionPage: pip install DrissionPage\n"
                "Or cloudscraper:      pip install cloudscraper"
            )

    def _init_browser(self):
        """Initialize a real headless Chrome via DrissionPage."""
        opts = ChromiumOptions()
        opts.set_argument('--headless=new')
        opts.set_argument('--no-sandbox')
        opts.set_argument('--disable-gpu')
        opts.set_argument('--disable-dev-shm-usage')
        opts.set_argument('--window-size=1920,1080')
        opts.set_argument('--disable-blink-features=AutomationControlled')
        opts.set_user_agent(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/125.0.0.0 Safari/537.36'
        )
        self._browser = ChromiumPage(opts)

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < 2.0:
            time.sleep(2.0 - elapsed)
        self._last_request_time = time.time()
        self._request_count += 1

    def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch a page using the best available method."""
        self._rate_limit()

        # Method 1: Real browser (passes Cloudflare)
        if self._browser:
            try:
                self._browser.get(url)
                self._browser.wait.doc_loaded(timeout=15)
                time.sleep(1.5)

                html = self._browser.html

                if html and 'challenge' in html[:3000].lower():
                    logger.info("Cloudflare challenge detected, waiting...")
                    time.sleep(8)
                    html = self._browser.html

                if html and len(html) > 500:
                    return html

            except Exception as e:
                logger.error(f"Browser fetch error for {url}: {e}")

        # Method 2: cloudscraper fallback
        if self._cloudscraper:
            try:
                resp = self._cloudscraper.get(url, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                else:
                    logger.warning(f"HTTP {resp.status_code}: {url}")
            except Exception as e:
                logger.error(f"Cloudscraper error for {url}: {e}")

        return None

    def close(self):
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None

    # ─── BOSS LIST DISCOVERY ────────────────────────────────────

    def discover_boss_links(self, progress_cb: Callable = None) -> List[Dict]:
        all_bosses = []
        seen_names = set()

        category_urls = [
            f"{WIKI_BASE}Category:Bosses",
            f"{WIKI_BASE}Category:Creatures",
        ]

        for cat_url in category_urls:
            if progress_cb:
                progress_cb(f"Scanning: {cat_url.split('/')[-1]}")

            page_url = cat_url
            page_num = 0

            while page_url:
                page_num += 1
                html = self._fetch_html(page_url)
                if not html:
                    break

                soup = BeautifulSoup(html, 'html.parser')
                found_on_page = 0
                for link in soup.select('a[href*="Creature:"]'):
                    href = link.get('href', '')
                    name = link.get_text(strip=True)
                    if not name or name in seen_names:
                        continue
                    if any(skip in name.lower() for skip in ['category:', 'template:', 'file:']):
                        continue
                    wiki_path = href.split('/wiki/')[-1] if '/wiki/' in href else f"Creature:{name.replace(' ', '_')}"
                    all_bosses.append({'name': name, 'wiki_path': wiki_path})
                    seen_names.add(name)
                    found_on_page += 1

                if progress_cb:
                    progress_cb(f"Page {page_num}: found {found_on_page} (total: {len(all_bosses)})")

                next_link = soup.find('a', string=re.compile(r'next\s*(page|\d+)', re.I))
                if not next_link:
                    next_link = soup.find('a', string='next 200')
                if next_link and next_link.get('href'):
                    nh = next_link['href']
                    page_url = f"https://wiki.wizard101central.com{nh}" if nh.startswith('/') else (nh if nh.startswith('http') else None)
                else:
                    page_url = None

        logger.info(f"Discovered {len(all_bosses)} unique creatures/bosses")
        return all_bosses

    # ─── INDIVIDUAL BOSS SCRAPING ───────────────────────────────

    def scrape_boss(self, name: str, wiki_path: str = None) -> Dict:
        if wiki_path is None:
            wiki_path = f"Creature:{name.replace(' ', '_')}"
        url = WIKI_BASE + wiki_path

        html = self._fetch_html(url)
        if not html:
            return self._empty_data(name, "Failed to fetch page (Cloudflare or network error)")

        if 'There is currently no text in this page' in html:
            return self._empty_data(name, "Page not found")

        soup = BeautifulSoup(html, 'html.parser')
        self._clean_soup(soup)

        data = self._extract_all(soup, name)
        data['wiki_path'] = wiki_path
        data['url'] = url
        data['raw_html'] = html
        return data

    def check_boss_active(self, url: str) -> bool:
        if self._cloudscraper:
            try:
                resp = self._cloudscraper.head(url, timeout=15, allow_redirects=True)
                return resp.status_code == 200
            except Exception:
                pass
        if self._browser:
            html = self._fetch_html(url)
            return html is not None and len(html) > 1000
        return False

    # ─── EXTRACTION ─────────────────────────────────────────────

    def _clean_soup(self, soup: BeautifulSoup):
        for tag in soup(['script', 'style', 'noscript', 'nav']):
            tag.decompose()
        for el in soup.select('.mw-editsection, .reference, .noprint'):
            el.decompose()

    def _extract_all(self, soup: BeautifulSoup, boss_name: str) -> Dict:
        return {
            'name': boss_name,
            'health': self._extract_stat(soup, ['Health', 'HP']),
            'rank': self._extract_stat(soup, ['Rank']),
            'school': self._extract_school(soup),
            'location': self._extract_location(soup),
            'description': self._extract_description(soup),
            'cheats': self._extract_cheats(soup),
            'battle_stats': self._extract_battle_stats(soup),
            'spells': self._extract_spells(soup),
            'drops': self._extract_drops(soup),
            'minions': self._extract_minions(soup),
            'resistances': self._extract_resistances(soup),
        }

    def _extract_stat(self, soup, labels):
        infobox = soup.find('table', class_=re.compile(r'infobox|creatureinfobox|infotable', re.I))
        if infobox:
            for row in infobox.find_all('tr'):
                header = row.find(['th', 'td'])
                if header:
                    ht = header.get_text(strip=True)
                    for label in labels:
                        if label.lower() in ht.lower():
                            cells = row.find_all(['td', 'th'])
                            if len(cells) >= 2:
                                return self._clean(cells[-1])
        for label in labels:
            m = re.search(rf'{label}\s*[:=]\s*([\d,.\w\s]+)', soup.get_text(), re.I)
            if m:
                return m.group(1).strip()
        return 'Unknown'

    def _extract_school(self, soup):
        schools = ['Fire','Ice','Storm','Myth','Life','Death','Balance','Shadow','Star','Sun','Moon']
        for img in soup.find_all('img'):
            alt, src = img.get('alt',''), img.get('src','')
            for s in schools:
                if s.lower() in alt.lower() and 'school' in (alt+src).lower():
                    return s
        infobox = soup.find('table', class_=re.compile(r'infobox|creature', re.I))
        if infobox:
            for s in schools:
                if re.search(rf'\b{s}\b', infobox.get_text(), re.I):
                    return s
        return 'Unknown'

    def _extract_location(self, soup):
        for h in soup.find_all(['h2','h3','th','b']):
            if any(w in h.get_text(strip=True).lower() for w in ['location','world']):
                nxt = h.find_next(['td','p','dd'])
                if nxt:
                    loc = self._clean(nxt)
                    if loc and len(loc) < 200:
                        return loc
        return 'Unknown'

    def _extract_description(self, soup):
        content = soup.find('div', class_=re.compile(r'mw-parser-output|mw-content', re.I))
        if content:
            for p in content.find_all('p', recursive=False)[:3]:
                t = self._clean(p)
                if t and len(t) > 20:
                    return t
        return ''

    def _extract_cheats(self, soup):
        cheats, seen = [], set()
        cheat_header = None
        for h in soup.find_all(['h2','h3','h4']):
            if re.search(r'cheat', h.get_text(), re.I):
                cheat_header = h
                break
        if not cheat_header:
            return self._extract_cheats_fallback(soup)

        content = []
        cur = cheat_header.find_next_sibling()
        while cur:
            if isinstance(cur, Tag) and cur.name in ['h2','h3'] and cur != cheat_header:
                if 'cheat' not in cur.get_text(strip=True).lower():
                    break
            content.append(cur)
            cur = cur.find_next_sibling()

        for el in content:
            if isinstance(el, Tag):
                for li in el.find_all('li', recursive=True):
                    text = self._clean(li)
                    if text and len(text) > 5 and text not in seen:
                        subs = []
                        sl = li.find(['ul','ol'])
                        if sl:
                            for sli in sl.find_all('li', recursive=False):
                                st = self._clean(sli)
                                if st: subs.append(st)
                            for si in subs:
                                text = text.replace(si, '').strip()
                        if text:
                            cheats.append({'text': text, 'type': self._classify_cheat(text), 'sub_points': subs})
                            seen.add(text)

        if not cheats:
            for el in content:
                if isinstance(el, Tag) and el.name in ['p','div']:
                    text = self._clean(el)
                    if text and len(text) > 10 and text not in seen:
                        if re.findall(r'"[^"]+"', text) or any(kw in text.lower() for kw in ['cheat','casts','interrupt','trigger']):
                            cheats.append({'text': text, 'type': self._classify_cheat(text), 'sub_points': []})
                            seen.add(text)

        for el in content:
            if isinstance(el, Tag) and el.name == 'table':
                for row in el.find_all('tr'):
                    cells = row.find_all(['td','th'])
                    if len(cells) >= 2:
                        c = f"{self._clean(cells[0])}: {self._clean(cells[1])}"
                        if c.strip(': ') and c not in seen:
                            cheats.append({'text': c, 'type': self._classify_cheat(c), 'sub_points': []})
                            seen.add(c)
        return cheats

    def _extract_cheats_fallback(self, soup):
        cheats = []
        for m in re.finditer(r'"([^"]{10,200})"[^"]{0,300}', soup.get_text()):
            qt = m.group(0)[:500]
            if any(kw in qt.lower() for kw in ['cast','cheat','interrupt','steal','summon']):
                cheats.append({'text': self._clean_str(qt), 'type': 'unknown', 'sub_points': []})
        return cheats

    def _classify_cheat(self, text):
        tl = text.lower()
        if any(w in tl for w in ['at the start','beginning','round 1']): return 'start_of_battle'
        if any(w in tl for w in ['interrupt','whenever','each time','every']): return 'interrupt'
        if any(w in tl for w in ['if ','when ','after ']): return 'conditional'
        if any(w in tl for w in ['cheat','always']): return 'passive'
        return 'unknown'

    def _extract_battle_stats(self, soup):
        stats = {}
        for h in soup.find_all(['h2','h3','h4']):
            ht = h.get_text(strip=True).lower()
            if 'battle' in ht and 'stat' in ht:
                cur = h.find_next_sibling()
                while cur and not (isinstance(cur, Tag) and cur.name in ['h2','h3']):
                    if isinstance(cur, Tag):
                        for line in self._clean(cur).split('\n'):
                            if ':' in line:
                                k, _, v = line.partition(':')
                                if k.strip() and v.strip():
                                    stats[k.strip()] = v.strip()
                    cur = cur.find_next_sibling()
        for table in soup.find_all('table'):
            tt = table.get_text().lower()
            if any(kw in tt for kw in ['critical','pierce','resist','block','pip','shadow pip']):
                for row in table.find_all('tr'):
                    cells = row.find_all(['td','th'])
                    if len(cells) >= 2:
                        k, v = self._clean(cells[0]), self._clean(cells[-1])
                        if k and v and not k.lower().startswith('stat'):
                            stats[k] = v
        return stats

    def _extract_spells(self, soup):
        spells = []
        for h in soup.find_all(['h2','h3','h4']):
            if re.search(r'spell|known\s+spell', h.get_text(), re.I):
                cur = h.find_next_sibling()
                while cur and not (isinstance(cur, Tag) and cur.name in ['h2','h3']):
                    if isinstance(cur, Tag):
                        for li in cur.find_all('li'):
                            s = self._clean(li)
                            if s and s not in spells: spells.append(s)
                    cur = cur.find_next_sibling()
        return spells

    def _extract_drops(self, soup):
        drops = []
        for h in soup.find_all(['h2','h3','h4']):
            if re.search(r'drop|loot|reward', h.get_text(), re.I):
                cur = h.find_next_sibling()
                while cur and not (isinstance(cur, Tag) and cur.name in ['h2','h3']):
                    if isinstance(cur, Tag):
                        for li in cur.find_all('li'):
                            d = self._clean(li)
                            if d and d not in drops: drops.append(d)
                        if cur.name == 'table':
                            for cell in cur.find_all('td'):
                                t = self._clean(cell)
                                if t and len(t) > 2 and t not in drops: drops.append(t)
                    cur = cur.find_next_sibling()
        return drops

    def _extract_minions(self, soup):
        minions = []
        for h in soup.find_all(['h2','h3','h4']):
            if re.search(r'minion', h.get_text(), re.I):
                cur = h.find_next_sibling()
                while cur and not (isinstance(cur, Tag) and cur.name in ['h2','h3']):
                    if isinstance(cur, Tag):
                        t = self._clean(cur)
                        if t and len(t) > 3:
                            m = {'name': t, 'health': 'Unknown', 'school': 'Unknown'}
                            for a in cur.find_all('a', href=re.compile(r'Creature:')):
                                m['name'] = a.get_text(strip=True)
                                m['wiki_path'] = a['href'].split('/wiki/')[-1]
                            if m['name'] not in [x['name'] for x in minions]:
                                minions.append(m)
                    cur = cur.find_next_sibling()
        return minions

    def _extract_resistances(self, soup):
        res = {}
        for table in soup.find_all('table'):
            tt = table.get_text().lower()
            if 'resist' in tt or 'boost' in tt:
                for row in table.find_all('tr'):
                    cells = row.find_all(['td','th'])
                    if len(cells) >= 2:
                        l, v = self._clean(cells[0]), self._clean(cells[-1])
                        if l and v and '%' in v: res[l] = v
        for m in re.finditer(r'(\w+)\s*(?:Resist(?:ance)?|Boost)\s*[:=]?\s*(\d+%)', soup.get_text(), re.I):
            res[f"{m.group(1)} Resistance"] = m.group(2)
        return res

    def _clean(self, el):
        if el is None: return ''
        if isinstance(el, str): return self._clean_str(el)
        return self._clean_str(el.get_text(separator=' ', strip=True))

    def _clean_str(self, text):
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\[.*?\]', '', text)
        return text.replace('\xa0', ' ').strip()

    def _empty_data(self, name, error=''):
        return {
            'name': name, 'health': 'Unknown', 'rank': 'Unknown', 'school': 'Unknown',
            'location': 'Unknown', 'description': f"Error: {error}" if error else '',
            'cheats': [], 'battle_stats': {}, 'spells': [], 'drops': [],
            'minions': [], 'resistances': {},
            'raw_html': '', 'url': '', 'wiki_path': '', 'error': error
        }
