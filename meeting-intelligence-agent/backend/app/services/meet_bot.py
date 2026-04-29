"""
Google Meet Bot Service — Production Grade
==========================================
Playwright-based headful Chromium automation.

Root causes fixed vs. previous version
────────────────────────────────────────
1. join button was VISIBLE but DISABLED — code never checked is_enabled() after name entry.
2. grant_permissions() lacked explicit origin= — Meet's getUserMedia re-check was failing.
3. Fixed asyncio.sleep(3) after goto() — replaced with poll-until-ready on DOM signals.
4. mic/cam state checked via data-is-muted attribute, not fragile aria-label text.
5. No handler for Meet's in-app "Allow microphone and camera" overlay.
6. :has-text() replaced with page.get_by_role() as primary — handles aria-name properly.
7. Added debug screenshots at every state transition for diagnosing future issues.

Full pipeline per session
──────────────────────────
 1. Upsert Meeting record → meeting_id
 2. Start ffmpeg recording (if available) with PulseAudio sink
 3. Launch Chromium with correct flags + env
 4. Inject webdriver spoof + WebRTC intercept scripts
 5. Navigate to Meet URL
 6. Grant permissions explicitly to meet.google.com origin
 7. Wait for pre-join screen (polls DOM, not fixed sleep)
 8. Dismiss any "Allow mic/camera" overlay
 9. Enter bot display name
10. Wait for join button to become ENABLED
11. Disable mic (data-is-muted=false → click)
12. Disable camera (data-is-muted=false → click)
13. Click "Join now" / "Ask to join" with 4-strategy fallback
14. Confirm in-meeting (polls for Leave-call button)
15. Start in-page Playwright recording if ffmpeg unavailable
16. Stay for configured duration
17. Stop recording → save path to DB
18. Dispatch process_meeting_recording_background()
19. Gracefully leave
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Fail loudly at import time if playwright is missing — don't let it surface
# as a silent task crash after the API already returned 200.
try:
    from playwright.async_api import async_playwright as _pw_check  # noqa
    print("[BOT] playwright import OK", flush=True)
except Exception as _pw_err:
    print(f"[BOT ERROR] playwright import FAILED: {_pw_err}", flush=True)
    print("[BOT] Run: pip install playwright && playwright install chromium", flush=True)

# ── In-memory registry ─────────────────────────────────────────────────────────
_active_bots: Dict[str, Dict[str, Any]] = {}

# ── Constants ──────────────────────────────────────────────────────────────────
# Matches the path component only — query-params stripped before test (see is_valid_meet_url).
# Accepts:  https://meet.google.com/abc-defg-hij
#           https://meet.google.com/abc-defg-hij?authuser=0   (Google appends this)
#           https://meet.google.com/abc-defg-hij/             (trailing slash)
MEET_URL_RE = re.compile(r"^https?://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(/.*)?$", re.I)
DEFAULT_STAY_SECONDS = 600
DEFAULT_BOT_NAME     = "SyncMinds Bot"
MAX_RETRIES          = 2

PAGE_LOAD_TIMEOUT    = 35_000   # ms — Meet SPA needs more time than a static page
PREJOIN_POLL_PER_ATTEMPT_S = 12  # s — DOM-poll window per attempt
PREJOIN_MAX_RETRIES        = 3   # reload attempts when "can't join" is shown
PREJOIN_BLOCKED_WAIT_S     = 5   # s — pause before page reload on "can't join"
ELEMENT_TIMEOUT      = 8_000    # ms — per-selector wait when probing
JOIN_ENABLED_TIMEOUT = 12_000   # ms — wait for join button to become enabled after name entry
IN_MEETING_TIMEOUT   = 40_000   # ms — wait to confirm inside the meeting

# Screenshots: always saved to /tmp/syncminds_bot/ for diagnosing join failures.
# Set MEET_BOT_DEBUG=1 to also save to ./debug/ for persistent storage.
_DEBUG = os.getenv("MEET_BOT_DEBUG", "0") == "1"
_DEBUG_DIR = Path("debug")
_SCREENSHOT_DIR = Path("/tmp/syncminds_bot")

# Headless mode: False requires an X11 display (local dev only).
# Containers and servers must use True. Override with MEET_BOT_HEADLESS=0 for local testing.
_HEADLESS = os.getenv("MEET_BOT_HEADLESS", "1") != "0"


def _bot_print(msg: str) -> None:
    """Print + log together so the message appears in both docker logs and structured logs."""
    print(msg, flush=True)
    logger.info(msg)


# ══════════════════════════════════════════════════════════════════════════════
# Selector catalogs
# Priority: data-attribute > jsname > role > aria-label > text
# ══════════════════════════════════════════════════════════════════════════════

# In-app "Do you want people to see and hear you in the meeting?" card.
# This is NOT the browser permission dialog. It is a React-rendered card with
# a backdrop that absorbs ALL click events behind it — including "Join now".
# It MUST be dismissed before any other interaction.
#
# Button text visible in the screenshot: "Allow microphone and camera"
# This card appears for signed-in users on the pre-join screen.
_MEDIA_CONSENT_CARD: List[str] = [
    # Exact match first — most reliable
    'button:has-text("Allow microphone and camera")',
    # Fallback: broader substring — catches locale variants
    'button:has-text("Allow microphone")',
    'button:has-text("Allow camera")',
    # Dialog-level dismissal attributes
    '[data-mdc-dialog-action="accept"]',
    '[jsname="IbE0S"]',
]

# Signals that confirm the media-consent card is GONE.
# We wait for ALL of these to be absent before proceeding.
_MEDIA_CONSENT_CARD_GONE: List[str] = [
    'button:has-text("Allow microphone and camera")',
    '[data-mdc-dialog-action="accept"]',
]

# Fallback overlay buttons (secondary priority — less specific)
_PERMISSION_OVERLAY_DISMISS: List[str] = [
    *_MEDIA_CONSENT_CARD,
    'button:has-text("Got it")',
    'button:has-text("Dismiss")',
    '[aria-label="Allow access"]',
    'button:has-text("Continue without")',
]

# Mic button: data-is-muted="false" means mic is currently ON
_MIC_ON: List[str] = [
    '[jsname="BOHaEe"][data-is-muted="false"]',     # most reliable
    '[aria-label="Turn off microphone"]',
    '[data-tooltip="Turn off microphone"]',
]
# Mic already muted signals (for logging only)
_MIC_OFF: List[str] = [
    '[jsname="BOHaEe"][data-is-muted="true"]',
    '[aria-label="Turn on microphone"]',
]

# Camera button: data-is-muted="false" means camera is currently ON
_CAM_ON: List[str] = [
    '[jsname="R3Gied"][data-is-muted="false"]',
    '[aria-label="Turn off camera"]',
    '[data-tooltip="Turn off camera"]',
]
_CAM_OFF: List[str] = [
    '[jsname="R3Gied"][data-is-muted="true"]',
    '[aria-label="Turn on camera"]',
]

# Name input
_NAME_INPUT: List[str] = [
    'input[placeholder*="name" i]',
    'input[aria-label*="name" i]',
    'input[jsname="YPqjbf"]',
    '[data-testid*="name"] input',
    'input[type="text"]',               # broad fallback
]

# Join buttons — ordered by specificity
_JOIN_BUTTON_TEXTS = ["Join now", "Ask to join", "Continue without microphone", "Continue without camera", "Join"]
_JOIN_BUTTONS_BY_JSNAME: List[str] = [
    'button[jsname="Qx7uuf"]',          # "Join now"
    'button[jsname="CwaK9"]',           # "Ask to join"
]

# In-meeting confirmation signals
_IN_MEETING: List[str] = [
    '[aria-label*="Leave call"]',
    '[data-tooltip*="Leave call"]',
    'button:has-text("Leave call")',
    '[data-allocation-index]',          # participant tile
    '[jscontroller="ynJ3Fb"]',          # meeting controls bar
]

# Leave buttons
_LEAVE_BUTTONS: List[str] = [
    '[aria-label*="Leave call"]',
    '[data-tooltip*="Leave call"]',
    'button:has-text("Leave call")',
]
_LEAVE_CONFIRM: List[str] = [
    'button:has-text("Leave meeting")',
    'button:has-text("Leave")',
    '[aria-label*="Leave meeting"]',
]


# ══════════════════════════════════════════════════════════════════════════════
# Public helpers
# ══════════════════════════════════════════════════════════════════════════════

def is_valid_meet_url(url: str) -> bool:
    # Strip query params and fragments before testing — Google Calendar's hangoutLink
    # often appends ?authuser=N or similar, which the regex would otherwise reject.
    clean = (url or "").strip().split("?")[0].split("#")[0]
    return bool(MEET_URL_RE.match(clean))


def get_bot_status(user_id: str) -> Optional[Dict[str, Any]]:
    session = _active_bots.get(user_id)
    if not session:
        return None
    task: Optional[asyncio.Task] = session.get("_task")
    return {
        "status": session["status"],
        "meet_url": session["meet_url"],
        "meeting_id": session.get("meeting_id"),
        "started_at": session["started_at"].isoformat(),
        "bot_name": session["bot_name"],
        "recording_path": session.get("recording_path"),
        "error": session.get("error"),
        "done": task.done() if task else True,
    }


async def stop_bot(user_id: str) -> bool:
    session = _active_bots.get(user_id)
    if not session:
        return False
    task: Optional[asyncio.Task] = session.get("_task")
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _active_bots.pop(user_id, None)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# DB helpers (synchronous — called via run_in_executor)
# ══════════════════════════════════════════════════════════════════════════════

def _db_upsert_meeting_sync(user_id: str, organization_id: str, meet_url: str, title: str) -> str:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    from sqlalchemy import select

    external_id = meet_url.rstrip("/").split("/")[-1]
    now = datetime.utcnow()

    with SessionLocal() as db:
        existing = db.execute(
            select(Meeting).where(
                Meeting.organizer_id == user_id,
                Meeting.platform == "meet",
                Meeting.external_id == external_id,
            )
        ).scalar_one_or_none()

        if existing:
            return str(existing.id)

        meeting = Meeting(
            organization_id=organization_id,
            title=title,
            platform="meet",
            external_id=external_id,
            meeting_url=meet_url,
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=1),
            actual_start=now,
            organizer_id=user_id,
            created_by=user_id,
            status="in_progress",
            recording_consent=True,
            transcription_status="pending",
            meeting_metadata={"source": "meet_bot"},
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return str(meeting.id)


def _db_save_recording_path_sync(meeting_id: str, recording_path: str) -> None:
    import uuid
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting

    with SessionLocal() as db:
        meeting = db.get(Meeting, uuid.UUID(meeting_id))
        if meeting:
            meeting.recording_path = recording_path
            meeting.status = "transcribing"
            meeting.transcription_status = "queued"
            db.commit()


def _db_mark_failed_sync(meeting_id: str, error: str) -> None:
    import uuid
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting

    with SessionLocal() as db:
        meeting = db.get(Meeting, uuid.UUID(meeting_id))
        if meeting:
            meeting.status = "failed"
            meeting.transcription_status = "failed"
            meeting.meeting_metadata = {
                **(meeting.meeting_metadata or {}),
                "bot_error": error,
                "failed_at": datetime.utcnow().isoformat(),
            }
            db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# Low-level Playwright helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _screenshot(page: Any, step: str, user_id: str) -> None:
    """Always saves to /tmp/syncminds_bot/; also to ./debug/ when MEET_BOT_DEBUG=1."""
    try:
        ts = datetime.now().strftime("%H%M%S")
        fname = f"{user_id[:8]}_{ts}_{step}.png"
        _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _SCREENSHOT_DIR / fname
        await page.screenshot(path=str(tmp_path), full_page=False)
        _bot_print(f"[BOT] Screenshot saved: {tmp_path}")
        if _DEBUG:
            _DEBUG_DIR.mkdir(exist_ok=True)
            await page.screenshot(path=str(_DEBUG_DIR / fname), full_page=False)
    except Exception as e:
        _bot_print(f"[BOT] Screenshot failed ({step}): {e}")


async def _is_visible(page: Any, selector: str, timeout_ms: int = 2_000) -> bool:
    """Quick visibility probe — returns bool, never raises."""
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:
        return False


async def _probe_first(page: Any, selectors: List[str], timeout_ms: int = 3_000) -> Optional[Any]:
    """
    Return the first Locator that is visible within timeout_ms total.
    Divides time equally across selectors.
    """
    if not selectors:
        return None
    per = max(400, timeout_ms // len(selectors))
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=per)
            return loc
        except Exception:
            continue
    return None


async def _click_probe(page: Any, selectors: List[str], timeout_ms: int = ELEMENT_TIMEOUT) -> bool:
    """Click the first visible selector. Returns True if clicked."""
    loc = await _probe_first(page, selectors, timeout_ms)
    if loc:
        await loc.click()
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Pre-join screen state machine
# ══════════════════════════════════════════════════════════════════════════════

async def _wait_media_card_gone(page: Any, user_id: str, timeout_s: float = 5.0) -> bool:
    """
    Block until the 'Allow microphone and camera' card is fully gone from the DOM.

    The card has a backdrop that absorbs all click events behind it. We must not
    proceed to disable mic/cam or click Join until this card is fully removed.
    Returns True once confirmed gone, False on timeout.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        still_present = False
        for sel in _MEDIA_CONSENT_CARD_GONE:
            try:
                if await page.locator(sel).first.is_visible(timeout=500):
                    still_present = True
                    break
            except Exception:
                pass
        if not still_present:
            logger.info("[BOT] [user=%s] media consent card gone — safe to proceed", user_id)
            return True
        await asyncio.sleep(0.4)
    logger.warning("[BOT] [user=%s] media consent card still present after %.1fs", user_id, timeout_s)
    return False


async def _handle_permissions(context: Any, page: Any, user_id: str) -> None:
    """
    Two-part permission handler:

    Part 1 — Playwright-level grant.
    Writes the permission grant into the browser's internal store for
    meet.google.com specifically. This prevents the *browser chrome* popup
    (the OS-level "allow/deny" dialog). Must be done before getUserMedia() runs.

    Part 2 — In-app media consent card dismissal.
    After the browser-level grant, Meet renders its own React card:
        "Do you want people to see and hear you in the meeting?"
    with a blue "Allow microphone and camera" button. This card has a backdrop
    that absorbs ALL click events behind it — including "Join now". It MUST be
    clicked and confirmed gone before any other interaction.

    We retry the dismissal up to 3 times with increasing waits because Meet
    can re-render the card after initial page hydration.
    """
    # Part 1: Playwright permission grant (browser-level)
    try:
        await context.grant_permissions(
            ["microphone", "camera"],
            origin="https://meet.google.com",
        )
        logger.info("[BOT] [user=%s] Playwright permission grant OK for meet.google.com", user_id)
    except Exception as exc:
        logger.warning("[BOT] [user=%s] grant_permissions failed (non-fatal): %s", user_id, exc)

    # Part 2: In-app card dismissal with retry + confirmed-gone check
    for attempt in range(1, 4):
        card_found = False
        for sel in _MEDIA_CONSENT_CARD:
            try:
                loc = page.locator(sel).first
                # Longer timeout on first attempt — card may still be rendering
                wait_ms = 3_000 if attempt == 1 else 1_500
                if await loc.is_visible(timeout=wait_ms):
                    await loc.click()
                    logger.info(
                        "[BOT] [user=%s] media consent card clicked (attempt %d, sel=%s)",
                        user_id, attempt, sel,
                    )
                    card_found = True
                    # Wait for the card to actually leave the DOM
                    gone = await _wait_media_card_gone(page, user_id, timeout_s=4.0)
                    if gone:
                        return   # success — card is gone
                    # Card is still there despite the click — retry
                    logger.warning(
                        "[BOT] [user=%s] card not gone after click (attempt %d) — retrying",
                        user_id, attempt,
                    )
                    break
            except Exception:
                continue

        if not card_found:
            logger.debug("[BOT] [user=%s] no media consent card visible (attempt %d)", user_id, attempt)
            return   # card was never there — nothing to do

        await asyncio.sleep(0.5 * attempt)  # back-off between retries


async def _wait_for_prejoin_screen(page: Any, user_id: str) -> bool:
    """
    Wait for Google Meet pre-join screen with retry-on-block logic.

    Per-attempt loop (up to PREJOIN_MAX_RETRIES = 3):
      Poll DOM for PREJOIN_POLL_PER_ATTEMPT_S (12 s) each attempt.
      Priority order checked on every poll tick:

        1. name input visible          → ready (guest flow)
        2. "Ask to join" button        → ready (waiting-room flow)
        3. "Join now" button           → ready (direct-join flow)
        4. mic/cam toggle present      → ready (any authenticated flow)
        5. "You can't join…" text      → NOT fatal — reload and retry
        6. fatal errors (ended/invalid)→ abort immediately
        7. URL left meet.google.com    → abort immediately

    "You can't join" is treated as a transient Meet state (race condition on
    session start, host hasn't started the meeting yet, etc.). Each detection
    takes a screenshot, waits PREJOIN_BLOCKED_WAIT_S (5 s), reloads the page,
    and continues to the next attempt. Only fails if it persists through all
    PREJOIN_MAX_RETRIES reloads.

    Returns True if any ready signal is found. False only after all retries
    exhausted or a genuinely fatal condition is detected.
    Screenshot saved on every non-ready exit.
    """

    # ── Ordered ready-state checks ────────────────────────────────────────────
    # Each entry: (CSS selector, human label)
    _READY: List[tuple] = [
        ('input[placeholder*="name" i]',  "NAME_INPUT"),
        ('button:has-text("Ask to join")', "ASK_TO_JOIN"),
        ('button[jsname="CwaK9"]',         "ASK_TO_JOIN"),
        ('button:has-text("Join now")',     "JOIN_NOW"),
        ('button[jsname="Qx7uuf"]',        "JOIN_NOW"),
        ('[jsname="BOHaEe"]',              "MIC_TOGGLE"),
        ('[jsname="R3Gied"]',              "CAM_TOGGLE"),
    ]

    # Texts that should trigger a reload-and-retry (NOT immediate abort)
    _CANT_JOIN: List[str] = [
        "You can't join this video call",
        "can't join this video call",
    ]

    # Texts that are genuinely fatal — reloading won't help
    _FATAL: List[str] = [
        "This call has ended",
        "Invalid video call name",
        "No longer available",
    ]

    async def _page_state() -> tuple:
        """
        Single DOM scan. Returns (kind, detail) where kind is one of:
          'ready'     — a pre-join signal is visible
          'cant_join' — transient block; caller should reload
          'fatal'     — unrecoverable error
          'redirect'  — URL left meet.google.com
          'loading'   — nothing matched yet; keep polling
        """
        if "meet.google.com" not in page.url:
            return ("redirect", page.url)

        for sel, label in _READY:
            try:
                if await page.locator(sel).first.is_visible(timeout=600):
                    return ("ready", label)
            except Exception:
                pass

        for text in _FATAL:
            try:
                if await page.get_by_text(text, exact=False).first.is_visible(timeout=400):
                    return ("fatal", text)
            except Exception:
                pass

        for text in _CANT_JOIN:
            try:
                if await page.get_by_text(text, exact=False).first.is_visible(timeout=400):
                    return ("cant_join", text)
            except Exception:
                pass

        return ("loading", "")

    async def _log_snippet() -> None:
        try:
            html  = await page.content()
            snip  = re.sub(r"<[^>]+>", " ", html)
            snip  = re.sub(r"\s+", " ", snip).strip()[:400]
            _bot_print(f"[BOT]   page snippet: {snip!r}")
        except Exception:
            pass

    # ── Retry loop ────────────────────────────────────────────────────────────
    for attempt in range(1, PREJOIN_MAX_RETRIES + 1):
        _bot_print(
            f"[BOT] Pre-join attempt {attempt}/{PREJOIN_MAX_RETRIES} — URL={page.url}"
        )

        blocked_text: Optional[str] = None
        deadline = asyncio.get_event_loop().time() + PREJOIN_POLL_PER_ATTEMPT_S

        # ── Inner poll loop ───────────────────────────────────────────────────
        while asyncio.get_event_loop().time() < deadline:
            kind, detail = await _page_state()

            if kind == "ready":
                _bot_print(
                    f"[BOT] ✓ Pre-join ready — state={detail!r} "
                    f"attempt={attempt}/{PREJOIN_MAX_RETRIES} URL={page.url}"
                )
                return True

            if kind == "redirect":
                _bot_print(
                    f"[BOT ERROR] URL left meet.google.com on attempt {attempt} — URL={detail}"
                )
                await _screenshot(page, f"prejoin_redirect_a{attempt}", user_id)
                return False

            if kind == "fatal":
                _bot_print(
                    f"[BOT ERROR] Fatal pre-join screen on attempt {attempt}: "
                    f"'{detail}' — URL={page.url}"
                )
                await _log_snippet()
                await _screenshot(page, f"prejoin_fatal_a{attempt}", user_id)
                return False

            if kind == "cant_join":
                # Stop polling this attempt — handle below
                blocked_text = detail
                break

            # kind == "loading" — React SPA still hydrating, keep polling
            await asyncio.sleep(0.8)

        # ── Post-poll handling ─────────────────────────────────────────────────
        if blocked_text:
            _bot_print(
                f"[BOT WARNING] 'Can't join' on attempt {attempt}/{PREJOIN_MAX_RETRIES}: "
                f"'{blocked_text}' — URL={page.url}"
            )
            await _log_snippet()
            await _screenshot(page, f"prejoin_blocked_attempt_{attempt}", user_id)

            if attempt < PREJOIN_MAX_RETRIES:
                _bot_print(
                    f"[BOT] Waiting {PREJOIN_BLOCKED_WAIT_S}s then reloading "
                    f"(attempt {attempt + 1} coming up)…"
                )
                await asyncio.sleep(PREJOIN_BLOCKED_WAIT_S)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=35_000)
                    _bot_print(f"[BOT] Page reloaded — URL={page.url}")
                except Exception as exc:
                    _bot_print(f"[BOT] Reload failed (non-fatal): {exc}")
        else:
            # Timed out with no signal at all — log and try next attempt
            _bot_print(
                f"[BOT] No pre-join signal after {PREJOIN_POLL_PER_ATTEMPT_S}s "
                f"on attempt {attempt}/{PREJOIN_MAX_RETRIES} — URL={page.url}"
            )
            await _log_snippet()
            await _screenshot(page, f"prejoin_timeout_a{attempt}", user_id)

    # ── All attempts exhausted ─────────────────────────────────────────────────
    _bot_print(
        f"[BOT ERROR] Pre-join failed after {PREJOIN_MAX_RETRIES} attempts — URL={page.url}"
    )
    await _screenshot(page, "prejoin_failed_all_attempts", user_id)
    return False


async def _enter_name(page: Any, bot_name: str, user_id: str) -> bool:
    """
    Fill the guest name input with bot_name.

    The name field only appears in the GUEST flow (no Google session).
    If it is not found we are in the signed-in flow — that is now treated as
    an error because we deliberately cleared cookies before navigation.

    Retries up to 3 times with a 2 s gap to handle React's async rendering.
    """
    for attempt in range(1, 4):
        loc = await _probe_first(page, _NAME_INPUT, 6_000)
        if loc:
            try:
                await loc.click()                 # focus the field
                await loc.fill("")                # clear any pre-filled text
                await loc.fill(bot_name)          # fill() replaces all content
                await loc.press("Tab")            # trigger React onChange
                await asyncio.sleep(0.4)          # let React propagate the value

                # Verify the field actually contains the name we typed
                actual = await loc.input_value()
                if bot_name.lower() in actual.lower():
                    _bot_print(f"[BOT] ✓ Name field filled: '{actual}' — bot will join as guest")
                    return True
                _bot_print(f"[BOT] Name field value mismatch (got '{actual}') — retrying")
            except Exception as exc:
                _bot_print(f"[BOT] Name entry attempt {attempt} error: {exc}")
        else:
            _bot_print(
                f"[BOT] Name input NOT found on attempt {attempt}/3 "
                f"(still loading? or signed-in flow?)"
            )

        if attempt < 3:
            await asyncio.sleep(2)

    # Log current URL to help diagnose whether we ended up on a sign-in page
    _bot_print(
        f"[BOT WARNING] Could not fill name field after 3 attempts "
        f"— URL={page.url} — bot may join as signed-in user"
    )
    return False


async def _wait_join_enabled(page: Any, user_id: str) -> bool:
    """
    Fix 4: Wait for the join button to become ENABLED, not just visible.

    When joining as a guest, the "Join now" button is VISIBLE but DISABLED
    until the name field has content. The previous code clicked a disabled
    button and wondered why nothing happened.
    """
    logger.info("Bot: [user=%s] waiting for join button to become enabled…", user_id)
    deadline = asyncio.get_event_loop().time() + JOIN_ENABLED_TIMEOUT / 1000

    while asyncio.get_event_loop().time() < deadline:
        # Check by jsname (stable IDs) first
        for sel in _JOIN_BUTTONS_BY_JSNAME:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500) and await loc.is_enabled():
                    logger.info("Bot: [user=%s] join button enabled (%s)", user_id, sel)
                    return True
            except Exception:
                continue

        # Fallback: role-based check
        for text in _JOIN_BUTTON_TEXTS:
            try:
                btn = page.get_by_role("button", name=re.compile(re.escape(text), re.I))
                if await btn.is_visible(timeout=500) and await btn.is_enabled():
                    logger.info("Bot: [user=%s] join button enabled (role: %s)", user_id, text)
                    return True
            except Exception:
                continue

        await asyncio.sleep(0.5)

    logger.warning("Bot: [user=%s] join button did not become enabled in %dms", user_id, JOIN_ENABLED_TIMEOUT)
    return False


async def _disable_mic(page: Any, user_id: str) -> None:
    """
    Disable microphone on the pre-join screen.

    Strategy order (most → least portable):
    1. aria-label contains "Turn off" AND "microphone" → mic is ON, click to mute
    2. aria-label contains "microphone" (broader) + check label for "Turn off"
    3. data-is-muted="false" (internal Meet attribute, may not exist in all versions)
    4. jsname + aria-pressed state
    If none match and mic appears already off, log and continue.
    """
    # Strategy 1 & 2: aria-label based — universally readable by screen readers,
    # maintained by Google across Meet UI versions, locale-independent when using
    # the English phrases that Meet's JS always sets regardless of browser language.
    aria_on_selectors = [
        '[aria-label*="Turn off microphone" i]',
        '[aria-label*="microphone" i][aria-label*="Turn off" i]',
        '[data-tooltip*="Turn off microphone" i]',
        # data-is-muted is internal but very stable — belt-and-suspenders
        '[jsname="BOHaEe"][data-is-muted="false"]',
        '[aria-label="Turn off microphone"]',
    ]
    for sel in aria_on_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2_000):
                await loc.click()
                logger.info("Bot: [user=%s] microphone disabled via '%s'", user_id, sel)
                await asyncio.sleep(0.3)
                return
        except Exception:
            continue

    # Strategy 3: find any button with "microphone" in aria-label and inspect it
    try:
        mic_btn = page.locator('[aria-label*="microphone" i]').first
        if await mic_btn.is_visible(timeout=2_000):
            label = (await mic_btn.get_attribute("aria-label") or "").lower()
            if "turn off" in label:
                await mic_btn.click()
                logger.info("Bot: [user=%s] microphone disabled (dynamic aria-label check)", user_id)
                await asyncio.sleep(0.3)
                return
            logger.info("Bot: [user=%s] microphone already muted (label: %s)", user_id, label)
            return
    except Exception:
        pass

    # Strategy 4: jsname + aria-pressed (older Meet versions)
    try:
        mic_btn = page.locator('[jsname="BOHaEe"]').first
        if await mic_btn.is_visible(timeout=1_500):
            pressed = await mic_btn.get_attribute("aria-pressed")
            if pressed == "false":
                await mic_btn.click()
                logger.info("Bot: [user=%s] microphone disabled via aria-pressed", user_id)
                return
            logger.info("Bot: [user=%s] microphone already muted (aria-pressed)", user_id)
            return
    except Exception:
        pass

    logger.warning("Bot: [user=%s] microphone button not found — continuing anyway", user_id)


async def _disable_camera(page: Any, user_id: str) -> None:
    """
    Disable camera on the pre-join screen. Mirror of _disable_mic.

    Strategy order (most → least portable):
    1. aria-label contains "Turn off" AND "camera"
    2. aria-label contains "camera" (broader) + label text inspection
    3. data-is-muted="false" on camera jsname element
    4. jsname + aria-pressed
    """
    aria_on_selectors = [
        '[aria-label*="Turn off camera" i]',
        '[aria-label*="camera" i][aria-label*="Turn off" i]',
        '[data-tooltip*="Turn off camera" i]',
        '[jsname="R3Gied"][data-is-muted="false"]',
        '[aria-label="Turn off camera"]',
    ]
    for sel in aria_on_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2_000):
                await loc.click()
                logger.info("Bot: [user=%s] camera disabled via '%s'", user_id, sel)
                await asyncio.sleep(0.3)
                return
        except Exception:
            continue

    try:
        cam_btn = page.locator('[aria-label*="camera" i]').first
        if await cam_btn.is_visible(timeout=2_000):
            label = (await cam_btn.get_attribute("aria-label") or "").lower()
            if "turn off" in label:
                await cam_btn.click()
                logger.info("Bot: [user=%s] camera disabled (dynamic aria-label check)", user_id)
                await asyncio.sleep(0.3)
                return
            logger.info("Bot: [user=%s] camera already off (label: %s)", user_id, label)
            return
    except Exception:
        pass

    try:
        cam_btn = page.locator('[jsname="R3Gied"]').first
        if await cam_btn.is_visible(timeout=1_500):
            pressed = await cam_btn.get_attribute("aria-pressed")
            if pressed == "false":
                await cam_btn.click()
                logger.info("Bot: [user=%s] camera disabled via aria-pressed", user_id)
                return
            logger.info("Bot: [user=%s] camera already off (aria-pressed)", user_id)
            return
    except Exception:
        pass

    logger.warning("Bot: [user=%s] camera button not found — continuing anyway", user_id)


async def _dismiss_media_popup(page: Any, user_id: str) -> None:
    """
    Dismiss the 'Allow microphone and camera' in-app overlay.

    This is NOT the browser permission dialog (handled by context.grant_permissions).
    It is a React component Meet renders when it cannot confirm media permission state.
    It overlays the join button and absorbs clicks silently.

    Called once before the join loop starts AND at the top of every retry attempt,
    because Meet can re-render this overlay between attempts.
    """
    popup_selectors = [
        'button:has-text("Allow")',
        'button:has-text("Got it")',
        'button:has-text("Dismiss")',
        'button:has-text("Continue without")',
        '[jsname="IbE0S"]',
        '[data-mdc-dialog-action="accept"]',
        '[aria-label="Allow access"]',
        '[aria-label*="Allow" i]',
    ]
    for sel in popup_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=1_000):
                await loc.click()
                logger.info("[BOT] [user=%s] media popup dismissed via '%s'", user_id, sel)
                await asyncio.sleep(0.4)
                return
        except Exception:
            continue


async def _click_join(page: Any, user_id: str, bot_name: str = "") -> bool:
    """
    Reliably click the Google Meet join button on both pre-join and waiting-room screens.

    Strategy per attempt (tried in order, stop on first success):
      A  :has-text("Join now")          force=True
      B  :has-text("Ask to join")       force=True
      C  :has-text("Join").first        force=True
      D  [aria-label*="Join" i].first   force=True
      E  JS includes() scan + dispatchEvent(bubbles:true)

    Why force=True everywhere: React marks buttons disabled/hidden during async
    state transitions; force=True bypasses Playwright's actionability checks.

    Why JS includes(): exact-text regex misses whitespace variants, icon text,
    and locale variants. substring match is intentionally liberal here.
    """

    async def _ss(tag: str) -> None:
        """Save screenshot to /tmp/syncminds_bot/ always."""
        try:
            _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            p = _SCREENSHOT_DIR / f"{user_id[:8]}_{ts}_{tag}.png"
            await page.screenshot(path=str(p))
            _bot_print(f"[BOT] Screenshot → {p}")
        except Exception as exc:
            _bot_print(f"[BOT] Screenshot error ({tag}): {exc}")

    async def _dump_buttons() -> list:
        """Return all button texts from DOM for logging."""
        try:
            return await page.evaluate("""() =>
                [...document.querySelectorAll('button, [role=\"button\"]')].map(b => ({
                    text:     (b.innerText || b.textContent || '').trim().slice(0, 80),
                    label:    (b.getAttribute('aria-label') || '').slice(0, 80),
                    jsname:   b.getAttribute('jsname') || '',
                    disabled: b.disabled || b.getAttribute('disabled') !== null,
                    tag:      b.tagName,
                }))
            """)
        except Exception:
            return []

    async def _js_click() -> bool:
        """
        Scan ALL buttons with includes('join') or includes('ask') in text/label.
        Fires a bubbling MouseEvent — reaches React's root delegation listener
        even when the button is technically marked disabled.
        Returns True if a candidate was found and event was dispatched.
        """
        try:
            return await page.evaluate("""() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const b of buttons) {
                    const text  = (b.innerText  || b.textContent || '').toLowerCase();
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    if (text.includes('join') || text.includes('ask') || label.includes('join')) {
                        b.dispatchEvent(new MouseEvent('click', {
                            bubbles: true, cancelable: true, view: window
                        }));
                        return true;
                    }
                }
                return false;
            }""")
        except Exception as exc:
            _bot_print(f"[BOT] JS click error: {exc}")
            return False

    # ── 5 s initial wait — Meet's React UI finishes async state updates ───────
    _bot_print(f"[BOT] _click_join: 5 s stabilisation wait — user={user_id}")
    await page.wait_for_timeout(5_000)

    for attempt in range(1, 6):
        _bot_print(f"[BOT] ── Join attempt {attempt}/5 ──")

        # Dismiss any overlay that blocks the join button
        await _dismiss_media_popup(page, user_id)

        # Log ALL buttons so we know exactly what's rendered right now
        buttons = await _dump_buttons()
        _bot_print(f"[BOT] Buttons on page ({len(buttons)} total):")
        for b in buttons:
            if b.get("text") or b.get("label") or b.get("jsname"):
                _bot_print(
                    f"  · text={b['text']!r:45s} "
                    f"label={b['label']!r:35s} "
                    f"jsname={b.get('jsname','')!r:12s} "
                    f"disabled={b['disabled']}"
                )

        # Detect post-join "can't join" error screen:
        # Google Meet shows "Return to home screen" + "Submit feedback" buttons
        # (jsname=dqt8Pb / rhHFf) after rejecting the bot post-click.
        # No join button will exist on this screen, so all strategies would
        # fail. Reload and re-enter the pre-join flow instead.
        _CANT_JOIN_JSNAMES = {"dqt8Pb", "rhHFf"}
        _CANT_JOIN_TEXT    = {"return to home screen", "submit feedback"}
        page_jsnames = {b.get("jsname", "") for b in buttons}
        page_texts   = {b.get("text", "").lower() for b in buttons}
        has_join_btn = any(
            "join" in b.get("text", "").lower() or "join" in b.get("label", "").lower()
            for b in buttons
        )
        is_blocked_screen = (
            bool(_CANT_JOIN_JSNAMES & page_jsnames) or
            bool(_CANT_JOIN_TEXT & page_texts)
        ) and not has_join_btn

        if is_blocked_screen:
            _bot_print(
                f"[BOT WARNING] Post-join 'can't join' screen detected "
                f"on attempt {attempt}/5 — jsnames={page_jsnames & _CANT_JOIN_JSNAMES}"
            )
            await _ss(f"cant_join_postclick_a{attempt}")
            if attempt < 5:
                await asyncio.sleep(3)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=35_000)
                    _bot_print(f"[BOT] Page reloaded for post-join retry — URL={page.url}")
                except Exception as exc:
                    _bot_print(f"[BOT] Reload failed (non-fatal): {exc}")
                prejoin_ok = await _wait_for_prejoin_screen(page, user_id)
                if prejoin_ok:
                    _bot_print("[BOT] Pre-join screen restored after reload")
                    if bot_name:
                        await _enter_name(page, bot_name, user_id)
                        await asyncio.sleep(0.6)
                    await _disable_mic(page, user_id)
                    await asyncio.sleep(0.3)
                    await _disable_camera(page, user_id)
                    await asyncio.sleep(0.3)
                    await _dismiss_media_popup(page, user_id)
                    await _wait_join_enabled(page, user_id)
                else:
                    _bot_print("[BOT WARNING] Pre-join did not render after reload — continuing anyway")
            await asyncio.sleep(3)
            continue

        # Screenshot BEFORE clicking
        await _ss(f"before_click_{attempt}")

        joined = False

        # ── Strategy A: "Join now" ────────────────────────────────────────────
        try:
            loc = page.locator('button:has-text("Join now")')
            if await loc.count() > 0:
                _bot_print("[BOT] Strategy A — clicking 'Join now' (force=True)")
                await loc.first.click(force=True, timeout=5_000)
                joined = True
        except Exception as exc:
            _bot_print(f"[BOT] Strategy A failed: {exc}")

        # ── Strategy B: "Ask to join" ─────────────────────────────────────────
        if not joined:
            try:
                loc = page.locator('button:has-text("Ask to join")')
                if await loc.count() > 0:
                    _bot_print("[BOT] Strategy B — clicking 'Ask to join' (force=True)")
                    await loc.first.click(force=True, timeout=5_000)
                    joined = True
            except Exception as exc:
                _bot_print(f"[BOT] Strategy B failed: {exc}")

        # ── Strategy C: any "Join" button ────────────────────────────────────
        if not joined:
            try:
                loc = page.locator('button:has-text("Join")')
                if await loc.count() > 0:
                    _bot_print(f"[BOT] Strategy C — clicking first 'Join' button (force=True)")
                    await loc.first.click(force=True, timeout=5_000)
                    joined = True
            except Exception as exc:
                _bot_print(f"[BOT] Strategy C failed: {exc}")

        # ── Strategy D: aria-label contains "Join" ───────────────────────────
        if not joined:
            try:
                loc = page.locator('[aria-label*="Join" i]')
                if await loc.count() > 0:
                    _bot_print(f"[BOT] Strategy D — clicking aria-label*=Join (force=True)")
                    await loc.first.click(force=True, timeout=5_000)
                    joined = True
            except Exception as exc:
                _bot_print(f"[BOT] Strategy D failed: {exc}")

        # ── Strategy E: JS includes() scan ───────────────────────────────────
        if not joined:
            _bot_print("[BOT] Strategy E — JS includes() scan + dispatchEvent")
            fired = await _js_click()
            _bot_print(f"[BOT] Strategy E: event dispatched={fired}")
            if fired:
                joined = True

        # ── Post-click checks ─────────────────────────────────────────────────
        if joined:
            _bot_print(f"[BOT] Click fired on attempt {attempt} — waiting 3 s to confirm join")
            await page.wait_for_timeout(3_000)
            await _ss(f"after_click_{attempt}")

            # Check in-meeting via Leave call button
            try:
                leave_count = await page.locator('[aria-label*="Leave call" i]').count()
                _bot_print(f"[BOT] Leave-call elements found: {leave_count}")
                if leave_count > 0:
                    _bot_print(f"[BOT] ✓ JOINED — Leave call button visible")
                    return True
            except Exception:
                pass

            if await _wait_in_meeting_quick(page):
                _bot_print(f"[BOT] ✓ JOINED — in-meeting signals confirmed")
                return True

            _bot_print(f"[BOT] Click fired but join not yet confirmed — will retry")
        else:
            _bot_print(f"[BOT] No join button found on attempt {attempt}")

        if attempt < 5:
            _bot_print(f"[BOT] Waiting 3 s before attempt {attempt + 1}")
            await asyncio.sleep(3)

    # ── All attempts exhausted ────────────────────────────────────────────────
    _bot_print(f"[BOT ERROR] All 5 join attempts failed — user={user_id}")
    await _ss("join_failed_final")
    return False


async def _wait_in_meeting_quick(page: Any) -> bool:
    """
    Fast non-blocking in-meeting probe (≤1s per signal).
    Used inside the join retry loop to detect a silent successful join.
    """
    for sel in _IN_MEETING:
        try:
            if await page.locator(sel).first.is_visible(timeout=1_000):
                return True
        except Exception:
            pass
    return False


async def _wait_in_meeting(page: Any, user_id: str) -> bool:
    """
    Poll until in-meeting UI is confirmed or IN_MEETING_TIMEOUT elapses.

    Signals checked (any one is sufficient):
      - 'Leave call' button  → inside a live meeting
      - data-allocation-index  → participant tile rendered
      - jscontroller="ynJ3Fb"  → meeting controls bar
    """
    logger.info("[BOT] [user=%s] waiting to confirm entry into meeting…", user_id)
    deadline = asyncio.get_event_loop().time() + IN_MEETING_TIMEOUT / 1000

    while asyncio.get_event_loop().time() < deadline:
        for sel in _IN_MEETING:
            try:
                if await page.locator(sel).first.is_visible(timeout=2_000):
                    logger.info("[BOT] [user=%s] ✓ in-meeting confirmed — signal: %s", user_id, sel)
                    return True
            except Exception:
                pass
        await asyncio.sleep(2)

    logger.warning("[BOT] [user=%s] ✗ in-meeting not confirmed within %dms", user_id, IN_MEETING_TIMEOUT)
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Core session
# ══════════════════════════════════════════════════════════════════════════════

# Chromium launch flags
_CHROMIUM_ARGS = [
    # ── Permission & media ───────────────────────────────────────────────────
    "--use-fake-ui-for-media-stream",       # auto-grant getUserMedia, no browser dialog
    "--use-fake-device-for-media-stream",   # inject fake audio/video device
    "--autoplay-policy=no-user-gesture-required",
    # ── Anti-detection ───────────────────────────────────────────────────────
    "--disable-blink-features=AutomationControlled",
    # ── Stability ────────────────────────────────────────────────────────────
    "--no-sandbox",
    "--disable-dev-shm-usage",              # prevents /dev/shm OOM in containers
    "--disable-gpu",                        # headful still works without GPU
    "--disable-infobars",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-popup-blocking",
    "--window-size=1280,720",
]

# JavaScript injected before every page load
_INIT_SCRIPTS = [
    # Remove webdriver flag that Google uses to detect automation
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
    # Spoof plugin count so Meet's device detection looks normal
    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});",
    # Spoof language list
    "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});",
]

# WebRTC intercept (injected for audio recording — must run before Meet JS)
_WEBRTC_INTERCEPT = """
(function() {
    if (window._syncMinds) return;
    window._syncMinds = {
        tracks: [],        // { track, remote: bool }
        chunks: [],
        recorder: null,
        audioCtx: null,
        destination: null,
    };

    function _capture(track, isRemote) {
        if (track.kind !== 'audio') return;
        if (window._syncMinds.tracks.some(t => t.track.id === track.id)) return;
        console.log('[SM] captured audio track remote=' + isRemote + ' id=' + track.id + ' state=' + track.readyState);
        window._syncMinds.tracks.push({ track: track, remote: isRemote });
        // Hot-wire into a running recorder (late-arriving remote tracks)
        const s = window._syncMinds;
        if (s.audioCtx && s.destination && track.readyState === 'live') {
            try {
                const src = s.audioCtx.createMediaStreamSource(new MediaStream([track]));
                src.connect(s.destination);
                console.log('[SM] hot-connected late track ' + track.id);
            } catch(e) { console.warn('[SM] hot-connect failed', e); }
        }
    }

    // 1. addTrack — local outgoing tracks (bot's fake mic, mark as local)
    // Mute local audio immediately so the fake-device sine-wave tone is never
    // transmitted to meeting participants. track.enabled=false silences the
    // MediaStreamTrack at the source; the connection still negotiates normally
    // so WebRTC doesn't detect the bot as "no audio" and drop the channel.
    const _origAddTrack = RTCPeerConnection.prototype.addTrack;
    RTCPeerConnection.prototype.addTrack = function(track) {
        if (track.kind === 'audio') {
            track.enabled = false;
        }
        _capture(track, false);
        return _origAddTrack.apply(this, arguments);
    };

    // 2. addEventListener wrapper — catches remote 'track' events registered by Meet's JS
    const _origAEL = RTCPeerConnection.prototype.addEventListener;
    RTCPeerConnection.prototype.addEventListener = function(type, fn, ...rest) {
        if (type === 'track') {
            const wrapped = function(e) {
                if (e.track) _capture(e.track, true);
                return fn.apply(this, arguments);
            };
            return _origAEL.call(this, type, wrapped, ...rest);
        }
        return _origAEL.call(this, type, fn, ...rest);
    };

    // 3. ontrack setter — Google Meet assigns .ontrack directly on the instance
    const _desc = Object.getOwnPropertyDescriptor(RTCPeerConnection.prototype, 'ontrack');
    if (_desc && _desc.set) {
        Object.defineProperty(RTCPeerConnection.prototype, 'ontrack', {
            get() { return this._sm_ontrack; },
            set(fn) {
                this._sm_ontrack = fn;
                _desc.set.call(this, fn ? function(e) {
                    if (e.track) _capture(e.track, true);
                    return fn.apply(this, arguments);
                } : null);
            },
            configurable: true,
        });
    }

    console.log('[SyncMinds] WebRTC intercept v2 active — remote+local track capture');
})();
"""


async def _run_session(
    meet_url: str,
    user_id: str,
    organization_id: str,
    meeting_id: str,
    bot_display_name: str,
    stay_duration_seconds: int,
    recordings_dir: str,
) -> None:
    """Full bot lifecycle. Up to MAX_RETRIES attempts. Never raises."""
    _bot_print(f"[BOT] _run_session STARTED — user={user_id} meeting={meeting_id} url={meet_url}")

    from playwright.async_api import async_playwright
    from app.services.recording_service import (
        RecordingSession,
        get_pulse_sink_env,
        start_recording,
        start_playwright_recording,
        stop_recording,
    )
    from app.tasks.meeting_processor import process_meeting_recording_background

    _bot_print(f"[BOT] All imports OK — headless={_HEADLESS}")
    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        _bot_print(f"[BOT] Attempt {attempt}/{MAX_RETRIES} — user={user_id} url={meet_url}")

        rec_session: Optional[RecordingSession] = None
        browser = None

        try:
            # ── Step 1: Start ffmpeg recording before browser (PulseAudio sink must exist) ──
            _bot_print(f"[BOT] Step 1: starting audio recording (meeting={meeting_id})")
            try:
                rec_session = await start_recording(meeting_id, output_dir=recordings_dir)
            except Exception as rec_exc:
                _bot_print(f"[BOT] Step 1: recording start failed (non-fatal) — {rec_exc}")
                rec_session = None
            pulse_env = get_pulse_sink_env(rec_session)
            _bot_print(f"[BOT] Step 1 done: rec_session={'ffmpeg' if rec_session else 'None — will use Playwright recorder after join'}")

            # ── Step 2: Launch Chromium ────────────────────────────────────────
            _bot_print(f"[BOT] Step 2: launching Chromium headless={_HEADLESS}")
            async with async_playwright() as pw:
                chromium_env = {**os.environ, **pulse_env}

                try:
                    browser = await pw.chromium.launch(
                        headless=_HEADLESS,
                        env=chromium_env,
                        args=_CHROMIUM_ARGS,
                    )
                except Exception as launch_exc:
                    _bot_print(f"[BOT ERROR] Browser launch failed: {launch_exc}")
                    raise

                _bot_print(f"[BOT] Step 2 done: Chromium launched")

                # storage_state=None + clear_cookies() guarantees the bot joins
                # as a GUEST ("SyncMinds Bot") rather than as the logged-in user.
                context = await browser.new_context(
                    storage_state=None,      # no saved cookies / localStorage
                    permissions=["microphone", "camera"],
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                )

                # Belt-and-suspenders: wipe any residual cookies that Chromium
                # may have inherited from the OS environment or a temp profile.
                await context.clear_cookies()
                _bot_print(f"[BOT] Browser context created fresh — no Google session, bot will join as guest")

                # Grant specifically to meet.google.com (suspenders)
                try:
                    await context.grant_permissions(
                        ["microphone", "camera"],
                        origin="https://meet.google.com",
                    )
                except Exception:
                    pass

                page = await context.new_page()

                # Inject init scripts — run before every navigation
                for script in _INIT_SCRIPTS:
                    await page.add_init_script(script)
                await page.add_init_script(_WEBRTC_INTERCEPT)

                # ── Step 3: Navigate ──────────────────────────────────────────
                _active_bots[user_id]["status"] = "navigating"
                _bot_print(f"[BOT] Step 3: navigating to {meet_url}")

                await page.goto(
                    meet_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT,
                )
                current_url = page.url
                _bot_print(f"[BOT] Step 3 done: page loaded — current URL: {current_url}")

                # ── Redirect guard ────────────────────────────────────────────
                # If Meet detects no session it redirects to accounts.google.com.
                # We don't want to sign in — navigate back to the bare Meet URL so
                # the guest flow renders (name input + "Ask to join").
                if "accounts.google.com" in current_url or "signin" in current_url.lower():
                    _bot_print(f"[BOT] Google sign-in redirect detected — re-navigating as guest")
                    await page.goto(meet_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                    _bot_print(f"[BOT] Re-navigated — URL now: {page.url}")

                # Wait for network to settle (Meet's React bundle + API calls).
                # networkidle may never fire if Meet keeps WebSocket connections open,
                # so cap at 10s and fall through — it's a best-effort gate.
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                    _bot_print(f"[BOT] networkidle reached")
                except Exception:
                    _bot_print(f"[BOT] networkidle timeout (normal for Meet) — continuing")

                # Confirm the browser has rendered at least one button before proceeding.
                try:
                    await page.wait_for_selector("button", timeout=15_000)
                    _bot_print(f"[BOT] first button visible — DOM ready")
                except Exception as exc:
                    _bot_print(f"[BOT WARNING] no button found after 15s: {exc}")

                await _screenshot(page, "01_loaded", user_id)

                # ── Step 4: Grant permissions + dismiss overlays ───────────────
                _bot_print(f"[BOT] Step 4: handling permissions/overlays")
                await _handle_permissions(context, page, user_id)

                # ── Step 5: Wait for pre-join screen (poll + fallback) ────────
                _active_bots[user_id]["status"] = "pre_join"
                _bot_print(f"[BOT] Step 5: waiting for pre-join screen")
                ready = await _wait_for_prejoin_screen(page, user_id)
                await _screenshot(page, "02_prejoin", user_id)

                if not ready:
                    raise RuntimeError("Pre-join screen did not render within timeout")

                _bot_print(f"[BOT] Step 5 done: pre-join screen ready")

                # ── Step 5b: CRITICAL — dismiss media consent card ────────────
                _bot_print(f"[BOT] Step 5b: checking for media consent card (signed-in flow)")
                await _handle_permissions(context, page, user_id)
                await _screenshot(page, "02b_after_consent", user_id)

                # ── Step 6: Enter guest name ──────────────────────────────────
                # Since we cleared cookies, Meet should show the guest pre-join
                # with a "Your name" input. Fill it with bot_display_name so the
                # bot appears as "SyncMinds Bot" (or configured name) in the call.
                _bot_print(f"[BOT] Step 6: entering guest name '{bot_display_name}'")
                name_entered = await _enter_name(page, bot_display_name, user_id)
                if name_entered:
                    _bot_print(f"[BOT] Step 6 done: guest name entered — bot identity confirmed")
                    await _screenshot(page, "02c_name_entered", user_id)
                    await asyncio.sleep(0.6)
                else:
                    _bot_print(f"[BOT] Step 6: name field not found — proceeding (may be signed-in flow)")
                    await _screenshot(page, "02c_name_failed", user_id)

                # ── Step 7: Wait for join button to be ENABLED ────────────────
                _bot_print(f"[BOT] Step 7: waiting for join button to become enabled")
                await _wait_join_enabled(page, user_id)
                await _screenshot(page, "03_ready_to_join", user_id)

                # ── Step 8: Disable mic and camera ────────────────────────────
                _bot_print(f"[BOT] Step 8: disabling mic and camera")
                await _disable_mic(page, user_id)
                await asyncio.sleep(0.3)
                await _disable_camera(page, user_id)
                await asyncio.sleep(0.3)
                await _screenshot(page, "04_media_off", user_id)

                # ── Step 8b: Final sweep for media consent card ───────────────
                await _dismiss_media_popup(page, user_id)
                await _wait_media_card_gone(page, user_id, timeout_s=3.0)

                # ── Step 9: Click join ────────────────────────────────────────
                _active_bots[user_id]["status"] = "joining"
                _bot_print(f"[BOT] Step 9: clicking join button")
                joined_click = await _click_join(page, user_id, bot_name=bot_display_name)
                if not joined_click:
                    await _screenshot(page, "05_join_failed", user_id)
                    raise RuntimeError("Could not find or click any join button")

                _bot_print(f"[BOT] Step 9 done: join button clicked")
                await _screenshot(page, "05_join_clicked", user_id)

                # ── Step 10: Confirm in-meeting ───────────────────────────────
                _bot_print(f"[BOT] Step 10: confirming we are in the meeting")
                in_meeting = await _wait_in_meeting(page, user_id)
                status = "in_meeting" if in_meeting else "waiting_admission"
                _active_bots[user_id]["status"] = status
                await _screenshot(page, "06_in_meeting" if in_meeting else "06_waiting_room", user_id)
                _bot_print(f"[BOT] Step 10 done: status={status} — staying {stay_duration_seconds}s")

                # ── Step 11: Start in-page recording if ffmpeg unavailable ────
                if rec_session is None:
                    _bot_print(f"[BOT] Step 11: starting Playwright MediaRecorder (ffmpeg unavailable)")
                    await asyncio.sleep(2)
                    rec_session = await start_playwright_recording(
                        page=page,
                        meeting_id=meeting_id,
                        output_dir=recordings_dir,
                    )
                    if rec_session:
                        _bot_print(f"[BOT] Step 11 done: Playwright recording started")
                    else:
                        _bot_print(f"[BOT WARNING] Step 11: no recording available for this session")

                # ── Step 12: Stay in meeting (early exit when everyone leaves) ───
                _bot_print(f"[BOT] Step 12: staying in meeting for up to {stay_duration_seconds}s")
                elapsed = 0
                poll_interval = 10
                consecutive_gone = 0   # Leave button disappeared
                alone_since: Optional[float] = None  # timestamp when bot became last participant
                ALONE_GRACE = 30       # seconds to wait after going alone before leaving

                while elapsed < stay_duration_seconds:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                    # ── Check 1: has the meeting page itself ended? ──────────
                    try:
                        leave_btn = page.locator('[aria-label*="Leave call" i]').first
                        visible = await leave_btn.is_visible(timeout=2_000)
                        if not visible:
                            consecutive_gone += 1
                            if consecutive_gone >= 2:
                                _bot_print(
                                    f"[BOT] Step 12: meeting page ended (Leave button gone after {elapsed}s)"
                                )
                                break
                        else:
                            consecutive_gone = 0
                    except Exception:
                        consecutive_gone += 1
                        if consecutive_gone >= 2:
                            _bot_print(
                                f"[BOT] Step 12: meeting likely ended (Leave button error after {elapsed}s)"
                            )
                            break

                    # ── Check 2: is the bot the only participant? ───────────
                    # Participants leave without "ending for all" → Leave button
                    # stays visible but the room is empty.
                    try:
                        is_alone = await page.evaluate("""
                            () => {
                                const body = document.body?.innerText || '';
                                // Meet shows these when you're the last person
                                if (/no one else|everyone.*left|only (you|one)/i.test(body)) return true;

                                // Participant count badge — Meet renders it as
                                // aria-label="Show everyone (N)" or similar
                                const btns = Array.from(document.querySelectorAll('button'));
                                for (const btn of btns) {
                                    const lbl = btn.getAttribute('aria-label') || '';
                                    if (/participant|people|everyone/i.test(lbl)) {
                                        const m = lbl.match(/\((\d+)\)/);
                                        if (m && parseInt(m[1]) <= 1) return true;
                                    }
                                }

                                // Count remote video/audio tiles (exclude self-preview)
                                // Meet wraps each participant in [data-participant-id]
                                const tiles = document.querySelectorAll('[data-participant-id]');
                                if (tiles.length === 0) {
                                    // Fallback: look for at least one non-local video element
                                    const vids = document.querySelectorAll('video');
                                    return vids.length <= 1;
                                }
                                return tiles.length <= 1;
                            }
                        """)
                        if is_alone:
                            if alone_since is None:
                                alone_since = elapsed
                                _bot_print(f"[BOT] Step 12: bot appears to be alone at {elapsed}s — waiting {ALONE_GRACE}s grace period")
                            elif elapsed - alone_since >= ALONE_GRACE:
                                _bot_print(f"[BOT] Step 12: still alone after grace period — stopping early at {elapsed}s")
                                break
                        else:
                            if alone_since is not None:
                                _bot_print(f"[BOT] Step 12: participant rejoined — resetting alone timer")
                            alone_since = None
                    except Exception as e:
                        _bot_print(f"[BOT] Step 12: alone-check error (non-fatal): {e}")

                _bot_print(f"[BOT] Step 12 done: stayed {elapsed}s")

                # ── Step 13: Stop recording ───────────────────────────────────
                _bot_print(f"[BOT] Step 13: stopping recording")
                _active_bots[user_id]["status"] = "stopping_recording"
                recording_path = await stop_recording(rec_session, page=page)
                rec_session = None

                if recording_path and recording_path.exists():
                    path_str = str(recording_path.resolve())
                    _active_bots[user_id]["recording_path"] = path_str
                    _bot_print(f"[BOT] Step 13 done: recording saved → {path_str}")

                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, _db_save_recording_path_sync, meeting_id, path_str
                    )
                    _bot_print(f"[BOT] dispatching transcription for meeting={meeting_id}")
                    process_meeting_recording_background(meeting_id, path_str)
                else:
                    _bot_print(f"[BOT WARNING] no recording file — skipping transcription")

                # ── Step 14: Leave meeting ────────────────────────────────────
                _bot_print(f"[BOT] Step 14: leaving meeting")
                _active_bots[user_id]["status"] = "leaving"
                try:
                    if await _click_probe(page, _LEAVE_BUTTONS, 5_000):
                        await asyncio.sleep(1)
                        await _click_probe(page, _LEAVE_CONFIRM, 4_000)
                except Exception:
                    pass

                await browser.close()
                _active_bots[user_id]["status"] = "completed"
                _bot_print(f"[BOT] Session COMPLETED — user={user_id} meeting={meeting_id}")
                return

        except asyncio.CancelledError:
            logger.info("Bot: [user=%s] task cancelled", user_id)
            _active_bots[user_id]["status"] = "cancelled"
            for cleanup in [
                lambda: stop_recording(rec_session) if rec_session else None,
                lambda: browser.close() if browser else None,
            ]:
                try:
                    result = cleanup()
                    if result:
                        await result
                except Exception:
                    pass
            return

        except Exception as exc:
            last_error = str(exc)
            _bot_print(f"[BOT ERROR] Attempt {attempt}/{MAX_RETRIES} FAILED — user={user_id}: {exc}")
            traceback.print_exc()
            logger.error("Bot: [user=%s] attempt %d failed: %s",
                         user_id, attempt, exc, exc_info=True)
            _active_bots[user_id]["status"] = f"error_attempt_{attempt}"

            for cleanup in [
                lambda: stop_recording(rec_session) if rec_session else None,
                lambda: browser.close() if browser else None,
            ]:
                try:
                    result = cleanup()
                    if result:
                        await result
                except Exception:
                    pass
            rec_session = None
            browser = None

            if attempt < MAX_RETRIES:
                _bot_print(f"[BOT] Retrying in 5s…")
                await asyncio.sleep(5)

    _active_bots[user_id]["status"] = "failed"
    _active_bots[user_id]["error"] = last_error
    _bot_print(f"[BOT ERROR] ALL {MAX_RETRIES} attempts failed — user={user_id} meeting={meeting_id}: {last_error}")
    logger.error("Bot: [user=%s meeting=%s] all attempts failed — %s",
                 user_id, meeting_id, last_error)

    if meeting_id and meeting_id != "unknown":
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, _db_mark_failed_sync, meeting_id, last_error or "unknown"
            )
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Public entry points
# ══════════════════════════════════════════════════════════════════════════════

async def join_google_meet(
    meet_url: str,
    user_id: str,
    organization_id: str,
    meeting_id: Optional[str] = None,
    bot_display_name: str = DEFAULT_BOT_NAME,
    stay_duration_seconds: int = DEFAULT_STAY_SECONDS,
    recordings_dir: str = "recordings",
) -> None:
    """
    Upsert a Meeting record, then dispatch the bot session as an asyncio Task.
    Called via asyncio.create_task() from the API endpoint. Never raises.
    """
    _bot_print(f"[BOT] join_google_meet ENTERED — user={user_id} url={meet_url}")

    if not is_valid_meet_url(meet_url):
        _bot_print(f"[BOT ERROR] Invalid Meet URL: {meet_url!r}")
        return

    existing = _active_bots.get(user_id, {})
    if existing.get("status") in ("navigating", "pre_join", "joining", "in_meeting", "waiting_admission"):
        existing_url = existing.get("meet_url", "")
        if existing_url == meet_url:
            _bot_print(f"[BOT] Bot already active for user={user_id} (status={existing['status']}) same URL — skipping")
            return
        # Different meeting — stop the old session and start the new one
        _bot_print(
            f"[BOT] Stopping existing session (status={existing['status']} url={existing_url!r}) "
            f"to join new meeting {meet_url!r} — user={user_id}"
        )
        old_task: Optional[asyncio.Task] = existing.get("_task")
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(old_task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        _active_bots.pop(user_id, None)

    if not meeting_id:
        try:
            loop = asyncio.get_event_loop()
            title = f"Google Meet — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
            _bot_print(f"[BOT] Upserting meeting record in DB")
            meeting_id = await loop.run_in_executor(
                None, _db_upsert_meeting_sync, user_id, organization_id, meet_url, title
            )
            _bot_print(f"[BOT] Meeting record upserted: {meeting_id}")
        except Exception as exc:
            _bot_print(f"[BOT ERROR] Could not upsert meeting record: {exc}")
            meeting_id = "unknown"

    _active_bots[user_id] = {
        "status": "starting",
        "meet_url": meet_url,
        "meeting_id": meeting_id,
        "started_at": datetime.now(timezone.utc),
        "bot_name": bot_display_name,
        "recording_path": None,
        "error": None,
        "_task": None,
    }

    def _on_task_done(t: asyncio.Task) -> None:
        exc = t.exception() if not t.cancelled() else None
        if exc:
            _bot_print(f"[BOT ERROR] _run_session task raised unhandled exception — user={user_id}: {exc}")
            traceback.print_exc()

    task = asyncio.create_task(
        _run_session(
            meet_url=meet_url,
            user_id=user_id,
            organization_id=organization_id,
            meeting_id=meeting_id,
            bot_display_name=bot_display_name,
            stay_duration_seconds=stay_duration_seconds,
            recordings_dir=recordings_dir,
        ),
        name=f"meet_bot:{user_id}",
    )
    task.add_done_callback(_on_task_done)
    _active_bots[user_id]["_task"] = task
    _bot_print(f"[BOT] _run_session task created — user={user_id} meeting={meeting_id}")


async def auto_join_upcoming_meets(
    user_id: str,
    organization_id: str,
    access_token: str,
    calendar_id: str = "primary",
    lead_time_minutes: int = 2,
    stay_duration_seconds: int = DEFAULT_STAY_SECONDS,
    bot_display_name: str = DEFAULT_BOT_NAME,
    recordings_dir: str = "recordings",
) -> Dict[str, Any]:
    """
    Fetch upcoming Google Calendar events and start the bot for meetings
    starting within lead_time_minutes from now.
    """
    import httpx
    from dateutil import parser as dtparser

    now_utc = datetime.now(timezone.utc)
    time_min = now_utc.isoformat().replace("+00:00", "Z")
    time_max = (now_utc + timedelta(minutes=lead_time_minutes + 1)).isoformat().replace("+00:00", "Z")

    triggered: List[Dict[str, Any]] = []
    skipped: List[str] = []

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 20,
                "timeMin": time_min,
                "timeMax": time_max,
            },
        )

    if resp.status_code != 200:
        logger.error("auto_join: calendar fetch failed for user=%s: %s", user_id, resp.text)
        return {"triggered": 0, "skipped": 0, "error": resp.text}

    for event in resp.json().get("items", []):
        meet_url = event.get("hangoutLink")
        if not meet_url:
            for ep in (event.get("conferenceData") or {}).get("entryPoints", []):
                if ep.get("entryPointType") == "video" and "meet.google.com" in str(ep.get("uri", "")):
                    meet_url = ep["uri"]
                    break

        if not meet_url or not is_valid_meet_url(meet_url):
            skipped.append(event.get("summary", "unknown"))
            continue

        start_str = (event.get("start") or {}).get("dateTime")
        if not start_str:
            skipped.append(event.get("summary", "unknown"))
            continue

        start_dt = dtparser.parse(start_str)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        delta_seconds = (start_dt - now_utc).total_seconds()
        if -60 <= delta_seconds <= lead_time_minutes * 60:
            await join_google_meet(
                meet_url=meet_url,
                user_id=user_id,
                organization_id=organization_id,
                bot_display_name=bot_display_name,
                stay_duration_seconds=stay_duration_seconds,
                recordings_dir=recordings_dir,
            )
            triggered.append({"title": event.get("summary"), "meet_url": meet_url})
        else:
            skipped.append(event.get("summary", "unknown"))

    return {"triggered": len(triggered), "skipped": len(skipped), "meetings": triggered}
