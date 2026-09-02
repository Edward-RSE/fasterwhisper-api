# fasterwhisper API

**This was vibe coded by Claude.**

A FastAPI wrapper around [faster-whisper](https://github.com/SYSTRAN/faster-whisper), built for deployment on SotonGPT.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Readiness — checks model loaded + DB reachable. Returns 503 if either is down. |
| `GET /metrics` | Prometheus exposition endpoint for a Kubernetes PodMonitor or ServiceMonitor. No API key required. |
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

The Open WebUI lookup currently reads `api_key`/`user` directly through the shared `sotongpt` app
role (see `k8s/secret.yaml`) — that role can read/write everything in `openwebui`, not just
`api_key`. Worth narrowing later: point a dedicated read-only role at a single purpose-built view
instead of the raw tables, so this service's access is defined by that view's column list rather
than by whatever else `openwebui` happens to contain — see the comment in `app/openwebui_auth.py`
for the exact `CREATE VIEW`/`GRANT` statements. It only writes back to `api_key` (`last_used_at`)
if you explicitly set `OPENWEBUI_UPDATE_LAST_USED=true`, which needs an `UPDATE` grant too.

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
   - **API Base URL**: `http://fasterwhisper-api.sotongpt.svc/v1` (in-cluster) or your ingress URL
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
docker build -t <registry>/fasterwhisper-api:latest .
docker push <registry>/fasterwhisper-api:latest
```

The image installs dependencies with `uv sync --frozen` against the committed `uv.lock`, so the
build fails loudly if the lockfile and `pyproject.toml` have drifted, rather than silently
re-resolving to different versions than what you tested locally.

## Deploying to Kubernetes

Manifests in `k8s/` deploy into their own `fasterwhisper` namespace, with a dedicated CNPG cluster
(`postgres-cnpg`, inside that namespace) for this service's own metadata — separate instances,
storage, and backup schedule from `openwebui`'s cluster in `sotongpt`, so a problem in one can't
take down the other. `DATABASE_URL` is sourced directly from that cluster's own auto-generated
`postgres-cnpg-app` Secret rather than a hand-copied one — one less place for the connection string
to drift out of sync with the actual database. The only coupling to `openwebui`'s cluster is
`OPENWEBUI_DATABASE_URL`, a cross-namespace read for API key lookups.

1. Apply the namespace and database cluster, and wait for it to come up:
   ```bash
   kubectl apply -f k8s/namespace.yaml
   kubectl apply -f k8s/cluster.yaml
   kubectl -n fasterwhisper get cluster postgres-cnpg   # wait for status: Cluster in healthy state
   ```
2. Edit `k8s/secret.yaml` — set a real `API_KEYS_RAW` (only if you need static keys at all) and the
   real password for `OPENWEBUI_DATABASE_URL`. Don't commit real values; apply this one out-of-band
   or via a secrets manager. Note the comment in that file: this currently reuses the shared
   `sotongpt` app role, which can read/write all of `openwebui`, not just `api_key` — worth
   narrowing to a dedicated read-only role scoped to a single view if this needs tightening later.
3. Edit `k8s/deployment.yaml` — set `image:` to your pushed image/tag if you're not tracking
   `0.1.0`.
4. Confirm your cluster actually schedules GPU pods (device plugin installed, correct
   `nodeSelector`/tolerations for your GPU node pool — add these to the Deployment if needed).
5. Apply the rest:
   ```bash
   kubectl apply -f k8s/configmap.yaml
   kubectl apply -f k8s/secret.yaml
   kubectl apply -f k8s/pvc.yaml
   kubectl apply -f k8s/deployment.yaml
   kubectl apply -f k8s/service.yaml
   ```
6. Check rollout:
   ```bash
   kubectl -n fasterwhisper rollout status deploy/fasterwhisper
   kubectl -n fasterwhisper logs -f deploy/fasterwhisper
   ```
7. Check `/health` — its `openwebui_auth` field confirms the cross-namespace connection to Open
   WebUI's database is actually working (`"ok"`, `"unreachable"`, or `"disabled"` if you skipped
   it).

The Deployment uses `strategy: Recreate` rather than the default rolling update, since two pods
briefly holding the same GPU during a rollout isn't something you want with `nvidia.com/gpu: 1`.
Expect a short gap in availability on every deploy as a result — fine for an internal service,
worth knowing about.

No ingress/route is included since that depends on what you're already using in front of
`openwebui` — add a matching one pointing at the `fasterwhisper` Service if you need external
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
