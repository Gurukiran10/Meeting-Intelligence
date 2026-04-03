"""
AI Services - Transcription Service using Whisper
"""
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# Optional AI dependencies
try:
    import whisper  # type: ignore
    WHISPER_AVAILABLE = True
except ImportError:
    logger.warning("Whisper not installed. Audio transcription will not be available.")
    WHISPER_AVAILABLE = False
    whisper = None

try:
    import torch  # type: ignore
    TORCH_AVAILABLE = True
except ImportError:
    logger.warning("PyTorch not installed. GPU acceleration will not be available.")
    TORCH_AVAILABLE = False
    torch = None

try:
    from pyannote.audio import Pipeline  # type: ignore
    PYANNOTE_AVAILABLE = True
except ImportError:
    logger.warning("Pyannote not installed. Speaker diarization will not be available.")
    PYANNOTE_AVAILABLE = False
    Pipeline = None

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
    """Service for audio transcription using Whisper"""
    
    def __init__(self):
        self.model_name = settings.WHISPER_MODEL
        self.device = settings.WHISPER_DEVICE
        self.model = None
        self.diarization_pipeline = None
        self._executor = ThreadPoolExecutor(max_workers=2)
    
    def _load_model(self):
        """Load Whisper model"""
        if self.model is None:
            logger.info(f"Loading Whisper model: {self.model_name}")
            self.model = whisper.load_model(  # type: ignore
                self.model_name,
                device=self.device,
            )
            logger.info("Whisper model loaded successfully")
    
    def _load_diarization_pipeline(self):
        """Load speaker diarization pipeline"""
        if self.diarization_pipeline is None:
            try:
                logger.info("Loading speaker diarization pipeline")
                self.diarization_pipeline = Pipeline.from_pretrained(  # type: ignore
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=None,  # Add Hugging Face token if needed
                )
                if torch.cuda.is_available():  # type: ignore
                    self.diarization_pipeline.to(torch.device("cuda"))  # type: ignore
                logger.info("Diarization pipeline loaded successfully")
            except Exception as e:
                logger.warning(f"Failed to load diarization: {e}")
    
    async def transcribe_audio(
        self,
        audio_path: str,
        enable_diarization: bool = True,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """
        Transcribe audio file with optional speaker diarization.
        Uses Groq Whisper API first (fast), falls back to local Whisper.
        """
        # Try Groq Whisper first — much faster than local
        if GROQ_AVAILABLE and settings.GROQ_API_KEY:
            try:
                return await self._transcribe_with_groq(audio_path)
            except Exception as exc:
                logger.warning(f"Groq transcription failed, falling back to local Whisper: {exc}")

        if not WHISPER_AVAILABLE:
            logger.warning("Whisper unavailable, using fallback transcription")
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

        loop = asyncio.get_event_loop()
        
        # Load models if needed
        self._load_model()
        if enable_diarization:
            self._load_diarization_pipeline()
        
        # Transcribe audio
        logger.info(f"Transcribing audio: {audio_path}")
        result = await loop.run_in_executor(
            self._executor,
            self._transcribe_sync,
            audio_path,
            language,
        )
        
        # Perform diarization if enabled
        if enable_diarization and self.diarization_pipeline:
            logger.info("Performing speaker diarization")
            diarization = await loop.run_in_executor(
                self._executor,
                self._diarize_sync,
                audio_path,
            )
            
            # Merge transcription with diarization
            segments = self._merge_transcription_diarization(
                result["segments"],
                diarization,
            )
        else:
            segments = [
                TranscriptionSegment(
                    start=seg["start"],
                    end=seg["end"],
                    text=seg["text"].strip(),
                    confidence=seg.get("confidence", 1.0),
                )
                for seg in result["segments"]
            ]
        
        logger.info(f"Transcription completed: {len(segments)} segments")
        
        return TranscriptionResult(
            segments=segments,
            language=result["language"],
            duration=result.get("duration", 0),
        )

    async def _transcribe_with_groq(self, audio_path: str) -> TranscriptionResult:
        """Transcribe using Groq's Whisper API — very fast cloud transcription."""
        import asyncio

        def _call_groq():
            client = GroqClient(api_key=settings.GROQ_API_KEY)
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

    def _transcribe_sync(
        self,
        audio_path: str,
        language: Optional[str],
    ) -> Dict:
        """Synchronous transcription"""
        return self.model.transcribe(  # type: ignore
            audio_path,
            language=language,
            task="transcribe",
            verbose=False,
            word_timestamps=True,
        )
    
    def _diarize_sync(self, audio_path: str) -> Dict:
        """Synchronous speaker diarization"""
        diarization = self.diarization_pipeline(audio_path)  # type: ignore
        
        # Convert to dict format
        result = {}
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            if speaker not in result:
                result[speaker] = []
            result[speaker].append({
                "start": turn.start,
                "end": turn.end,
            })
        
        return result

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
            "Transcription unavailable in local minimal mode. "
            "Install Whisper dependencies and add API keys for full AI transcription."
        )
    
    def _merge_transcription_diarization(
        self,
        segments: List[Dict],
        diarization: Dict,
    ) -> List[TranscriptionSegment]:
        """Merge transcription segments with speaker diarization"""
        result = []
        
        for seg in segments:
            start = seg["start"]
            end = seg["end"]
            text = seg["text"].strip()
            
            # Find overlapping speaker
            speaker = self._find_speaker_at_time(diarization, start, end)
            
            result.append(TranscriptionSegment(
                start=start,
                end=end,
                text=text,
                speaker=speaker,
                confidence=seg.get("confidence", 1.0),
            ))
        
        return result
    
    def _find_speaker_at_time(
        self,
        diarization: Dict,
        start: float,
        end: float,
    ) -> Optional[str]:
        """Find speaker for time range"""
        mid_point = (start + end) / 2
        
        for speaker, turns in diarization.items():
            for turn in turns:
                if turn["start"] <= mid_point <= turn["end"]:
                    return speaker
        
        return None
    
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
