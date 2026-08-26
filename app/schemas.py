import uuid
from typing import Optional

from pydantic import BaseModel, Field


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str


class OpenAITranscriptionResponse(BaseModel):
    """Shape matches OpenAI's /v1/audio/transcriptions so this endpoint can be
    dropped straight into Open WebUI's "OpenAI-compatible" STT engine setting."""

    text: str
    language: Optional[str] = None
    duration: Optional[float] = None
    segments: Optional[list[TranscriptionSegment]] = None


class JobSubmitResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    poll_url: str


class JobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    original_filename: Optional[str] = None
    detected_language: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    processing_time_seconds: Optional[float] = None
    result_text: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class HealthResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    status: str
    model_loaded: bool
    database_connected: bool
    openwebui_auth: str = Field(
        description='"disabled", "ok", or "unreachable" — whether Open WebUI-backed API key lookup is configured and working'
    )
    device: str
    model: str
    queue_depth: int = Field(
        description="Number of async jobs currently queued or processing"
    )
