"""
Zoom Bot Service — mirrors the working Google Meet bot architecture.

Key design (same as meet_bot.py):
  • Cookie-free fresh context (clear_cookies) — bot joins as guest
  • Anti-detection: navigator.webdriver spoof + plugin spoof via add_init_script
  • Chromium args: --disable-blink-features=AutomationControlled
  • Step-by-step join flow: navigate → name → join → confirm → record
  • Recording starts ONLY after in_meeting confirmed
  • Guard by meeting_id (not user_id) to prevent duplicate sessions
  • BotState (Redis) for frontend polling
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright as _pw_check  # noqa
    print("[ZOOM-BOT] playwright import OK", flush=True)
except Exception as _pw_err:
    print(f"[ZOOM-BOT ERROR] playwright import FAILED: {_pw_err}", flush=True)

# ── In-memory registry — key by meeting_id ───────────────────────────────
_active_bots: Dict[str, Dict[str, Any]] = {}

# ── Constants ───────────────────────────────────────────────────────────────
DEFAULT_STAY_SECONDS = 600
DEFAULT_BOT_NAME     = "SyncMinds Bot"
MAX_RETRIES          = 2
PAGE_LOAD_TIMEOUT    = 35_000
ELEMENT_TIMEOUT      = 8_000
JOIN_ENABLED_TIMEOUT = 12_000
IN_MEETING_TIMEOUT   = 40_000

_HEADLESS = os.getenv("MEET_BOT_HEADLESS", "1") != "0"

# Zoom URL patterns
ZOOM_URL_RE = re.compile(
    r"^https?://(?:[a-z0-9]+\.)?zoom\.us/(?:j|wc)/(\d{8,11})(?:/(?:join|start))?(?:[?&].*)?$",
    re.I,
)


def _zprint(msg: str) -> None:
    print(msg, flush=True)
    logger.info(msg)


def is_valid_zoom_url(url: str) -> bool:
    return bool(ZOOM_URL_RE.match(url.strip()))


def _parse_zoom_url(url: str) -> tuple[str, Optional[str]]:
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


# ── Public helpers ────────────────────────────────────────────────────────────

def get_zoom_bot_status(meeting_id: str) -> Optional[Dict[str, Any]]:
    entry = _active_bots.get(meeting_id)
    if not entry:
        return None
    return {k: v for k, v in entry.items() if not k.startswith("_")}


async def stop_zoom_bot(meeting_id: str) -> bool:
    entry = _active_bots.get(meeting_id)
    if not entry:
        return False
    task: Optional[asyncio.Task] = entry.get("_task")
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _active_bots.pop(meeting_id, None)
    return True


# ── DB helpers ───────────────────────────────────────────────────────────────

def _db_upsert_meeting_sync(
    user_id: str, organization_id: str, zoom_url: str, title: str,
) -> str:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting
    from sqlalchemy import select

    zoom_url = zoom_url.split("?")[0].split("#")[0].rstrip("/")
    external_id = zoom_url.split("/")[-1]
    now = datetime.utcnow()

    with SessionLocal() as db:
        existing = db.execute(
            select(Meeting).where(
                Meeting.organizer_id == user_id,
                Meeting.meeting_url.startswith(zoom_url),
                Meeting.deleted_at.is_(None),
            )
        ).scalar_one_or_none()

        if existing:
            existing.actual_start = now
            existing.status = "in_progress"
            existing.recording_consent = True
            db.commit()
            return str(existing.id)

        meeting = Meeting(
            organization_id=organization_id,
            title=title,
            platform="zoom",
            external_id=external_id,
            meeting_url=zoom_url,
            scheduled_start=now,
            scheduled_end=now + timedelta(hours=1),
            actual_start=now,
            organizer_id=user_id,
            created_by=user_id,
            status="in_progress",
            recording_consent=True,
            transcription_status="pending",
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
            m.status = "transcribing"
            m.transcription_status = "queued"
            db.commit()


def _db_mark_failed_sync(meeting_id: str, error: str) -> None:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting

    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if m:
            m.status = "failed"
            m.transcription_status = "failed"
            m.meeting_metadata = {
                **(m.meeting_metadata or {}),
                "last_error": error,
                "failed_at": datetime.now(timezone.utc).isoformat()
            }
            db.commit()


def _db_mark_waiting_for_host_sync(meeting_id: str) -> None:
    from app.core.database import SessionLocal
    from app.models.meeting import Meeting

    with SessionLocal() as db:
        m = db.get(Meeting, meeting_id)
        if m and m.status not in ("completed", "transcribing", "analyzing"):
            m.status = "waiting_for_host"
            m.transcription_status = "pending"
            db.commit()


# ── Playwright low-level helpers ─────────────────────────────────────────────

async def _screenshot(page: Any, step: str, meeting_id: str) -> str:
    path = f"/tmp/zoom_bot_{meeting_id[:8]}_{step}.png"
    try:
        await page.screenshot(path=path, full_page=False, animations="disabled")
        _zprint(f"[ZOOM-BOT] screenshot: {path}")
        return path
    except Exception as e:
        _zprint(f"[ZOOM-BOT] screenshot failed ({step}): {e}")
        return ""


async def _is_visible(page: Any, selector: str, timeout_ms: int = 2_000) -> bool:
    try:
        return await page.locator(selector).first.is_visible(timeout=timeout_ms)
    except Exception:
        return False


async def _probe_first(page: Any, selectors: List[str], timeout_ms: int = 3_000) -> Optional[Any]:
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
    el = await _probe_first(page, selectors, timeout_ms)
    if el:
        try:
            await el.click()
            return True
        except Exception:
            pass
    return False


# ── Chromium args + anti-detection scripts (same as meet_bot.py) ─────────

_CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-popup-blocking",
    "--window-size=1280,720",
]

_INIT_SCRIPTS = [
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
    "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});",
    "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});",
]

_WEBRTC_INTERCEPT = """
(function() {
    if (window._syncMinds) return;
    window._syncMinds = { tracks: [], chunks: [], recorder: null, audioCtx: null, destination: null };
    function _capture(track, isRemote) {
        if (track.kind !== 'audio') return;
        if (window._syncMinds.tracks.some(t => t.track.id === track.id)) return;
        console.log('[SM] captured audio track remote=' + isRemote + ' id=' + track.id + ' state=' + track.readyState);
        window._syncMinds.tracks.push({ track: track, remote: isRemote });
        const s = window._syncMinds;
        if (s.audioCtx && s.destination && track.readyState === 'live') {
            try {
                const src = s.audioCtx.createMediaStreamSource(new MediaStream([track]));
                src.connect(s.destination);
            } catch(e) { console.warn('[SM] hot-connect failed', e); }
        }
    }
    const _origAddTrack = RTCPeerConnection.prototype.addTrack;
    RTCPeerConnection.prototype.addTrack = function(track) {
        if (track.kind === 'audio') {
            _capture(track, false);
        }
        return _origAddTrack.apply(this, arguments);
    };
    const _origAEL = RTCPeerConnection.prototype.addEventListener;
    RTCPeerConnection.prototype.addEventListener = function(type, fn, ...rest) {
        if (type === 'track') {
            return _origAEL.call(this, type, function(e) {
                if (e.track) _capture(e.track, true);
                return fn.apply(this, arguments);
            }, ...rest);
        }
        return _origAEL.call(this, type, fn, ...rest);
    };
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
    console.log('[SyncMinds] WebRTC intercept v2 active');
})();
"""


# ── Selectors ────────────────────────────────────────────────────────────────

_NAME_INPUT = [
    "input#input-for-name",
    "input#inputname",
    "input[placeholder*='Your Name']",
    "input[placeholder*='Name']",
    "input[aria-label*='name']",
    "input.preview-join-input",
]

_JOIN_BUTTONS = [
    "button#join_meeting_btn",
    "button.preview-join-button",
    "button:has-text('Join Meeting')",
    "button:has-text('Join')",
    "button[aria-label='Join Meeting']",
    "button[aria-label='Join']",
]

_AUDIO_DIALOG = [
    "button.join-audio-by-voip__join-btn",
    "button[aria-label='Join with Computer Audio']",
    "button:has-text('Join with Computer Audio')",
]

_IN_MEETING = [
    "button[aria-label='Mute']",
    "button[aria-label='Unmute']",
    "button[aria-label='Leave']",
    "button[aria-label='End']",
    "button[aria-label='Participants']",
    ".meeting-app",
    "#wc-footer",
]

_PASSCODE_INPUT = [
    "input#input-for-pwd",
    "input[placeholder*='Passcode']",
]


# ── Join flow steps ──────────────────────────────────────────────────────────

async def _grant_permissions(context: Any, meeting_id: str) -> None:
    try:
        await context.grant_permissions(["microphone", "camera"], origin="https://zoom.us")
        _zprint(f"[ZOOM-BOT] permissions granted — meeting={meeting_id}")
    except Exception as e:
        _zprint(f"[ZOOM-BOT] permission grant failed (non-fatal): {e}")


async def _handle_browser_redirect(page: Any, meeting_id: str) -> None:
    btn = await _probe_first(page, [
        "a#joinBtn",
        "a[href*='prefer=1']",
        "text=Join from Your Browser",
    ], timeout_ms=5_000)
    if btn:
        _zprint(f"[ZOOM-BOT] clicking 'Join from browser' — meeting={meeting_id}")
        await btn.click()
        await asyncio.sleep(2)


async def _wait_for_name_screen(page: Any, meeting_id: str) -> bool:
    _zprint(f"[ZOOM-BOT] waiting for name screen — meeting={meeting_id}")
    for _ in range(25):
        for sel in _NAME_INPUT:
            if await _is_visible(page, sel, timeout_ms=1_000):
                _zprint(f"[ZOOM-BOT] name screen found via {sel!r}")
                return True
        await asyncio.sleep(1)
    _zprint(f"[ZOOM-BOT] WARN: name screen not found after 25s")
    return False


async def _enter_name(page: Any, bot_name: str, meeting_id: str) -> bool:
    el = await _probe_first(page, _NAME_INPUT, timeout_ms=5_000)
    if not el:
        _zprint(f"[ZOOM-BOT] name input not found — meeting={meeting_id}")
        return False
    await el.click()
    await el.fill(bot_name)
    await page.evaluate("""() => {
        const el = document.querySelector('input#input-for-name');
        if (el) {
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true}));
        }
    }""")
    await asyncio.sleep(0.5)
    _zprint(f"[ZOOM-BOT] name entered: {bot_name!r} — meeting={meeting_id}")
    return True


async def _wait_join_enabled(page: Any, meeting_id: str) -> bool:
    deadline = asyncio.get_event_loop().time() + JOIN_ENABLED_TIMEOUT / 1000
    while asyncio.get_event_loop().time() < deadline:
        for sel in _JOIN_BUTTONS:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500) and await loc.is_enabled():
                    _zprint(f"[ZOOM-BOT] join button enabled — meeting={meeting_id}")
                    return True
            except Exception:
                continue
        await asyncio.sleep(0.5)
    _zprint(f"[ZOOM-BOT] WARN: join button not enabled in {JOIN_ENABLED_TIMEOUT}ms")
    return False


async def _click_join(page: Any, meeting_id: str) -> bool:
    for sel in _JOIN_BUTTONS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=2_000):
                await loc.click(force=True, timeout=5_000)
                _zprint(f"[ZOOM-BOT] join clicked via {sel!r} — meeting={meeting_id}")
                return True
        except Exception:
            continue
    # JS fallback
    try:
        fired = await page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button'));
            for (const b of btns) {
                const t = (b.innerText||'').toLowerCase();
                if (t.includes('join')) {
                    b.dispatchEvent(new MouseEvent('click', {bubbles:true,cancelable:true,view:window}));
                    return true;
                }
            }
            return false;
        }""")
        if fired:
            _zprint(f"[ZOOM-BOT] join fired via JS fallback — meeting={meeting_id}")
            return True
    except Exception:
        pass
    return False


async def _handle_audio_dialog(page: Any, meeting_id: str) -> None:
    await asyncio.sleep(2)
    clicked = await _click_probe(page, _AUDIO_DIALOG, timeout_ms=8_000)
    if clicked:
        _zprint(f"[ZOOM-BOT] audio dialog dismissed — meeting={meeting_id}")
    else:
        _zprint(f"[ZOOM-BOT] audio dialog not found (may already be in meeting) — meeting={meeting_id}")


async def _wait_in_meeting(page: Any, meeting_id: str) -> bool:
    _zprint(f"[ZOOM-BOT] waiting for in-meeting state — meeting={meeting_id}")
    deadline = asyncio.get_event_loop().time() + IN_MEETING_TIMEOUT / 1000
    while asyncio.get_event_loop().time() < deadline:
        for sel in _IN_MEETING:
            if await _is_visible(page, sel, timeout_ms=1_000):
                _zprint(f"[ZOOM-BOT] IN MEETING confirmed via {sel!r} — meeting={meeting_id}")
                return True
        await asyncio.sleep(1)
    _zprint(f"[ZOOM-BOT] WARN: could not confirm in-meeting — meeting={meeting_id}")
    return False


async def _handle_passcode_if_needed(
    page: Any, pwd: Optional[str], meeting_id: str,
) -> None:
    """Check if a passcode field appeared after join click. Fill if we have one."""
    try:
        pwd_field = page.locator("input#input-for-pwd").first
        if await pwd_field.is_visible(timeout=3_000):
            _zprint(f"[ZOOM-BOT] passcode screen appeared after join — meeting={meeting_id}")
            if pwd:
                await pwd_field.fill(pwd)
                _zprint(f"[ZOOM-BOT] passcode filled from URL — meeting={meeting_id}")
                await _click_join(page, meeting_id)
                await asyncio.sleep(3)
            else:
                _zprint(f"[ZOOM-BOT] passcode required but not available — meeting={meeting_id}")
    except Exception:
        pass


async def _wait_meeting_end(page: Any, meeting_id: str, max_seconds: int) -> None:
    end_signals = [
        "text=This meeting has been ended",
        "text=The meeting has been ended",
        "text=Return to home screen",
        "button:has-text('Return to home screen')",
    ]
    elapsed = 0
    while elapsed < max_seconds:
        await asyncio.sleep(10)
        elapsed += 10
        for sel in end_signals:
            if await _is_visible(page, sel, timeout_ms=500):
                _zprint(f"[ZOOM-BOT] meeting ended via {sel!r} at {elapsed}s")
                return
        gone = not await _is_visible(page, "button[aria-label='Leave']", timeout_ms=500)
        gone_end = not await _is_visible(page, "button[aria-label='End']", timeout_ms=500)
        if gone and gone_end and elapsed > 20:
            _zprint(f"[ZOOM-BOT] meeting controls gone at {elapsed}s — meeting ended")
            return
    _zprint(f"[ZOOM-BOT] max stay reached ({max_seconds}s)")


async def _leave_meeting(page: Any, meeting_id: str) -> None:
    await _click_probe(page, [
        "button[aria-label='Leave']",
        "button[aria-label='End']",
        "button:has-text('Leave')",
    ], timeout_ms=5_000)
    await asyncio.sleep(1)
    await _click_probe(page, [
        "button:has-text('Leave Meeting')",
        "button[aria-label='Leave Meeting']",
    ], timeout_ms=3_000)
    _zprint(f"[ZOOM-BOT] leave done — meeting={meeting_id}")


async def _send_zoom_chat_message(page: Any, meeting_id: str, message: str) -> bool:
    _zprint(f"[ZOOM-BOT] sending chat message — meeting={meeting_id}")

    # Wait for meeting UI to fully render
    await asyncio.sleep(2)

    # Use page.evaluate() to find chat-related elements in the DOM
    # This bypasses Playwright's visual detection and checks actual DOM state
    chat_btn_info = await page.evaluate("""() => {
        // Find all buttons with "chat" in aria-label or title
        const buttons = Array.from(document.querySelectorAll('button'));
        const chatBtns = buttons.filter(b => {
            const label = (b.getAttribute('aria-label') || '').toLowerCase();
            const title = (b.getAttribute('title') || '').toLowerCase();
            return label.includes('chat') || title.includes('chat') || label === 'chat';
        });
        if (chatBtns.length > 0) {
            const rect = chatBtns[0].getBoundingClientRect();
            return { found: true, label: chatBtns[0].getAttribute('aria-label'), rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height} };
        }
        // Try SVG-based chat button
        const svgs = Array.from(document.querySelectorAll('svg'));
        for (const svg of svgs) {
            const parent = svg.closest('button');
            if (parent && parent.getAttribute('aria-label')) {
                const label = parent.getAttribute('aria-label').toLowerCase();
                if (label.includes('chat') || label === 'chat') {
                    const rect = parent.getBoundingClientRect();
                    return { found: true, label: parent.getAttribute('aria-label'), rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height} };
                }
            }
        }
        // Try to find footer toolbar buttons
        const footerBtns = Array.from(document.querySelectorAll('[class*="footer"] button, [class*="toolbar"] button'));
        const footerChat = footerBtns.find(b => {
            const label = (b.getAttribute('aria-label') || '').toLowerCase();
            return label.includes('chat') || label === 'chat';
        });
        if (footerChat) {
            const rect = footerChat.getBoundingClientRect();
            return { found: true, label: footerChat.getAttribute('aria-label'), rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height} };
        }
        return { found: false };
    }""")
    _zprint(f"[ZOOM-BOT] DOM chat probe: {chat_btn_info}")

    # Try clicking by DOM-based selector if found
    if chat_btn_info.get("found"):
        label = chat_btn_info.get("label", "")
        try:
            await page.locator(f"button[aria-label='{label}']").click()
            await asyncio.sleep(1.5)
            _zprint(f"[ZOOM-BOT] chat opened via DOM label: {label}")
        except Exception as e:
            _zprint(f"[ZOOM-BOT] DOM chat click failed: {e}")

    # Fallback: try all known selectors
    opened = await _click_probe(page, [
        "button[aria-label='Chat']",
        "button[aria-label*='Chat' i]",
        "[aria-label='Chat with everyone']",
        "button[data-type='chat']",
        "[data-popup='chat']",
        "[aria-label='Open chat']",
        "button.chat-button",
        "#chat-button",
        ".chat-btn",
    ], timeout_ms=5_000)

    if not opened:
        _zprint(f"[ZOOM-BOT] chat button not found — skipping consent message")
        return False

    await asyncio.sleep(1.5)

    # Try page.evaluate to find and click chat input directly
    input_clicked = await page.evaluate("""() => {
        const inputs = Array.from(document.querySelectorAll(
            'div[contenteditable="true"][role="textbox"], ' +
            'div[contenteditable="true"], ' +
            'textarea[aria-label*="message" i], ' +
            'textarea'
        ));
        for (const inp of inputs) {
            const style = window.getComputedStyle(inp);
            if (style.display !== 'none' && style.visibility !== 'hidden') {
                inp.click();
                return true;
            }
        }
        return false;
    }""")
    if input_clicked:
        try:
            await page.keyboard.type(message, delay=50)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            _zprint(f"[ZOOM-BOT] chat message sent via keyboard.type")
            return True
        except Exception as e:
            _zprint(f"[ZOOM-BOT] keyboard.type send failed: {e}")

    # Try Playwright input selectors as fallback
    sent = False
    for inp_sel in [
        "div[contenteditable='true'][role='textbox']",
        "div[contenteditable='true']",
        "textarea[aria-label*='message' i]",
        "textarea",
    ]:
        try:
            inp = page.locator(inp_sel).first
            if await inp.is_visible(timeout=2_000):
                await inp.click()
                await inp.fill(message)
                await asyncio.sleep(0.3)
                await inp.press("Enter")
                sent = True
                _zprint(f"[ZOOM-BOT] chat message sent via fill+Enter")
                break
        except Exception:
            continue

    if not sent:
        _zprint(f"[ZOOM-BOT] chat input not found")
    return sent


# ── Full session (mirrors meet_bot.py structure exactly) ──────────────────

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
    if meeting_id not in _active_bots:
        _active_bots[meeting_id] = {
            "status": "pending",
            "bot_display_name": bot_display_name,
            "stay_duration_seconds": stay_duration_seconds,
            "recordings_dir": recordings_dir,
            "recording_path": None,
            "error": None,
            "_task": None,
            "meeting_id": meeting_id,
        }

    _zprint(f"[ZOOM-BOT] _run_zoom_session STARTED  meeting={meeting_id}")

    from playwright.async_api import async_playwright
    from app.services.recording_service import (
        RecordingSession, get_pulse_sink_env,
        prepare_audio_sink, remove_audio_sink,
        start_recording, start_playwright_recording, stop_recording,
        stop_ffmpeg_recording,
    )
    from app.services.bot_state import set_bot_state, BotStatus
    from app.tasks.meeting_processor import process_meeting_recording_background

    meeting_id_str, pwd = _parse_zoom_url(zoom_url)
    web_url = _web_client_url(meeting_id_str, pwd)
    _zprint(f"[ZOOM-BOT] parsed  meeting_id={meeting_id_str}  web_url={web_url}")

    last_error: Optional[str] = None

    # Create PulseAudio sink ONCE before retries so Chromium audio routes correctly
    precreated_sink_name: Optional[str] = None
    precreated_module_id: Optional[int] = None
    try:
        precreated_sink_name, precreated_module_id = await prepare_audio_sink(meeting_id)
        if precreated_sink_name:
            _zprint(f"[ZOOM-BOT] audio sink prepared: {precreated_sink_name}")
    except Exception as sink_err:
        _zprint(f"[ZOOM-BOT] audio sink preparation failed (non-fatal): {sink_err}")

    for attempt in range(1, MAX_RETRIES + 1):
        _zprint(f"[ZOOM-BOT] Attempt {attempt}/{MAX_RETRIES}  meeting={meeting_id}")

        rec_session: Optional[RecordingSession] = None
        _ffmpeg_rec: Optional[RecordingSession] = None
        browser = None

        try:
            # ── Step 1: Launch Chromium ─────────────────────────────────────
            _zprint(f"[ZOOM-BOT] Step 1: launching Chromium  headless={_HEADLESS}")
            async with async_playwright() as pw:
                chromium_args = _CHROMIUM_ARGS.copy()
                if _HEADLESS:
                    chromium_args.append("--use-fake-device-for-media-stream")

                # Build Chromium env: include PULSE_SINK so Zoom WebRTC audio
                # gets routed to our pre-created sink for ffmpeg capture
                chromium_env = {**os.environ}
                if precreated_sink_name:
                    chromium_env["PULSE_SINK"] = precreated_sink_name

                try:
                    browser = await pw.chromium.launch(
                        headless=_HEADLESS,
                        env=chromium_env,
                        args=chromium_args,
                    )
                except Exception as launch_exc:
                    _zprint(f"[ZOOM-BOT ERROR] Chromium launch failed: {launch_exc}")
                    raise

                _zprint(f"[ZOOM-BOT] Step 1 done: Chromium launched")

                context = await browser.new_context(
                    storage_state=None,          # no saved cookies — fresh session
                    permissions=["microphone", "camera"],
                    viewport={"width": 1280, "height": 720},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                )

                # CRITICAL: wipe cookies so Zoom sees a guest session
                await context.clear_cookies()
                _zprint(f"[ZOOM-BOT] Step 1b: fresh context — no session cookies")

                await _grant_permissions(context, meeting_id)
                page = await context.new_page()

                # Anti-detection init scripts (same as meet_bot.py)
                for script in _INIT_SCRIPTS:
                    await page.add_init_script(script)
                await page.add_init_script(_WEBRTC_INTERCEPT)

                # ── Step 2: Navigate to Zoom Web Client ───────────────────────
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "navigating"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.NAVIGATING,
                    user_id=user_id, platform="zoom", meet_url=zoom_url))
                _zprint(f"[ZOOM-BOT] Step 2: navigating to {web_url}")
                response = await page.goto(web_url, timeout=PAGE_LOAD_TIMEOUT, wait_until="domcontentloaded")
                current_url = page.url
                _zprint(f"[ZOOM-BOT] Step 2 done: page loaded  status={response.status if response else 'none'}  url={current_url}")

                # Handle redirect page if Zoom shows it
                await _handle_browser_redirect(page, meeting_id)
                await asyncio.sleep(2)

                # Wait for at least one button to render before interacting
                try:
                    await page.wait_for_selector("button", timeout=15_000)
                    _zprint(f"[ZOOM-BOT] DOM ready — button found")
                except Exception as exc:
                    _zprint(f"[ZOOM-BOT] WARNING: no button after 15s: {exc}")

                await _screenshot(page, "01_loaded", meeting_id)

                # ── Step 3: Wait for name screen + enter name ─────────────────
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "pre_join"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.PRE_JOIN, user_id=user_id))
                name_found = await _wait_for_name_screen(page, meeting_id)
                await _screenshot(page, "02_name_screen", meeting_id)

                if name_found:
                    name_entered = await _enter_name(page, bot_display_name, meeting_id)
                    _zprint(f"[ZOOM-BOT] Step 3: name entered={name_entered}")
                else:
                    _zprint(f"[ZOOM-BOT] Step 3: name screen not found — continuing anyway")
                    name_entered = False

                # ── Step 3b: Fill passcode if in URL ──────────────────────────
                if pwd:
                    try:
                        pwd_field = page.locator("input#input-for-pwd").first
                        if await pwd_field.is_visible(timeout=2_000):
                            await pwd_field.fill(pwd)
                            _zprint(f"[ZOOM-BOT] Step 3b: passcode filled from URL")
                    except Exception:
                        pass

                # ── Step 4: Wait for join button to be enabled ───────────────
                _zprint(f"[ZOOM-BOT] Step 4: waiting for join button enabled")
                await _wait_join_enabled(page, meeting_id)
                await _screenshot(page, "03_ready_to_join", meeting_id)

                # ── Step 5: Click Join ────────────────────────────────────────
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "joining"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.JOINING, user_id=user_id))
                _zprint(f"[ZOOM-BOT] Step 5: clicking join button")
                join_clicked = await _click_join(page, meeting_id)
                if not join_clicked:
                    await _screenshot(page, "04_join_failed", meeting_id)
                    raise RuntimeError("Could not find or click any join button")
                await asyncio.sleep(3)
                await _screenshot(page, "05_after_join", meeting_id)

                # ── Step 5b: Handle passcode screen if it appeared ───────────
                await _handle_passcode_if_needed(page, pwd, meeting_id)
                await _screenshot(page, "05b_after_passcode", meeting_id)

                # ── Step 6: Dismiss audio dialog ──────────────────────────────
                await _handle_audio_dialog(page, meeting_id)
                await _screenshot(page, "06_audio_dialog", meeting_id)

                # ── Step 7: Confirm in-meeting ────────────────────────────────
                _zprint(f"[ZOOM-BOT] Step 7: confirming in-meeting state")
                in_meeting = await _wait_in_meeting(page, meeting_id)
                await _screenshot(page, "07_in_meeting" if in_meeting else "07_waiting_room", meeting_id)

                if not in_meeting:
                    if meeting_id in _active_bots:
                        _active_bots[meeting_id]["status"] = "join_failed"
                    asyncio.create_task(set_bot_state(meeting_id, BotStatus.FAILED,
                        user_id=user_id, last_error="in_meeting_not_confirmed"))
                    _zprint(f"[ZOOM-BOT] Step 7: could not confirm in-meeting — NOT recording — meeting={meeting_id}")
                    return  # Don't stay without being in the meeting

                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "in_meeting"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.IN_MEETING, user_id=user_id))
                _zprint(f"[ZOOM-BOT] Step 7: in_meeting confirmed=True — meeting={meeting_id}")

                # ── Step 8: Send recording consent in chat ──────────────────
                await asyncio.sleep(3)
                await _screenshot(page, "08_before_chat", meeting_id)
                try:
                    chat_sent = await _send_zoom_chat_message(
                        page, meeting_id,
                        "\U0001f916 SyncMinds Bot is recording this meeting for "
                        "transcription and AI summarisation. If you do not consent, "
                        "please ask the organiser to remove me from the call.",
                    )
                    _zprint(f"[ZOOM-BOT] Step 8: chat sent={chat_sent} — meeting={meeting_id}")
                except Exception as _chat_err:
                    _zprint(f"[ZOOM-BOT] Step 8: chat error (non-fatal): {_chat_err}")
                await _screenshot(page, "08_after_chat", meeting_id)

                # ── Step 9: Start recording (ONLY after in_meeting confirmed) ──
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "recording"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.RECORDING, user_id=user_id))
                _zprint(f"[ZOOM-BOT] Step 9: starting ffmpeg recording — meeting={meeting_id}")
                try:
                    rec_session = await start_recording(
                        meeting_id,
                        output_dir=recordings_dir,
                        precreated_sink_name=precreated_sink_name,
                        precreated_module_id=precreated_module_id,
                    )
                    _zprint(f"[ZOOM-BOT] Step 9: ffmpeg recording started  rec_session={rec_session is not None}")
                except Exception as rec_exc:
                    _zprint(f"[ZOOM-BOT] Step 9: ffmpeg failed (non-fatal): {rec_exc}")
                    rec_session = None
                _ffmpeg_rec = rec_session  # save ref — may be replaced by Playwright below

                # Always start Playwright MediaRecorder (WebRTC direct capture) as primary
                # Playwright intercepts RTCPeerConnection tracks directly — more reliable
                # than PulseAudio routing for capturing participant audio.
                _zprint(f"[ZOOM-BOT] Step 9b: starting Playwright MediaRecorder (WebRTC direct capture)")
                await asyncio.sleep(2)
                _pw_session = await start_playwright_recording(
                    page=page, meeting_id=meeting_id, output_dir=recordings_dir,
                )
                if _pw_session:
                    _zprint(f"[ZOOM-BOT] Step 9b: Playwright recording active — using as primary")
                    rec_session = _pw_session  # Playwright is primary; ffmpeg runs as backup
                elif rec_session is None:
                    _zprint(f"[ZOOM-BOT] Step 9b: WARNING — no recording available")

                # ── Step 10: Stay in meeting ───────────────────────────────────
                _zprint(f"[ZOOM-BOT] Step 10: staying {stay_duration_seconds}s — meeting={meeting_id}")
                await _wait_meeting_end(page, meeting_id, stay_duration_seconds)

                # ── Step 11: Stop recording ───────────────────────────────────
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "stopping"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.STOPPING, user_id=user_id))
                # Flush ffmpeg backup if Playwright is primary
                if _ffmpeg_rec is not None and rec_session is not _ffmpeg_rec:
                    try:
                        await stop_ffmpeg_recording(_ffmpeg_rec)
                        _zprint(f"[ZOOM-BOT] Step 11: ffmpeg backup flushed")
                    except Exception as _fe:
                        _zprint(f"[ZOOM-BOT] Step 11: ffmpeg flush error (non-fatal): {_fe}")
                recording_path = await stop_recording(rec_session, page=page)
                rec_session = None
                _zprint(f"[ZOOM-BOT] Step 11: recording stopped  path={recording_path}")

                if recording_path and Path(recording_path).exists() and Path(recording_path).stat().st_size > 1024:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, _db_save_recording_sync, meeting_id, str(recording_path),
                    )
                    _zprint(f"[ZOOM-BOT] Step 11: recording saved  path={recording_path}")
                    process_meeting_recording_background(meeting_id, str(recording_path))
                else:
                    _zprint(f"[ZOOM-BOT] Step 11: no audio captured — meeting={meeting_id}")
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, _db_mark_failed_sync, meeting_id, "no audio captured",
                    )

                # ── Step 12: Leave meeting ─────────────────────────────────────
                _zprint(f"[ZOOM-BOT] Step 12: leaving meeting")
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "leaving"
                await _leave_meeting(page, meeting_id)

                await browser.close()
                if meeting_id in _active_bots:
                    _active_bots[meeting_id]["status"] = "completed"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.COMPLETED, user_id=user_id))
                _zprint(f"[ZOOM-BOT] Session COMPLETED — meeting={meeting_id}")
                return

        except asyncio.CancelledError:
            _zprint(f"[ZOOM-BOT] session cancelled — meeting={meeting_id}")
            if meeting_id in _active_bots:
                _active_bots[meeting_id]["status"] = "cancelled"
                asyncio.create_task(set_bot_state(meeting_id, BotStatus.CANCELLED, user_id=user_id))
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            return

        except Exception as exc:
            last_error = str(exc)
            _zprint(f"[ZOOM-BOT] Attempt {attempt} error: {exc}\n{traceback.format_exc()}")
            if meeting_id in _active_bots:
                _active_bots[meeting_id]["status"] = f"error_attempt_{attempt}"
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            browser = None
            if attempt < MAX_RETRIES:
                _zprint(f"[ZOOM-BOT] Retrying in 5s…")
                await asyncio.sleep(5)

    if meeting_id in _active_bots:
        _active_bots[meeting_id]["status"] = "failed"
        asyncio.create_task(set_bot_state(meeting_id, BotStatus.FAILED,
            user_id=user_id, last_error=last_error))
        _zprint(f"[ZOOM-BOT] ALL ATTEMPTS FAILED — meeting={meeting_id}  last_error={last_error}")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _db_mark_failed_sync, meeting_id, last_error or "unknown")
    else:
        _zprint(f"[ZOOM-BOT] ALL ATTEMPTS FAILED — meeting={meeting_id}  (already cleaned up)")


# ── Public entry point ─────────────────────────────────────────────────────

async def join_zoom_meeting(
    zoom_url: str,
    user_id: str,
    organization_id: str,
    meeting_id: Optional[str] = None,
    bot_display_name: str = DEFAULT_BOT_NAME,
    stay_duration_seconds: int = DEFAULT_STAY_SECONDS,
    recordings_dir: str = "recordings",
    topic: Optional[str] = None,
) -> str:
    """
    Dispatch the Zoom bot as an asyncio Task.
    Guards by meeting_id to prevent duplicate sessions.
    Mirrors meet_bot.py join pattern exactly.
    """
    # Keep the FULL url (with ?pwd=...) for the bot session
    full_zoom_url = zoom_url.strip()
    # Strip query params only for DB dedup / upsert
    db_zoom_url = zoom_url.split("?")[0].split("#")[0].rstrip("/")

    _zprint(f"[ZOOM-BOT] join_zoom_meeting ENTERED  user={user_id}  url={full_zoom_url}")

    if not is_valid_zoom_url(full_zoom_url):
        raise ValueError(f"Not a valid Zoom URL: {full_zoom_url!r}")

    # ── Upsert meeting record first to get meeting_id ───────────────────
    if not meeting_id:
        try:
            loop = asyncio.get_event_loop()
            meeting_id_str, _ = _parse_zoom_url(full_zoom_url)
            title = topic if topic else f"Zoom — {meeting_id_str} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
            meeting_id = await loop.run_in_executor(
                None, _db_upsert_meeting_sync, user_id, organization_id, db_zoom_url, title,
            )
            _zprint(f"[ZOOM-BOT] meeting record upserted  meeting_id={meeting_id}")
        except Exception as exc:
            _zprint(f"[ZOOM-BOT ERROR] DB upsert failed: {exc}")
            raise

    # ── Guard by meeting_id to prevent duplicate sessions ──────────────────
    existing = _active_bots.get(meeting_id)
    if existing and existing.get("status") in (
        "dispatched", "pre_join", "joining", "in_meeting",
    ):
        _zprint(f"[ZOOM-BOT] already active for meeting={meeting_id} — skipping dispatch")
        return meeting_id

    # Verify Zoom Web Client URL reachable (diagnostic)
    try:
        import httpx
        mid, p = _parse_zoom_url(full_zoom_url)
        resp = httpx.get(_web_client_url(mid, p), timeout=10)
        _zprint(f"[ZOOM-BOT] zoom.us/wc reachable  status={resp.status_code}")
    except Exception as exc:
        _zprint(f"[ZOOM-BOT] zoom.us/wc unreachable (non-fatal): {exc}")

    _active_bots[meeting_id] = {
        "status":     "dispatched",
        "zoom_url":   full_zoom_url,
        "meeting_id": meeting_id,
        "user_id":    user_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "_task":      None,
    }
    _zprint(f"[ZOOM-BOT] state registered  meeting={meeting_id}")

    task = asyncio.create_task(
        _run_zoom_session(
            zoom_url=full_zoom_url,
            user_id=user_id,
            organization_id=organization_id,
            meeting_id=meeting_id,
            bot_display_name=bot_display_name,
            stay_duration_seconds=stay_duration_seconds,
            recordings_dir=recordings_dir,
        ),
        name=f"zoom_bot:{meeting_id}",
    )

    def _on_done(t: asyncio.Task) -> None:
        exc = t.exception()
        if exc:
            _zprint(f"[ZOOM-BOT] task EXCEPTION: {exc!r}  meeting={meeting_id}")
        else:
            _zprint(f"[ZOOM-BOT] task completed normally  meeting={meeting_id}")
        _active_bots.pop(meeting_id, None)

    task.add_done_callback(_on_done)
    _active_bots[meeting_id]["_task"] = task
    _zprint(f"[ZOOM-BOT] task created  meeting={meeting_id}")

    return meeting_id
