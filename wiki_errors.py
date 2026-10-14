"""
wiki_errors.py  —  Failure codes shared by the wiki scrapers + GUI
══════════════════════════════════════════════════════════════════
stdlib-only (like cf_alert.py) so the GUI can import it without pulling in
nodriver / bs4.

Two kinds of failure:

  • Run-stopping (``WikiStop``) — the wiki said "no" (blocked, rate-limited,
    robots.txt, nobody clicked the checkbox …). The scraper stops *for good*,
    prints a ``STOP_MARKER`` line and exits with the matching process exit
    code. Nothing retries around a block: if the wiki blocks us, that's their
    answer.

  • Per-item (``WikiFetchError``) — one page failed (404, a 5xx after a retry,
    a network hiccup). The run logs ``[FAIL:<code>]`` and moves on.

Process exit codes are stable so the GUI can map them to a message.
"""

import logging

# ── Process exit codes ──────────────────────────────────────────────────────
EXIT_OK                = 0    # finished, nothing failed
EXIT_PARTIAL           = 1    # finished, but one or more items failed (or uncaught error)
EXIT_USAGE             = 2    # bad command-line arguments (argparse default)
EXIT_NODRIVER_MISSING  = 3    # nodriver / Chrome not available
EXIT_CHALLENGE_TIMEOUT = 10   # Cloudflare checkbox was never clicked
EXIT_BLOCKED           = 11   # wiki refused access (403 / 444 / "Access Blocked" / CF 1020)
EXIT_RATE_LIMITED      = 12   # HTTP 429 / CF 1015 — slow down and try later
EXIT_NO_RESPONSE       = 13   # site closed the connection / unreachable
EXIT_ROBOTS_DISALLOWED = 14   # robots.txt forbids the URLs the page fetcher needs
EXIT_API_UNAVAILABLE   = 15   # API mode: api.php is not answering
EXIT_UNEXPECTED_PAGE   = 16   # page loaded but never looked like a wiki page
EXIT_CHALLENGE_REPEATED = 17  # Cloudflare challenges this request every single time
EXIT_CANCELLED         = 130  # Ctrl+C

# ── Code names (used in WikiStop / WikiFetchError and in log lines) ─────────
CHALLENGE_TIMEOUT = "CHALLENGE_TIMEOUT"
BLOCKED           = "BLOCKED"
RATE_LIMITED      = "RATE_LIMITED"
NO_RESPONSE       = "NO_RESPONSE"
ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
API_UNAVAILABLE   = "API_UNAVAILABLE"
UNEXPECTED_PAGE   = "UNEXPECTED_PAGE"
CHALLENGE_REPEATED = "CHALLENGE_REPEATED"
NODRIVER_MISSING  = "NODRIVER_MISSING"
# per-item only
NOT_FOUND         = "NOT_FOUND"
SERVER_ERROR      = "SERVER_ERROR"
NETWORK_ERROR     = "NETWORK_ERROR"
HTTP_ERROR        = "HTTP_ERROR"

EXIT_FOR_CODE = {
    CHALLENGE_TIMEOUT: EXIT_CHALLENGE_TIMEOUT,
    BLOCKED:           EXIT_BLOCKED,
    RATE_LIMITED:      EXIT_RATE_LIMITED,
    NO_RESPONSE:       EXIT_NO_RESPONSE,
    ROBOTS_DISALLOWED: EXIT_ROBOTS_DISALLOWED,
    API_UNAVAILABLE:   EXIT_API_UNAVAILABLE,
    UNEXPECTED_PAGE:   EXIT_UNEXPECTED_PAGE,
    CHALLENGE_REPEATED: EXIT_CHALLENGE_REPEATED,
    NODRIVER_MISSING:  EXIT_NODRIVER_MISSING,
}

# Human text per exit code — shown by the GUI when a fetch ends.
EXIT_MESSAGES = {
    EXIT_OK:                "Finished.",
    EXIT_PARTIAL:           "Finished, but some items failed — see the log.",
    EXIT_USAGE:             "The scraper was started with invalid arguments.",
    EXIT_NODRIVER_MISSING:  "nodriver is not installed (pip install nodriver) or Chrome is missing.",
    EXIT_CHALLENGE_TIMEOUT: "Cloudflare's “verify you are human” checkbox was not "
                            "clicked in time. Run the fetch again and click it in the Chrome window.",
    EXIT_BLOCKED:           "The wiki blocked access (403 / 444 “Access Blocked”). "
                            "The fetch stopped and will not retry.",
    EXIT_RATE_LIMITED:      "The wiki asked us to slow down (HTTP 429). The fetch stopped — "
                            "wait a while, then use Resume.",
    EXIT_NO_RESPONSE:       "The wiki closed the connection without answering (likely an "
                            "HTTP 444 block, or the site/your network is down).",
    EXIT_ROBOTS_DISALLOWED: "The wiki's robots.txt does not allow the pages the page fetcher needs.",
    EXIT_API_UNAVAILABLE:   "The wiki API (api.php) is not answering — it is currently blocked. "
                            "Switch Settings → Wiki Fetch Source to “Page fetch”.",
    EXIT_UNEXPECTED_PAGE:   "Pages loaded but never looked like wiki pages — the fetch stopped.",
    EXIT_CHALLENGE_REPEATED: "Cloudflare challenged a request that has no page fallback "
                             "(it answers every time, so clicking won't help). The fetch stopped.",
    EXIT_CANCELLED:         "Cancelled.",
}

# Printed (flushed, on its own line) right before a run-stopping exit so the
# GUI can react even before the process finishes. Format:
#   [[WIKI_STOP:BLOCKED]] human message
STOP_MARKER_PREFIX = "[[WIKI_STOP:"


def describe_exit(code) -> str:
    """Human text for a scraper exit code (unknown codes get a generic line)."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return f"Exited with code {code}."
    return EXIT_MESSAGES.get(code, f"Exited with code {code}.")


def is_stop_exit(code) -> bool:
    """True for the run-stopping codes (the ones worth a pop-up)."""
    try:
        return int(code) in EXIT_FOR_CODE.values()
    except (TypeError, ValueError):
        return False


class WikiFetchError(Exception):
    """One page/file failed; the run continues."""

    def __init__(self, code: str, message: str = "", status=None, url: str = ""):
        self.code = code
        self.status = status
        self.url = url
        super().__init__(message or code)

    def __str__(self):
        bits = [self.code]
        if self.status not in (None, ""):
            bits.append(f"HTTP {self.status}")
        msg = self.args[0] if self.args else ""
        if msg and msg != self.code:
            bits.append(msg)
        return " — ".join(str(b) for b in bits)


class WikiStop(WikiFetchError):
    """The run must stop for good (block, rate limit, robots, …)."""

    @property
    def exit_code(self) -> int:
        return EXIT_FOR_CODE.get(self.code, EXIT_PARTIAL)


def announce_stop(err: "WikiStop") -> int:
    """Print the stop marker + reason (and log it), return the exit code."""
    logging.getLogger("wiki").error(f"STOP {err} (exit {err.exit_code}) {err.url}")
    print(f"\n{STOP_MARKER_PREFIX}{err.code}]] [STOP] {err}", flush=True)
    print(f"  {describe_exit(err.exit_code)}", flush=True)
    return err.exit_code


def parse_stop_marker(text: str):
    """Return the stop code name in a stdout chunk, or None."""
    if not text:
        return None
    i = text.find(STOP_MARKER_PREFIX)
    if i < 0:
        return None
    j = text.find("]]", i)
    if j < 0:
        return None
    return text[i + len(STOP_MARKER_PREFIX):j] or None


def classify_http_status(status):
    """
    Map an HTTP status to (code, is_stop). 200–299 → (None, False).

    444 is nginx's "close the connection, send nothing"; the wiki's
    "Access Blocked" response uses it, so it counts as a block.
    """
    try:
        s = int(status)
    except (TypeError, ValueError):
        return (NETWORK_ERROR, False)
    if 200 <= s < 300:
        return (None, False)
    if s in (401, 403, 444, 451):
        return (BLOCKED, True)
    if s == 429:
        return (RATE_LIMITED, True)
    if s in (404, 410):
        return (NOT_FOUND, False)
    if 500 <= s < 600:
        return (SERVER_ERROR, False)
    return (HTTP_ERROR, False)
