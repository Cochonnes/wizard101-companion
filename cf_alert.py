"""
cf_alert.py  —  Cloudflare-challenge signalling (shared by scrapers + GUI)
══════════════════════════════════════════════════════════════════════════
The wiki fetch runs inside a real Chrome (nodriver) as a *subprocess*.
Every ~10 minutes Cloudflare re-shows its "verify you are human" checkbox,
which the user has to click by hand in the Chrome window. This module lets
the scraper tell the parent GUI "the checkbox is up right now" so the GUI
can play an alert sound.

Design (deliberately NOT OCR, and deliberately NOT a live DOM probe):
  • Detection reads only ``document.title`` — a string the scraper already
    fetches once per wait-loop tick — and classifies it in Python
    (``title_indicates_challenge``). We do NOT execute scripts or force layout
    in the Cloudflare page's context: doing so makes Turnstile flag the browser
    as automated and loop forever without ever passing.
  • Signalling is done over the channel that already exists: the scraper
    prints a one-line marker (``MARKER``) to stdout, which the GUI reads via
    QProcess.readyReadStandardOutput and turns into a sound.

This file is intentionally **stdlib-only** so it imports cleanly inside the
scraper subprocess (no PyQt / no Qt event loop needed there). The actual
sound playback lives GUI-side in hud_overlays.py (CloudflareAlertPlayer).
"""

import sys
import time

# ── The stdout token the GUI scans for. Distinctive + unlikely to collide
#    with any wiki text or log line. Kept on its own line, flushed. ──────────
MARKER = "[[CF_CHALLENGE]]"

# ── Detection is TITLE-ONLY and Python-side. ────────────────────────────────
# IMPORTANT: we deliberately do NOT run any JS / DOM probe against a live
# Cloudflare challenge page. Executing scripts or forcing layout (e.g. reading
# document.body.innerText) in the challenge's context while Turnstile is solving
# makes it flag the browser as automated → it loops and never passes. The
# scrapers already read ``document.title`` once per wait-loop tick for free;
# we classify the challenge from that string alone, touching nothing.
_CHALLENGE_TITLE_PHRASES = (
    "just a moment",
    "attention required",
    "checking your browser",
    "checking if the site connection is secure",
    "verify you are human",
    "verifying you are human",
    "one moment",
    "please wait",
    "cloudflare",
)


def title_indicates_challenge(title) -> bool:
    """
    True when ``document.title`` looks like a Cloudflare interstitial /
    "verify you are human" page. Pure string check — no page interaction.
    Returns False for an empty/unknown title to avoid false alerts during a
    normal slow page load.
    """
    if not title:
        return False
    t = str(title).lower()
    if "wizard101" in t or "wiki" in t:
        return False
    return any(p in t for p in _CHALLENGE_TITLE_PHRASES)


def strip_marker(text: str) -> str:
    """
    Remove the raw MARKER token from a stdout chunk so the progress log the
    user sees stays clean (the human-readable half of the line remains).
    """
    if not text:
        return text
    return text.replace(MARKER + " ", "").replace(MARKER, "")


class ChallengeNotifier:
    """
    Throttled rising-edge emitter used by the scrapers.

    Call ``note(present)`` once per wait-loop iteration with the result of the
    DOM probe. It prints the stdout MARKER:
      • once when the challenge first appears (rising edge), and
      • again every ``reemit_after`` seconds while it is *still* up
        (so a user who stepped away gets re-alerted).
    Call ``reset()`` once the challenge clears so the next one re-alerts.
    """

    def __init__(self, reemit_after: float = 25.0):
        self._active = False
        self._last_emit = 0.0
        self._reemit_after = reemit_after

    def note(self, present: bool) -> None:
        if present:
            now = time.monotonic()
            if (not self._active) or (now - self._last_emit >= self._reemit_after):
                self._emit()
                self._last_emit = now
            self._active = True
        else:
            self._active = False

    def reset(self) -> None:
        self._active = False

    @staticmethod
    def _emit() -> None:
        # Machine token + human note on one line. flush=True so the GUI sees it
        # immediately rather than when the subprocess buffer fills.
        print(
            f"{MARKER} \u26a0 Cloudflare check appeared \u2014 click the checkbox "
            f"in the Chrome window to continue.",
            flush=True,
        )
        try:
            sys.stdout.flush()
        except Exception:
            pass
