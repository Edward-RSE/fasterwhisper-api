# Examples

Complete, working examples for both transcription flows. All examples assume a static API key or an Open WebUI key as
the bearer token — see [Authentication](01-authentication.md).

## curl — sync transcription

```bash
curl -X POST https://sotongpt.soton.ac.uk/whisper/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key" \
  -F "file=@clip.mp3"
```

## curl — async transcription with a polling loop

```bash
#!/bin/bash
set -euo pipefail

API_KEY="sk-your-api-key"
BASE="https://sotongpt.soton.ac.uk/whisper"
FILE="lecture.wav"

# Submit. curl streams the file from disk rather than loading it into memory,
# so this is fine even for very large files.
job_id=$(curl -sS --max-time 300 -X POST "$BASE/transcriptions" \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@$FILE" | jq -r '.job_id')

echo "Submitted job: $job_id"

# Poll until it reaches a terminal state. No fixed timeout here deliberately —
# a large file can legitimately take a long time.
while true; do
  status_json=$(curl -sS "$BASE/transcriptions/$job_id" -H "Authorization: Bearer $API_KEY")
  status=$(echo "$status_json" | jq -r '.status')
  echo "$(date +%T) — status: $status"

  case "$status" in
    completed)
      echo "$status_json" | jq -r '.result_text' > transcript.txt
      echo "Done — saved to transcript.txt"
      break
      ;;
    failed)
      echo "Failed:"; echo "$status_json" | jq -r '.error_message'
      exit 1
      ;;
  esac

  sleep 15
done
```

Requires [`jq`](https://jqlang.org/) for parsing the JSON responses.

## Python — sync transcription

```python
import requests

BASE = "https://sotongpt.soton.ac.uk/whisper"
HEADERS = {"Authorization": "Bearer sk-your-api-key"}

with open("clip.mp3", "rb") as f:
    r = requests.post(f"{BASE}/v1/audio/transcriptions", headers=HEADERS, files={"file": f})
r.raise_for_status()
print(r.json()["text"])
```

## Python — async transcription with polling

```python
import time
import requests

API_KEY = "sk-your-api-key"
BASE = "https://sotongpt.soton.ac.uk/whisper"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# requests streams multipart uploads from an open file handle — it does NOT
# read the whole file into memory first, so this scales to large files.
with open("lecture.wav", "rb") as f:
    r = requests.post(f"{BASE}/transcriptions", headers=HEADERS, files={"file": f}, timeout=300)
r.raise_for_status()
job_id = r.json()["job_id"]
print("submitted:", job_id)

while True:
    status = requests.get(f"{BASE}/transcriptions/{job_id}", headers=HEADERS, timeout=30).json()
    print(status["status"])

    if status["status"] == "completed":
        with open("transcript.txt", "w") as out:
            out.write(status["result_text"])
        print("done")
        break
    elif status["status"] == "failed":
        raise RuntimeError(status["error_message"])

    time.sleep(15)
```

## Python — checking service health before submitting work

Useful in a script that runs unattended (a cron job, a batch pipeline) where you'd rather fail fast with a clear message
than submit a job to a service that's still starting up:

```python
import requests

r = requests.get("https://sotongpt.soton.ac.uk/whisper/health")
health = r.json()

if health["status"] != "ok":
    raise RuntimeError(f"Service not ready: {health}")

print(f"Ready — running {health['model']}")
```
