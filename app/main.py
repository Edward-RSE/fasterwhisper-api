import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from app.auth import get_api_key_label, get_openwebui_key_store
from app.config import get_settings
from app.database import async_session_maker, db_healthy, init_db
from app.jobs import Job, JobQueue
from app.logging_config import configure_logging
from app.models import JobStatus, RequestMode, TranscriptionRequest
from app.schemas import (
    HealthResponse,
    JobStatusResponse,
    JobSubmitResponse,
    OpenAITranscriptionResponse,
    TranscriptionSegment,
)
from app.whisper_engine import WhisperEngine

logger = logging.getLogger("fasterwhisper")

settings = get_settings()
configure_logging(settings)

whisper_engine = WhisperEngine(settings)
job_queue = JobQueue(whisper_engine, concurrency=settings.gpu_concurrency)
openwebui_keys = get_openwebui_key_store()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize application state on startup and clean up on shutdown.

    Parameters
    ----------
    app : FastAPI
        The FastAPI application instance.

    Yields
    ------
    None
        The application remains active while the context manager is open.

    """
    os.makedirs(settings.tmp_upload_dir, exist_ok=True)
    await init_db()
    # Model load is blocking and can take a while (esp. large-v3 on a cold PVC) —
    # run it off the event loop so startup doesn't block signal handling.
    import asyncio

    await asyncio.to_thread(whisper_engine.load)
    job_queue.start()

    if openwebui_keys.enabled:
        reachable = await openwebui_keys.ping()
        logger.info(
            "Open WebUI key lookup %s",
            "reachable" if reachable else "configured but unreachable",
        )
    else:
        logger.info("Open WebUI key lookup disabled (OPENWEBUI_DATABASE_URL not set)")

    logger.info("fasterwhisper-api ready")
    yield
    await job_queue.stop()
    await openwebui_keys.close()


app = FastAPI(
    title="fasterwhisper API",
    description="faster-whisper transcription service (SotonGPT)",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log request metadata for non-health traffic.

    Parameters
    ----------
    request : Request
        The incoming HTTP request.
    call_next : Callable
        The next ASGI callable in the middleware chain.

    Returns
    -------
    Response
        The response returned by the downstream application.

    """
    if request.url.path in {"/health", "/health/live"}:
        response = await call_next(request)
        return response

    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.monotonic()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "request handled",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": getattr(response, "status_code", 500),
                "duration_ms": round(elapsed_ms, 1),
            },
        )


async def _save_upload(file: UploadFile) -> tuple[str, int]:
    """Persist an uploaded file to disk and enforce the configured size limit.

    Parameters
    ----------
    file : UploadFile
        The uploaded audio file.

    Returns
    -------
    tuple[str, int]
        The destination path and the file size in bytes.

    Raises
    ------
    HTTPException
        If the upload exceeds the configured maximum size.

    """
    dest_path = os.path.join(
        settings.tmp_upload_dir, f"{uuid.uuid4()}_{file.filename or 'upload'}"
    )
    size = 0
    chunk_size = 1024 * 1024
    f = await asyncio.to_thread(open, dest_path, "wb")
    try:
        while chunk := await file.read(chunk_size):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                await asyncio.to_thread(f.close)
                await asyncio.to_thread(os.remove, dest_path)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds max upload size of {settings.max_upload_mb} MB",
                )
            await asyncio.to_thread(f.write, chunk)
    finally:
        await asyncio.to_thread(f.close)
    return dest_path, size


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    """Report whether the event loop is still responsive.

    Returns
    -------
    dict
        A minimal liveness payload indicating the service is still alive.

    """
    db_ok = await db_healthy()
    model_ok = whisper_engine.is_loaded
    healthy = db_ok and model_ok

    status_message = "ok" if healthy else "degraded"
    status_code = status_message.HTTP_200_OK if healthy else status_message.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(status_code=status_code, content={"status": status_message, "model": settings.whisper_model})


# --------------------------------------------------------------------------
# OpenAI-compatible sync transcription
# (points Open WebUI's "OpenAI" STT provider straight at this service)
# --------------------------------------------------------------------------


@app.post("/v1/audio/transcriptions", response_model=OpenAITranscriptionResponse)
async def transcribe_sync(
    request: Request,
    file: UploadFile,
    language: str | None = None,
    api_key_label: str = Depends(get_api_key_label),
):
    """Transcribe a file via the OpenAI-compatible synchronous API.

    Parameters
    ----------
    request : Request
        The incoming HTTP request.
    file : UploadFile
        Audio file uploaded for transcription.
    language : str or None, default=None
        Optional source-language hint.
    api_key_label : str
        Label derived from the valid API key used to authorize the request.

    Returns
    -------
    OpenAITranscriptionResponse
        The transcription output and metadata.

    """
    file_path, size = await _save_upload(file)

    if size > settings.sync_max_upload_bytes:
        os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File is {size / 1024 / 1024:.1f} MB, over the {settings.sync_max_upload_mb} MB "
                "sync limit. Submit it to POST /transcriptions (async) and poll for the result instead."
            ),
        )

    async with async_session_maker() as session:
        record = TranscriptionRequest(
            id=uuid.uuid4(),
            api_key_label=api_key_label,
            client_host=request.client.host if request.client else None,
            mode=RequestMode.sync,
            status=JobStatus.processing,
            original_filename=file.filename,
            content_type=file.content_type,
            file_size_bytes=size,
            requested_language=language,
            model_name=settings.whisper_model,
            started_at=datetime.now(UTC),
        )
        session.add(record)
        await session.commit()
        record_id = record.id

    try:
        result = await whisper_engine.transcribe(file_path, language=language)
    except Exception as exc:
        async with async_session_maker() as session:
            row = await session.get(TranscriptionRequest, record_id)
            row.status = JobStatus.failed
            row.error_message = str(exc)[:2000]
            row.completed_at = datetime.now(UTC)
            await session.commit()
        logger.exception("Sync transcription failed for %s", record_id)
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    async with async_session_maker() as session:
        row = await session.get(TranscriptionRequest, record_id)
        row.status = JobStatus.completed
        row.detected_language = result.language
        row.audio_duration_seconds = result.duration
        row.processing_time_seconds = result.processing_time
        row.result_text = result.text
        row.completed_at = datetime.now(UTC)
        await session.commit()

    return OpenAITranscriptionResponse(
        text=result.text,
        language=result.language,
        duration=result.duration,
        segments=[TranscriptionSegment(**s) for s in result.segments],
    )


# --------------------------------------------------------------------------
# Async transcription for long files: submit -> poll
# --------------------------------------------------------------------------


@app.post(
    "/transcriptions",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def transcribe_async(
    request: Request,
    file: UploadFile,
    language: str | None = None,
    api_key_label: str = Depends(get_api_key_label),
):
    """Queue a long-running transcription job for asynchronous processing.

    Parameters
    ----------
    request : Request
        The incoming HTTP request.
    file : UploadFile
        Audio file uploaded for background transcription.
    language : str or None, default=None
        Optional source-language hint.
    api_key_label : str
        Label derived from the valid API key used to authorize the request.

    Returns
    -------
    JobSubmitResponse
        Metadata describing the queued job and polling location.

    """
    file_path, size = await _save_upload(file)
    job_id = uuid.uuid4()

    async with async_session_maker() as session:
        record = TranscriptionRequest(
            id=job_id,
            api_key_label=api_key_label,
            client_host=request.client.host if request.client else None,
            mode=RequestMode.async_,
            status=JobStatus.queued,
            original_filename=file.filename,
            content_type=file.content_type,
            file_size_bytes=size,
            requested_language=language,
            model_name=settings.whisper_model,
        )
        session.add(record)
        await session.commit()

    await job_queue.submit(Job(job_id=job_id, file_path=file_path, language=language))

    return JobSubmitResponse(
        job_id=job_id,
        status=JobStatus.queued.value,
        poll_url=f"/transcriptions/{job_id}",
    )


@app.get("/transcriptions/{job_id}", response_model=JobStatusResponse)
async def get_transcription_status(
    job_id: uuid.UUID, api_key_label: str = Depends(get_api_key_label)
):
    """Return the current processing status for a queued transcription job.

    Parameters
    ----------
    job_id : uuid.UUID
        Unique identifier for the transcription job.
    api_key_label : str
        Label derived from the valid API key used to authorize the request.

    Returns
    -------
    JobStatusResponse
        The current job status and any available result metadata.

    Raises
    ------
    HTTPException
        If the requested job does not exist.

    """
    async with async_session_maker() as session:
        row = await session.get(TranscriptionRequest, job_id)

    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=row.id,
        status=row.status.value,
        original_filename=row.original_filename,
        detected_language=row.detected_language,
        audio_duration_seconds=row.audio_duration_seconds,
        processing_time_seconds=row.processing_time_seconds,
        result_text=row.result_text,
        error_message=row.error_message,
        created_at=row.created_at.isoformat(),
        completed_at=row.completed_at.isoformat() if row.completed_at else None,
    )
