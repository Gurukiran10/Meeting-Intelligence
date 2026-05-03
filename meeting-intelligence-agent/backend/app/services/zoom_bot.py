"""
Zoom Bot Service — Playwright-based Zoom Web Client automation.

Works entirely free — uses zoom.us/wc/{id}/join (no SDK, no Recall.ai).
Same recording pipeline as the Google Meet bot (ffmpeg / Playwright WebRTC fallback).

Join flow
─────────
1. Parse meeting URL → extract meeting_id + password
2. Navigate to https://zoom.us/wc/{id}/join?prefer=1&audio=voip[&pwd={pwd}]
3. Click "Join from your browser" if the redirect page appears
4. Grant mic/camera permissions
5. Enter bot display name
6. Click "Join" / "Join Meeting" button
7. Dismiss "Join Audio" dialog → "Join with Computer Audio"
8. Confirm in-meeting (toolbar visible)
9. Record audio for stay_duration_seconds
10. Leave → save recording → trigger processing
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright as _pw_check  # noqa
    print("[ZOOM-BOT] playwright import OK", flush=True)
except Exception as _pw_err:
    print(f"[ZOOM-BOT ERROR] playwright import FAILED: {_pw_err}", flush=True)

# ── In-memory registry ──────────────────────────────────────────────────────
_active_zoom_bots: Dict[str, Dict[str, Any]] = {}

# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_STAY_SECONDS = 600
DEFAULT_BOT_NAME     = "SyncMinds Bot"
MAX_RETRIES          = 2
PAGE_LOAD_TIMEOUT    = 40_000
ELEMENT_TIMEOUT      = 10_000

# Use same env var as Google Meet bot — MEET_BOT_HEADLESS=0 in docker-compose means headful
_HEADLESS = os.getenv("MEET_BOT_HEADLESS", "1") != "0"

# Zoom URL patterns
# https://zoom.us/j/12345678901
# https://zoom.us/j/12345678901?pwd=xxxxx
# https://us06web.zoom.us/j/12345678901?pwd=xxxxx
ZOOM_URL_RE = re.compile(
    r"^https?://(?:[a-z0-9]+\.)?zoom\.us/(?:j|wc)/(\d{8,11})(/join)?(?:[?&].*)?$",
    re.I,
)


def _zprint(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


def is_valid_zoom_url(url: str) -> bool:
    return bool(ZOOM_URL_RE.match(url.strip()))


def _parse_zoom_url(url: str) -> tuple[str, Optional[str]]:
    """Return (meeting_id, password_or_None)."""
    m = ZOOM_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"Not a valid Zoom URL: {url!r}")
    meeting_id = m.group(1)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    pwd = qs.get("pwd", [None])[0]
    return meeting_id, pwd


def _web_client_url(meeting_id: str, pwd: Optional[str]) -> str:
    base = f"https://zoom.us/wc/{meeting_id}/join?prefer=1&audio=voip"
    if pwd:
        base += f"&pwd={pwd}"
    return base


def get_zoom_bot_status(user_id: str) -> Optional[Dict[str, Any]]:
    entry = _active_zoom_bots.get(user_id)
    if not entry:
        return None
    return {k: v for k, v in entry.items() if not k.startswith("_")}


async def stop_zoom_bot(user_id: str) -> bool:
    entry = _active_zoom_bots.get(user_id)
    if not entry:
        return False
    task: Optional[asyncio.Task] = entry.get("_task")
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _active_zoom_bots.pop(user_id, None)
    return True


# ── DB helpers ──────────────────────────────────────────────────────────────

def _db_upsert_meeting_sync(user_id: str, organization_id: str, zoom_url: str, title: str) -> str:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    from sqlalchemy import select
    from datetime import timedelta

    # Normalize URL — strip query params / fragments / trailing slash
    zoom_url = zoom_url.split("?")[0].split("#")[0].rstrip("/")
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with SessionLocal() as db:
        existing = db.execute(
            select(Meeting).where(
                Meeting.meeting_url == zoom_url,
                Meeting.organizer_id == user_id,
                Meeting.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing:
            if not existing.organization_id:
                existing.organization_id = organization_id
            existing.actual_start = now
            existing.status = "in_progress"
            existing.transcription_status = "processing"
            existing.recording_consent = True
            db.commit()
            return str(existing.id)
        meeting = Meeting(
            title=title,
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=1),
            actual_start=now,
            organizer_id=user_id,
            created_by=user_id,
            organization_id=organization_id,
            platform="zoom",
            meeting_url=zoom_url,
            status="in_progress",
            recording_consent=True,
            transcription_status="processing",
            meeting_metadata={"source": "zoom_bot"},
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return str(meeting.id)


def _db_save_recording_sync(meeting_id: str, recording_path: str) -> None:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if m:
            m.recording_path = recording_path
            m.actual_end = datetime.now(timezone.utc).replace(tzinfo=None)
            m.status = "completed"
            db.commit()


def _db_mark_failed_sync(meeting_id: str, error: str) -> None:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if m:
            m.status = "failed"
            m.transcription_status = "failed"
            db.commit()


def _db_mark_completed_no_audio_sync(meeting_id: str) -> None:
    """Mark meeting completed when bot attended but couldn't capture audio."""
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    from datetime import datetime, timezone
    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if m:
            m.status = "completed"
            m.actual_end = datetime.now(timezone.utc).replace(tzinfo=None)
            m.transcription_status = "unavailable"
            m.summary = (
                "Audio capture was not available for this Zoom meeting. "
                "The SyncMinds Bot attended the meeting but could not record audio "
                "(Zoom's WebRTC streams require PulseAudio on the host to capture). "
                "To enable full transcription, run the backend with PulseAudio support."
            )
            db.commit()


# ── Playwright helpers ───────────────────────────────────────────────────────

async def _is_visible(page: Any, selector: str, timeout_ms: int = 2_000) -> bool:
    try:
        el = page.locator(selector).first
        await el.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _probe_first(page: Any, selectors: List[str], timeout_ms: int = 3_000) -> Optional[Any]:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=timeout_ms)
            return el
        except Exception:
            continue
    return None


async def _click_probe(page: Any, selectors: List[str], timeout_ms: int = ELEMENT_TIMEOUT) -> bool:
    el = await _probe_first(page, selectors, timeout_ms)
    if el:
        try:
            await el.click(timeout=timeout_ms)
            return True
        except Exception:
            pass
    return False


async def _screenshot(page: Any, step: str, user_id: str) -> None:
    try:
        path = f"/tmp/zoom_bot_{user_id}_{step}.png"
        await page.screenshot(path=path, full_page=False)
        _zprint(f"[ZOOM-BOT] screenshot: {path}")
    except Exception:
        pass


# ── Join flow steps ─────────────────────────────────────────────────────────

async def _handle_browser_redirect(page: Any, user_id: str) -> None:
    """
    Zoom sometimes shows a 'Launch Meeting' page before the web client.
    Click 'Join from Your Browser' to stay in the web client.
    """
    join_from_browser = await _probe_first(page, [
        "a#joinBtn",
        "a[href*='prefer=1']",
        "text=Join from Your Browser",
        "text=join from your browser",
        "a:has-text('browser')",
    ], timeout_ms=5_000)
    if join_from_browser:
        _zprint(f"[ZOOM-BOT] clicking 'Join from browser' — user={user_id}")
        await join_from_browser.click()
        await asyncio.sleep(2)


async def _grant_permissions(context: Any, user_id: str) -> None:
    try:
        await context.grant_permissions(["microphone", "camera"], origin="https://zoom.us")
        _zprint(f"[ZOOM-BOT] permissions granted — user={user_id}")
    except Exception as e:
        _zprint(f"[ZOOM-BOT] permission grant failed (non-fatal): {e}")


async def _wait_for_name_screen(page: Any, user_id: str) -> bool:
    """Wait until the name input is visible."""
    _zprint(f"[ZOOM-BOT] waiting for name screen — user={user_id}")
    name_selectors = [
        "input#input-for-name",        # Zoom web client 2024+
        "input#inputname",             # older Zoom web client
        "input[placeholder*='Your Name']",
        "input[placeholder*='Name']",
        "input[placeholder*='name']",
        "input[aria-label*='name']",
        "input[aria-label*='Name']",
        "input.preview-join-input",
        "input[data-testid='name-input']",
    ]
    for _ in range(25):
        for sel in name_selectors:
            if await _is_visible(page, sel, timeout_ms=1_000):
                _zprint(f"[ZOOM-BOT] name screen found via {sel!r}")
                return True
        await asyncio.sleep(1)
    # Last-resort: any visible text input on the page
    try:
        visible = await page.evaluate("""() => {
            const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type])'));
            return inputs.filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            }).map(el => el.id || el.name || el.className).join('|');
        }""")
        _zprint(f"[ZOOM-BOT] visible inputs on page: {visible!r}")
    except Exception:
        pass
    _zprint(f"[ZOOM-BOT] WARN: name screen not found after 25s")
    return False


async def _enter_name(page: Any, bot_name: str, user_id: str) -> bool:
    name_selectors = [
        "input#input-for-name",        # Zoom web client 2024+
        "input#inputname",             # older Zoom web client
        "input[placeholder*='Your Name']",
        "input[placeholder*='Name']",
        "input[placeholder*='name']",
        "input[aria-label*='name']",
        "input.preview-join-input",
        "input[data-testid='name-input']",
    ]
    el = await _probe_first(page, name_selectors, timeout_ms=5_000)
    if not el:
        # JS fallback: fill the first visible text input
        try:
            filled = await page.evaluate(f"""() => {{
                const inputs = Array.from(document.querySelectorAll('input[type="text"],input:not([type])'));
                const visible = inputs.find(el => {{
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                }});
                if (visible) {{
                    visible.value = {bot_name!r};
                    visible.dispatchEvent(new Event('input', {{bubbles: true}}));
                    visible.dispatchEvent(new Event('change', {{bubbles: true}}));
                    return true;
                }}
                return false;
            }}""")
            if filled:
                _zprint(f"[ZOOM-BOT] name entered via JS fallback — user={user_id}")
                return True
        except Exception as e:
            _zprint(f"[ZOOM-BOT] JS fallback failed: {e}")
        _zprint(f"[ZOOM-BOT] WARN: name input not found — user={user_id}")
        return False
    await el.click()
    await el.fill(bot_name)
    # Fire React/Vue synthetic events so the Join button enables
    await page.evaluate("""(selector) => {
        const el = document.querySelector(selector);
        if (el) {
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }
    }""", "input#input-for-name")
    await asyncio.sleep(0.5)
    _zprint(f"[ZOOM-BOT] name entered: {bot_name!r} — user={user_id}")
    return True


async def _click_join_button(page: Any, user_id: str) -> bool:
    await asyncio.sleep(0.5)
    join_selectors = [
        "button#join_meeting_btn",
        "button.preview-join-button",
        "button[class*='join-btn']",
        "button:has-text('Join Meeting')",
        "button:has-text('Join')",
        "button[aria-label='Join Meeting']",
        "button[aria-label='Join']",
        "button[data-testid='join-btn']",
    ]
    for sel in join_selectors:
        try:
            btn = page.locator(sel).first
            await btn.wait_for(state="visible", timeout=3_000)
            # Use force=True because Zoom's button may have 'disabled' CSS class
            # even when the name is filled — the button IS interactive.
            await btn.click(force=True, timeout=5_000)
            _zprint(f"[ZOOM-BOT] join button clicked via {sel!r} — user={user_id}")
            return True
        except Exception:
            continue
    _zprint(f"[ZOOM-BOT] WARN: join button not found — user={user_id}")
    return False


async def _handle_audio_dialog(page: Any, user_id: str) -> None:
    """Dismiss the 'Join Audio' dialog by clicking 'Join with Computer Audio'."""
    await asyncio.sleep(2)
    audio_selectors = [
        "button.join-audio-by-voip__join-btn",
        "button[aria-label='Join with Computer Audio']",
        "button:has-text('Join with Computer Audio')",
        "button:has-text('Join Audio by Computer')",
        "button[class*='join-audio']",
        "button[data-testid='join-audio-btn']",
    ]
    clicked = await _click_probe(page, audio_selectors, timeout_ms=8_000)
    if clicked:
        _zprint(f"[ZOOM-BOT] audio dialog dismissed — user={user_id}")
    else:
        _zprint(f"[ZOOM-BOT] audio dialog not found (may already be joined) — user={user_id}")


async def _wait_in_meeting(page: Any, user_id: str) -> bool:
    """Confirm we're in the meeting by checking for the meeting toolbar."""
    _zprint(f"[ZOOM-BOT] waiting for in-meeting state — user={user_id}")
    in_meeting_selectors = [
        "button[aria-label*='Leave']",
        "button[aria-label*='End']",
        ".meeting-app",
        "#wc-footer",
        "div[class*='footer']",
        "button[aria-label*='Mute']",
        "button[aria-label*='mute']",
        "div[class*='in-meeting']",
    ]
    for _ in range(30):
        for sel in in_meeting_selectors:
            if await _is_visible(page, sel, timeout_ms=1_000):
                _zprint(f"[ZOOM-BOT] IN MEETING confirmed via {sel!r} — user={user_id}")
                return True
        await asyncio.sleep(1)
    _zprint(f"[ZOOM-BOT] WARN: could not confirm in-meeting state — user={user_id}")
    return False


async def _send_zoom_chat_message(page: Any, user_id: str, message: str) -> bool:
    """Open the Zoom in-meeting chat panel and send a message."""
    _zprint(f"[ZOOM-BOT] sending chat message — user={user_id}")
    chat_btn_selectors = [
        "button[aria-label*='chat' i]",
        "button[aria-label*='Chat' i]",
        "#chat-btn",
        "button[class*='chat']",
        "span[class*='chat-btn']",
    ]
    opened = False
    for sel in chat_btn_selectors:
        if await _is_visible(page, sel, timeout_ms=2_000):
            try:
                await page.click(sel)
                await asyncio.sleep(1)
                opened = True
                _zprint(f"[ZOOM-BOT] chat panel opened via {sel!r}")
                break
            except Exception:
                pass
    if not opened:
        _zprint(f"[ZOOM-BOT] chat panel not found — skipping consent message")
        return False
    chat_input_selectors = [
        "div[contenteditable='true'][aria-label*='chat' i]",
        "textarea[placeholder*='chat' i]",
        "div[contenteditable='true']",
        ".chat-input__chat-textarea",
        "#chat-input",
    ]
    for sel in chat_input_selectors:
        if await _is_visible(page, sel, timeout_ms=2_000):
            try:
                await page.click(sel)
                await page.keyboard.type(message)
                await page.keyboard.press("Enter")
                _zprint(f"[ZOOM-BOT] chat message sent via {sel!r}")
                return True
            except Exception:
                pass
    _zprint(f"[ZOOM-BOT] chat input not found — could not send consent message")
    return False


async def _wait_meeting_end(page: Any, user_id: str, max_seconds: int) -> None:
    """
    Wait until the meeting ends or max_seconds elapses.
    Polls every 5 seconds for signs that the meeting has ended:
    - "This meeting has been ended by the host"
    - Page navigated away from the meeting
    - Leave/End button disappeared (meeting ended naturally)
    """
    end_signals = [
        "text=This meeting has been ended",
        "text=Meeting is end",
        "text=The meeting has been ended",
        "text=ended by the host",
        "text=Return to home screen",
        "button:has-text('Return to home screen')",
        "a:has-text('Return to home screen')",
    ]
    elapsed = 0
    poll_interval = 5
    while elapsed < max_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        # Check for meeting-ended signals
        for sel in end_signals:
            if await _is_visible(page, sel, timeout_ms=500):
                _zprint(f"[ZOOM-BOT] meeting ended signal via {sel!r} after {elapsed}s — user={user_id}")
                return
        # Check if Leave button is gone (meeting ended / bot kicked)
        leave_visible = await _is_visible(page, "button[aria-label*='Leave']", timeout_ms=500)
        end_visible = await _is_visible(page, "button[aria-label*='End']", timeout_ms=500)
        mute_visible = await _is_visible(page, "button[aria-label*='Mute']", timeout_ms=500)
        if not leave_visible and not end_visible and not mute_visible and elapsed > 15:
            _zprint(f"[ZOOM-BOT] meeting controls gone after {elapsed}s — assuming ended — user={user_id}")
            return
        if elapsed % 60 == 0:
            _zprint(f"[ZOOM-BOT] still in meeting ({elapsed}/{max_seconds}s) — user={user_id}")
    _zprint(f"[ZOOM-BOT] max stay reached ({max_seconds}s) — user={user_id}")


async def _leave_meeting(page: Any, user_id: str) -> None:
    leave_selectors = [
        "button[aria-label='Leave']",
        "button[aria-label='End']",
        "button:has-text('Leave')",
        "button[class*='leave']",
    ]
    clicked = await _click_probe(page, leave_selectors, timeout_ms=5_000)
    if clicked:
        await asyncio.sleep(1)
        # Confirm "Leave Meeting" in the dialog that appears
        await _click_probe(page, [
            "button:has-text('Leave Meeting')",
            "button[aria-label='Leave Meeting']",
            "button.leave-meeting-options__btn",
        ], timeout_ms=3_000)
    _zprint(f"[ZOOM-BOT] leave attempt done — user={user_id}")


# ── Full session ─────────────────────────────────────────────────────────────

async def _run_zoom_session(
    zoom_url: str,
    user_id: str,
    organization_id: str,
    meeting_id: str,
    bot_display_name: str,
    stay_duration_seconds: int,
    recordings_dir: str,
) -> None:
    """Full Zoom bot lifecycle. Never raises."""
    _zprint(f"[ZOOM-BOT] session START — user={user_id} meeting={meeting_id} url={zoom_url}")

    from playwright.async_api import async_playwright
    from app.services.recording_service import (
        RecordingSession,
        get_pulse_sink_env,
        start_recording,
        start_playwright_recording,
        stop_recording,
    )
    from app.services.meet_bot import _WEBRTC_INTERCEPT
    from app.tasks.meeting_processor import process_meeting_recording_background

    meeting_id_str, pwd = _parse_zoom_url(zoom_url)
    web_url = _web_client_url(meeting_id_str, pwd)
    last_error: Optional[str] = None

    for attempt in range(1, MAX_RETRIES + 1):
        _zprint(f"[ZOOM-BOT] attempt {attempt}/{MAX_RETRIES} — user={user_id}")
        rec_session: Optional[RecordingSession] = None
        browser = None

        try:
            # Start audio recording
            try:
                rec_session = await start_recording(meeting_id, output_dir=recordings_dir)
            except Exception as exc:
                _zprint(f"[ZOOM-BOT] recording start failed (non-fatal): {exc}")
                rec_session = None
            pulse_env = get_pulse_sink_env(rec_session)

            async with async_playwright() as pw:
                chromium_env = {**os.environ, **pulse_env}
                browser = await pw.chromium.launch(
                    headless=_HEADLESS,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        "--disable-web-security",
                        "--allow-running-insecure-content",
                        "--autoplay-policy=no-user-gesture-required",
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                    env=chromium_env,
                )

                context = await browser.new_context(
                    permissions=["microphone", "camera"],
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                )

                await _grant_permissions(context, user_id)
                page = await context.new_page()

                # Inject WebRTC intercept so we can capture audio after joining
                await page.add_init_script(_WEBRTC_INTERCEPT)

                _active_zoom_bots[user_id]["status"] = "navigating"
                _zprint(f"[ZOOM-BOT] navigating to {web_url}")
                await page.goto(web_url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                await _screenshot(page, "01_loaded", user_id)

                # Handle redirect page
                await _handle_browser_redirect(page, user_id)
                await _screenshot(page, "02_after_redirect", user_id)

                _active_zoom_bots[user_id]["status"] = "pre_join"

                # Wait for and fill name
                await _wait_for_name_screen(page, user_id)
                await _screenshot(page, "03_name_screen", user_id)
                await _enter_name(page, bot_display_name, user_id)
                await asyncio.sleep(0.5)

                # Fill passcode if shown (for password-protected meetings not in URL)
                _, pwd_from_url = _parse_zoom_url(zoom_url)
                if pwd_from_url:
                    try:
                        pwd_field = page.locator("input#input-for-pwd").first
                        await pwd_field.wait_for(state="visible", timeout=2_000)
                        await pwd_field.fill(pwd_from_url)
                        _zprint(f"[ZOOM-BOT] passcode filled — user={user_id}")
                    except Exception:
                        pass  # No passcode field visible, URL pwd was accepted

                # Click join
                _active_zoom_bots[user_id]["status"] = "joining"
                await _click_join_button(page, user_id)
                await asyncio.sleep(3)
                await _screenshot(page, "04_after_join_click", user_id)

                # After join click, check if passcode screen appeared
                try:
                    pwd_field2 = page.locator("input#input-for-pwd").first
                    await pwd_field2.wait_for(state="visible", timeout=3_000)
                    _zprint(f"[ZOOM-BOT] passcode screen appeared — user={user_id}")
                    if pwd_from_url:
                        await pwd_field2.fill(pwd_from_url)
                    await _click_join_button(page, user_id)
                    await asyncio.sleep(3)
                except Exception:
                    pass  # No passcode screen, already past it

                # Handle audio dialog
                await _handle_audio_dialog(page, user_id)
                await _screenshot(page, "05_after_audio", user_id)

                # Confirm in meeting
                in_meeting = await _wait_in_meeting(page, user_id)
                _active_zoom_bots[user_id]["status"] = "in_meeting" if in_meeting else "unknown"
                await _screenshot(page, "06_in_meeting", user_id)

                if not in_meeting:
                    _zprint(f"[ZOOM-BOT] WARNING: could not confirm in-meeting, staying anyway")

                # Send recording consent announcement in chat
                try:
                    await asyncio.sleep(3)
                    await _send_zoom_chat_message(
                        page,
                        user_id,
                        "\U0001f916 SyncMinds Bot is recording this meeting for transcription "
                        "and AI summarisation. If you do not consent, please ask the organiser "
                        "to remove me from the call.",
                    )
                except Exception as _chat_err:
                    _zprint(f"[ZOOM-BOT] chat consent error (non-fatal): {_chat_err}")

                # Start Playwright WebRTC recording if ffmpeg unavailable
                if rec_session is None:
                    _zprint(f"[ZOOM-BOT] starting Playwright MediaRecorder (ffmpeg unavailable)")
                    await asyncio.sleep(2)
                    rec_session = await start_playwright_recording(
                        page=page,
                        meeting_id=meeting_id,
                        output_dir=recordings_dir,
                    )

                _zprint(f"[ZOOM-BOT] staying up to {stay_duration_seconds}s, polling for meeting end — user={user_id}")
                await _wait_meeting_end(page, user_id, stay_duration_seconds)

                await _leave_meeting(page, user_id)

            # Save recording
            recording_path: Optional[str] = None
            if rec_session:
                recording_path = await stop_recording(rec_session)

            if recording_path and os.path.exists(recording_path) and os.path.getsize(recording_path) > 1024:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _db_save_recording_sync, meeting_id, recording_path)
                _zprint(f"[ZOOM-BOT] recording saved: {recording_path}")
                process_meeting_recording_background(meeting_id, recording_path)
                _zprint(f"[ZOOM-BOT] processing task dispatched — meeting={meeting_id}")
            else:
                # No audio recording (Zoom WebRTC is encrypted — PulseAudio required for capture)
                # Mark as completed so it shows in the UI; trigger analysis without transcript
                _zprint(f"[ZOOM-BOT] no audio recording captured — marking completed — meeting={meeting_id}")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _db_mark_completed_no_audio_sync, meeting_id)

            _active_zoom_bots[user_id]["status"] = "completed"
            return

        except asyncio.CancelledError:
            _zprint(f"[ZOOM-BOT] session cancelled — user={user_id}")
            _active_zoom_bots[user_id]["status"] = "stopped"
            return
        except Exception as exc:
            last_error = str(exc)
            _zprint(f"[ZOOM-BOT] attempt {attempt} error: {exc}\n{traceback.format_exc()}")
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            if attempt < MAX_RETRIES:
                await asyncio.sleep(3)

    _zprint(f"[ZOOM-BOT] all attempts failed — user={user_id} last_error={last_error}")
    _active_zoom_bots[user_id]["status"] = "failed"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _db_mark_failed_sync, meeting_id, last_error or "unknown")


# ── Public entry point ───────────────────────────────────────────────────────

async def join_zoom_meeting(
    zoom_url: str,
    user_id: str,
    organization_id: str,
    meeting_id: Optional[str] = None,
    bot_display_name: str = DEFAULT_BOT_NAME,
    stay_duration_seconds: int = DEFAULT_STAY_SECONDS,
    recordings_dir: str = "recordings",
) -> None:
    """Dispatch the Zoom bot as an asyncio Task. Never raises."""
    # Normalize URL before any comparison or DB lookup
    zoom_url = zoom_url.split("?")[0].split("#")[0].rstrip("/")

    _zprint(f"[ZOOM-BOT] join_zoom_meeting — user={user_id} url={zoom_url}")

    if not is_valid_zoom_url(zoom_url):
        _zprint(f"[ZOOM-BOT ERROR] invalid Zoom URL: {zoom_url!r}")
        return

    existing = _active_zoom_bots.get(user_id, {})
    if existing.get("status") in ("navigating", "pre_join", "joining", "in_meeting"):
        if existing.get("zoom_url") == zoom_url:
            _zprint(f"[ZOOM-BOT] already active for same URL — skipping")
            return
        await stop_zoom_bot(user_id)

    if not meeting_id:
        try:
            loop = asyncio.get_event_loop()
            meeting_id_str, _ = _parse_zoom_url(zoom_url)
            title = f"Zoom Meeting {meeting_id_str} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
            meeting_id = await loop.run_in_executor(
                None, _db_upsert_meeting_sync, user_id, organization_id, zoom_url, title
            )
        except Exception as exc:
            _zprint(f"[ZOOM-BOT ERROR] DB upsert failed: {exc}")
            return

    _active_zoom_bots[user_id] = {
        "status": "navigating",
        "zoom_url": zoom_url,
        "meeting_id": meeting_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    def _on_done(t: asyncio.Task) -> None:
        err = t.exception()
        if err:
            _zprint(f"[ZOOM-BOT] task error: {err}")
        _active_zoom_bots.pop(user_id, None)

    task = asyncio.create_task(
        _run_zoom_session(
            zoom_url=zoom_url,
            user_id=user_id,
            organization_id=organization_id,
            meeting_id=meeting_id,
            bot_display_name=bot_display_name,
            stay_duration_seconds=stay_duration_seconds,
            recordings_dir=recordings_dir,
        )
    )
    task.add_done_callback(_on_done)
    _active_zoom_bots[user_id]["_task"] = task
    _zprint(f"[ZOOM-BOT] task dispatched — meeting={meeting_id}")
