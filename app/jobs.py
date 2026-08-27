"""
Lightweight in-process job queue for long transcriptions.

Why not Celery/RQ: the GPU running the model is a single, non-shareable
resource tied to this pod, and the pod already has an async event loop.
An asyncio.Queue + a small pool of worker tasks (sized to gpu_concurrency,
normally 1) gives "submit now, poll later" semantics without standing up
a broker. Job state itself lives in Postgres (transcription_requests), so
status survives a pod restart even though any jobs that were mid-flight at
the moment of a restart are lost — Kubernetes will simply restart the pod
and clients can resubmit. If you outgrow a single GPU/pod, swap this for
Celery + Redis/RabbitMQ and keep the same DB row shape.
"""

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database import async_session_maker
from app.models import JobStatus, TranscriptionRequest
from app.whisper_engine import WhisperEngine

logger = logging.getLogger("fasterwhisper")


@dataclass
class Job:
    job_id: uuid.UUID
    file_path: str
    language: str | None


class JobQueue:
    def __init__(self, engine: WhisperEngine, concurrency: int = 1):
        self._engine = engine
        self._concurrency = concurrency
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    def qsize(self) -> int:
        return self._queue.qsize()

    async def submit(self, job: Job) -> None:
        await self._queue.put(job)

    def start(self) -> None:
        for i in range(self._concurrency):
            self._workers.append(asyncio.create_task(self._worker_loop(i)))
        logger.info("Started %d transcription worker(s)", self._concurrency)

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process(job, worker_id)
            except Exception:
                logger.exception(
                    "Worker %d failed processing job %s", worker_id, job.job_id
                )
            finally:
                self._queue.task_done()

    async def _process(self, job: Job, worker_id: int) -> None:
        async with async_session_maker() as session:
            row = await session.get(TranscriptionRequest, job.job_id)
            if row is None:
                logger.warning("Job %s vanished from DB before processing", job.job_id)
                return

            row.status = JobStatus.processing
            row.started_at = datetime.now(UTC)
            await session.commit()

        logger.info("Worker %d processing job %s", worker_id, job.job_id)
        try:
            result = await self._engine.transcribe(job.file_path, language=job.language)
            async with async_session_maker() as session:
                row = await session.get(TranscriptionRequest, job.job_id)
                row.status = JobStatus.completed
                row.detected_language = result.language
                row.audio_duration_seconds = result.duration
                row.processing_time_seconds = result.processing_time
                row.result_text = result.text
                row.completed_at = datetime.now(UTC)
                await session.commit()
            logger.info(
                "Job %s completed in %.1fs (audio duration %.1fs)",
                job.job_id,
                result.processing_time,
                result.duration,
            )
        except Exception as exc:
            logger.exception("Job %s failed", job.job_id)
            async with async_session_maker() as session:
                row = await session.get(TranscriptionRequest, job.job_id)
                row.status = JobStatus.failed
                row.error_message = str(exc)[:2000]
                row.completed_at = datetime.now(UTC)
                await session.commit()
        finally:
            # Clean up the temp upload regardless of outcome.
            try:
                os.remove(job.file_path)
            except OSError:
                pass
