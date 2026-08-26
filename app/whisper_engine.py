"""
Thin wrapper around faster-whisper. The model is loaded once at process
startup (see main.py lifespan) and reused for every request — loading it
per-request would be far too slow to be usable.

faster-whisper's transcribe() is a blocking/CPU+GPU-bound call, so it's always
run inside a worker thread via asyncio.to_thread to avoid blocking the event loop.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field

from faster_whisper import WhisperModel

from app.config import Settings

logger = logging.getLogger("fastwhisper")


@dataclass
class TranscriptionResult:
    text: str
    language: str
    duration: float
    processing_time: float
    segments: list[dict] = field(default_factory=list)


class WhisperEngine:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._model: WhisperModel | None = None

    def load(self) -> None:
        logger.info(
            "Loading faster-whisper model",
            extra={
                "model": self._settings.whisper_model,
                "device": self._settings.whisper_device,
                "compute_type": self._settings.whisper_compute_type,
            },
        )
        self._model = WhisperModel(
            self._settings.whisper_model,
            device=self._settings.whisper_device,
            compute_type=self._settings.whisper_compute_type,
            download_root=self._settings.whisper_download_root,
            num_workers=self._settings.whisper_num_workers,
            cpu_threads=self._settings.whisper_cpu_threads,
        )
        logger.info("faster-whisper model loaded")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _transcribe_sync(self, file_path: str, language: str | None) -> TranscriptionResult:
        assert self._model is not None, "Model not loaded"
        start = time.monotonic()

        segments_iter, info = self._model.transcribe(
            file_path,
            language=language,
            vad_filter=True,  # trims silence, meaningfully speeds up long files
        )

        segments = []
        text_parts = []
        for seg in segments_iter:
            segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
            text_parts.append(seg.text.strip())

        elapsed = time.monotonic() - start
        return TranscriptionResult(
            text=" ".join(text_parts).strip(),
            language=info.language,
            duration=info.duration,
            processing_time=elapsed,
            segments=segments,
        )

    async def transcribe(self, file_path: str, language: str | None = None) -> TranscriptionResult:
        return await asyncio.to_thread(self._transcribe_sync, file_path, language)
