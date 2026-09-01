# Asynchronous transcription

Two endpoints working together: submit a file, then poll for the result. Use this for anything that would take more than
a few seconds to transcribe — lecture recordings, long meetings, anything where you'd rather not hold an HTTP connection
open and wait.

**Both endpoints require authentication** — see [Authentication](01-authentication.md).

## Step 1 — submit: `POST /transcriptions`

### Request

`multipart/form-data`, same fields as the sync endpoint:

| Field | Required | Description |
| --- | --- | --- |
| `file` | Yes | The audio file. |
| `language` | No | ISO 639-1 language code, to skip auto-detection. |

```bash
curl -X POST https://sotongpt.soton.ac.uk/whisper/transcriptions \
  -H "Authorization: Bearer sk-your-api-key" \
  -F "file=@lecture.wav"
```

### Response — `202 Accepted`

Returned immediately — before transcription has even started, let alone finished:

```json
{
  "job_id": "b3f1a2c4-1234-4abc-9def-abcdef123456",
  "status": "queued",
  "poll_url": "/transcriptions/b3f1a2c4-1234-4abc-9def-abcdef123456"
}
```

Hang on to `job_id` — you'll need it for the next step.

## Step 2 — poll: `GET /transcriptions/{job_id}`

```bash
curl https://sotongpt.soton.ac.uk/whisper/transcriptions/b3f1a2c4-1234-4abc-9def-abcdef123456 \
  -H "Authorization: Bearer sk-your-api-key"
```

### Response — `200 OK`

The shape is the same regardless of status; fields fill in as the job progresses:

```json
{
  "job_id": "b3f1a2c4-1234-4abc-9def-abcdef123456",
  "status": "completed",
  "original_filename": "lecture.wav",
  "detected_language": "en",
  "audio_duration_seconds": 5412.7,
  "processing_time_seconds": 187.2,
  "result_text": "Good morning everyone, today we're going to cover...",
  "error_message": null,
  "created_at": "2026-08-28T09:15:03.120000+00:00",
  "completed_at": "2026-08-28T09:18:10.340000+00:00"
}
```

### `status` values

| Value | Meaning |
| --- | --- |
| `queued` | Submitted, waiting for a worker to pick it up. |
| `processing` | Actively being transcribed right now. |
| `completed` | Done — `result_text` is populated. |
| `failed` | Something went wrong — check `error_message`. |

### Fields

| Field | Type | Description |
| --- | --- | --- |
| `status` | string | One of the values above. |
| `original_filename` | string \| null | The filename you uploaded. |
| `detected_language` | string \| null | Populated once processing starts producing output; `null` while still `queued`. |
| `audio_duration_seconds` | number \| null | Length of the audio. Populated on completion. |
| `processing_time_seconds` | number \| null | How long transcription actually took (wall-clock, not audio length). Populated on completion. |
| `result_text` | string \| null | The transcript. Only populated when `status` is `completed`. |
| `error_message` | string \| null | Only populated when `status` is `failed`. |
| `created_at` | string | ISO 8601 timestamp of submission. |
| `completed_at` | string \| null | ISO 8601 timestamp of completion or failure; `null` while still in progress. |

## How to poll

There's no push notification or webhook — you have to ask. A reasonable approach: poll every 10–15 seconds until
`status` is `completed` or `failed`. There's no progress percentage reported in between
`queued`/`processing`/`completed` — a long `processing` state for a large file is expected, not a sign anything is
stuck. See [Examples](06-examples.md) for a complete polling loop in both bash and Python.

## Job IDs are not permanent

Currently, results are stored indefinitely — there's no automatic cleanup.
