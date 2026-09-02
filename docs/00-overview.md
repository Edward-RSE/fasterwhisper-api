# fasterwhisper API — documentation

This is the user-facing guide to calling the fasterwhisper transcription API. For deploying or operating the service
itself, see the top-level `README.md` instead — these docs are about *using* an already-running instance.

## Contents

1. [Authentication](01-authentication.md) — how to get and use an API key
2. [Health endpoints](02-health-endpoints.md) — `/health`, `/health/live`
3. [Synchronous transcription](03-synchronous-transcription.md) — `POST /v1/audio/transcriptions`
4. [Asynchronous transcription](04-asynchronous-transcription.md) — `POST /transcriptions` + `GET
   /transcriptions/{job_id}`
5. [Errors and limits](05-errors-and-limits.md) — status codes, size limits, what to do about them
6. [Examples](06-examples.md) — complete curl and Python scripts for both transcription flows

## Base URL

```
https://sotongpt.soton.ac.uk/whisper
```

## The two ways to transcribe

There are two transcription endpoints, and picking the right one matters — see [03](03-synchronous-transcription.md) and
[04](04-asynchronous-transcription.md) for the full detail, but the short version:

| | Use for | How it works |
| --- | --- | --- |
| **Sync** (`/v1/audio/transcriptions`) | Short clips, voice notes | Upload, wait, get the text back in the same request |
| **Async** (`/transcriptions`) | Long recordings (lectures, meetings) | Upload, get a `job_id` back immediately, poll for the result |

The sync endpoint has a hard file size ceiling and will reject anything over it with a `413` pointing you at the async
endpoint instead — so if you're not sure which to use, the error message will tell you.
