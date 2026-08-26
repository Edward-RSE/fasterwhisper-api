# CUDA + cuDNN runtime — faster-whisper's CTranslate2 backend needs cuBLAS/cuDNN,
# not the full CUDA devel toolchain, so the smaller "runtime" image is enough.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3.11 /usr/bin/python

WORKDIR /app

# Run as non-root.
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --create-home appuser

# Install uv itself, then use it to install deps from the lockfile — --frozen
# fails the build if pyproject.toml and uv.lock have drifted, which is what
# you want in CI rather than a silent re-resolve.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

# Model weights are downloaded to whisper_download_root on first run — mount a
# PVC there (see k8s/deployment.yaml) so a pod restart doesn't re-download a
# multi-GB model. This directory just needs to exist and be writable.
RUN mkdir -p /models /tmp/fastwhisper-uploads \
    && chown -R appuser:appuser /app /models /tmp/fastwhisper-uploads

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Single worker: one process holds the loaded model on the GPU. Scale by
# adding replicas (each with its own GPU) rather than uvicorn workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
