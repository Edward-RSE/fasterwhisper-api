# Health endpoints

These two endpoints exist mainly for the service's own Kubernetes probes, but they're public (no
API key required) and useful for confirming the service is actually up before you go debugging
your own code.

## `GET /health`

A much simpler check — just confirms the process is up and responsive at all. Deliberately does
**not** check the database or model, so a brief database blip doesn't make this fail.

**No authentication required.**

**Response — always `200 OK` if the process is running:**

```json
{"status": "ok"}
```

**Response — `503 Service Unavailable`** if the model isn't loaded yet or the database is
unreachable — same body shape, `"status": "degraded"`.

```json
{"status": "degraded"}

If this endpoint doesn't respond at all, the service is down, not just degraded — that's a
different problem than anything `/health` would report.
