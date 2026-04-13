"""
AI Services - Transcription Service using Groq Whisper API
"""
import logging
from pathlib import Path
from typing import Any, List, Optional
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# Groq Whisper API (fast cloud transcription)
try:
    from groq import Groq as GroqClient  # type: ignore
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    GroqClient = None


class TranscriptionSegment(BaseModel):
    """Transcription segment"""
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    confidence: float = 1.0


class TranscriptionResult(BaseModel):
    """Full transcription result"""
    segments: List[TranscriptionSegment]
    language: str
    duration: float


class TranscriptionService:
    """Service for audio transcription using Groq Whisper API"""
    
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2)

    def _build_fallback_result(self, audio_path: str) -> TranscriptionResult:
        fallback_text = self._fallback_text_from_file(audio_path)
        return TranscriptionResult(
            segments=[
                TranscriptionSegment(
                    start=0.0,
                    end=30.0,
                    text=fallback_text,
                    speaker="speaker_0",
                    confidence=0.5,
                )
            ],
            language="en",
            duration=30.0,
        )
    
    async def transcribe_audio(
        self,
        audio_path: str,
        enable_diarization: bool = True,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Transcribe audio file using Groq Whisper API only.
        Local Whisper fallback is intentionally disabled to avoid local model failures.
        """
        _ = language  # API auto-detects language for now.
        if enable_diarization:
            logger.info("Diarization requested but disabled in API-only transcription mode")

        api_key = settings.GROQ_API_KEY or settings.GROK_API_KEY
        if not GROQ_AVAILABLE:
            logger.warning("Groq SDK not installed, using fallback transcription")
            return self._build_fallback_result(audio_path)

        if not api_key:
            logger.warning("No GROQ_API_KEY/GROK_API_KEY configured, using fallback transcription")
            return self._build_fallback_result(audio_path)

        try:
            return await self._transcribe_with_groq(audio_path, api_key)
        except Exception as exc:
            logger.warning(f"API transcription failed; using fallback transcript: {exc}")
            return self._build_fallback_result(audio_path)

    async def _transcribe_with_groq(self, audio_path: str, api_key: str) -> TranscriptionResult:
        """Transcribe using Groq's Whisper API — very fast cloud transcription."""
        def _call_groq():
            client = GroqClient(api_key=api_key)
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(Path(audio_path).name, f),
                    model="whisper-large-v3-turbo",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
            return response

        logger.info(f"Transcribing with Groq Whisper API: {audio_path}")
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(self._executor, _call_groq)

        raw_segments = getattr(response, "segments", None) or []
        segments = []
        for seg in raw_segments:
            segments.append(TranscriptionSegment(
                start=float(seg.get("start", 0)),
                end=float(seg.get("end", 0)),
                text=str(seg.get("text", "")).strip(),
                speaker=None,
                confidence=1.0,
            ))

        if not segments:
            # Groq returned flat text only
            full_text = getattr(response, "text", "") or ""
            segments = [TranscriptionSegment(start=0.0, end=0.0, text=full_text, confidence=1.0)]

        duration = float(segments[-1].end) if segments else 0.0
        language = getattr(response, "language", "en") or "en"
        logger.info(f"Groq transcription completed: {len(segments)} segments")
        return TranscriptionResult(segments=segments, language=language, duration=duration)

    def _fallback_text_from_file(self, audio_path: str) -> str:
        """Generate fallback transcript text from upload when AI libs are unavailable"""
        path = Path(audio_path)
        if path.suffix.lower() == ".txt":
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    return content[:4000]
            except Exception as exc:
                logger.warning(f"Failed to read txt upload for fallback transcript: {exc}")

        return (
            "Transcription unavailable. Configure GROQ_API_KEY (or GROK_API_KEY alias) "
            "for API-based transcription."
        )
    
    async def extract_audio_from_video(
        self,
        video_path: str,
        output_path: str,
    ) -> str:
        """Extract audio from video file"""
        from moviepy.editor import VideoFileClip  # type: ignore
        
        loop = asyncio.get_event_loop()
        
        def extract():
            video = VideoFileClip(video_path)
            video.audio.write_audiofile(output_path, logger=None)
            video.close()
            return output_path
        
        return await loop.run_in_executor(self._executor, extract)


# Global instance
transcription_service = TranscriptionService()
