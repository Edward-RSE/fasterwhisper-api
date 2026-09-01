# Synchronous transcription

`POST /v1/audio/transcriptions`

Upload an audio file and get the transcript back in the same HTTP response. Use this for short clips — voice notes,
brief recordings. For anything long, see [Asynchronous transcription](04-asynchronous-transcription.md) instead: this
endpoint has a size limit specifically so it can't be used to submit something that would take too long to transcribe.

**Requires authentication** — see [Authentication](01-authentication.md).

## Request

`multipart/form-data` with:

| Field | Required | Description |
| --- | --- | --- |
| `file` | Yes | The audio file. Most common formats work (mp3, wav, m4a, flac, ogg, webm — anything faster-whisper's underlying decoder supports). |
| `language` | No | An ISO 639-1 language code (e.g. `en`, `fr`, `de`) to skip language auto-detection. Omit it to let the model detect the language itself. |

```bash
curl -X POST https://sotongpt.soton.ac.uk/whisper/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key" \
  -F "file=@clip.mp3"
```

With an explicit language:

```bash
curl -X POST https://sotongpt.soton.ac.uk/whisper/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key" \
  -F "file=@clip.mp3" \
  -F "language=en"
```

## Response — `200 OK`

```json
{
  "text": "Right, so the main thing we need to sort out this week is...",
  "language": "en",
  "duration": 42.3,
  "segments": [
    {"start": 0.0, "end": 3.4, "text": "Right, so the main thing we need to sort out this week is..."}
  ]
}
```

| Field | Type | Description |
| --- | --- | --- |
| `text` | string | The full transcript, all segments joined together. |
| `language` | string \| null | The detected (or requested) language code. |
| `duration` | number \| null | Length of the audio, in seconds. |
| `segments` | array \| null | Timestamped chunks of the transcript — each has `start`, `end` (seconds), and `text`. |

## Size limit

There's a **separate, smaller** size cap on this endpoint than the service's general upload limit — see [Errors and
limits](05-errors-and-limits.md) for the exact numbers and what the rejection looks like. If your file is too large for
this endpoint, the error message tells you to use the async endpoint instead.

## Things worth knowing

- **The whole request blocks until transcription finishes.** For a multi-minute clip, expect the HTTP response itself to
  take a while. If your client has its own timeout, you may need to raise it for longer clips, or just use the async
  endpoint instead.
- **The `model` field some OpenAI-compatible clients send is ignored.**
