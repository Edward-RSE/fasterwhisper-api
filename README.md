# FastWhisper API

**This was vibe coded by Claude.**

A FastAPI wrapper around [faster-whisper](https://github.com/SYSTRAN/faster-whisper), built for deployment on SotonGPT.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Readiness — checks model loaded + DB reachable. Returns 503 if either is down. |
| `GET /health/live` | Liveness — just confirms the process is responsive (used by K8s so a slow DB doesn't cause a restart loop). |
| `POST /v1/audio/transcriptions` | Synchronous, **OpenAI-compatible** — same request/response shape as OpenAI's endpoint. Rejects files over `SYNC_MAX_UPLOAD_MB` (default 25MB) with a hint to use the async endpoint instead. |
| `POST /transcriptions` | Asynchronous — accepts the file, returns `202` with a `job_id` immediately, and processes it in the background. Use this for long recordings. |
| `GET /transcriptions/{job_id}` | Poll a job's status/result (`queued` → `processing` → `completed`/`failed`). |

All endpoints except the two health checks require `Authorization: Bearer <api-key>`. See
[Authentication](#authentication) below for what counts as a valid key.

## Authentication

There's no user management in this service — a bearer token is accepted if it matches either:

1. **A static pre-shared key** from `API_KEYS_RAW` (`key:label` pairs, comma-separated) — for
   service accounts or internal tooling that isn't an Open WebUI user.
2. **A live Open WebUI personal API key** — if `OPENWEBUI_DATABASE_URL` is set, any key a user has
   generated for themselves in Open WebUI (**Settings → Account → API keys**) works here too, with
   nothing to configure per-key. A key counts as valid if it exists in Open WebUI's `api_key` table
   and isn't expired (`expires_at` is `NULL` or in the future) — same rule Open WebUI itself uses.

Static keys are checked first (no I/O); the Open WebUI lookup only runs if the token doesn't match
one of those. A successful Open WebUI lookup is cached in memory for `OPENWEBUI_KEY_CACHE_SECONDS`
(default 30s) so steady traffic doesn't hit that database on every request — a revoked key can
still work here for up to that long. Both a positive and a negative lookup are cached, so retries
with a bad key don't repeatedly round-trip either.

The Open WebUI lookup is a plain read against its `api_key` table (joined to `user` for an
email/name to use as the label in `transcription_requests`/logs) — a read-only DB role is enough.
It only writes back to that table (`last_used_at`) if you explicitly set
`OPENWEBUI_UPDATE_LAST_USED=true`, which then needs an `UPDATE` grant too.

If neither `API_KEYS_RAW` nor `OPENWEBUI_DATABASE_URL` is configured, auth is disabled entirely —
fine for local development, not something to leave on anywhere reachable.

## Why two transcription endpoints

Short files can be transcribed and returned within a normal HTTP request/response cycle. Long
recordings (an hour-long meeting, say) can take minutes even on a GPU, and most HTTP clients —
including Open WebUI's own STT caller — have their own timeout that will give up long before
that. So:

- **Small/short → `/v1/audio/transcriptions`**: wait, get the text back directly.
- **Long → `/transcriptions`**: submit, get a `job_id` back in milliseconds, poll `/transcriptions/{job_id}` until `status` is `completed`.

The async path is backed by an in-process job queue sized to `GPU_CONCURRENCY` (default 1, since
one GPU can only usefully run one model inference at a time). Job state lives in Postgres, so a
`GET` on the status endpoint works from any pod if you ever run more than one replica behind the
same DB — though see the GPU-per-replica note in `k8s/02-deployment.yaml` first.

## Request tracking

Every request (sync or async) writes a row to the `transcription_requests` table: which API key
made it, the filename/size, timing, detected language, status, and (on success) the transcript
text. This is your audit trail / usage log — query it directly in Postgres, or extend `/health`
or add a small `/stats` endpoint later if you want it in-app.

Structured JSON logs (one line per request, with `request_id`, path, status code, duration) go to
stdout for collection by whatever your cluster uses for log aggregation.

## Long-term storage note

`result_text` currently stores the full transcript in Postgres indefinitely. If you're
transcribing a lot of long audio, consider a periodic job to null out `result_text` for rows older
than `JOB_RESULT_RETENTION_DAYS` while keeping the metadata — the setting is defined in
`app/config.py` but not yet enforced by a cleanup task, since that's the kind of thing best run as
a K8s CronJob against your actual retention needs rather than baked in.

## Wiring up Open WebUI

The sync endpoint deliberately mirrors OpenAI's `/v1/audio/transcriptions` request/response shape
and reads the key the same way (`Authorization: Bearer <key>`), and auth accepts Open WebUI's own
API keys directly (see [Authentication](#authentication)). In Open WebUI:

1. Have the user generate a personal API key: **Settings → Account → API keys**.
2. **Admin Settings → Audio → Speech-to-Text Engine → OpenAI**, then set:
   - **API Base URL**: `http://fastwhisper-api.sotongpt.svc/v1` (in-cluster) or your ingress URL
   - **API Key**: the key from step 1
   - **Model**: any value — this service ignores the model field and always uses whichever model
     it loaded (`WHISPER_MODEL`)

No key needs creating or distributing on this side — as long as `OPENWEBUI_DATABASE_URL` is set,
any Open WebUI user's key works the moment they generate it, and stops working the moment it's
revoked or expires there (modulo the cache window above).

Open WebUI's built-in STT client calls the sync endpoint, so this works out of the box for
shorter clips. For longer voice notes you'd need custom UI-side handling of the async job flow —
not something Open WebUI's STT setting supports natively today.

## Local development

Requires [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# edit .env — a local Postgres instance and (ideally) a GPU

uv sync          # creates .venv and installs exact versions from uv.lock
uv run uvicorn app.main:app --reload
```

Without a GPU, set `WHISPER_DEVICE=cpu` and `WHISPER_COMPUTE_TYPE=int8` in `.env` — much slower,
but fine for testing the API surface with a small model (`WHISPER_MODEL=small` or `base`).

To add a dependency: `uv add <package>` (updates `pyproject.toml` and `uv.lock` together — commit
both). To pick up upstream version bumps within the pins in `pyproject.toml`: `uv lock --upgrade`.

## Building the image

```bash
docker build -t <registry>/fastwhisper-api:latest .
docker push <registry>/fastwhisper-api:latest
```

The image installs dependencies with `uv sync --frozen` against the committed `uv.lock`, so the
build fails loudly if the lockfile and `pyproject.toml` have drifted, rather than silently
re-resolving to different versions than what you tested locally.

## Deploying to Kubernetes

Manifests in `k8s/` assume deployment into the existing `sotongpt` namespace, reusing the
`postgres-cnpg` cluster already running there (same pattern as the `openwebui` database) rather
than standing up a separate Postgres instance.

1. On `postgres-cnpg`, create a `fastwhisper` database/role for this service's own metadata (a
   CNPG `Database` custom resource, the same way `openwebui`'s database was provisioned).
2. On the same cluster, create a **read-only** role against the existing `openwebui` database for
   the API key lookup — the exact grants are commented in `k8s/00-config.yaml`.
3. Edit `k8s/00-config.yaml` — fill in real `API_KEYS_RAW` (if you need any static keys at all),
   the `fastwhisper` role's password in `DATABASE_URL`, and the read-only role's password in
   `OPENWEBUI_DATABASE_URL`. Don't commit real values; apply this one out-of-band or via a secrets
   manager.
4. Edit `k8s/02-deployment.yaml` — set `image:` to your pushed image.
5. Confirm your cluster actually schedules GPU pods (device plugin installed, correct
   `nodeSelector`/tolerations for your GPU node pool — add these to the Deployment if needed).
6. Apply:

   ```bash
   kubectl apply -f k8s/00-config.yaml
   kubectl apply -f k8s/01-pvc.yaml
   kubectl apply -f k8s/02-deployment.yaml
   ```

7. Check rollout:

   ```bash
   kubectl -n sotongpt rollout status deploy/fastwhisper-api
   kubectl -n sotongpt logs -f deploy/fastwhisper-api
   ```

8. Check `/health` — its `openwebui_auth` field confirms the read-only connection to Open WebUI's
   database is actually working (`"ok"`, `"unreachable"`, or `"disabled"` if you skipped it).

The Deployment uses `strategy: Recreate` rather than the default rolling update, since two pods
briefly holding the same GPU during a rollout isn't something you want with `nvidia.com/gpu: 1`.
Expect a short gap in availability on every deploy as a result — fine for an internal service,
worth knowing about.

No ingress/route is included since that depends on what you're already using in front of
`openwebui` — add a matching one pointing at the `fastwhisper-api` Service if you need external
access rather than in-cluster only.

## What's deliberately not here

- **User accounts / RBAC of its own** — no signup, roles, or permissions here; it either accepts a
  static pre-shared key or defers entirely to whether Open WebUI considers a key valid. Anyone
  with an Open WebUI account can transcribe — there's no separate allow-list layered on top.
- **DB migrations (Alembic)** — the single table is created via `create_all` on startup. Worth
  adding Alembic if the schema grows or you need to change it without downtime.
- **Multi-GPU/replica scheduling** — the job queue and `GPU_CONCURRENCY` setting assume one GPU
  per pod. Scaling to several GPUs means several replicas, each independently polling the shared
  queue table isn't implemented — right now each pod only knows about jobs submitted to it.
