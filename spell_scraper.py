"""
spell_scraper.py
════════════════
Subprocess scraper for Wizard101 spell pages.

Usage:
    python spell_scraper.py --spell "Vengeance"
    python spell_scraper.py --school "Fire"
    python spell_scraper.py --all
    python spell_scraper.py --reparse            # reparse ALL from cached wikitext
    python spell_scraper.py --reparse-spell "Vengeance"   # reparse one spell (offline)
    python spell_scraper.py --list-category      # list available spells in DB

Uses the same BrowserAPIClient approach as db_builder.py:
  Chrome opens once → Cloudflare bypass → all API calls via XHR from inside browser.

Images are saved to:  spell_images/{SpellName}.png
Wikitext cached in:   spell_cache/{SpellName}.txt
"""

import sys
import os
import re
import json
import time
import asyncio
import argparse
import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlencode, unquote
from typing import Optional, List, Dict

# ── Windows UTF-8 fix ─────────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False
    BeautifulSoup = None

try:
    import nodriver as uc
    NODRIVER_OK = True
except ImportError:
    NODRIVER_OK = False

import database_spells as ds
import cf_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("spell_scraper.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

APP_DIR   = Path(__file__).parent
API_URL   = "https://wiki.wizard101central.com/wiki/api.php"
WIKI_BASE = "https://wiki.wizard101central.com/wiki/"
WIKI_ROOT = "https://wiki.wizard101central.com"
IMG_DIR   = APP_DIR / "spell_images"
CACHE_DIR = APP_DIR / "spell_cache"
# Rendered-HTML cache (its own subfolder, .txt files, mirroring the wikitext
# cache above). The rich Training Sources / Other Acquisition / Fusion Formulae
# blocks are produced by Semantic MediaWiki at render time and never appear in
# the raw wikitext, so we cache the rendered HTML here to let --reparse rebuild
# them offline just like everything else.
HTML_CACHE = APP_DIR / "spell_html_cache"
# Fallback images for fusion reagents/results that aren't (yet) fetched as
# their own spell — downloaded here so the A + B = C visual always renders.
FUSION_IMG_DIR = APP_DIR / "spell_images_fusion"

IMG_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
HTML_CACHE.mkdir(exist_ok=True)
FUSION_IMG_DIR.mkdir(exist_ok=True)

SCHOOL_CATEGORY_MAP = {
    "Fire":    "Fire Spells",
    "Ice":     "Ice Spells",
    "Storm":   "Storm Spells",
    "Myth":    "Myth Spells",
    "Life":    "Life Spells",
    "Death":   "Death Spells",
    "Balance": "Balance Spells",
    "Star":    "Star Spells",
    "Moon":    "Moon Spells",
    "Sun":     "Sun Spells",
    "Shadow":  "Shadow Spells",
}


# ═══════════════════════════════════════════════════════════════════════
# BROWSER API CLIENT  (identical pattern to db_builder.py)
# ═══════════════════════════════════════════════════════════════════════

class BrowserAPIClient:
    def __init__(self):
        self.browser = None
        self.page = None
        self._req_count = 0
        # Alerts the parent GUI (via a stdout marker) when the Cloudflare
        # "verify you are human" checkbox appears and needs a manual click.
        self._cf_notifier = cf_alert.ChallengeNotifier()

    async def start(self):
        print("\n  Opening Chrome to solve Cloudflare challenge...")
        self.browser = await uc.start(headless=False)
        self.page = await self.browser.get(WIKI_BASE + "Wizard101_Wiki")
        print("  Waiting for Cloudflare…")
        for i in range(60):
            await asyncio.sleep(1)
            try:
                title = await self.page.evaluate("document.title")
                if "wizard101" in title.lower() or "wiki" in title.lower():
                    print(f"  [OK] Cloudflare passed! ({title[:60]})")
                    self._cf_notifier.reset()
                    break
                # Still on the challenge → alert the GUI (throttled).
                # Title-only: do NOT probe the page or Turnstile loops forever.
                self._cf_notifier.note(cf_alert.title_indicates_challenge(title))
                if i > 0 and i % 10 == 0:
                    print(f"  … still waiting ({i}s)")
            except Exception:
                pass
        await asyncio.sleep(2)
        print("  [OK] Browser ready\n")

    async def stop(self):
        if self.browser:
            self.browser.stop()
            self.browser = None
            self.page = None

    async def api_get(self, params: dict, _retry: int = 0) -> Optional[dict]:
        if not self.page:
            return None
        self._req_count += 1
        params["format"] = "json"
        url = f"{API_URL}?{urlencode(params)}"
        await asyncio.sleep(0.3)
        try:
            result = await self.page.evaluate("""
                (() => {
                    try {
                        const xhr = new XMLHttpRequest();
                        xhr.open("GET", "%s", false);
                        xhr.send();
                        return xhr.status === 200 ? xhr.responseText
                               : JSON.stringify({error: xhr.status});
                    } catch(e) { return JSON.stringify({error: e.message}); }
                })()
            """ % url.replace('"', '\\"'))
            if not result:
                return None
            data = json.loads(result)
            if "error" in data:
                ev = data["error"]
                if ev == 403 and _retry < 2:
                    await self._refresh_session()
                    return await self.api_get(params, _retry=_retry + 1)
            return data
        except Exception as e:
            logger.debug(f"XHR failed ({e})")
            return None

    async def _refresh_session(self):
        try:
            await self.page.get(WIKI_BASE + "Wizard101_Wiki")
            for _ in range(30):
                await asyncio.sleep(1)
                try:
                    t = await self.page.evaluate("document.title")
                    if "wizard101" in t.lower():
                        self._cf_notifier.reset()
                        return
                    # Challenge re-appeared mid-run → alert the GUI (throttled).
                    self._cf_notifier.note(cf_alert.title_indicates_challenge(t))
                except Exception:
                    pass
        except Exception:
            pass

    async def fetch_wikitext(self, wiki_path: str) -> Optional[str]:
        result = await self.api_get({
            "action": "parse",
            "page": wiki_path,
            "prop": "wikitext",
        })
        if result and "parse" in result:
            return result["parse"]["wikitext"].get("*", "")
        return None

    async def fetch_rendered_html(self, wiki_path: str) -> Optional[str]:
        """
        Fetch the fully rendered page HTML (action=parse&prop=text).

        Unlike the raw wikitext, this contains the Semantic-MediaWiki-computed
        Training Sources (Trainer + level, Requirements to Train, Prerequisite,
        "cannot be trained" notes), Other Acquisition Sources, and Fusion
        Formulae blocks — none of which exist as literal wikitext parameters.
        """
        result = await self.api_get({
            "action": "parse",
            "page": wiki_path,
            "prop": "text",
            "disablelimitreport": "1",
        })
        if result and "parse" in result:
            return result["parse"]["text"].get("*", "")
        return None

    async def fetch_image_url(self, wiki_path: str, spell_name: str = "") -> Optional[str]:
        """
        Get the direct URL of the spell card image.

        Strategy (most to least reliable):
          1. query prop=images  → find the "(Spell) ..." file title
             then  query prop=imageinfo&iiprop=url  → get the real URL
          2. parse  prop=text   → BeautifulSoup td.infobox-images img[src]
        """

        # ── Step 1: list images on the page ──────────────────────────
        result = await self.api_get({
            "action": "query",
            "titles": wiki_path,
            "prop": "images",
            "imlimit": "30",
        })

        file_title: Optional[str] = None
        if result and "query" in result:
            pages = result["query"].get("pages", {})
            for page in pages.values():
                imgs = page.get("images", [])
                file_title = _select_card_image_title(imgs, spell_name)
                if file_title:
                    break

        if file_title:
            # ── Step 2: get imageinfo URL ─────────────────────────────
            info = await self.api_get({
                "action": "query",
                "titles": file_title,
                "prop": "imageinfo",
                "iiprop": "url",
            })
            if info and "query" in info:
                for page in info["query"].get("pages", {}).values():
                    iis = page.get("imageinfo", [])
                    if iis and iis[0].get("url"):
                        logger.debug(f"  image via imageinfo: {iis[0]['url'][:80]}")
                        return iis[0]["url"]

        # ── Fallback: parse rendered HTML and grab infobox-images img ──
        logger.debug(f"  imageinfo gave nothing — falling back to HTML parse for {wiki_path}")
        parse_result = await self.api_get({
            "action": "parse",
            "page":   wiki_path,
            "prop":   "text",
            "disablelimitreport": "1",
        })
        if parse_result and "parse" in parse_result:
            html = parse_result["parse"]["text"].get("*", "")
            url = _pick_card_image_from_html(html, spell_name)
            if url:
                return url

        return None

    async def list_spells_in_category(self, category: str) -> List[Dict]:
        """List all Spell: pages in a wiki category."""
        spells: List[Dict] = []
        seen: set = set()
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": "500",
            "cmtype": "page",
            "cmprop": "title|ids",
        }
        while True:
            result = await self.api_get(params)
            if not result or "query" not in result:
                break
            for m in result["query"].get("categorymembers", []):
                title = m.get("title", "")
                if title.startswith("Spell:") and title not in seen:
                    seen.add(title)
                    name = title.replace("Spell:", "").replace("_", " ").strip()
                    spells.append({"name": name, "wiki_path": title})
            print(f"    {category}: {len(spells)} spells so far…")
            if "continue" in result:
                params["cmcontinue"] = result["continue"]["cmcontinue"]
            else:
                break
        return spells

    async def download_image(self, url: str, dest: Path) -> bool:
        """
        Download an image to `dest` via the browser's fetch() API.
        Falls back to navigating directly to the URL and capturing the
        base64 response if the Promise approach times out.
        """
        import base64

        # Approach A: fetch() → Blob → FileReader (works for same-origin images)
        try:
            js = """
                (() => {
                    return new Promise((resolve, reject) => {
                        const timeout = setTimeout(() => resolve(null), 15000);
                        fetch("%s", {credentials: "include"})
                            .then(r => {
                                if (!r.ok) { clearTimeout(timeout); resolve(null); return; }
                                return r.blob();
                            })
                            .then(blob => {
                                if (!blob) { clearTimeout(timeout); resolve(null); return; }
                                const reader = new FileReader();
                                reader.onload = () => { clearTimeout(timeout); resolve(reader.result); };
                                reader.onerror = () => { clearTimeout(timeout); resolve(null); };
                                reader.readAsDataURL(blob);
                            })
                            .catch(e => { clearTimeout(timeout); resolve(null); });
                    });
                })()
            """ % url.replace("\\", "\\\\").replace('"', '\\"')
            data_url = await self.page.evaluate(js)
            if data_url and isinstance(data_url, str) and data_url.startswith("data:"):
                _, b64 = data_url.split(",", 1)
                dest.write_bytes(base64.b64decode(b64))
                return True
        except Exception as e:
            logger.debug(f"fetch() approach failed for {url}: {e}")

        # Approach B: navigate to the image URL directly, read it as data URL
        try:
            await self.page.get(url)
            await asyncio.sleep(1.5)
            # Try to read the page content as an image
            data_url = await self.page.evaluate("""
                (() => {
                    const img = document.querySelector('img');
                    if (!img) return null;
                    const canvas = document.createElement('canvas');
                    canvas.width  = img.naturalWidth  || img.width  || 1;
                    canvas.height = img.naturalHeight || img.height || 1;
                    const ctx = canvas.getContext('2d');
                    try { ctx.drawImage(img, 0, 0); } catch(e) { return null; }
                    return canvas.toDataURL('image/png');
                })()
            """)
            if data_url and isinstance(data_url, str) and data_url.startswith("data:"):
                _, b64 = data_url.split(",", 1)
                dest.write_bytes(base64.b64decode(b64))
                # Navigate back to the wiki
                await self.page.get(WIKI_BASE + "Wizard101_Wiki")
                await asyncio.sleep(0.5)
                return True
        except Exception as e:
            logger.debug(f"Navigation approach failed for {url}: {e}")

        logger.warning(f"All download approaches failed for {url}")
        return False


# ═══════════════════════════════════════════════════════════════════════
# WIKITEXT PARSER
# ═══════════════════════════════════════════════════════════════════════

class SpellParser:

    # Fields we extract from the SpellInfobox template. Verified against
    # the OFFICIAL template doc (Template:SpellInfobox/doc on the wiki):
    #   {{SpellInfobox | school= | pipcost= | shadpipcost= |
    #    schoolpipcost= | accuracy= | type= | PvP= | PvPlevel= |
    #    descrip= | reqspell= | trainpoint= }}
    #
    # CRITICAL BUG FIXED HERE: this map previously looked for a field
    # named "description", which does not exist in the real template —
    # the actual parameter is the abbreviated "descrip". Every spell
    # page on the wiki carries a plain-text description like "Deals
    # 245-285 to target." in this field, which is far more reliable
    # than OCR-ing the number off the card image — but the lookup was
    # silently missing it on every single scrape because of this typo,
    # forcing total reliance on OCR for a value the wiki had as text
    # the whole time. "description" is kept as a secondary alias in
    # case any older/inconsistent page uses the unabbreviated name.
    _FIELD_MAP = {
        "school":          "school",
        "pip cost":        "pip_cost",
        "pipcost":         "pip_cost",
        "shadpipcost":     "shadow_pip_cost",
        "school pip cost": "school_pip_cost",
        "schoolpipcost":   "school_pip_cost",
        "accuracy":        "accuracy",
        "type":            "spell_type",
        "pvp":             "pvp",
        "pvplevel":        "pvp_level",
        "descrip":         "description",
        "description":     "description",  # fallback alias, see above
    }

    @classmethod
    def parse(cls, wikitext: str, name: str, wiki_path: str = "") -> dict:
        data: dict = {
            "name":        name,
            "wiki_path":   wiki_path or f"Spell:{name.replace(' ', '_')}",
            "school":      "Unknown",
            "pip_cost":    "0",
            "school_pip_cost": 0,
            "shadow_pip_cost": 0,
            "accuracy":    0,
            "spell_type":  "",
            "pvp":         False,
            "description": "",
            "where_to_train": "",
            "training_sources": [],
            "spellement_paths": [],
            "raw_wikitext": wikitext,
            "ocr_raw":     "",
            "ocr_damage":  "",
            "ocr_effect":  "",
            "image_path":  "",
        }

        if not wikitext:
            return data

        # ── Parse SpellInfobox template ───────────────────────────────
        infobox_m = re.search(
            r"\{\{SpellInfobox(.*?)\}\}", wikitext, re.DOTALL | re.IGNORECASE
        )
        if not infobox_m:
            # Try generic infobox
            infobox_m = re.search(
                r"\{\{[Ss]pell[^}]{0,20}\n(.*?)\}\}", wikitext, re.DOTALL
            )

        if infobox_m:
            block = infobox_m.group(1) if infobox_m.lastindex else wikitext
            for line in block.split("\n"):
                if "=" not in line:
                    continue
                raw_key, _, raw_val = line.partition("=")
                key = raw_key.strip().lstrip("|").strip().lower()
                val = raw_val.strip()
                val = cls._clean_wikitext(val)
                target = cls._FIELD_MAP.get(key)
                if target == "school":
                    data["school"] = cls._clean_school(val)
                elif target == "pip_cost":
                    data["pip_cost"] = cls._clean_pip(val)
                elif target == "school_pip_cost":
                    try:
                        data["school_pip_cost"] = int(re.search(r"\d+", val).group())
                    except Exception:
                        pass
                elif target == "shadow_pip_cost":
                    try:
                        data["shadow_pip_cost"] = int(re.search(r"\d+", val).group())
                    except Exception:
                        pass
                elif target == "accuracy":
                    m = re.search(r"(\d+)", val)
                    if m:
                        data["accuracy"] = int(m.group(1))
                elif target == "spell_type":
                    data["spell_type"] = val
                elif target == "pvp":
                    data["pvp"] = cls._parse_pvp(raw_val)
                elif target == "description":
                    data["description"] = val

        # ── Training sources ──────────────────────────────────────────
        sources = []
        train_block_m = re.search(
            r"(?:Training Points?|Training Source)[^\n]*\n(.*?)(?:\n==|\Z)",
            wikitext, re.DOTALL | re.IGNORECASE
        )
        if train_block_m:
            block = train_block_m.group(1)
            for line in block.split("\n"):
                clean = cls._clean_wikitext(line).strip("*# ")
                if clean and len(clean) > 2:
                    sources.append(clean)
        data["training_sources"] = sources[:20]
        data["where_to_train"] = "; ".join(sources[:5])

        # ── Spellement paths ──────────────────────────────────────────
        paths = cls._parse_spellement_paths(wikitext)
        data["spellement_paths"] = paths

        return data

    @staticmethod
    def _clean_wikitext(text: str) -> str:
        """Strip wiki markup."""
        # Remove [[File:...]] and [[Image:...]]
        text = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", text, flags=re.IGNORECASE)
        # Remove [[Link|Display]] → keep Display, or [[Target]] → keep Target
        text = re.sub(r"\[\[(?:[^\|\]]*\|)?([^\]]*)\]\]", r"\1", text)
        # Remove {{template|...}} simple
        text = re.sub(r"\{\{[^}]*\}\}", "", text)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    @staticmethod
    def _clean_school(val: str) -> str:
        known = {s.lower(): s for s in ds.SPELL_SCHOOLS}
        v = val.lower().strip()
        return known.get(v, val.title() if val else "Unknown")

    @staticmethod
    def _clean_pip(val: str) -> str:
        v = val.strip()
        if not v or v == "0":
            return "0"
        if re.match(r"^[xX]$", v):
            return "X"
        m = re.search(r"(\d+)", v)
        return m.group(1) if m else "0"

    @staticmethod
    def _parse_pvp(raw_val: str) -> bool:
        """
        Determine PvP legality from a raw (uncleaned) infobox field value.

        Wiki convention often represents this field with an icon image
        rather than literal text — e.g. '[[File:PvPYes.png]]' for legal
        spells, '[[File:NoPvP.png]]' or literal "No" text for banned
        ones. Stripping image links (as _clean_wikitext does for every
        other field) silently empties this field for the common "Yes"
        case, which previously caused every such spell to be wrongly
        marked as PvP-banned. This checks the raw text and any embedded
        filenames before falling back to a safe default.
        """
        raw = (raw_val or "").strip()
        if not raw:
            return False  # field genuinely absent — assume unknown/no

        raw_lower = raw.lower()

        # Explicit "No" indication — either literal text or a filename
        # like "NoPvP.png" / "PvP_No.png" / "PvPNo.png".
        if re.search(r'\bno\b', raw_lower) and not re.search(r'\byes\b', raw_lower):
            return False
        if re.search(r'no[\s_]?pvp|pvp[\s_]?no', raw_lower):
            return False

        # Explicit "Yes" indication.
        if re.search(r'\byes\b', raw_lower):
            return True
        if re.search(r'pvp[\s_]?yes|yes[\s_]?pvp', raw_lower):
            return True

        # No explicit Yes/No wording found, but the field has SOME
        # content (most commonly a bare PvP-allowed icon with no
        # qualifying text). Wiki convention: presence without a "No"
        # marker means the spell is PvP-legal.
        return True

    @staticmethod
    def _parse_spellement_paths(wikitext: str) -> list:
        """Extract spellement upgrade tiers from wikitext."""
        paths = []
        # Look for SpellElement / Spellement sections
        block_m = re.search(
            r"(?:Spellement|SpellElement)[^\n]*\n(.*?)(?:\n==|\Z)",
            wikitext, re.DOTALL | re.IGNORECASE
        )
        if not block_m:
            return paths
        block = block_m.group(1)
        # Each tier looks like "Tier N: ..." or "Level N:"
        tiers = re.findall(
            r"(?:Tier|Level)\s*(\d+)\s*[:–-]\s*([^\n]+)",
            block, re.IGNORECASE
        )
        for tier_n, desc in tiers:
            desc_clean = re.sub(r"\{\{[^}]*\}\}", "", desc).strip()
            desc_clean = re.sub(r"\[\[(?:[^\|\]]*\|)?([^\]]*)\]\]", r"\1", desc_clean)
            desc_clean = re.sub(r"<[^>]+>", "", desc_clean).strip()
            if desc_clean:
                paths.append({
                    "tier": int(tier_n),
                    "description": desc_clean,
                    "damage": "",
                    "effect": "",
                    "image_path": "",
                })
        return paths


# ═══════════════════════════════════════════════════════════════════════
# RENDERED-HTML PARSERS  (training status + fusion formulae)
# ═══════════════════════════════════════════════════════════════════════
# These blocks are generated by Semantic MediaWiki at render time and do NOT
# appear in the raw wikitext, so they are parsed from the rendered page HTML
# (BrowserAPIClient.fetch_rendered_html). Structure verified against real wiki
# output for the trainable / cannot-train / Beastmoon / spellements-to-learn /
# Other-Acquisition / fusion cases.

def _abs_wiki_url(src: str) -> str:
    """Turn a relative /wiki/... image src into an absolute URL."""
    if src and src.startswith("/"):
        return WIKI_ROOT + src
    return src or ""


def _spell_name_from_anchor(a) -> str:
    """Best-effort spell/reagent/NPC/quest display name from an <a> tag."""
    href = a.get("href", "")
    m = re.search(r"/wiki/(?:Spell|Reagent|NPC|Quest|BeastmoonForm):(.+)$", href)
    if m:
        return unquote(m.group(1)).replace("_", " ").strip()
    img = a.find("img")
    if img and img.get("alt"):
        alt = re.sub(r"^\((?:Spell|Reagent)\)\s*", "", img["alt"])
        return re.sub(r"\.png$", "", alt, flags=re.IGNORECASE).strip()
    return a.get_text(" ", strip=True)


def _training_category_lines(cat):
    """Return (heading, [lines]) for one .column-category div."""
    heading_el = cat.find(class_="infobox-plain-heading")
    heading = heading_el.get_text(" ", strip=True) if heading_el else ""
    heading = re.sub(r"\s+", " ", heading).strip()
    lines = []
    ul = cat.find("ul")
    nores = cat.find(class_="noresults")
    if ul:
        for li in ul.find_all("li", recursive=False):
            txt = re.sub(r"\s+", " ", li.get_text(" ", strip=True)).strip()
            if txt:
                lines.append(txt)
    elif nores:
        lines.append(re.sub(r"\s+", " ", nores.get_text(" ", strip=True)).strip())
    else:
        # Free text after the heading (e.g. the Beastmoon / Polymorph
        # "This Spell cannot be trained" explanation).
        full = re.sub(r"\s+", " ", cat.get_text(" ", strip=True)).strip()
        rest = full[len(heading):].strip() if heading and full.startswith(heading) else \
               ("" if full == heading else full)
        if rest:
            lines.append(rest)
    return heading, lines


def _norm_name(s: str) -> str:
    """Normalize a name so spaces/underscores/case don't matter."""
    return re.sub(r"[\s_]+", " ", s or "").strip().lower()


# Enchantment / treasure-card "(Spell) …" files that commonly appear in a
# spell page's Enchantments section but are NOT the spell's own card. Used
# as a safety net so a page-wide image scan can never grab one of these.
_ENCHANT_CARD_NAMES = {
    "accurate", "sharpened blade", "aegis", "cloak", "potent", "indemnity",
    "monstrous", "bladestorm", "primordial", "colossal", "gargantuan",
    "epic", "unbalancer", "extraordinary", "vengeful", "keen eyes",
    "mander treasure", "tough", "giant", "strong", "reliable",
}


def _spell_img_core(text: str) -> str:
    """Extract a normalized card name from a '(Spell) X.png' alt or src.

    Handles both the space form in alt text ("(Spell) Fire Dragon.png") and
    the URL-encoded/underscored form in src ("%28Spell%29_Fire_Dragon.png").
    """
    t = unquote(text or "")
    m = re.search(r"\(spell\)[_\s]*(.+?)\.png", t, re.I)
    return _norm_name(m.group(1)) if m else ""


def _img_matches_spell(text: str, spell_name: str) -> bool:
    """True if an image alt/src is THIS spell's card (and not an enchant)."""
    core = _spell_img_core(text)
    if not core or core in _ENCHANT_CARD_NAMES:
        return False
    target = _norm_name(spell_name)
    # Exact card, or a suffixed variant like "Name (680-720)" / "Name (Tier 2a)".
    return bool(target) and (core == target or core.startswith(target + " "))


def _pick_card_image_from_html(html: str, spell_name: str) -> Optional[str]:
    """
    Choose the spell's card image URL from a rendered page's HTML, matching by
    filename so an enchant card in the Enchantments section is never returned.

    Order:
      1. an image in the infobox-images cell whose filename matches the spell,
      2. any image on the page whose filename matches the spell,
      3. (last resort) a single non-enchant image in the infobox cell.
    Returns None when nothing safe matches → caller yields no image
    (placeholder) rather than a wrong enchant card.
    """
    if not html or not BS4_OK:
        return None
    soup = BeautifulSoup(html, "html.parser")

    def _abs(s: str) -> str:
        return ("https://wiki.wizard101central.com" + s) if s.startswith("/") else s

    td = soup.find("td", class_="infobox-images")
    cell_imgs = td.find_all("img") if td else []

    for img_tag in cell_imgs:
        alt_src = img_tag.get("alt", "") or img_tag.get("src", "")
        if img_tag.get("src") and _img_matches_spell(alt_src, spell_name):
            return _abs(img_tag["src"])

    for img_tag in soup.find_all("img"):
        alt_src = img_tag.get("alt", "") or img_tag.get("src", "")
        if img_tag.get("src") and _img_matches_spell(alt_src, spell_name):
            return _abs(img_tag["src"])

    for img_tag in cell_imgs:
        alt_src = img_tag.get("alt", "") or img_tag.get("src", "")
        core = _spell_img_core(alt_src)
        if img_tag.get("src") and core and core not in _ENCHANT_CARD_NAMES:
            return _abs(img_tag["src"])

    return None


def _select_card_image_title(images: list, spell_name: str) -> Optional[str]:
    """
    Choose the correct "(Spell) …" card-image File: title for a spell from a
    prop=images list, matching by NAME rather than page order.

    A spell page lists not only its own card but also its enchantment cards,
    which are "(Spell) …" files too — e.g. the Accuracy enchant is
    "(Spell) Accurate.png". The old logic compared an underscored name
    ("fires_of_mars") against space-separated titles ("(Spell) Fires of
    Mars.png"), so the exact match never fired and it fell back to the first
    "(Spell)" file alphabetically — usually an enchant like "Accurate",
    "Aegis" or "Sharpened Blade" — which is why base spells silently grabbed
    the wrong art while tier pages (no enchant section) were fine.

    Returns the matching File: title, or None when there's no confident card
    match (the caller then falls back to the rendered infobox image, and
    finally to no image — never to an unrelated enchant card).
    """
    def _norm(s: str) -> str:
        return re.sub(r"[\s_]+", " ", s or "").strip().lower()

    target = _norm(spell_name)
    partial = None
    for img in images or []:
        t = img.get("title", "") if isinstance(img, dict) else str(img)
        m = re.search(r"\(spell\)\s*(.+?)\.png$", t, re.I)
        if not m:
            continue
        core = _norm(m.group(1))
        if core in _ENCHANT_CARD_NAMES:
            continue                       # never an enchant card
        if target and core == target:
            return t                       # exact card-name match — done
        # tolerate suffixed variants like "Name (Battle Card)"
        if target and core.startswith(target + " ") and partial is None:
            partial = t
    return partial


def parse_training_html(html: str) -> dict:
    """
    Parse the Training Sources + Other Acquisition Sources blocks from a
    spell page's rendered HTML into a faithful, render-agnostic structure:

        {"sections": [
            {"title": "Training Sources",
             "categories": [{"heading": "...", "lines": ["...", ...]}, ...]},
            {"title": "Other Acquisition Sources", "categories": [...]},
        ]}

    Empty structure ({"sections": []}) is returned when the HTML has no such
    blocks (or is missing). BeautifulSoup is required; without it, an empty
    structure is returned.
    """
    if not html or not BS4_OK:
        return {"sections": []}
    soup = BeautifulSoup(html, "html.parser")
    sections = []
    for heading_div in soup.find_all(class_="data-table-heading"):
        title = re.sub(r"\s+", " ", heading_div.get_text(" ", strip=True)).strip()
        if not (title.startswith("Training Sources") or
                title.startswith("Other Acquisition")):
            continue
        container = heading_div.parent
        cols = container.find(class_="columns") if container else None
        search_root = cols if cols else container
        cats = []
        if search_root:
            for cat in search_root.find_all(class_="column-category", recursive=True):
                heading, lines = _training_category_lines(cat)
                if heading or lines:
                    cats.append({"heading": heading, "lines": lines})
        if cats:
            sections.append({"title": title, "categories": cats})
    return {"sections": sections}


def parse_fusion_html(html: str) -> list:
    """
    Parse the Fusion Formulae block(s) into a list of recipes:

        [{"components": [{"name": "Anubis",    "img_url": "https://…"},
                         {"name": "Deathblade", "img_url": "https://…"}],
          "result":      {"name": "Anubis' Bite", "img_url": "https://…"}}, …]

    Each `.fusion-container` is a single "A + B = C" recipe; the anchors before
    the '=' are the reagents and the one after is the result (which for a
    reagent's own page is a DIFFERENT spell, so `result` is stored explicitly
    rather than assumed to be the current page).
    """
    if not html or not BS4_OK:
        return []
    soup = BeautifulSoup(html, "html.parser")
    recipes = []
    for fc in soup.find_all(class_="fusion-container"):
        components, result, side = [], None, "components"
        for node in fc.children:
            if getattr(node, "name", None) == "a":
                img = node.find("img")
                entry = {
                    "name": _spell_name_from_anchor(node),
                    "img_url": _abs_wiki_url(img["src"]) if img and img.get("src") else "",
                }
                if side == "components":
                    components.append(entry)
                else:
                    result = entry
            else:
                text = node if isinstance(node, str) else node.get_text()
                if "=" in (text or ""):
                    side = "result"
        if components:
            recipes.append({"components": components, "result": result})
    return recipes


def _safe_spell_name(name: str) -> str:
    """Filename-safe spell name — matches how spell card images are saved."""
    return re.sub(r'[<>:"/\\|?*]', "_", name)


async def _download_fusion_images(client: "BrowserAPIClient", recipes: list):
    """
    Best-effort download of each fusion reagent/result card image into
    FUSION_IMG_DIR, so the A + B = C visual renders even when a component
    spell hasn't been fetched as its own entry. Skips anything already
    present as a normal spell image (spell_images/) or already cached here.
    Never raises — a failed image just falls back to a placeholder in the UI.
    """
    seen = set()
    for recipe in recipes or []:
        parts = list(recipe.get("components", []))
        if recipe.get("result"):
            parts.append(recipe["result"])
        for part in parts:
            nm  = part.get("name", "")
            url = part.get("img_url", "")
            if not nm or not url or nm in seen:
                continue
            seen.add(nm)
            safe = _safe_spell_name(nm)
            if (IMG_DIR / f"{safe}.png").exists():
                continue  # already have the real spell card image
            dest = FUSION_IMG_DIR / f"{safe}.png"
            if dest.exists():
                continue
            try:
                if await client.download_image(url, dest):
                    print(f"    [FUSION] Cached image for {nm}")
            except Exception as e:
                logger.debug(f"Fusion image download failed for {nm}: {e}")


# ═══════════════════════════════════════════════════════════════════════
# OCR
# ═══════════════════════════════════════════════════════════════════════

def _structure_description_text(description: str, spell_type: str = "") -> dict:
    """
    Parse the wiki's own plain-text spell description (the `descrip`
    SpellInfobox field — e.g. "Deals 245-285 to target." or "Heals 75
    over 3 rounds.") into the same structured fields as OCR.

    This is the PREFERRED source over card-image OCR whenever it's
    available: it's clean, grammatical wiki prose written by a human,
    not characters guessed off a small stylized in-game badge. Results
    from here should take priority over OCR results — see fetch_spell()
    in this module, which tries this first and only falls back to OCR
    when the description doesn't yield a parseable number.

    Returns the same dict shape as _structure_ocr_text: damage,
    dot_damage, dot_rounds, heal, heal_rounds, gambit, divided,
    conditional, clear_effect, accuracy_buff, keywords.
    """
    result = {
        "damage": "", "dot_damage": "", "dot_rounds": "",
        "heal": "", "heal_rounds": "",
        "gambit": "", "divided": "", "conditional": "", "clear_effect": "",
        "accuracy_buff": "", "keywords": [],
    }
    if not description:
        return result

    text = description.strip()
    low = text.lower()
    type_lower = (spell_type or "").lower()
    is_heal_type = "heal" in type_lower

    # ── Damage / Heal range: "Deals 245-285 to target." ─────────────────
    # ── DoT range/value FIRST: "X-Y damage over N rounds" — checked
    # before the plain range pattern below so a pure-DoT spell (no
    # separate initial hit) doesn't also get a redundant "damage" value.
    dot_m = re.search(
        r"(\d[\d,]*(?:\s*[-–]\s*\d[\d,]*)?)\s*(?:\w+\s+)?damage over (\d+)\s*round",
        low,
    )
    if dot_m:
        result["dot_damage"] = dot_m.group(1)
        result["dot_rounds"] = dot_m.group(2)

    range_m = re.search(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)\s*(?:\w+\s+)?(?:damage|to|health)", low)
    if range_m and not result["dot_damage"]:
        lo, hi = range_m.group(1), range_m.group(2)
        val = f"{lo}-{hi}"
        if is_heal_type or re.search(r"\bheal", low):
            result["heal"] = val
        else:
            result["damage"] = val

    # ── Heal over time: "Heals 75 over 3 rounds" ─────────────────────────
    hot_m = re.search(r"heal[s]?\s*(\d[\d,]*)\s*over\s*(\d+)\s*round", low)
    if hot_m:
        result["heal"] = hot_m.group(1)
        result["heal_rounds"] = hot_m.group(2)
    elif not result["heal"] and not result["damage"]:
        # Plain heal: "Heals 75 to target" / "Heals 75 health"
        heal_plain = re.search(r"heal[s]?\s*(?:by\s*)?(\d[\d,]*)", low)
        if heal_plain:
            result["heal"] = heal_plain.group(1)

    # ── Plain damage (no range): "Deals 530 to target." ──────────────────
    if not result["damage"] and not result["heal"] and not result["dot_damage"]:
        dmg_m = re.search(r"deals?\s*(\d[\d,]*)\s*(?:\w+\s+)?(?:damage\b|to\s)", low)
        if dmg_m:
            result["damage"] = dmg_m.group(1)

    # ── Buff/Ward/Charm percentage: "applies a -25% Ward" ─────────────────
    buff_m = re.findall(r"([+-]\d+)\s*%", low)
    if buff_m:
        result["accuracy_buff"] = "; ".join(f"{b}%" for b in buff_m[:3])

    # ── Divided among targets: "divided among all enemies" ───────────────
    div_m = re.search(r"divided\s*(?:among|between|to)?\s*(?:all\s*)?(enem|friend)?", low)
    if div_m and "divided" in low and result["damage"]:
        target = div_m.group(1)
        result["divided"] = f"{result['damage']} divided" + (f" ({target}ies)" if target else "")

    # ── Steal / drain: "Steals X health from target" ─────────────────────
    steal_m = re.search(r"steals?\s*(\d[\d,]*)\s*health", low)
    if steal_m:
        result["damage"] = steal_m.group(1)
        result["keywords"].append("Drain/Steal")

    # ── Gambit/Detonate prose (rare in descrip, but check) ────────────────
    if "gambit" in low:
        result["keywords"].append("Gambit")
    if "detonate" in low:
        result["keywords"].append("Detonate")
    if "clear" in low and ("ward" in low or "charm" in low or "trap" in low):
        result["keywords"].append("Clear")

    # ── Icon-preset keywords straight from the clean description prose ──
    # The wiki description names the spell's mechanics in plain English
    # ("... applies a -50% Damage Ward on caster"), so scanning it against
    # the shared icon-keyword patterns lets a spell resolve to SEVERAL
    # icons (Damage + Ward + Caster (Self), ...) instead of one/none.
    # ds.extract_icon_keyword_labels already de-duplicates specific vs
    # generic (e.g. drops "Aura" when "Harmful Aura" is present).
    for label in ds.extract_icon_keyword_labels(text):
        if label not in result["keywords"]:
            result["keywords"].append(label)

    return result


def _structure_ocr_text(raw_text: str, spell_type: str = "", accuracy=None) -> dict:
    """
    Parse raw OCR text into structured spell-effect fields.

    OCR cannot recognize icon graphics directly (Gambit clover, DoT bomb,
    All-Enemies target icon, etc.) — but the TEXT printed alongside those
    icons on the card ("Gambit", "and ... (N)", "then", "to N spell(s)")
    is readable, and is what this function pattern-matches on.

    Uses the spell's already-known `spell_type` field (Healing / Damage /
    Ward / etc., from the reliable wikitext infobox) to correctly
    interpret bare numbers — e.g. a lone "75 (3)" on a Healing-type card
    means "Heal 75 over 3 rounds", not damage.

    Several real-world OCR failure modes are corrected here, found by
    testing against actual production output:

    1. EasyOCR frequently misreads the accuracy badge's "%" as a stray
       apostrophe — "100%" becomes "100'". This orphaned number used to
       be indistinguishable from real damage and got grabbed by the
       fallback parser. It's now normalized back to "100%" up front,
       and numbers followed by "%" (or matching the known accuracy
       value directly) are excluded from the damage fallback.

    2. The fallback bare-number-as-damage logic used to apply to ANY
       non-Heal type. Ward/Charm/Trap/Jinx/Curse/Aura/Enchantment/
       Manipulation/Polymorph/Dispel/Block spells deal in percentages
       or special effects, not flat hit numbers — for these types the
       fallback never fires, so a per-pip-scaling Ward no longer
       fabricates a damage number from its accuracy badge.

    3. A mandatory third capture group for round-count digits could
       backtrack into and cannibalize part of a real second number
       (e.g. "150" silently split into damage="15" + rounds="0"). The
       round-count clause is now a single optional unit that only
       matches a literal "(N)", never bare trailing digits.

    4. Severely corrupted OCR runs (e.g. several numbers merged into
       one 5+ digit blob with no spaces — a sign EasyOCR's text
       regions ran together) are detected and the fallback is skipped
       entirely rather than guessing a number from the wreckage. The
       raw text is always shown in the UI so this can be fixed by hand.

    Returns dict with: damage, dot_damage, dot_rounds, heal,
    heal_rounds, gambit, divided, conditional, clear_effect,
    accuracy_buff, keywords, uncertain (bool).
    """
    result = {
        "damage": "", "dot_damage": "", "dot_rounds": "",
        "heal": "", "heal_rounds": "",
        "gambit": "", "divided": "", "conditional": "", "clear_effect": "",
        "accuracy_buff": "", "keywords": [], "uncertain": False,
    }
    if not raw_text:
        return result

    flat = re.sub(r"\s+", " ", raw_text).strip()

    # Normalize "100'" -> "100%" before anything else parses this text.
    flat = re.sub(r"(\d)\s*['\u2018\u2019\u2032]", r"\1%", flat)

    low = flat.lower()
    type_lower = (spell_type or "").lower()
    is_heal_type = "heal" in type_lower
    is_damage_type = "damage" in type_lower
    NO_DAMAGE_FALLBACK_TYPES = (
        "ward", "charm", "trap", "jinx", "curse", "aura",
        "enchant", "manipulat", "polymorph", "dispel", "block",
    )
    is_non_damage_type = any(t in type_lower for t in NO_DAMAGE_FALLBACK_TYPES)

    # Corruption signal: a 5+ digit unbroken run almost never occurs on
    # a real spell card — it means separate numbers/icons got merged
    # together during OCR with no preserved spacing.
    is_corrupted = bool(re.search(r"\d{5,}", low))
    if is_corrupted:
        result["uncertain"] = True

    # ── Icon-backed keyword detection ──────────────────────────────────
    KEYWORD_PATTERNS = {
        "Gambit":            r"\bgambit\b",
        "Detonate":          r"\bdetonate\b",
        "Damage Over Time":  r"\bdot\b|damage over time",
        "Heal Over Time":    r"\bhot\b|heal over time",
        "All Enemies":       r"all enem",
        "All Friends":       r"all friend",
        "Stun":              r"\bstun\b",
        "Minion":            r"\bminion\b|\bsummon",
        "Polymorph":         r"\bpolymorph\b",
        "Dispel":            r"\bdispel\b",
        "Absorb":            r"\babsorb",
        "Block":             r"\bblock\b",
        "Critical":          r"\bcritical\b",
        "Armor Piercing":    r"\bpierc",
        "Aura":              r"\baura\b",
        "Charm":             r"\bcharm\b",
        "Ward":              r"\bward\b",
        "Trap":              r"\btrap\b",
        "Jinx":              r"\bjinx\b",
        "Curse":             r"\bcurse\b",
        "Blade":             r"\bblade\b",
        "Enchantment":       r"\benchant",
        "Afterlife":         r"\bafterlife\b",
        "Threat":            r"\bthreat\b",
        "No Discard":        r"no discard",
        "Divided":           r"\bdivided\b",
        "Clear":             r"\bclear\b",
        "Per Pip Scaling":   r"per\s*pip",
    }
    for label, pat in KEYWORD_PATTERNS.items():
        if re.search(pat, low):
            result["keywords"].append(label)

    # ── Gambit: "2x Gambit : 100 (10%) then 100" ────────────────────────
    gm = re.search(
        r"(\d+)\s*x\s*gambit\D{0,12}(\d[\d,]*)\D{0,12}(\d{1,3})\s*%\D{0,12}then\D{0,12}(\d[\d,]*)",
        low,
    )
    if gm:
        result["gambit"] = (
            f"{gm.group(1)}x Gambit: {gm.group(2)} "
            f"({gm.group(3)}% chance) then {gm.group(4)}"
        )
    elif "gambit" in low:
        nums = re.findall(r"\d[\d,]*%?", low)
        result["gambit"] = (
            f"Gambit detected — numbers found: {', '.join(nums[:6])}"
            if nums else "Gambit (details unclear from OCR — check raw text)"
        )

    # ── Detonate: "Detonate for +180% then 470 (4)" ─────────────────────
    dm = re.search(
        r"detonate\D{0,12}([+-]?\d+)\s*%\D{0,12}then\D{0,12}(\d[\d,]*)\D{0,12}\(?\s*(\d+)\s*\)?",
        low,
    )
    if dm:
        detonate_txt = (f"Detonate for {dm.group(1)}% then "
                        f"{dm.group(2)} damage over {dm.group(3)} round(s)")
        result["gambit"] = (result["gambit"] + "; " + detonate_txt) if result["gambit"] else detonate_txt

    # ── Divided / AOE: "876 divided to <icon> No Discard" ───────────────
    div = re.search(r"(\d[\d,]*)\D{0,20}divided", low)
    if div:
        result["divided"] = f"{div.group(1)} divided among targets"
        if "enem" in low:
            result["divided"] += " (enemies)"
        elif "friend" in low:
            result["divided"] += " (friends)"

    # ── Clear-for pattern: "225 and Clear [ward] 150 for" or "...for 150"
    # Order-agnostic since OCR reading order on wrapped card text varies.
    clear_m = re.search(
        r"(\d[\d,]*)\D{0,15}and\D{0,3}clear\D{0,30}(\d[\d,]*)",
        low,
    )
    if clear_m and not is_corrupted:
        result["clear_effect"] = (
            f"{clear_m.group(1)} damage, AND Clears target's Ward — "
            f"if cleared, deals an additional {clear_m.group(2)} damage"
        )
        if not result["damage"]:
            result["damage"] = clear_m.group(1)

    # ── Per-pip scaling Ward: "+% per Pip to next [school] spell" ────────
    if re.search(r"per\s*pip", low):
        result["conditional"] = (
            "Scales with pips spent — applies a Ward to your next spell "
            "(no flat percentage readable from card text)"
        )

    # ── Conditional effect: "200 and -50% ... if ... has ..." ───────────
    cond = re.search(
        r"(\d[\d,]*)\D{0,8}and\D{0,4}([+-]\d+)\s*%\D{0,30}if\D{0,30}has",
        low,
    )
    if cond and not is_corrupted:
        result["conditional"] = (
            f"{cond.group(1)} damage, plus {cond.group(2)}% conditional "
            "effect if target has a matching ward/shield"
        )
        if not result["damage"] and not result["dot_damage"]:
            result["damage"] = cond.group(1)

    # ── Heal / Heal Over Time (uses known spell_type to disambiguate) ───
    if is_heal_type and not result["gambit"] and not is_corrupted:
        hot = re.search(r"(\d[\d,]*)\D{0,10}\(\s*(\d+)\s*\)", low)
        if hot:
            result["heal"] = hot.group(1)
            result["heal_rounds"] = hot.group(2)
        else:
            hm = re.search(r"\b(\d{1,4})\b(?!\s*%)", low)
            if hm:
                result["heal"] = hm.group(1)

    # ── DoT pattern: "250 and 300 (3)" → initial + DoT over N rounds ────
    # The round-count clause is ONE optional unit requiring a literal
    # "(N)" — never bare trailing digits — which previously let greedy
    # backtracking cannibalize digits out of the middle of the second
    # number (e.g. "150" silently became damage=15, rounds=0).
    if (is_damage_type or not is_heal_type) and not result["clear_effect"] and not is_corrupted:
        dot = re.search(
            r"(\d[\d,]*)\s*and\s*(?:[a-z]{2,10}\s+){0,2}(\d[\d,]*)\b(?:\D{0,12}\(\s*(\d+)\s*\))?",
            low,
        )
        if dot and "gambit" not in low and "detonate" not in low and not result["heal"]:
            result["damage"]     = dot.group(1)
            result["dot_damage"] = dot.group(2)
            result["dot_rounds"] = dot.group(3) or ""

    # ── Accuracy / generic buff: "+15% to 1 spell" ───────────────────────
    buff = re.search(r"([+-]\d+)\s*%[^\n]{0,12}to\s+(\d+|all)\s+spell", low)
    if buff:
        result["accuracy_buff"] = f"{buff.group(1)}% to {buff.group(2)} spell(s)"
    elif (not result["damage"] and not result["dot_damage"]
          and not result["gambit"] and not result["heal"]
          and not result["divided"] and not result["conditional"]
          and not result["clear_effect"]):
        pct = re.findall(r"([+-]\d+)\s*%", low)
        if pct:
            result["accuracy_buff"] = "; ".join(f"{p}%" for p in pct[:3])

    # ── Plain single-hit fallback — type-aware, accuracy-safe, corruption-aware
    # Only fires if NOTHING else matched. Safeguards against grabbing the
    # accuracy badge or other garbage as if it were damage:
    #   1. Negative lookahead excludes numbers immediately followed by
    #      "%" (covers genuine percentages and the normalized "100'"
    #      misread fixed above).
    #   2. The number is compared against the spell's actual known
    #      accuracy value and skipped if it matches exactly.
    #   3. Ward/Charm/Trap/Jinx/Curse/Aura/Enchantment/Manipulation/
    #      Polymorph/Dispel/Block types never get a damage fallback —
    #      those mechanics aren't flat-hit numbers.
    #   4. If the raw text shows signs of merged/garbled OCR output
    #      (a 5+ digit unbroken run), the fallback is skipped entirely
    #      rather than guessing from the wreckage — better to show
    #      nothing than a confidently wrong number.
    if (not result["damage"] and not result["dot_damage"]
            and not result["gambit"] and not result["heal"]
            and not result["divided"] and not result["conditional"]
            and not result["clear_effect"] and not is_corrupted):
        candidates = re.findall(r"\b(\d{2,4})\b(?!\s*%)", low)
        acc_str = str(accuracy) if accuracy not in (None, "", 0) else None
        candidates = [c for c in candidates if c != acc_str]
        if candidates:
            if is_heal_type:
                result["heal"] = candidates[0]
            elif not is_non_damage_type:
                result["damage"] = candidates[0]

    return result
def _reconstruct_lines_from_boxes(detections) -> str:
    """
    Rebuild human-reading-order text from EasyOCR's per-box detections
    (detail=1 output: list of (bbox, text, confidence)), instead of
    relying on EasyOCR's own paragraph=True merging.

    paragraph=True was found (via real production samples) to actively
    corrupt spell-card text — it merges spatially separate badges (pip
    cost, accuracy circle, damage numbers, round-count parens) into a
    single run-on string with no spacing between them, e.g. distinct
    "1140", "(5)", "75%" badges collapsing into "1140639(58". Cards
    have short, spatially distinct text regions (not paragraphs of
    prose), so reconstructing rows by Y-coordinate clustering preserves
    far more structure: boxes whose vertical centers are close together
    become one line (read left-to-right), separated by newlines.
    """
    if not detections:
        return ""

    # Each detection: (bbox, text, confidence); bbox is 4 corner points
    items = []
    for bbox, text, conf in detections:
        text = str(text).strip()
        if not text:
            continue
        ys = [pt[1] for pt in bbox]
        xs = [pt[0] for pt in bbox]
        y_center = sum(ys) / len(ys)
        x_left   = min(xs)
        items.append((y_center, x_left, text))

    if not items:
        return ""

    items.sort(key=lambda it: it[0])  # sort by Y first

    # Cluster into rows: items within ~12px vertical center distance of
    # the running row average are considered the same line.
    rows = []
    current_row = [items[0]]
    row_y = items[0][0]
    for y, x, text in items[1:]:
        if abs(y - row_y) <= 14:
            current_row.append((y, x, text))
            row_y = sum(it[0] for it in current_row) / len(current_row)
        else:
            rows.append(current_row)
            current_row = [(y, x, text)]
            row_y = y
    rows.append(current_row)

    lines = []
    for row in rows:
        row.sort(key=lambda it: it[1])  # left-to-right within the row
        lines.append(" ".join(it[2] for it in row))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# OCR SCAN STRENGTH
# ═══════════════════════════════════════════════════════════════════════
# User-tunable via HUD & Settings → OCR Settings. The chosen level is saved
# to hud_settings.json (_global.spell_ocr_strength) by the GUI, and read
# here — this scraper runs as its own subprocess and cannot see the GUI's
# in-memory settings object, so it reads the value straight from the file.
#
# Each level maps to a bundle of EasyOCR readtext() parameters:
#   • mag_ratio       — image magnification before detection. Higher upscales
#                       the card so small badges (pip cost, %, damage) are
#                       resolved, at a speed cost.
#   • text_threshold  — detector confidence to accept a character region.
#                       Lower accepts fainter text.
#   • low_text        — low-bound score used to grow text boxes. Lower grows
#                       regions more aggressively.
#   • contrast_ths /  — boxes below contrast_ths are re-read after boosting
#     adjust_contrast   contrast by adjust_contrast; higher helps faint text.
#
# "standard" is exactly EasyOCR's own defaults, so leaving the setting alone
# reproduces the original scan behaviour byte-for-byte.
OCR_STRENGTH_PRESETS = {
    "standard": {"mag_ratio": 1.0, "text_threshold": 0.7, "low_text": 0.4,
                 "contrast_ths": 0.1, "adjust_contrast": 0.5},
    "enhanced": {"mag_ratio": 1.5, "text_threshold": 0.6, "low_text": 0.35,
                 "contrast_ths": 0.1, "adjust_contrast": 0.5},
    "high":     {"mag_ratio": 2.0, "text_threshold": 0.5, "low_text": 0.3,
                 "contrast_ths": 0.2, "adjust_contrast": 0.7},
    "maximum":  {"mag_ratio": 2.5, "text_threshold": 0.4, "low_text": 0.25,
                 "contrast_ths": 0.3, "adjust_contrast": 0.8},
}
_DEFAULT_OCR_STRENGTH = "standard"
_SETTINGS_FILE = APP_DIR / "hud_settings.json"

# Resolved once per process run and cached: a fetch subprocess is launched
# fresh for each user action, so it always picks up the latest saved value,
# while bulk runs (--all / --reparse) read the file only once.
_ocr_strength_kwargs = None


def _get_ocr_strength_kwargs() -> dict:
    """Return the EasyOCR readtext kwargs for the user's saved scan strength."""
    global _ocr_strength_kwargs
    if _ocr_strength_kwargs is not None:
        return _ocr_strength_kwargs

    level = _DEFAULT_OCR_STRENGTH
    try:
        if _SETTINGS_FILE.exists():
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = str(data.get("_global", {}).get("spell_ocr_strength", ""))
            if saved in OCR_STRENGTH_PRESETS:
                level = saved
    except Exception as e:
        logger.debug(f"Could not read OCR strength from settings: {e}")

    _ocr_strength_kwargs = dict(OCR_STRENGTH_PRESETS[level])
    logger.info(f"Spell OCR scan strength: {level} ({_ocr_strength_kwargs})")
    return _ocr_strength_kwargs


# Whether the wiki description / structured fields may assign icons, or only
# true card-image scan results (text OCR + visual matching) may. Read once per
# process from hud_settings.json; each fetch/reparse is a fresh subprocess so
# it always reflects the latest saved value.
_icons_from_desc = None


def _icons_from_description() -> bool:
    global _icons_from_desc
    if _icons_from_desc is not None:
        return _icons_from_desc
    val = True
    try:
        if _SETTINGS_FILE.exists():
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            val = bool(data.get("_global", {}).get(
                "spell_ocr_icons_from_description", True))
    except Exception as e:
        logger.debug(f"Could not read icons-from-description setting: {e}")
    _icons_from_desc = val
    logger.info(f"Icons from description: {val}")
    return val


# Match-confidence threshold for visual icon template matching. Read once per
# process from hud_settings.json; falls back to icon_detector.DEFAULT_CONFIDENCE.
_visual_conf = None


def _visual_confidence():
    global _visual_conf
    if _visual_conf is not None:
        return _visual_conf
    val = None
    try:
        if _SETTINGS_FILE.exists():
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("_global", {}).get("spell_icon_visual_confidence")
            if raw is not None:
                val = max(0.60, min(0.95, float(raw)))
    except Exception as e:
        logger.debug(f"Could not read visual confidence setting: {e}")
    if val is None:
        try:
            from icon_detector import DEFAULT_CONFIDENCE
            val = DEFAULT_CONFIDENCE
        except Exception:
            val = 0.85
    _visual_conf = val
    logger.info(f"Visual icon confidence threshold: {val}")
    return val


def _run_ocr_on_image(image_path: Path, spell_type: str = "", accuracy=None,
                      enable_visual_icons: bool = True, preset_images=None) -> dict:
    """
    Run EasyOCR (text) on a spell card image, structuring the result
    into Gambit / DoT / Heal / Divided / Conditional / Clear-effect
    fields using the spell's already-known Type and Accuracy to
    correctly route bare numbers and reject misread accuracy badges.

    Uses coordinate-based line reconstruction (see
    _reconstruct_lines_from_boxes) instead of EasyOCR's paragraph=True,
    which was found to merge spatially separate card badges into
    unrecoverable garbage on real samples.

    Visual icon-graphic recognition (icon_detector.py) is ON by
    default — real OpenCV template matching against the in-game icon
    dictionary, validated on two independent synthetic benchmarks at
    precision 0.93-1.00 / recall 0.50-0.68 (see icon_detector.py's
    module docstring for the full methodology and numbers). Pass
    enable_visual_icons=False to skip it (e.g. for faster bulk
    reparse runs where you only want the text-based fields refreshed).

    Returns dict with keys: raw, damage, effect, dot_damage, dot_rounds,
    heal, heal_rounds, gambit, divided, conditional, clear_effect,
    keywords, uncertain.
    """
    result = {
        "raw": "", "damage": "", "effect": "",
        "dot_damage": "", "dot_rounds": "",
        "heal": "", "heal_rounds": "",
        "gambit": "", "divided": "", "conditional": "", "clear_effect": "",
        "keywords": "", "uncertain": False,
    }
    if not image_path.exists():
        return result

    all_keywords = []

    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        detections = reader.readtext(str(image_path), detail=1,
                                     **_get_ocr_strength_kwargs())
        result["raw"] = _reconstruct_lines_from_boxes(detections)

        structured = _structure_ocr_text(result["raw"], spell_type, accuracy=accuracy)
        result["damage"]       = structured["damage"]
        result["dot_damage"]   = structured["dot_damage"]
        result["dot_rounds"]   = structured["dot_rounds"]
        result["heal"]         = structured["heal"]
        result["heal_rounds"]  = structured["heal_rounds"]
        result["gambit"]       = structured["gambit"]
        result["divided"]      = structured["divided"]
        result["conditional"]  = structured["conditional"]
        result["clear_effect"] = structured["clear_effect"]
        result["effect"]       = structured["accuracy_buff"]
        result["uncertain"]    = structured["uncertain"]
        all_keywords.extend(structured["keywords"])
    except Exception as e:
        logger.debug(f"Text OCR failed for {image_path}: {e}")

    # Visual icon-graphic recognition — see icon_detector.py. Matches each
    # icon PRESET's own image (Choose Image…) against the card, at the
    # user-tuned confidence threshold (falls back to DEFAULT_CONFIDENCE).
    # Skipped when no presets carry an image.
    if enable_visual_icons and preset_images:
        try:
            from icon_detector import detect_icons, is_available
            if is_available():
                icon_matches = detect_icons(str(image_path), preset_images,
                                            confidence_threshold=_visual_confidence())
                for m in icon_matches:
                    label = f"{m['icon']} (visual, {m['confidence']:.0%})"
                    if label not in all_keywords:
                        all_keywords.append(label)
        except Exception as e:
            logger.debug(f"Icon detection failed for {image_path}: {e}")

    result["keywords"] = ", ".join(all_keywords)
    return result


# ═══════════════════════════════════════════════════════════════════════
# MAIN FETCH LOGIC
# ═══════════════════════════════════════════════════════════════════════

async def fetch_spell(client: BrowserAPIClient, conn: sqlite3.Connection,
                      name: str, wiki_path: str = "", force: bool = False) -> bool:
    """
    Fetch, OCR and store one spell. Returns True on success.

    force=True (used by the detail view's "Re-fetch" button) ignores every
    local cache — wikitext, rendered HTML and the card image are all
    re-downloaded and overwritten, and the auto-detected icon links are
    rebuilt from scratch (manual "＋ Add" picks are preserved).
    """
    if not wiki_path:
        wiki_path = f"Spell:{name.replace(' ', '_')}"
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)

    print(f"  Fetching: {name} ({wiki_path}){'  [FORCE]' if force else ''}")

    # ── Wikitext ──────────────────────────────────────────────────────
    cache_file = CACHE_DIR / f"{safe_name}.txt"
    if cache_file.exists() and not force:
        wikitext = cache_file.read_text(encoding="utf-8", errors="replace")
        print(f"    [CACHE] Using cached wikitext")
    else:
        wikitext = await client.fetch_wikitext(wiki_path)
        if not wikitext:
            print(f"    [FAIL] No wikitext returned for {name}")
            return False
        cache_file.write_text(wikitext, encoding="utf-8")
        print(f"    [OK] Fetched wikitext ({len(wikitext)} chars)")

    # ── Parse ─────────────────────────────────────────────────────────
    data = SpellParser.parse(wikitext, name, wiki_path)
    print(f"    School={data['school']}  Pips={data['pip_cost']}  Acc={data['accuracy']}%")

    # ── Image ─────────────────────────────────────────────────────────
    img_dest = IMG_DIR / f"{safe_name}.png"
    if img_dest.exists() and not force:
        data["image_path"] = str(img_dest)
        print(f"    [CACHE] Image already downloaded")
    else:
        img_url = await client.fetch_image_url(wiki_path, name)
        if img_url:
            ok = await client.download_image(img_url, img_dest)
            if ok:
                data["image_path"] = str(img_dest)
                print(f"    [OK] Image saved → {img_dest.name}")
            else:
                print(f"    [WARN] Image download failed for URL: {img_url[:80]}")
        else:
            print(f"    [WARN] No image URL found for {name}")
        # On a forced re-fetch that failed to download, keep any existing
        # image rather than dropping the reference.
        if not data.get("image_path") and img_dest.exists():
            data["image_path"] = str(img_dest)

    # ── Rendered-HTML training status + fusion formulae ──────────────────
    # These come from the fully rendered page (SMW-computed), never the raw
    # wikitext. Cached to spell_html_cache/{safe}.txt so --reparse can rebuild
    # them offline (see reparse_from_cache).
    html_cache_file = HTML_CACHE / f"{safe_name}.txt"
    if html_cache_file.exists() and not force:
        rendered_html = html_cache_file.read_text(encoding="utf-8", errors="replace")
        print(f"    [CACHE] Using cached rendered HTML")
    else:
        rendered_html = await client.fetch_rendered_html(wiki_path)
        if rendered_html:
            html_cache_file.write_text(rendered_html, encoding="utf-8")
            print(f"    [OK] Fetched rendered HTML ({len(rendered_html)} chars)")
        else:
            print(f"    [WARN] No rendered HTML for {name} (training/fusion skipped)")
    if rendered_html:
        data["training_info"]   = parse_training_html(rendered_html)
        data["fusion_formulae"] = parse_fusion_html(rendered_html)
        n_secs = len(data["training_info"].get("sections", []))
        n_fus  = len(data["fusion_formulae"])
        print(f"    [HTML] Training sections: {n_secs}  •  Fusion recipes: {n_fus}")
        await _download_fusion_images(client, data["fusion_formulae"])

    # ── Description-text parsing (PRIMARY source) ────────────────────────
    # The wiki's own `descrip` field — e.g. "Deals 245-285 to target." —
    # is clean human-written prose, dramatically more reliable than
    # OCR-ing a small stylized number off the card image. This is now
    # tried FIRST (previously this field was silently empty everywhere
    # due to a field-name bug — "description" vs the template's actual
    # "descrip" — forcing total reliance on OCR even though the wiki
    # had the answer in text the whole time).
    desc_parsed = _structure_description_text(data.get("description", ""), data.get("spell_type", ""))
    if desc_parsed["damage"] or desc_parsed["heal"] or desc_parsed["dot_damage"]:
        print(f"    [DESC] Parsed from wiki description text (preferred over OCR):")
        for k in ("damage", "heal", "dot_damage", "dot_rounds", "heal_rounds",
                  "accuracy_buff", "divided", "conditional"):
            if desc_parsed[k]:
                print(f"      {k}: {desc_parsed[k]}")

    # ── OCR (fills gaps the description text didn't cover, plus icon
    # graphic detection which has no text-description equivalent) ───────
    if img_dest.exists():
        ocr = _run_ocr_on_image(img_dest, data.get("spell_type", ""),
                                accuracy=data.get("accuracy", 0),
                                preset_images=ds.list_icon_presets(conn))
        data["ocr_raw"]     = ocr["raw"]
        _img_scan_kw = ocr["keywords"]  # true card-image scan (text OCR + visual)
        data["ocr_keywords"] = ", ".join(
            list(dict.fromkeys(desc_parsed["keywords"] + ocr["keywords"].split(", ")))
        ).strip(", ")

        # Description-parsed value wins; OCR only fills what's missing.
        data["ocr_damage"]       = desc_parsed["damage"]       or ocr["damage"]
        data["ocr_dot_damage"]   = desc_parsed["dot_damage"]   or ocr["dot_damage"]
        data["ocr_dot_rounds"]   = desc_parsed["dot_rounds"]   or ocr["dot_rounds"]
        data["ocr_heal"]         = desc_parsed["heal"]         or ocr["heal"]
        data["ocr_heal_rounds"]  = desc_parsed["heal_rounds"]  or ocr["heal_rounds"]
        data["ocr_gambit"]       = desc_parsed["gambit"]       or ocr["gambit"]
        data["ocr_divided"]      = desc_parsed["divided"]      or ocr["divided"]
        data["ocr_conditional"]  = desc_parsed["conditional"]  or ocr["conditional"]
        data["ocr_clear_effect"] = desc_parsed["clear_effect"] or ocr["clear_effect"]
        data["ocr_effect"]       = desc_parsed["accuracy_buff"] or ocr["effect"]
        # "Uncertain" (garbled OCR warning) only matters if we actually
        # had to fall back to OCR for the core numbers — if the
        # description text gave us the damage/heal value, OCR garbling
        # on the card image is irrelevant and shouldn't scare the user.
        data["ocr_uncertain"] = ocr["uncertain"] and not (
            desc_parsed["damage"] or desc_parsed["heal"] or desc_parsed["dot_damage"]
        )

        if ocr["damage"] and not desc_parsed["damage"]:
            print(f"    [OCR] Damage (no description match, used OCR): {ocr['damage'][:60]}")
        if ocr["dot_damage"] and not desc_parsed["dot_damage"]:
            print(f"    [OCR] DoT (no description match, used OCR): {ocr['dot_damage']} over {ocr['dot_rounds']} round(s)")
        if ocr["heal"] and not desc_parsed["heal"]:
            print(f"    [OCR] Heal (no description match, used OCR): {ocr['heal']}")
        if data["ocr_uncertain"]:
            print(f"    [OCR] WARNING: raw text shows signs of corruption — verify manually")
        if data["ocr_keywords"]:
            print(f"    [OCR] Keywords (text + visual icons): {data['ocr_keywords']}")
    else:
        # No image — still keep whatever the description text gave us.
        data["ocr_damage"]       = desc_parsed["damage"]
        data["ocr_dot_damage"]   = desc_parsed["dot_damage"]
        data["ocr_dot_rounds"]   = desc_parsed["dot_rounds"]
        data["ocr_heal"]         = desc_parsed["heal"]
        data["ocr_heal_rounds"]  = desc_parsed["heal_rounds"]
        data["ocr_gambit"]       = desc_parsed["gambit"]
        data["ocr_divided"]      = desc_parsed["divided"]
        data["ocr_conditional"]  = desc_parsed["conditional"]
        data["ocr_clear_effect"] = desc_parsed["clear_effect"]
        data["ocr_effect"]       = desc_parsed["accuracy_buff"]
        data["ocr_keywords"]     = ", ".join(desc_parsed["keywords"])
        data["ocr_uncertain"]    = False
        _img_scan_kw = ""  # no card image → no true image-scan icons

    # Merge structured-field icon labels (name / Type / school / pip
    # costs / PvP flag) into the keyword string so the icon legend fills
    # in even when the description text and card OCR never name a mechanic
    # explicitly (e.g. "Aegis Deathblade" → Blade + Charm + Death School +
    # Pip). auto_link_ocr_icons resolves each to a real preset (with alias
    # and fuzzy fallback) and drops anything unmatched.
    _field_labels = ds.derive_all_icon_labels(data)
    _existing_kw = [k for k in (data.get("ocr_keywords", "") or "").split(", ") if k.strip()]
    data["ocr_keywords"] = ", ".join(dict.fromkeys(_existing_kw + _field_labels)).strip(", ")

    # If the user disabled description→icon assignment, discard everything
    # except the true card-image scan results (text OCR + visual matching),
    # so descriptions/structured fields no longer add icons that aren't on
    # the card.
    if not _icons_from_description():
        data["ocr_keywords"] = ", ".join(
            dict.fromkeys(k for k in _img_scan_kw.split(", ") if k.strip())
        ).strip(", ")

    # ── Save ──────────────────────────────────────────────────────────
    spell_id = ds.upsert_spell(conn, data)
    # Auto-link OCR-detected keywords to icon presets immediately after
    # saving so the icon legend is populated on first fetch, not only
    # when the user opens the spell detail dialog for the first time.
    if data.get("ocr_keywords"):
        ds.auto_link_ocr_icons(conn, spell_id, data["ocr_keywords"], replace_auto=force)
    elif force:
        # Force re-fetch with nothing detected → clear stale auto links
        # (e.g. a previously mis-attached "No PvP") while keeping manual picks.
        ds.auto_link_ocr_icons(conn, spell_id, "", replace_auto=True)
    print(f"    [OK] Saved to DB")
    return True


def _reparse_row(conn: sqlite3.Connection, row) -> bool:
    """
    Re-parse ONE spell from its cached wikitext / rendered HTML and re-run
    OCR on its existing card image — no network needed. `row` must expose
    the columns: name, raw_wikitext, wiki_path, image_path.

    Returns True if the spell was reparsed and upserted, False if it was
    skipped because no cached wikitext exists for it.

    Shared by both the bulk `--reparse` pass and the single-spell
    `--reparse-spell` path so their behaviour can never drift apart.
    """
    name = row["name"]
    wikitext = row["raw_wikitext"] or ""
    # Also try disk cache
    if not wikitext:
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
        cache_file = CACHE_DIR / f"{safe_name}.txt"
        if cache_file.exists():
            wikitext = cache_file.read_text(encoding="utf-8", errors="replace")
    if not wikitext:
        print(f"  [SKIP] No cached data for {name}")
        return False
    data = SpellParser.parse(wikitext, name, row["wiki_path"] or "")
    data["image_path"] = row["image_path"] or data["image_path"]

    # Rebuild training status + fusion from cached rendered HTML if present.
    # Only set the keys when HTML is available — upsert_spell COALESCEs
    # these, so a spell whose HTML was never cached keeps whatever it had
    # rather than being wiped to empty.
    safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
    html_cache_file = HTML_CACHE / f"{safe_name}.txt"
    if html_cache_file.exists():
        rendered_html = html_cache_file.read_text(encoding="utf-8", errors="replace")
        data["training_info"]   = parse_training_html(rendered_html)
        data["fusion_formulae"] = parse_fusion_html(rendered_html)

    # Description text (preferred source) — re-parsed fresh each
    # time so improvements to _structure_description_text apply
    # retroactively without needing to re-fetch from the wiki.
    desc_parsed = _structure_description_text(data.get("description", ""), data.get("spell_type", ""))

    img_path = Path(data["image_path"]) if data["image_path"] else Path("")
    if img_path.exists():
        ocr = _run_ocr_on_image(img_path, data.get("spell_type", ""),
                                accuracy=data.get("accuracy", 0),
                                preset_images=ds.list_icon_presets(conn))
        data["ocr_raw"] = ocr["raw"]
        _img_scan_kw = ocr["keywords"]  # true card-image scan (text OCR + visual)
        data["ocr_keywords"] = ", ".join(
            list(dict.fromkeys(desc_parsed["keywords"] + ocr["keywords"].split(", ")))
        ).strip(", ")
        data["ocr_damage"]       = desc_parsed["damage"]       or ocr["damage"]
        data["ocr_dot_damage"]   = desc_parsed["dot_damage"]   or ocr["dot_damage"]
        data["ocr_dot_rounds"]   = desc_parsed["dot_rounds"]   or ocr["dot_rounds"]
        data["ocr_heal"]         = desc_parsed["heal"]         or ocr["heal"]
        data["ocr_heal_rounds"]  = desc_parsed["heal_rounds"]  or ocr["heal_rounds"]
        data["ocr_gambit"]       = desc_parsed["gambit"]       or ocr["gambit"]
        data["ocr_divided"]      = desc_parsed["divided"]      or ocr["divided"]
        data["ocr_conditional"]  = desc_parsed["conditional"]  or ocr["conditional"]
        data["ocr_clear_effect"] = desc_parsed["clear_effect"] or ocr["clear_effect"]
        data["ocr_effect"]       = desc_parsed["accuracy_buff"] or ocr["effect"]
        data["ocr_uncertain"] = ocr["uncertain"] and not (
            desc_parsed["damage"] or desc_parsed["heal"] or desc_parsed["dot_damage"]
        )
    else:
        data["ocr_damage"]       = desc_parsed["damage"]
        data["ocr_dot_damage"]   = desc_parsed["dot_damage"]
        data["ocr_dot_rounds"]   = desc_parsed["dot_rounds"]
        data["ocr_heal"]         = desc_parsed["heal"]
        data["ocr_heal_rounds"]  = desc_parsed["heal_rounds"]
        data["ocr_gambit"]       = desc_parsed["gambit"]
        data["ocr_divided"]      = desc_parsed["divided"]
        data["ocr_conditional"]  = desc_parsed["conditional"]
        data["ocr_clear_effect"] = desc_parsed["clear_effect"]
        data["ocr_effect"]       = desc_parsed["accuracy_buff"]
        data["ocr_keywords"]     = ", ".join(desc_parsed["keywords"])
        data["ocr_uncertain"]    = False
        _img_scan_kw = ""  # no card image → no true image-scan icons

    # Same structured-field icon-label enrichment as fetch_spell, so
    # Reparse retroactively fills icon legends for spells scraped
    # before this logic existed.
    _field_labels = ds.derive_all_icon_labels(data)
    _existing_kw = [k for k in (data.get("ocr_keywords", "") or "").split(", ") if k.strip()]
    data["ocr_keywords"] = ", ".join(dict.fromkeys(_existing_kw + _field_labels)).strip(", ")

    # Description→icon assignment disabled → keep only true card-image scan
    # results (text OCR + visual matching).
    if not _icons_from_description():
        data["ocr_keywords"] = ", ".join(
            dict.fromkeys(k for k in _img_scan_kw.split(", ") if k.strip())
        ).strip(", ")

    spell_id = ds.upsert_spell(conn, data)
    # Reparse REBUILDS the auto-detected icon set: clears the previous
    # auto links then re-adds the freshly detected ones (manual "+ Add"
    # picks are preserved). This is how re-running Reparse corrects
    # icons an older, looser detection pass mis-linked.
    ds.auto_link_ocr_icons(conn, spell_id, data.get("ocr_keywords", ""),
                           replace_auto=True)
    print(f"  [OK] {name}")
    return True


async def reparse_from_cache(conn: sqlite3.Connection):
    """Re-parse all spells that have cached wikitext (no network needed)."""
    rows = conn.execute(
        "SELECT name, raw_wikitext, wiki_path, image_path FROM spells"
    ).fetchall()
    ok = fail = 0
    for row in rows:
        if _reparse_row(conn, row):
            ok += 1
        else:
            fail += 1
    print(f"\n  Reparsed {ok} spells, {fail} skipped (no cache)")


def reparse_single_spell(conn: sqlite3.Connection, name: str) -> bool:
    """
    Re-parse a single spell by name from cached data (no network) — the
    offline counterpart to `--spell NAME --force`. Returns True on success.

    Looks the spell up case-insensitively so the name passed from the
    detail view (which the user is already viewing) always resolves.
    """
    row = conn.execute(
        "SELECT name, raw_wikitext, wiki_path, image_path FROM spells "
        "WHERE name = ? COLLATE NOCASE",
        (name,),
    ).fetchone()
    if row is None:
        print(f"  [FAIL] '{name}' is not in the database — fetch it first.")
        return False
    ok = _reparse_row(conn, row)
    print(f"\n  {'[OK]' if ok else '[SKIP]'} {name}")
    return ok


async def download_missing_images(conn: sqlite3.Connection, client: "BrowserAPIClient"):
    """
    (Re-)download spell card images for all spells that are missing one.
    Requires an active browser session.
    """
    rows = conn.execute(
        "SELECT name, wiki_path, image_path FROM spells"
    ).fetchall()
    missing = [
        r for r in rows
        if not r["image_path"] or not Path(r["image_path"]).exists()
    ]
    print(f"\n  {len(missing)} spells missing images — downloading…\n")
    ok = fail = 0
    for row in missing:
        name      = row["name"]
        wiki_path = row["wiki_path"] or f"Spell:{name.replace(' ', '_')}"
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
        img_dest  = IMG_DIR / f"{safe_name}.png"
        print(f"  [{ok + fail + 1}/{len(missing)}] {name}")
        img_url = await client.fetch_image_url(wiki_path, name)
        if not img_url:
            print(f"    [WARN] No image URL found")
            fail += 1
            continue
        success = await client.download_image(img_url, img_dest)
        if success:
            conn.execute(
                "UPDATE spells SET image_path=? WHERE name=? COLLATE NOCASE",
                (str(img_dest), name),
            )
            conn.commit()
            print(f"    [OK] Saved → {img_dest.name}")
            ok += 1
        else:
            print(f"    [FAIL] Download failed")
            fail += 1
    print(f"\n  Done: {ok} downloaded, {fail} failed")


async def main():
    parser = argparse.ArgumentParser(description="Wizard101 Spell Scraper")
    parser.add_argument("--spell",    metavar="NAME",   help="Fetch a single spell by name")
    parser.add_argument("--school",   metavar="SCHOOL", help="Fetch all spells for a school")
    parser.add_argument("--all",      action="store_true", help="Fetch all spells (all schools)")
    parser.add_argument("--reparse",  action="store_true", help="Reparse ALL cached data (no network)")
    parser.add_argument("--reparse-spell", metavar="NAME",
                        help="Reparse a single spell by name from cached data (no network)")
    parser.add_argument("--images",   action="store_true",
                        help="Download missing images for already-scraped spells")
    parser.add_argument("--force",    action="store_true",
                        help="Re-fetch and overwrite caches/images (ignore local cache)")
    parser.add_argument("--resume",   action="store_true",
                        help="Skip spells already in the database — resume an interrupted "
                             "fetch (or grab only newly-added spells) without re-processing "
                             "everything. Ignored when --force is set.")
    args = parser.parse_args()

    # Reparse modes are fully offline — they never open a browser, so the
    # nodriver requirement doesn't apply to them.
    offline_reparse = args.reparse or args.reparse_spell
    if not NODRIVER_OK and not offline_reparse:
        sys.exit("[FAIL] nodriver not installed. Run: pip install nodriver")
    if not BS4_OK:
        print("[WARN] beautifulsoup4 not installed — HTML image fallback disabled")

    conn = ds.get_connection()
    ds.init_spell_tables(conn)

    if args.reparse:
        await reparse_from_cache(conn)
        return

    if args.reparse_spell:
        reparse_single_spell(conn, args.reparse_spell)
        return

    client = BrowserAPIClient()
    try:
        await client.start()

        # Resume: preload the names already in the DB (one cheap query) so a
        # re-started fetch skips completed spells in O(1) instead of grinding
        # back through them (re-OCR + re-upsert). fetch_spell only upserts on a
        # fully successful pass, so "in the DB" reliably means "already done".
        resume_skip = args.resume and not args.force
        existing_names = set()
        if resume_skip:
            existing_names = {r[0].lower() for r in conn.execute("SELECT name FROM spells")}
            print(f"\n  [RESUME] {len(existing_names)} spells already fetched — "
                  f"skipping straight to new/unfetched ones.")
        elif args.resume and args.force:
            print("\n  [RESUME] ignored because --force was given (forcing a full re-fetch).")

        if args.images:
            await download_missing_images(conn, client)

        elif args.spell:
            ok = await fetch_spell(client, conn, args.spell, force=args.force)
            print(f"\n  {'[OK]' if ok else '[FAIL]'} {args.spell}")

        elif args.school:
            school = args.school.strip()
            cat = SCHOOL_CATEGORY_MAP.get(school, f"{school} Spells")
            print(f"\n  Listing {school} spells from Category:{cat}…")
            spells = await client.list_spells_in_category(cat)
            print(f"  Found {len(spells)} spells\n")
            ok = fail = skipped = 0
            for sp in spells:
                if resume_skip and sp["name"].lower() in existing_names:
                    skipped += 1
                    continue
                try:
                    success = await fetch_spell(client, conn, sp["name"], sp["wiki_path"], force=args.force)
                    if success:
                        ok += 1
                    else:
                        fail += 1
                except Exception as e:
                    logger.error(f"Error fetching {sp['name']}: {e}")
                    fail += 1
            tail = f", {skipped} skipped" if skipped else ""
            print(f"\n  Done: {ok} OK, {fail} failed{tail}")

        elif args.all:
            total_ok = total_fail = total_skipped = 0
            for school, cat_name in SCHOOL_CATEGORY_MAP.items():
                print(f"\n── {school} Spells ───────────────────")
                spells = await client.list_spells_in_category(cat_name)
                school_skipped = sum(
                    1 for sp in spells
                    if resume_skip and sp["name"].lower() in existing_names
                )
                note = f"  ({school_skipped} already fetched, skipping)" if school_skipped else ""
                print(f"  {len(spells)} spells found{note}")
                for sp in spells:
                    if resume_skip and sp["name"].lower() in existing_names:
                        total_skipped += 1
                        continue
                    try:
                        ok = await fetch_spell(client, conn, sp["name"], sp["wiki_path"], force=args.force)
                        if ok:
                            total_ok += 1
                        else:
                            total_fail += 1
                    except Exception as e:
                        logger.error(f"Error: {sp['name']}: {e}")
                        total_fail += 1
            print(f"\n{'='*50}")
            tail = f", {total_skipped} skipped" if total_skipped else ""
            print(f"  TOTAL: {total_ok} OK, {total_fail} failed{tail}")

        else:
            parser.print_help()

    finally:
        await client.stop()
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
