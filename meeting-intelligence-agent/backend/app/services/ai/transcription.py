"""
AI Services - Transcription Service

Priority:
  1. AssemblyAI  — if ASSEMBLYAI_API_KEY is set (real speaker diarization)
  2. Groq Whisper — fast cloud transcription (gap-heuristic speaker labels)
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Optional

import httpx
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

try:
    from groq import Groq as GroqClient  # type: ignore
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    GroqClient = None

ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    confidence: float = 1.0


class TranscriptionResult(BaseModel):
    segments: List[TranscriptionSegment]
    language: str
    duration: float
    diarization_method: str = "none"


class TranscriptionService:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2)

    async def transcribe_audio(
        self,
        audio_path: str,
        enable_diarization: bool = True,
        language: Optional[str] = None,
        attendee_names: Optional[List[str]] = None,
    ) -> TranscriptionResult:
        assemblyai_key = getattr(settings, "ASSEMBLYAI_API_KEY", "")
        if assemblyai_key and enable_diarization:
            logger.info("Using AssemblyAI for transcription + speaker diarization")
            try:
                return await self._transcribe_with_assemblyai(audio_path, assemblyai_key, attendee_names or [])
            except Exception as exc:
                logger.warning("AssemblyAI failed — falling back to Groq. Reason: %s", exc, exc_info=True)

        groq_key = settings.GROQ_API_KEY or settings.GROK_API_KEY
        if not GROQ_AVAILABLE or not groq_key:
            logger.warning("No cloud transcription provider available — using placeholder")
            return self._build_placeholder_result(audio_path)

        try:
            return await self._transcribe_with_groq(audio_path, groq_key)
        except Exception as e:
            logger.error(f"Cloud transcription failed: {e}")
            return self._build_placeholder_result(audio_path)

    def _build_placeholder_result(self, audio_path: str) -> TranscriptionResult:
        """Fallback result when all transcription APIs fail"""
        return TranscriptionResult(
            segments=[
                TranscriptionSegment(
                    start=0.0,
                    end=1.0,
                    text="[Transcription unavailable — AI provider rate limited or offline]",
                    speaker="System",
                    confidence=0.0
                )
            ],
            language="en",
            duration=0.0,
            diarization_method="fallback",
        )

    # ── AssemblyAI ─────────────────────────────────────────────────────────

    async def _transcribe_with_assemblyai(
        self,
        audio_path: str,
        api_key: str,
        attendee_names: List[str],
    ) -> TranscriptionResult:
        headers = {"authorization": api_key}

        # 1. Guard: skip AssemblyAI for very small files (< 50 KB).
        # Files this small are essentially silence and AssemblyAI returns 400.
        file_size_bytes = Path(audio_path).stat().st_size
        file_size_mb = file_size_bytes / 1_048_576
        if file_size_bytes < 50_000:
            raise RuntimeError(
                f"AssemblyAI skipped — file too small ({file_size_bytes} bytes). "
                f"Likely a silent/cancelled recording."
            )

        # 2. Upload audio
        logger.info("AssemblyAI: uploading audio %s (%.1f MB)", audio_path, file_size_mb)
        # Allow up to 10 min for large recordings (180s upload + read timeout)
        upload_timeout = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)
        async with httpx.AsyncClient(timeout=upload_timeout) as client:
            with open(audio_path, "rb") as f:
                upload_resp = await client.post(
                    f"{ASSEMBLYAI_BASE}/upload",
                    headers={**headers, "content-type": "application/octet-stream"},
                    content=f.read(),
                )
        if upload_resp.status_code != 200:
            raise RuntimeError(f"AssemblyAI upload failed {upload_resp.status_code}: {upload_resp.text[:200]}")
        audio_url = upload_resp.json()["upload_url"]
        logger.info("AssemblyAI: audio uploaded → %s", audio_url)

        # 3. Request transcription with speaker diarization
        async with httpx.AsyncClient(timeout=30.0) as client:
            transcript_resp = await client.post(
                f"{ASSEMBLYAI_BASE}/transcript",
                headers=headers,
                json={
                    "audio_url": audio_url,
                    "speaker_labels": True,
                    "language_detection": True,
                },
            )
        # A 400 from AssemblyAI means the audio is invalid/too short — treat
        # as a soft failure so we fall through to Groq.
        if transcript_resp.status_code == 400:
            raise RuntimeError(
                f"AssemblyAI rejected audio (400) — likely too short or silent. "
                f"Response: {transcript_resp.text[:200]}"
            )
        transcript_resp.raise_for_status()
        transcript_id = transcript_resp.json()["id"]
        logger.info("AssemblyAI: transcript job started → id=%s", transcript_id)

        # 3. Poll until complete (max 20 min)
        poll_url = f"{ASSEMBLYAI_BASE}/transcript/{transcript_id}"
        deadline = time.time() + 1200
        async with httpx.AsyncClient(timeout=30.0) as client:
            while time.time() < deadline:
                await asyncio.sleep(5)
                poll = await client.get(poll_url, headers=headers)
                poll.raise_for_status()
                data = poll.json()
                status = data.get("status")
                if status == "completed":
                    logger.info("AssemblyAI: transcript completed (%d utterances)", len(data.get("utterances") or []))
                    return self._parse_assemblyai_response(data, attendee_names)
                if status == "error":
                    raise RuntimeError(f"AssemblyAI error: {data.get('error')}")
                logger.debug("AssemblyAI: status=%s — waiting…", status)

        raise TimeoutError("AssemblyAI transcript timed out after 20 minutes")

    def _parse_assemblyai_response(self, data: dict, attendee_names: List[str]) -> TranscriptionResult:
        utterances = data.get("utterances") or []
        language = data.get("language_code") or "en"

        # Build speaker → name mapping
        # AssemblyAI labels: "A", "B", "C"...
        # Map to attendee names if available, otherwise keep "Speaker A" format
        unique_speakers = list(dict.fromkeys(u["speaker"] for u in utterances))
        speaker_map = _build_speaker_map(unique_speakers, attendee_names)

        segments: List[TranscriptionSegment] = []
        for u in utterances:
            speaker_label = speaker_map.get(u["speaker"], f"Speaker {u['speaker']}")
            segments.append(TranscriptionSegment(
                start=u["start"] / 1000.0,
                end=u["end"] / 1000.0,
                text=u["text"].strip(),
                speaker=speaker_label,
                confidence=u.get("confidence", 1.0),
            ))

        duration = segments[-1].end if segments else 0.0
        return TranscriptionResult(
            segments=segments,
            language=language,
            duration=duration,
            diarization_method="assemblyai",
        )

    # ── Groq Whisper ───────────────────────────────────────────────────────

    async def _transcribe_with_groq(self, audio_path: str, api_key: str) -> TranscriptionResult:
        def _call():
            client = GroqClient(api_key=api_key, timeout=600.0)
            with open(audio_path, "rb") as f:

                return client.audio.transcriptions.create(
                    file=(Path(audio_path).name, f),
                    model="whisper-large-v3-turbo",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )

        logger.info("Transcribing with Groq Whisper API: %s", audio_path)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(self._executor, _call)

        raw_segments = getattr(response, "segments", None) or []
        segments = [
            TranscriptionSegment(
                start=float(seg.get("start", 0)),
                end=float(seg.get("end", 0)),
                text=str(seg.get("text", "")).strip(),
                speaker=None,
                confidence=1.0,
            )
            for seg in raw_segments
        ]

        if not segments:
            full_text = getattr(response, "text", "") or ""
            segments = [TranscriptionSegment(start=0.0, end=0.0, text=full_text, confidence=1.0)]

        duration = segments[-1].end if segments else 0.0
        language = getattr(response, "language", "en") or "en"
        logger.info("Groq transcription completed: %d segments", len(segments))
        return TranscriptionResult(
            segments=segments,
            language=language,
            duration=duration,
            diarization_method="gap_heuristic",
        )

    async def extract_audio_from_video(self, video_path: str, output_path: str) -> str:
        from moviepy.editor import VideoFileClip  # type: ignore
        loop = asyncio.get_event_loop()

        def extract():
            video = VideoFileClip(video_path)
            video.audio.write_audiofile(output_path, logger=None)
            video.close()
            return output_path

        return await loop.run_in_executor(self._executor, extract)


def _build_speaker_map(speaker_labels: List[str], attendee_names: List[str]) -> dict:
    """
    Map AssemblyAI speaker labels (A, B, C...) to human-readable names.
    If attendee names are provided, map label order → attendee order.
    Always falls back to 'Speaker A', 'Speaker B' etc.
    """
    result = {}
    clean_names = [n.strip() for n in attendee_names if n.strip()]
    for i, label in enumerate(speaker_labels):
        if i < len(clean_names):
            result[label] = clean_names[i]
        else:
            result[label] = f"Speaker {label}"
    return result


transcription_service = TranscriptionService()
