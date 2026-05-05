"""
Recording Service
=================
Captures meeting audio during a bot session and saves to a file for transcription.

Two capture strategies (auto-selected by OS and availability):

Strategy A — ffmpeg OS-level capture (RECOMMENDED for production Linux):
  Linux/PulseAudio:
    • Each bot gets a dedicated virtual PulseAudio sink (module-null-sink).
    • Chromium is launched with PULSE_SINK=bot_{user_id} so all browser audio
      goes into that sink.
    • ffmpeg records from sink.monitor → 100% reliable, zero bleed from other processes.
  macOS/avfoundation:
    • Requires BlackHole (https://existential.audio/blackhole/) or Loopback installed
      as a virtual audio device.
    • ffmpeg -f avfoundation -i ":BlackHole 2ch" -ar 16000 -ac 1 output.wav
    • For dev machines without BlackHole, falls back to Strategy B.

Strategy B — Playwright WebRTC interception (UNIVERSAL FALLBACK):
  • An init-script injected before page.goto() patches RTCPeerConnection.addTrack
    so every incoming audio track is captured before Meet's JS can process it.
  • After joining, we wire those tracks through an AudioContext → MediaRecorder.
  • Recording is streamed as audio/webm (Opus) in 2-second chunks.
  • After stop(), chunks are base64-encoded and written to disk as a .webm file.
  • Groq Whisper accepts .webm natively — no re-encoding needed.

Output format:
  Strategy A → recordings/{meeting_id}.wav   (16 kHz, mono, PCM)
  Strategy B → recordings/{meeting_id}.webm  (Opus in WebM container)

Both formats are accepted by transcription_service.transcribe_audio().
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Directory & file helpers ───────────────────────────────────────────────────

RECORDINGS_DIR = Path("recordings")


def _recording_path(meeting_id: str, ext: str) -> Path:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    base = RECORDINGS_DIR / f"{meeting_id}"
    # Avoid overwriting an existing recording
    path = base.with_suffix(f".{ext}")
    if path.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = RECORDINGS_DIR / f"{meeting_id}_{ts}.{ext}"
    return path


# ── Session dataclass ──────────────────────────────────────────────────────────

@dataclass
class RecordingSession:
    meeting_id: str
    output_path: Path
    strategy: str                               # "ffmpeg" | "playwright"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ffmpeg-specific
    ffmpeg_process: Optional[asyncio.subprocess.Process] = None

    # PulseAudio sink cleanup (Linux only)
    pulse_sink_name: Optional[str] = None
    pulse_module_id: Optional[int] = None

    def as_dict(self):
        return {
            "meeting_id": self.meeting_id,
            "output_path": str(self.output_path),
            "strategy": self.strategy,
            "started_at": self.started_at.isoformat(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY A — ffmpeg OS-level capture
# ══════════════════════════════════════════════════════════════════════════════

async def _is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def _create_pulse_sink(sink_name: str) -> Optional[int]:
    """Create a PulseAudio null sink. Returns module_id or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "load-module", "module-null-sink",
            f"sink_name={sink_name}",
            f"sink_properties=device.description={sink_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        raw = (stdout or b"") + (stderr or b"")
        if not raw.strip():
            logger.warning("Recording: pactl load-module returned empty output (PulseAudio may not have module-null-sink available)")
            return None
        try:
            module_id = int(raw.strip().splitlines()[-1])
        except ValueError:
            logger.warning("Recording: could not parse PulseAudio module ID from: %r", raw)
            return None
        logger.info("Recording: created PulseAudio sink %s (module %d)", sink_name, module_id)
        return module_id
    except Exception as exc:
        logger.warning("Recording: could not create PulseAudio sink: %s", exc)
        return None


async def _remove_pulse_sink(module_id: int) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "unload-module", str(module_id),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        logger.info("Recording: removed PulseAudio module %d", module_id)
    except Exception as exc:
        logger.warning("Recording: could not remove PulseAudio module %d: %s", module_id, exc)


def _build_ffmpeg_cmd(output_path: Path, pulse_sink_name: Optional[str] = None) -> Optional[List[str]]:
    """
    Build the platform-appropriate ffmpeg command.
    Returns None if the required audio device isn't available.
    """
    system = platform.system()

    if system == "Linux":
        source = f"{pulse_sink_name}.monitor" if pulse_sink_name else "default"
        return [
            "ffmpeg", "-y",
            "-f", "pulse",
            "-i", source,
            "-ar", "16000",   # 16 kHz — ideal for Whisper
            "-ac", "1",       # mono
            "-acodec", "pcm_s16le",
            str(output_path),
        ]

    if system == "Darwin":
        # Check if BlackHole is installed (virtual loopback for macOS)
        blackhole = _get_macos_blackhole_device()
        if blackhole is None:
            # No loopback device — can only capture microphone (not meeting audio).
            # Log a warning and use mic as best-effort.
            logger.warning(
                "Recording: BlackHole not found on macOS. "
                "Capturing from default mic (will NOT capture meeting participants). "
                "Install BlackHole: https://existential.audio/blackhole/"
            )
            blackhole = ":0"   # first audio input = built-in mic
        return [
            "ffmpeg", "-y",
            "-f", "avfoundation",
            "-i", blackhole,
            "-ar", "16000",
            "-ac", "1",
            "-acodec", "pcm_s16le",
            str(output_path),
        ]

    # Windows — not supported (server product runs on Linux)
    logger.warning("Recording: ffmpeg capture not implemented for %s", system)
    return None


def _get_macos_blackhole_device() -> Optional[str]:
    """
    Return the avfoundation device string for BlackHole if it is installed,
    else None.  We probe by listing avfoundation devices via ffmpeg.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stderr
        lines = output.splitlines()
        for i, line in enumerate(lines):
            if "BlackHole" in line or "blackhole" in line.lower():
                # Find the device index in brackets like [1]
                import re
                m = re.search(r"\[(\d+)\]", line)
                if m:
                    return f":{m.group(1)}"   # audio-only: ":N"
    except Exception:
        pass
    return None


async def start_ffmpeg_recording(
    meeting_id: str,
    output_dir: str = "recordings",
    precreated_sink_name: Optional[str] = None,
    precreated_module_id: Optional[int] = None,
) -> Optional[RecordingSession]:
    """
    Start ffmpeg recording.  Returns a RecordingSession on success, None on failure.

    On Linux, uses a pre-created PulseAudio sink if provided (via prepare_audio_sink),
    otherwise creates one.  The caller MUST set PULSE_SINK=<sink_name> in the
    Chromium environment so browser audio routes into the sink.
    """
    if not await _is_ffmpeg_available():
        logger.warning("Recording: ffmpeg not found — skipping ffmpeg recording")
        return None

    global RECORDINGS_DIR
    RECORDINGS_DIR = Path(output_dir)
    output_path = _recording_path(meeting_id, "wav")

    pulse_sink_name: Optional[str] = precreated_sink_name
    pulse_module_id: Optional[int] = precreated_module_id

    if platform.system() == "Linux" and pulse_sink_name is None:
        pulse_sink_name = f"bot_{meeting_id.replace('-', '_')[:20]}"
        pulse_module_id = await _create_pulse_sink(pulse_sink_name)

    cmd = _build_ffmpeg_cmd(output_path, pulse_sink_name)
    if cmd is None:
        return None

    logger.info("Recording: starting ffmpeg → %s", output_path)
    logger.debug("Recording: cmd = %s", " ".join(cmd))

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("Recording: ffmpeg binary not found")
        return None
    except Exception as exc:
        logger.error("Recording: failed to start ffmpeg: %s", exc)
        return None

    # Give ffmpeg 1.5s to initialise — if it dies immediately, the device isn't available
    await asyncio.sleep(1.5)
    if process.returncode is not None:
        stderr_bytes = await process.stderr.read()
        logger.error("Recording: ffmpeg exited immediately: %s", stderr_bytes.decode(errors="replace"))
        if pulse_module_id is not None:
            await _remove_pulse_sink(pulse_module_id)
        return None

    logger.info("Recording: ffmpeg running (pid=%d) → %s", process.pid, output_path)
    return RecordingSession(
        meeting_id=meeting_id,
        output_path=output_path,
        strategy="ffmpeg",
        ffmpeg_process=process,
        pulse_sink_name=pulse_sink_name,
        pulse_module_id=pulse_module_id,
    )


async def stop_ffmpeg_recording(session: RecordingSession) -> Optional[Path]:
    """
    Gracefully stop ffmpeg (send 'q' to stdin, wait up to 15s, then SIGTERM).
    Returns the output path if the file exists and has content, else None.
    """
    proc = session.ffmpeg_process
    if proc is None or proc.returncode is not None:
        return session.output_path if session.output_path.exists() else None

    logger.info("Recording: stopping ffmpeg (pid=%d)…", proc.pid)

    # Send 'q' to ffmpeg stdin = graceful stop (writes file trailer)
    try:
        proc.stdin.write(b"q")
        await proc.stdin.drain()
    except Exception:
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning("Recording: ffmpeg did not stop gracefully — sending SIGTERM")
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            proc.kill()

    # Remove PulseAudio sink
    if session.pulse_module_id is not None:
        await _remove_pulse_sink(session.pulse_module_id)

    if session.output_path.exists() and session.output_path.stat().st_size > 0:
        size_kb = session.output_path.stat().st_size // 1024
        logger.info("Recording: ffmpeg stopped. File: %s (%d KB)", session.output_path, size_kb)
        return session.output_path

    logger.warning("Recording: output file missing or empty: %s", session.output_path)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY B — Playwright WebRTC track interception
# ══════════════════════════════════════════════════════════════════════════════

# This script is injected via page.add_init_script() BEFORE page.goto().
# It patches RTCPeerConnection.addTrack so that every incoming remote audio
# track is captured in window._syncMinds.tracks[] before Meet can process it.
WEBRTC_INTERCEPT_SCRIPT = """
(function() {
    if (window._syncMinds) return;
    window._syncMinds = {
        tracks: [],
        chunks: [],
        recorder: null,
        audioCtx: null,
        destination: null,
    };

    // Patch RTCPeerConnection to intercept remote audio tracks
    const _origAddTrack = RTCPeerConnection.prototype.addTrack;
    RTCPeerConnection.prototype.addTrack = function(track, ...streams) {
        if (track.kind === 'audio') {
            console.log('[SyncMinds] Intercepted audio track:', track.id);
            window._syncMinds.tracks.push({ track, streams });
        }
        return _origAddTrack.apply(this, arguments);
    };

    // Also intercept ontrack events (some browsers use this path)
    const _origSetRemoteDesc = RTCPeerConnection.prototype.setRemoteDescription;
    RTCPeerConnection.prototype.setRemoteDescription = function(...args) {
        this.addEventListener('track', (e) => {
            if (e.track && e.track.kind === 'audio') {
                const already = window._syncMinds.tracks.some(t => t.track.id === e.track.id);
                if (!already) {
                    console.log('[SyncMinds] Intercepted track via ontrack:', e.track.id);
                    window._syncMinds.tracks.push({ track: e.track, streams: e.streams });
                }
            }
        });
        return _origSetRemoteDesc.apply(this, arguments);
    };

    console.log('[SyncMinds] WebRTC intercept active');
})();
"""

# Called from Python via page.evaluate() AFTER joining the meeting.
# Wires all captured tracks into an AudioContext and starts MediaRecorder.
_START_RECORDER_SCRIPT = """
async () => {
    const state = window._syncMinds;
    if (!state) return { ok: false, error: 'intercept_not_installed' };
    if (state.recorder) return { ok: true, status: 'already_recording' };

    // Prefer remote tracks (actual meeting participants) over local fake mic tracks.
    // Fall back to all live tracks if no remote ones were captured.
    let remoteTracks = (state.tracks || []).filter(t => t.remote && t.track.readyState === 'live');
    let allLive      = (state.tracks || []).filter(t => t.track.readyState === 'live');
    let candidates   = remoteTracks.length ? remoteTracks : allLive;

    // DOM fallback: grab audio from any <audio>/<video> elements Meet may have created
    if (candidates.length === 0) {
        document.querySelectorAll('audio, video').forEach(el => {
            if (!el.srcObject) return;
            el.srcObject.getAudioTracks().forEach(t => {
                if (t.readyState === 'live' && !state.tracks.some(x => x.track.id === t.id)) {
                    state.tracks.push({ track: t, remote: true });
                    candidates.push({ track: t, remote: true });
                }
            });
        });
    }

    if (candidates.length === 0) {
        return {
            ok: false,
            error: 'no_live_audio_tracks',
            totalCaptured: (state.tracks || []).length,
            hint: 'Remote participants may not have joined yet',
        };
    }

    // Use native WebRTC sample rate (48 kHz) to avoid resampling artefacts.
    // Groq Whisper accepts webm/opus at any sample rate.
    const ctx  = new AudioContext({ sampleRate: 48000 });
    const dest = ctx.createMediaStreamDestination();
    state.audioCtx   = ctx;
    state.destination = dest;

    let connected = 0;
    state._keepalive = state._keepalive || [];
    candidates.forEach(({ track }) => {
        try {
            const stream = new MediaStream([track]);
            
            // CRITICAL WORKAROUND for Chromium bug: WebRTC remote tracks are silent
            // in Web Audio API (createMediaStreamSource) unless they are also attached
            // to an HTMLMediaElement that is actively playing.
            const audioEl = document.createElement('audio');
            audioEl.autoplay = true;
            audioEl.srcObject = stream;
            audioEl.play().catch(e => console.warn('[SyncMinds] audioEl.play() failed', e));
            state._keepalive.push(audioEl); // prevent garbage collection
            
            const src = ctx.createMediaStreamSource(stream);
            src.connect(dest);
            connected++;
        } catch(e) {
            console.warn('[SyncMinds] Could not connect track', track.id, e);
        }
    });

    if (connected === 0) {
        return { ok: false, error: 'track_connect_failed' };
    }

    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm';

    const recorder = new MediaRecorder(dest.stream, { mimeType, audioBitsPerSecond: 128000 });
    recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
            // Save first chunk as init segment — WebM header, needed to make
            // each extracted window a valid standalone file for Whisper.
            if (!state.initChunk) state.initChunk = e.data;
            state.chunks.push(e.data);
        }
    };
    recorder.start(2000);
    state.recorder = recorder;

    console.log('[SyncMinds] MediaRecorder started', mimeType, 'remote=' + remoteTracks.length + ' connected=' + connected);
    return { ok: true, mimeType, trackCount: connected, remoteCount: remoteTracks.length };
}
"""

_STOP_RECORDER_SCRIPT = """
async () => {
    const state = window._syncMinds;
    if (!state || !state.recorder) return null;

    return new Promise((resolve) => {
        state.recorder.addEventListener('stop', async () => {
            // One final flush
            const allChunks = [...state.chunks];
            if (allChunks.length === 0) { resolve(null); return; }

            const blob = new Blob(allChunks, { type: state.recorder.mimeType });
            const reader = new FileReader();
            reader.onloadend = () => {
                // Strip the data URL prefix (data:audio/webm;base64,...)
                const b64 = reader.result.split(',')[1];
                resolve(b64);
            };
            reader.readAsDataURL(blob);
        });

        state.recorder.stop();
        if (state.audioCtx) state.audioCtx.close().catch(() => {});
    });
}
"""

# Called every N seconds during the meeting to extract the current audio window.
# Returns a base64-encoded webm blob (with WebM init headers prepended so Whisper
# can decode it as a standalone file) or null if no new audio.
EXTRACT_CHUNK_SCRIPT = """
async () => {
    const state = window._syncMinds;
    if (!state || !state.recorder || !state.chunks || state.chunks.length === 0) return null;

    // Drain current chunks into this window
    const windowChunks = state.chunks.splice(0, state.chunks.length);
    if (windowChunks.length === 0) return null;

    // Prepend init segment so the window is a valid standalone WebM file.
    // Without this, Whisper rejects the file (missing EBML header).
    const blobParts = state.initChunk
        ? [state.initChunk, ...windowChunks]
        : windowChunks;

    const blob = new Blob(blobParts, { type: 'audio/webm;codecs=opus' });
    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onloadend = () => {
            // Strip "data:audio/webm;base64," prefix
            const b64 = reader.result ? reader.result.split(',')[1] : null;
            resolve(b64);
        };
        reader.readAsDataURL(blob);
    });
}
"""


async def start_playwright_recording(
    page: "Any",
    meeting_id: str,
    output_dir: str = "recordings",
) -> Optional[RecordingSession]:
    """
    Wire up the already-intercepted WebRTC tracks and start MediaRecorder in-page.
    Call this AFTER the bot has confirmed it is inside the meeting.
    The WEBRTC_INTERCEPT_SCRIPT must have been injected as an add_init_script before.
    """
    global RECORDINGS_DIR
    RECORDINGS_DIR = Path(output_dir)
    output_path = _recording_path(meeting_id, "webm")

    try:
        result = await page.evaluate(_START_RECORDER_SCRIPT)
    except Exception as exc:
        logger.error("Recording: could not start in-page MediaRecorder: %s", exc)
        return None

    if not result or not result.get("ok"):
        logger.warning(
            "Recording: MediaRecorder start failed: %s",
            result.get("error") if result else "no result",
        )
        return None

    logger.info(
        "Recording: in-page MediaRecorder started. tracks=%d mimeType=%s → %s",
        result.get("trackCount", 0),
        result.get("mimeType"),
        output_path,
    )
    return RecordingSession(
        meeting_id=meeting_id,
        output_path=output_path,
        strategy="playwright",
    )


async def stop_playwright_recording(
    page: "Any",
    session: RecordingSession,
) -> Optional[Path]:
    """
    Stop the in-page MediaRecorder, collect audio chunks, write to disk.
    Returns output path on success.
    """
    logger.info("Recording: stopping in-page MediaRecorder…")
    try:
        b64_data = await asyncio.wait_for(
            page.evaluate(_STOP_RECORDER_SCRIPT),
            timeout=30,
        )
    except asyncio.TimeoutError:
        logger.error("Recording: MediaRecorder stop timed out")
        return None
    except Exception as exc:
        logger.error("Recording: error stopping MediaRecorder: %s", exc)
        return None

    if not b64_data:
        logger.warning("Recording: MediaRecorder returned no data")
        return None

    audio_bytes = base64.b64decode(b64_data)
    session.output_path.parent.mkdir(parents=True, exist_ok=True)
    session.output_path.write_bytes(audio_bytes)

    size_kb = len(audio_bytes) // 1024
    logger.info("Recording: in-page recording saved. %s (%d KB)", session.output_path, size_kb)
    return session.output_path


# ══════════════════════════════════════════════════════════════════════════════
# Unified public API — used by meet_bot.py
# ══════════════════════════════════════════════════════════════════════════════

async def start_recording(
    meeting_id: str,
    output_dir: str = "recordings",
    precreated_sink_name: Optional[str] = None,
    precreated_module_id: Optional[int] = None,
) -> Optional[RecordingSession]:
    """
    Try ffmpeg first, return RecordingSession on success.
    Returns None if ffmpeg is unavailable (bot will fall back to Strategy B).

    If precreated_sink_name/module_id are provided (via prepare_audio_sink called
    before Chromium launch), reuses that sink so Chromium's audio is already
    routed there when it starts.
    """
    session = await start_ffmpeg_recording(
        meeting_id, output_dir,
        precreated_sink_name=precreated_sink_name,
        precreated_module_id=precreated_module_id,
    )
    if session is not None:
        return session
    logger.info("Recording: ffmpeg unavailable — will use Playwright MediaRecorder after join")
    return None  # Caller signals Strategy B by getting page-level recording


async def stop_recording(
    session: Optional[RecordingSession],
    page: Optional["Any"] = None,
) -> Optional[Path]:
    """
    Stop whichever strategy is active.
    - If session.strategy == "ffmpeg" → stop ffmpeg process
    - If session.strategy == "playwright" → collect from page
    Returns the file path on success, None on failure.
    """
    if session is None:
        return None

    if session.strategy == "ffmpeg":
        return await stop_ffmpeg_recording(session)

    if session.strategy == "playwright":
        if page is None:
            logger.error("Recording: stop_recording called with strategy='playwright' but page=None")
            return None
        return await stop_playwright_recording(page, session)

    logger.error("Recording: unknown strategy %s", session.strategy)
    return None


async def prepare_audio_sink(meeting_id: str) -> tuple[Optional[str], Optional[int]]:
    """
    Create a PulseAudio null sink for bot audio capture.
    Call this BEFORE launching Chromium so PULSE_SINK can be passed to the
    Chromium environment — Zoom WebRTC audio will then route to this sink.

    Returns (sink_name, module_id). Caller stores module_id for cleanup via
    remove_audio_sink() when the session ends.
    """
    if platform.system() != "Linux":
        return None, None
    sink_name = f"bot_{meeting_id.replace('-', '_')[:20]}"
    module_id = await _create_pulse_sink(sink_name)
    return sink_name, module_id


async def remove_audio_sink(module_id: Optional[int]) -> None:
    """Remove a PulseAudio sink created by prepare_audio_sink()."""
    if module_id is not None:
        await _remove_pulse_sink(module_id)


def get_pulse_sink_env(session: Optional[RecordingSession]) -> dict:
    """
    Return env vars to pass to Chromium so its audio output goes into the
    dedicated PulseAudio sink.  Returns {} if no sink was created.
    """
    if session and session.pulse_sink_name:
        return {"PULSE_SINK": session.pulse_sink_name}
    return {}
