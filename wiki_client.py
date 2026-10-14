"""
wiki_client.py  —  One shared wiki client for db_builder.py + spell_scraper.py
══════════════════════════════════════════════════════════════════════════════
Replaces the two copied ``BrowserAPIClient`` classes. Everything runs inside
one real Chrome (nodriver) session; the user clicks Cloudflare's checkbox by
hand (cf_alert plays the sound). Two sources, picked by the
Settings → "Wiki Fetch Source" toggle (``hud_settings.json`` →
``_global.wiki_fetch_mode``) or ``--source`` on the command line:

  api    The original path: MediaWiki api.php via XHR from inside the page.
         Fast, but api.php now answers HTTP 444 "Access Blocked".

  pages  Reads normal wiki pages the way a visitor's browser does:
           wikitext   index.php?title=X&action=raw   (same-site fetch)
           HTML       the loaded page's #mw-content-text
           category   Category: page links, following "next page"
           images     same-site fetch()
         Slow on purpose: 4–6 s between requests (random), one tab, one
         request at a time, ≤ 600 requests/hour, robots.txt respected.

Rules (both modes): a block stops the run for good (``WikiStop`` → process
exit code, see wiki_errors.py). No proxies, no fingerprint tricks, no
automated CAPTCHA solving, and no DOM reads on a challenge page — only
``document.title`` is read until the title says we're on a real wiki page.

Standalone Step 0 check (opens Chrome, you click the checkbox once):
    python wiki_client.py --probe
"""

import re
import sys
import json
import time
import random
import asyncio
import argparse
import logging
from collections import deque
from pathlib import Path
from typing import Callable, Optional, List, Dict, Tuple
from urllib.parse import quote, urlencode
from urllib import robotparser

try:
    import nodriver as uc
    NODRIVER_OK = True
except ImportError:
    uc = None
    NODRIVER_OK = False

import cf_alert
import wiki_errors as we
from wiki_errors import WikiFetchError, WikiStop

logger = logging.getLogger(__name__)

APP_DIR       = Path(__file__).parent
SETTINGS_FILE = APP_DIR / "hud_settings.json"

WIKI_ROOT = "https://wiki.wizard101central.com"
WIKI_BASE = WIKI_ROOT + "/wiki/"
API_URL   = WIKI_BASE + "api.php"
INDEX_URL = WIKI_BASE + "index.php"
HOME_PATH = "Wizard101_Wiki"

MODE_API   = "api"
MODE_PAGES = "pages"
MODES      = (MODE_API, MODE_PAGES)
DEFAULT_MODE = MODE_API

# Page-mode pacing (see the plan): 4–6 s random gap, ≤ 600 requests/hour.
PAGE_GAP_MIN   = 4.0
PAGE_GAP_MAX   = 6.0
PAGE_HOURLY_CAP = 600
API_GAP        = 0.3     # unchanged from the old BrowserAPIClient

CHALLENGE_WAIT = 180     # s to wait for a human to tick the checkbox
LOAD_TIMEOUT   = 45      # s for a normal page load
NET_ERRORS_BEFORE_STOP = 3

# Written onto the page we're leaving so we can tell when the next document's
# title has actually replaced it (otherwise we'd read the old page's title).
_NAV_TOKEN = "__w101_navigating__"
# Content is in the DOM at "interactive"; "complete" can hang on slow ads.
_READY = ("interactive", "complete")


# ═══════════════════════════════════════════════════════════════════════
# SETTINGS
# ═══════════════════════════════════════════════════════════════════════

def load_fetch_mode() -> str:
    """The saved Settings toggle (hud_settings.json), default 'api'."""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                v = str(json.load(f).get("_global", {}).get("wiki_fetch_mode", DEFAULT_MODE))
                if v in MODES:
                    return v
    except Exception as e:
        logger.debug(f"Could not read wiki_fetch_mode: {e}")
    return DEFAULT_MODE


def resolve_mode(cli_value: Optional[str] = None) -> str:
    return cli_value if cli_value in MODES else load_fetch_mode()


def add_source_argument(parser: argparse.ArgumentParser):
    parser.add_argument(
        "--source", choices=MODES, default=None,
        help="Wiki source: 'api' (api.php) or 'pages' (slow page fetch). "
             "Default: the Settings toggle.")


def page_url(wiki_path: str) -> str:
    return WIKI_BASE + quote(wiki_path.replace(" ", "_"), safe=":/()',!-._~")


def raw_url(wiki_path: str) -> str:
    return INDEX_URL + "?" + urlencode({"title": wiki_path.replace(" ", "_"), "action": "raw"})


# ═══════════════════════════════════════════════════════════════════════
# PROGRESS / ETA
# ═══════════════════════════════════════════════════════════════════════

def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


class Progress:
    """ETA from the observed rate of items actually fetched (skips excluded)."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.started = time.monotonic()

    def line(self, skipped: int = 0) -> str:
        self.done += 1
        elapsed = time.monotonic() - self.started
        remaining = max(0, self.total - skipped - self.done)
        eta = (elapsed / self.done) * remaining if self.done else 0
        return (f"  [ETA] {self.done + skipped}/{self.total}  ·  "
                f"{format_duration(elapsed)} elapsed  ·  ~{format_duration(eta)} left")


# ═══════════════════════════════════════════════════════════════════════
# PACER
# ═══════════════════════════════════════════════════════════════════════

class Pacer:
    """Random gap between requests plus a sliding one-hour request cap."""

    def __init__(self, gap_min: float, gap_max: float, hourly_cap: Optional[int]):
        self.gap_min = gap_min
        self.gap_max = gap_max
        self.hourly_cap = hourly_cap
        self._last = None
        self._window = deque()

    async def wait(self):
        if self._last is not None:
            target = self._last + random.uniform(self.gap_min, self.gap_max)
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        if self.hourly_cap:
            announced = False
            while True:
                now = time.monotonic()
                while self._window and now - self._window[0] >= 3600:
                    self._window.popleft()
                if len(self._window) < self.hourly_cap:
                    break
                wait_for = 3600 - (now - self._window[0])
                if not announced:
                    print(f"  [PACE] {self.hourly_cap} requests in the last hour — "
                          f"waiting {format_duration(wait_for)}", flush=True)
                    announced = True
                await asyncio.sleep(min(wait_for, 60))
        now = time.monotonic()
        self._last = now
        if self.hourly_cap:
            self._window.append(now)


# ═══════════════════════════════════════════════════════════════════════
# CLIENT
# ═══════════════════════════════════════════════════════════════════════

def _looks_like_challenge(body: str) -> bool:
    b = (body or "")[:6000].lower()
    return ("challenge-platform" in b or "cf-chl" in b or "just a moment" in b
            or "verify you are human" in b)


def _short_body(body: str) -> str:
    """One-line summary of a response body; an HTML page → its <title>/<h1>."""
    body = body or ""
    if "<" in body[:200]:
        for tag in ("title", "h1"):
            m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", body, re.I | re.S)
            if m and m.group(1).strip():
                return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split())[:120]
        body = re.sub(r"<[^>]+>", " ", body)
    return " ".join(body.split())[:120]


class WikiClient:
    """Shared Chrome-session wiki client. Subclasses add page-type helpers."""

    def __init__(self, mode: Optional[str] = None):
        self.mode = resolve_mode(mode)
        self.browser = None
        self.page = None
        self.requests = 0
        self._cf_notifier = cf_alert.ChallengeNotifier()
        self._robots: Optional[robotparser.RobotFileParser] = None
        self._net_errors = 0
        self._on_wiki_page = False
        self._page_cache: Dict[str, dict] = {}
        if self.is_pages:
            self._pacer = Pacer(PAGE_GAP_MIN, PAGE_GAP_MAX, PAGE_HOURLY_CAP)
        else:
            self._pacer = Pacer(API_GAP, API_GAP, None)

    @property
    def is_pages(self) -> bool:
        return self.mode == MODE_PAGES

    # ── lifecycle ─────────────────────────────────────────────────────

    async def start(self, check_source: bool = True):
        if not NODRIVER_OK:
            raise WikiStop(we.NODRIVER_MISSING, "pip install nodriver (and install Chrome)")
        label = ("PAGE FETCH (slow: ~5 s between requests, ≤600/hour)"
                 if self.is_pages else "API (api.php)")
        print(f"\n  Wiki source: {label}", flush=True)
        logger.info(f"Wiki source: {self.mode}")
        print("  Opening Chrome (a window will appear — click Cloudflare's "
              "checkbox there if it shows up)…", flush=True)
        self.browser = await uc.start(headless=False)
        self.requests += 1
        self.page = await self.browser.get(WIKI_BASE + HOME_PATH)
        title, _ = await self._wait_for_wiki_page(WIKI_BASE + HOME_PATH)
        print(f"  [OK] Wiki reached: {title[:60]}", flush=True)
        await asyncio.sleep(2)  # let cookies settle

        if check_source:
            if self.is_pages:
                await self.load_robots()
            else:
                await self.test_api()
        print("  [OK] Browser ready\n", flush=True)

    async def stop(self):
        if self.browser:
            try:
                self.browser.stop()
            except Exception:
                pass
            self.browser = None
            self.page = None

    # ── low-level browser helpers ─────────────────────────────────────

    async def _title(self) -> Optional[str]:
        try:
            t = await self.page.evaluate("document.title")
        except Exception:
            return None
        return t if isinstance(t, str) else None

    async def _eval_json(self, js: str, await_promise: bool = False):
        try:
            res = await self.page.evaluate(js, await_promise=await_promise,
                                           return_by_value=True)
        except Exception as e:
            raise WikiFetchError(we.NETWORK_ERROR, f"browser evaluate failed: {e}")
        if not isinstance(res, str):
            raise WikiFetchError(we.NETWORK_ERROR, f"browser evaluate failed: {str(res)[:120]}")
        return json.loads(res)

    async def _read_page_state(self) -> Optional[dict]:
        """DOM read — only call once the title says this is a wiki page."""
        try:
            return await self._eval_json("""
                (() => {
                  const el = document.getElementById('mw-content-text');
                  const cfg = (window.mw && mw.config && mw.config.get) ? mw.config : null;
                  return JSON.stringify({
                    ready: document.readyState,
                    href: location.href,
                    is_mediawiki: !!el,
                    article_id: cfg ? cfg.get('wgArticleId') : null,
                    missing: !!document.querySelector('.noarticletext'),
                    html: el ? el.innerHTML : null,
                  });
                })()
            """)
        except WikiFetchError:
            return None

    async def _wait_for_wiki_page(self, url: str) -> Tuple[str, dict]:
        """
        Poll ``document.title`` (title only!) until the navigation ends on a
        real wiki page, then read the DOM once. Challenge → wait for the human
        click (with alert sound); block / error page → WikiStop.
        """
        start = time.monotonic()
        deadline = start + LOAD_TIMEOUT
        challenge_since = None
        unknown_title, unknown_since = None, 0.0
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            title = await self._title()

            if not title or title == _NAV_TOKEN:
                if now > deadline:
                    raise WikiStop(we.NO_RESPONSE, "page never finished loading", url=url)
                continue

            if cf_alert.title_indicates_challenge(title):
                if challenge_since is None:
                    challenge_since = now
                    logger.info(f"Cloudflare challenge on {url}")
                    print("  [CHALLENGE] Cloudflare check — click the checkbox in "
                          "the Chrome window…", flush=True)
                self._cf_notifier.note(True)
                if now - challenge_since > CHALLENGE_WAIT:
                    raise WikiStop(we.CHALLENGE_TIMEOUT,
                                   f"checkbox not clicked within {CHALLENGE_WAIT} s", url=url)
                continue
            if challenge_since is not None:
                logger.info("Cloudflare challenge passed")
                print("  [OK] Cloudflare check passed", flush=True)
                challenge_since = None
                deadline = now + LOAD_TIMEOUT
            self._cf_notifier.reset()

            block = cf_alert.title_indicates_block(title)
            if block:
                raise WikiStop(block, f'block page: "{title[:80]}"', url=url)
            if cf_alert.title_looks_like_error_page(title):
                raise WikiStop(we.NO_RESPONSE,
                               "Chrome error page — connection closed or site unreachable",
                               url=url)

            if cf_alert.title_is_wiki_page(title):
                info = await self._read_page_state()
                if info and info.get("is_mediawiki") and info.get("ready") in _READY:
                    self._on_wiki_page = True
                    return title, info
            elif title != unknown_title:
                unknown_title, unknown_since = title, now
            elif now - unknown_since >= 3:
                # Unrecognised but stable, not a challenge/block/error title:
                # confirm from the DOM (covers a site-name change).
                info = await self._read_page_state()
                if info and info.get("is_mediawiki") and info.get("ready") in _READY:
                    self._on_wiki_page = True
                    return title, info

            if now > deadline:
                raise WikiStop(we.UNEXPECTED_PAGE, f'unexpected page "{title[:80]}"', url=url)

    async def _navigate(self, url: str, robots_fatal: bool = True) -> Tuple[str, dict]:
        self._check_robots(url, fatal=robots_fatal)
        await self._pacer.wait()
        self.requests += 1
        self._on_wiki_page = False
        try:
            await self.page.evaluate(f"document.title = {json.dumps(_NAV_TOKEN)}")
        except Exception:
            pass
        try:
            await self.page.get(url)
        except Exception as e:
            raise WikiFetchError(we.NETWORK_ERROR, str(e), url=url)
        return await self._wait_for_wiki_page(url)

    async def _ensure_wiki_origin(self):
        """same-site fetch() needs a wiki page as the current document."""
        if not self._on_wiki_page:
            await self._navigate(WIKI_BASE + HOME_PATH)

    async def _same_site_fetch(self, url: str, as_data_url: bool = False) -> dict:
        """fetch() from the current wiki page. Caller ensures the origin."""
        js = """
            (async () => {
              const ctl = new AbortController();
              const timer = setTimeout(() => ctl.abort(), 30000);
              try {
                const r = await fetch(%s, {credentials: 'include', signal: ctl.signal});
                const ct = r.headers.get('content-type') || '';
                let body;
                if (%s && r.ok) {
                  const blob = await r.blob();
                  body = await new Promise((res, rej) => {
                    const fr = new FileReader();
                    fr.onload = () => res(fr.result);
                    fr.onerror = () => rej(fr.error);
                    fr.readAsDataURL(blob);
                  });
                } else {
                  body = await r.text();
                }
                return JSON.stringify({status: r.status, ct: ct, body: body});
              } catch (e) {
                return JSON.stringify({status: 0, error: String((e && e.message) || e)});
              } finally {
                clearTimeout(timer);
              }
            })()
        """ % (json.dumps(url), "true" if as_data_url else "false")
        return await self._eval_json(js, await_promise=True)

    def _check_response(self, res: dict, url: str) -> str:
        """'ok' / 'challenge', or raise WikiFetchError / WikiStop."""
        status = res.get("status", 0)
        if not status:
            self._net_errors += 1
            if self._net_errors >= NET_ERRORS_BEFORE_STOP:
                raise WikiStop(we.NO_RESPONSE,
                               f"{self._net_errors} requests in a row got no response "
                               f"(last: {res.get('error', '')})", url=url)
            raise WikiFetchError(we.NETWORK_ERROR, res.get("error", ""), url=url)
        self._net_errors = 0
        body = res.get("body") or ""
        if status in (403, 503) and _looks_like_challenge(body):
            return "challenge"
        code, is_stop = we.classify_http_status(status)
        if code is None:
            if "access blocked" in body[:300].lower():
                raise WikiStop(we.BLOCKED, "Access Blocked", status=status, url=url)
            return "ok"
        if is_stop:
            raise WikiStop(code, _short_body(body), status=status, url=url)
        raise WikiFetchError(code, _short_body(body), status=status, url=url)

    async def _paced_fetch(self, url: str, as_data_url: bool = False,
                           robots_fatal: bool = True, challenge_page: str = "",
                           show_challenge: bool = True) -> dict:
        """
        One paced same-site request with block handling. A challenge on the
        fetch is shown to the user by loading a normal page, then retried
        once (``show_challenge=False``: raise CHALLENGE_REPEATED straight
        away instead). 5xx / a single network error are retried once.
        """
        self._check_robots(url, fatal=robots_fatal)
        challenged = retried = False
        while True:
            await self._ensure_wiki_origin()
            await self._pacer.wait()
            self.requests += 1
            res = await self._same_site_fetch(url, as_data_url=as_data_url)
            try:
                verdict = self._check_response(res, url)
            except WikiStop:
                raise
            except WikiFetchError as e:
                if e.code in (we.SERVER_ERROR, we.NETWORK_ERROR) and not retried:
                    retried = True
                    print(f"    [RETRY] {e} — retrying once in 15 s", flush=True)
                    await asyncio.sleep(15)
                    continue
                raise
            if verdict == "ok":
                return res
            if not show_challenge:
                raise WikiStop(we.CHALLENGE_REPEATED, "request was answered with a "
                               "Cloudflare challenge", url=url)
            if challenged:
                raise WikiStop(we.CHALLENGE_REPEATED,
                               "challenged again right after the checkbox was passed",
                               url=url)
            challenged = True
            print("    [CHALLENGE] request was challenged — loading a page so the "
                  "checkbox can be clicked", flush=True)
            await self._navigate(challenge_page or (WIKI_BASE + HOME_PATH))

    # ── robots.txt ────────────────────────────────────────────────────

    async def load_robots(self, enforce: bool = True) -> str:
        """Fetch + apply robots.txt. Returns its text (for the probe report)."""
        url = WIKI_ROOT + "/robots.txt"
        await self._ensure_wiki_origin()
        await self._pacer.wait()
        self.requests += 1
        res = await self._same_site_fetch(url)
        status = res.get("status", 0)
        body = res.get("body") or ""
        rp = robotparser.RobotFileParser()
        if status in (404, 410):
            rp.parse([])  # no robots.txt → everything allowed
            print("  [ROBOTS] none published — no restrictions", flush=True)
        elif 500 <= int(status or 0) < 600:
            raise WikiStop(we.ROBOTS_DISALLOWED,
                           f"robots.txt unavailable (HTTP {status}) — treated as disallow-all",
                           status=status, url=url)
        else:
            self._check_response(res, url)  # 0 / 403 / 444 / 429 → stop
            rp.parse(body.splitlines())
            print(f"  [ROBOTS] loaded ({len(body.splitlines())} lines)", flush=True)
        self._robots = rp

        delay = rp.crawl_delay("*")
        if delay and float(delay) > self._pacer.gap_min:
            self._pacer.gap_min = float(delay)
            self._pacer.gap_max = max(self._pacer.gap_max, float(delay) + 2)
            print(f"  [ROBOTS] Crawl-delay {delay} s — pacing adjusted", flush=True)
        rate = rp.request_rate("*")
        if rate and rate.seconds:
            per_hour = int(rate.requests * 3600 / rate.seconds)
            if self._pacer.hourly_cap is None or per_hour < self._pacer.hourly_cap:
                self._pacer.hourly_cap = max(1, per_hour)
                print(f"  [ROBOTS] Request-rate → ≤{per_hour}/hour", flush=True)

        if enforce and self.is_pages:
            sample = page_url("Spell:Fire_Cat")
            if not rp.can_fetch("*", sample):
                raise WikiStop(we.ROBOTS_DISALLOWED, "robots.txt disallows wiki pages", url=sample)
        return body

    def robots_allows(self, url: str) -> bool:
        return self._robots is None or self._robots.can_fetch("*", url)

    def _check_robots(self, url: str, fatal: bool = True):
        if not self.is_pages or self.robots_allows(url):
            return
        cls = WikiStop if fatal else WikiFetchError
        raise cls(we.ROBOTS_DISALLOWED, "robots.txt disallows this URL", url=url)

    # ── API mode ──────────────────────────────────────────────────────

    async def api_get(self, params: dict, _retry: int = 0) -> Optional[dict]:
        """
        MediaWiki API call via synchronous XHR inside the page (API mode).
        403 → refresh the Cloudflare session (max 2×); 444 / 429 / repeated
        403 → WikiStop. Other errors → None (the item fails, run continues).
        """
        if not self.page:
            return None
        params = dict(params, format="json")
        url = f"{API_URL}?{urlencode(params)}"
        await self._pacer.wait()
        self.requests += 1
        try:
            result = await self.page.evaluate("""
                (() => {
                    try {
                        const xhr = new XMLHttpRequest();
                        xhr.open("GET", %s, false);
                        xhr.send();
                        return xhr.status === 200 ? xhr.responseText
                               : JSON.stringify({error: xhr.status,
                                                 body: (xhr.responseText || '').slice(0, 300)});
                    } catch (e) { return JSON.stringify({error: 0, body: String(e.message)}); }
                })()
            """ % json.dumps(url))
        except Exception as e:
            logger.debug(f"XHR evaluate failed: {e}")
            return None
        if not isinstance(result, str) or not result:
            return None
        try:
            data = json.loads(result)
        except ValueError:
            if "access blocked" in result[:300].lower():
                raise WikiStop(we.BLOCKED, "api.php: Access Blocked", url=url)
            logger.warning(f"API returned non-JSON: {result[:120]}")
            return None

        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, int):
            body = data.get("body", "")
            if err == 0:
                self._net_errors += 1
                if self._net_errors >= NET_ERRORS_BEFORE_STOP:
                    raise WikiStop(we.NO_RESPONSE, "api.php closed the connection "
                                   f"{self._net_errors}× in a row ({body})", url=url)
                logger.warning(f"API network error: {body}")
                return None
            self._net_errors = 0
            if err == 403 and _retry < 2:
                print("  [WARN] HTTP 403 — refreshing the Cloudflare session…", flush=True)
                await self._refresh_session()
                return await self.api_get(params, _retry=_retry + 1)
            code, is_stop = we.classify_http_status(err)
            if is_stop:
                raise WikiStop(code, f"api.php: {_short_body(body)}", status=err, url=url)
            logger.warning(f"API error: HTTP {err}")
            return None
        self._net_errors = 0
        return data

    async def _refresh_session(self):
        try:
            await self._navigate(WIKI_BASE + HOME_PATH)
            print("  [OK] Session refreshed", flush=True)
            await asyncio.sleep(2)
        except WikiStop:
            raise
        except Exception as e:
            logger.error(f"Session refresh failed: {e}")

    async def test_api(self):
        """API mode start-up check. A block here means: use page fetch."""
        print("  Testing api.php…", flush=True)
        try:
            result = await self.api_get({"action": "query", "meta": "siteinfo",
                                         "siprop": "general"})
        except WikiStop as e:
            detail = e.args[0] if e.args else e.code
            raise WikiStop(we.API_UNAVAILABLE, f"{e.code}: {detail}", status=e.status, url=e.url)
        if result and "query" in result:
            site = result["query"].get("general", {}).get("sitename", "?")
            print(f"  [OK] API accessible — site: {site}", flush=True)
            return
        raise WikiStop(we.API_UNAVAILABLE, f"unexpected answer: {str(result)[:150]}")

    # ── high-level fetches (both modes) ───────────────────────────────

    async def load_page(self, wiki_path: str) -> dict:
        """Pages mode: load a wiki page once, cache its content HTML."""
        if wiki_path in self._page_cache:
            return self._page_cache[wiki_path]
        title, info = await self._navigate(page_url(wiki_path))
        if info.get("missing") or info.get("article_id") == 0:
            raise WikiFetchError(we.NOT_FOUND, "page does not exist", url=page_url(wiki_path))
        entry = {"title": title, "html": info.get("html") or ""}
        if len(self._page_cache) >= 4:
            self._page_cache.pop(next(iter(self._page_cache)))
        self._page_cache[wiki_path] = entry
        return entry

    async def fetch_wikitext(self, wiki_path: str) -> Optional[str]:
        if not self.is_pages:
            result = await self.api_get({"action": "parse", "page": wiki_path,
                                         "prop": "wikitext"})
            if result and "parse" in result:
                return result["parse"]["wikitext"].get("*", "")
            return None
        # A challenge here isn't shown to the user: the wiki challenges
        # action=raw on every request, so clicking would never help.
        url = raw_url(wiki_path)
        res = await self._paced_fetch(url, show_challenge=False)
        body = res.get("body") or ""
        if body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            raise WikiFetchError(we.HTTP_ERROR, "action=raw returned an HTML page", url=url)
        return body

    # Set once action=raw comes back challenged; the rest of the run then
    # rebuilds wikitext from the page HTML (see fetch_page_wikitext).
    raw_unavailable = False

    async def fetch_page_wikitext(self, wiki_path: str,
                                  rebuild: Callable[[str], Optional[str]]) -> Optional[str]:
        """
        Wikitext for one page. API mode: unchanged. Page fetch: action=raw
        while it works; once the wiki challenges it, ``rebuild(html)`` turns
        the loaded page back into the infobox wikitext the parsers expect.
        That page load is cached, so the HTML/image steps after it reuse it.
        """
        if self.is_pages and not self.raw_unavailable:
            try:
                return await self.fetch_wikitext(wiki_path)
            except WikiStop as e:
                if e.code != we.CHALLENGE_REPEATED:
                    raise
                self.raw_unavailable = True
                logger.warning("action=raw is challenged — reading pages' HTML instead")
                print("    [INFO] Raw wikitext is challenged by Cloudflare — reading the "
                      "infobox from the page instead (for the rest of this run)", flush=True)
        if not self.is_pages:
            return await self.fetch_wikitext(wiki_path)
        html = await self.fetch_rendered_html(wiki_path)
        wt = rebuild(html or "")
        if not wt:
            raise WikiFetchError(we.HTTP_ERROR, "no infobox found on the page",
                                 url=page_url(wiki_path))
        return wt

    async def fetch_rendered_html(self, wiki_path: str) -> Optional[str]:
        if not self.is_pages:
            result = await self.api_get({"action": "parse", "page": wiki_path,
                                         "prop": "text", "disablelimitreport": "1"})
            if result and "parse" in result:
                return result["parse"]["text"].get("*", "")
            return None
        return (await self.load_page(wiki_path))["html"]

    async def list_category(self, category: str, prefix: str,
                            subcats: bool = False) -> Tuple[List[str], List[str]]:
        """
        Page titles in ``Category:<category>`` starting with ``prefix``, plus
        (if ``subcats``) the subcategory titles. Follows paging in both modes.
        """
        if not category.startswith("Category:"):
            category = f"Category:{category}"
        pages: List[str] = []
        seen = set()
        sub: List[str] = []

        if not self.is_pages:
            params = {"action": "query", "list": "categorymembers", "cmtitle": category,
                      "cmlimit": "500", "cmtype": "page", "cmprop": "title|ids"}
            while True:
                result = await self.api_get(params)
                if not result or "query" not in result:
                    break
                for m in result["query"].get("categorymembers", []):
                    t = m.get("title", "")
                    if t.startswith(prefix) and t not in seen:
                        seen.add(t)
                        pages.append(t)
                print(f"    {category}: {len(pages)} so far…", flush=True)
                if "continue" in result:
                    params["cmcontinue"] = result["continue"]["cmcontinue"]
                else:
                    break
            if subcats:
                result = await self.api_get({"action": "query", "list": "categorymembers",
                                             "cmtitle": category, "cmlimit": "500",
                                             "cmtype": "subcat"})
                if result and "query" in result:
                    sub = [m.get("title", "") for m in result["query"].get("categorymembers", [])]
            return pages, sub

        url = page_url(category)
        visited = set()
        first = True
        while url and url not in visited:
            visited.add(url)
            await self._navigate(url)
            data = await self._eval_json("""
                (() => {
                  const box = document.getElementById('mw-pages');
                  const pages = box ? [...box.querySelectorAll('a[title]')]
                                        .map(a => a.getAttribute('title')) : [];
                  let next = null;
                  if (box) for (const a of box.querySelectorAll('a')) {
                    if (/next page/i.test(a.textContent)) { next = a.href; break; }
                  }
                  const sb = document.getElementById('mw-subcategories');
                  const subs = sb ? [...sb.querySelectorAll('a[title]')]
                                      .map(a => a.getAttribute('title')) : [];
                  return JSON.stringify({pages, next, subs});
                })()
            """)
            for t in data.get("pages", []):
                if t and t.startswith(prefix) and t not in seen:
                    seen.add(t)
                    pages.append(t)
            if first and subcats:
                sub = [t for t in data.get("subs", []) if t and t.startswith("Category:")]
            first = False
            print(f"    {category}: {len(pages)} so far…", flush=True)
            url = data.get("next")
        return pages, sub

    async def download_image(self, url: str, dest: Path) -> bool:
        """
        Save an image via same-site fetch(). Falls back to opening the image
        URL and drawing it to a canvas. Blocks still stop the run; anything
        else returns False.
        """
        import base64
        # Cross-origin (CDN) images can't be read by fetch() — CORS would
        # show up as "no response" and count toward the NO_RESPONSE stop.
        if url.startswith(WIKI_ROOT + "/"):
            try:
                if self.is_pages:
                    res = await self._paced_fetch(url, as_data_url=True, robots_fatal=False)
                else:
                    await self._ensure_wiki_origin()
                    res = await self._same_site_fetch(url, as_data_url=True)
                    if self._check_response(res, url) != "ok":
                        res = {}
                data_url = res.get("body") or ""
                if data_url.startswith("data:"):
                    dest.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
                    return True
            except WikiStop:
                raise
            except WikiFetchError as e:
                logger.debug(f"image fetch failed for {url}: {e}")
                if e.code != we.NETWORK_ERROR:
                    return False   # 404 / robots / 5xx: opening it won't help
            except Exception as e:
                logger.debug(f"image fetch failed for {url}: {e}")

        # Fallback: open the image itself (e.g. a CORS-blocked CDN URL).
        try:
            self._check_robots(url, fatal=False)
            await self._pacer.wait()
            self.requests += 1
            self._on_wiki_page = False
            await self.page.get(url)
            await asyncio.sleep(1.5)
            data_url = await self.page.evaluate("""
                (() => {
                    const img = document.querySelector('img');
                    if (!img) return null;
                    const c = document.createElement('canvas');
                    c.width  = img.naturalWidth  || img.width  || 1;
                    c.height = img.naturalHeight || img.height || 1;
                    try { c.getContext('2d').drawImage(img, 0, 0); } catch (e) { return null; }
                    return c.toDataURL('image/png');
                })()
            """)
            if isinstance(data_url, str) and data_url.startswith("data:"):
                dest.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
                return True
        except WikiFetchError as e:
            logger.debug(f"image fallback skipped for {url}: {e}")
        except Exception as e:
            logger.debug(f"image fallback failed for {url}: {e}")
        logger.warning(f"All download approaches failed for {url}")
        return False


def run_exit_code(failed: int) -> int:
    return we.EXIT_PARTIAL if failed else we.EXIT_OK


_PROBE_HARD_STOPS = (we.BLOCKED, we.RATE_LIMITED, we.NO_RESPONSE)


# ═══════════════════════════════════════════════════════════════════════
# STEP 0 PROBE  (python wiki_client.py --probe)
# ═══════════════════════════════════════════════════════════════════════

async def probe(spell: str, category: str) -> int:
    """
    The plan's Step 0 checks, one request at a time, page-fetch pacing:
      robots.txt · normal page · action=raw · category listing · api.php
    Each check is reported on its own; a hard block (403/444/429/no
    response/checkbox timeout) ends the probe so we don't keep knocking.
    """
    client = WikiClient(MODE_PAGES)
    report: List[str] = []
    results: Dict[str, str] = {}
    hard_stop: Optional[WikiStop] = None

    def out(line=""):
        print(line, flush=True)
        report.append(line)

    def record(key, status, detail=""):
        results[key] = status
        out(f"  [{status:<17}] {key:<11} {detail}")

    async def check(key, url, fn):
        """Run one check unless robots.txt forbids it or we're already blocked."""
        nonlocal hard_stop
        if hard_stop is not None:
            record(key, "SKIPPED", f"after {hard_stop.code}")
            return
        if url and not client.robots_allows(url):
            record(key, we.ROBOTS_DISALLOWED, url)
            return
        try:
            await fn(key)
        except WikiFetchError as e:
            record(key, e.code, str(e))
            # Only a real block ends the probe; a challenged fetch is a
            # result for that one check.
            if isinstance(e, WikiStop) and e.code in _PROBE_HARD_STOPS:
                hard_stop = e

    spell_path = f"Spell:{spell.replace(' ', '_')}"
    cat_path = f"Category:{category.replace(' ', '_')}"

    async def do_robots(key):
        body = await client.load_robots(enforce=False)
        record(key, "OK", f"{len(body.splitlines())} lines")
        # Only the rules that apply to us (the "User-agent: *" group).
        in_star = False
        for ln in body.splitlines():
            low = ln.strip().lower()
            if low.startswith("user-agent:"):
                in_star = low.split(":", 1)[1].strip() == "*"
            if in_star and low and not low.startswith("#"):
                out(f"      | {ln.rstrip()}")
        for label, u in (("page", page_url(spell_path)), ("action=raw", raw_url(spell_path)),
                         ("category", page_url(cat_path)), ("api.php", API_URL)):
            out(f"      allowed for *  {label:<11}: {client.robots_allows(u)}")

    async def do_page(key):
        page = await client.load_page(spell_path)
        html = page["html"]
        record(key, "OK", f'"{page["title"][:50]}" · {len(html)} chars · '
                          f'{html.count("<img")} <img>')

    async def do_raw(key):
        wt = await client.fetch_wikitext(spell_path) or ""
        looks = "wikitext" if "{{" in wt[:400] else "UNEXPECTED content"
        record(key, "OK", f"{len(wt)} chars ({looks})")
        out("      | " + " ".join(wt[:160].split()))

    async def do_raw_nav(key):
        """
        Open the action=raw URL as a visitor would (a navigation, not a
        fetch). Title-only while a challenge is up; a plain-text document has
        an empty title, so "settled" = not our token and not a challenge for
        3 s in a row. Then one DOM read for the content type + first chars.
        """
        url = raw_url(spell_path)
        await client._pacer.wait()
        client.requests += 1
        client._on_wiki_page = False
        try:
            await client.page.evaluate(f"document.title = {json.dumps(_NAV_TOKEN)}")
        except Exception:
            pass
        await client.page.get(url)
        settled, challenge_since, start = 0, None, time.monotonic()
        while True:
            await asyncio.sleep(1.0)
            title = await client._title()
            if title is not None and cf_alert.title_indicates_challenge(title):
                if challenge_since is None:
                    challenge_since = time.monotonic()
                    out("      (challenge shown on the raw URL — waiting for it to pass)")
                client._cf_notifier.note(True)
                if time.monotonic() - challenge_since > CHALLENGE_WAIT:
                    raise WikiStop(we.CHALLENGE_TIMEOUT, "raw URL challenge not passed", url=url)
                settled = 0
                continue
            client._cf_notifier.reset()
            block = cf_alert.title_indicates_block(title)
            if block:
                raise WikiFetchError(block, f'block page: "{title[:80]}"', url=url)
            if title == _NAV_TOKEN:
                if time.monotonic() - start > LOAD_TIMEOUT:
                    record(key, "NO_NAVIGATION",
                           "Chrome stayed on the old page (downloaded the file?)")
                    return
                continue
            settled += 1
            if settled >= 3:
                break
        info = await client._eval_json("""
            (() => JSON.stringify({
              ct: document.contentType, href: location.href,
              text: document.body ? document.body.innerText.slice(0, 400) : ''
            }))()
        """)
        text = info.get("text") or ""
        looks = "wikitext" if "{{" in text else "NOT wikitext"
        status = "OK" if "{{" in text else "UNEXPECTED"
        if challenge_since is not None:
            status += " (after challenge)"
        record(key, status, f'{info.get("ct")} · {looks}')
        out("      | " + " ".join(text[:160].split()))

    async def do_category(key):
        await client._navigate(page_url(cat_path))
        data = await client._eval_json("""
            (() => {
              const box = document.getElementById('mw-pages');
              const n = box ? box.querySelectorAll('a[title^="Spell:"]').length : 0;
              let next = false;
              if (box) for (const a of box.querySelectorAll('a'))
                if (/next page/i.test(a.textContent)) next = true;
              return JSON.stringify({n: n, next: next});
            })()
        """)
        record(key, "OK" if data["n"] else "EMPTY",
               f'{data["n"]} Spell: links on page 1 · "next page" link: {data["next"]}')

    async def do_api(key):
        # Not paced through _paced_fetch on purpose: a 444 here is the
        # expected answer and must not end the probe.
        await client._ensure_wiki_origin()
        await client._pacer.wait()
        client.requests += 1
        res = await client._same_site_fetch(
            API_URL + "?" + urlencode({"action": "query", "meta": "siteinfo", "format": "json"}))
        st = res.get("status", 0)
        record(key, "OK" if st == 200 else f"HTTP {st}",
               _short_body(res.get("body") or res.get("error", "")))

    exit_code = we.EXIT_OK
    try:
        try:
            await client.start(check_source=False)
        except WikiStop as e:
            out(f"\n  Could not reach the wiki at all.")
            return we.announce_stop(e)

        out("\n" + "=" * 64)
        out(f"  STEP 0 PROBE  ·  {time.strftime('%Y-%m-%d %H:%M')}")
        out("=" * 64)
        await check("robots.txt", None, do_robots)
        await check("page", page_url(spell_path), do_page)
        await check("action=raw", raw_url(spell_path), do_raw)
        if results.get("action=raw") != "OK":
            await check("raw (opened)", raw_url(spell_path), do_raw_nav)
        await check("category", page_url(cat_path), do_category)
        await check("api.php", None, do_api)

        out("")
        page_ok = results.get("page") == "OK"
        raw_ok = results.get("action=raw") == "OK"
        raw_nav = results.get("raw (opened)", "")
        cat_ok = results.get("category") == "OK"
        if page_ok and raw_ok and cat_ok:
            out("  VERDICT: best case — action=raw works. Switch Settings → Wiki Fetch "
                "Source to \u201cPage fetch\u201d; all parsers stay as they are.")
        elif page_ok and raw_nav.startswith("OK"):
            out("  VERDICT: action=raw is challenged when fetched, but opens when visited "
                f"as a page ({raw_nav}). Page fetch would have to open the raw URL "
                "as a tab navigation — not built; decide before going further.")
            exit_code = we.EXIT_PARTIAL
        elif page_ok and not raw_ok:
            out("  VERDICT: normal pages load but action=raw does not. Page fetch "
                "would need new HTML parsers (infobox + boss cheat notes) — not built yet.")
            exit_code = we.EXIT_PARTIAL
        elif page_ok:
            out("  VERDICT: page + wikitext load but the category listing failed — "
                "single-spell fetches can work, school / all fetches cannot.")
            exit_code = we.EXIT_PARTIAL
        else:
            out("  VERDICT: stop here — normal pages don't load either.")
            exit_code = hard_stop.exit_code if hard_stop else we.EXIT_BLOCKED
        out(f"  Requests made: {client.requests}")
    finally:
        await client.stop()
        try:
            path = APP_DIR / "wiki_probe_report.txt"
            path.write_text("\n".join(report) + "\n", encoding="utf-8")
            print(f"\n  Report saved to {path.name}", flush=True)
        except Exception:
            pass
    return exit_code


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    parser = argparse.ArgumentParser(description="Wizard101 wiki client — Step 0 probe")
    parser.add_argument("--probe", action="store_true",
                        help="Run the Step 0 checks (robots.txt, page, action=raw, "
                             "category, api.php) and print a verdict")
    parser.add_argument("--spell", default="Fire Cat", help="Spell page to test with")
    parser.add_argument("--category", default="Fire Spells", help="Category to test with")
    args = parser.parse_args()
    if not args.probe:
        parser.print_help()
        return we.EXIT_USAGE
    if not NODRIVER_OK:
        print("[FAIL] nodriver not installed. Run: pip install nodriver")
        return we.EXIT_NODRIVER_MISSING
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        return uc.loop().run_until_complete(probe(args.spell, args.category))
    except KeyboardInterrupt:
        return we.EXIT_CANCELLED


if __name__ == "__main__":
    sys.exit(main())
