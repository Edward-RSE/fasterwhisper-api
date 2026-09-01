# Errors and limits

## HTTP status codes you'll see

| Code | Meaning | What to do |
| --- | --- | --- |
| `200` | Success (sync transcription, health, job status poll) | — |
| `202` | Accepted (async job submitted) | Poll the returned `poll_url` |
| `401` | Missing or invalid API key | Check your `Authorization` header — see [Authentication](01-authentication.md) |
| `404` | Job ID not found | Double-check the `job_id` you're polling — it's a UUID, easy to typo or truncate |
| `413` | File too large | See the size limits below — the message tells you which limit you hit |
| `500` | Transcription failed unexpectedly | Retry; if it keeps happening, it's worth reporting to whoever runs this deployment along with the filename and roughly when it happened |
| `503` | Service not ready (only from `/health`) | The model is still loading or the database is unreachable — wait and check again |

Error responses have this shape:

```json
{"detail": "human-readable explanation"}
```

## Size limits

There are **two separate limits**, and it matters which one you hit:

| Limit | Default | Applies to | What happens if you exceed it |
| --- | --- | --- | --- |
| Sync upload limit | 25 MB | `POST /v1/audio/transcriptions` only | `413` — the error message specifically tells you to use `POST /transcriptions` (async) instead |
| Overall upload limit | 500 MB | Both endpoints | `413` — the file is simply too large for this service, full stop |

The actual configured values on a given deployment may differ from these defaults — the exact numbers are always echoed
back in the `413` error message itself, so you don't need to guess.

**Why the sync limit exists**: transcription can take a long time. The sync endpoint holds your HTTP connection open for
the entire duration, which becomes impractical well before you hit the overall 500 MB ceiling — so it has its own, much
lower limit specifically to push large files toward the async flow instead.

## A job stuck at "processing"

If this service restarts while your job is mid-transcription (a deployment rollout, a crash), that job's status stays at
`"processing"` indefinitely — it was abandoned, not silently retried. If a job has been `"processing"` for far longer
than the audio length would justify, don't keep waiting: resubmit it instead.

## Rate limits / concurrency

There's no per-key rate limit. There is, however, a hardware constraint worth knowing about: this service uses a single
 GPU, which can only usefully run one transcription at a time. If you and someone else both submit jobs around the same
time, the second one queues up and waits — reflected in `status: "queued"` for longer than usual, and in `/health`'s
`queue_depth` field. This isn't a bug or a rejection; the request has been accepted, it's just waiting its turn.

## Authentication caching

Covered in full in [Authentication](01-authentication.md), but worth repeating here: a newly-revoked Open WebUI key may
keep authenticating for up to ~30 seconds after revocation, due to a short-lived cache. This isn't an error state, just
a timing detail if you're testing key revocation.
