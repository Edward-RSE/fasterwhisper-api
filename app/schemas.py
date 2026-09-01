import uuid

from pydantic import BaseModel


class TranscriptionSegment(BaseModel):
    """A single transcription segment returned by the model."""

    start: float
    end: float
    text: str


class OpenAITranscriptionResponse(BaseModel):
    """Response schema matching OpenAI's transcription API.

    The payload is shaped to be compatible with Open WebUI's OpenAI-style STT
    provider configuration.
    """

    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[TranscriptionSegment] | None = None


class JobSubmitResponse(BaseModel):
    """Response returned when a request is accepted into the async queue."""

    job_id: uuid.UUID
    status: str
    poll_url: str


class JobStatusResponse(BaseModel):
    """Current status payload for a queued or completed transcription job."""

    job_id: uuid.UUID
    status: str
    original_filename: str | None = None
    detected_language: str | None = None
    audio_duration_seconds: float | None = None
    processing_time_seconds: float | None = None
    result_text: str | None = None
    error_message: str | None = None
    created_at: str
    completed_at: str | None = None


class HealthResponse(BaseModel):
    """Readiness health payload reported by the service health endpoint."""

    model_config = {"protected_namespaces": ()}

    status: str
    model: str
