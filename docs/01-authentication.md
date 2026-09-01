# Authentication

Every endpoint except the two health checks ([02](02-health-endpoints.md)) requires a bearer token:

```
Authorization: Bearer <your-api-key>
```

There's no separate signup or account system for this API — a key is valid if it matches either of the following.

## Option 1: your SotonGPT API key

If you already have an account on SotonGPT, you can use your API key with no extra setup:

1. In Open WebUI: **Settings → Account → Secrets → API keys**.
2. Use it directly as the bearer token against this API.

It works immediately — there's nothing to register or approve. The key will stop working the moment it's revoked or
expires in SotonGPT.

## Option 2: a static API key

If you're calling this from a service which doesn't require a SotonGPT login, a static key will be provided instead.

## What happens if the key is missing or wrong

| Situation | Response |
| --- | --- |
| No `Authorization` header at all | `401 Unauthorized` — `{"detail": "Missing bearer token"}` |
| Header present but the key doesn't match anything | `401 Unauthorized` — `{"detail": "Invalid API key"}` |

## A note on timing

If you're using a SotonGPT API key and you just generated it, changed it, or had it revoked: this service caches the
result of each key check for a short window (30 seconds by default). In practice this means:

- A **brand-new** key works on its very first use — there's no cache entry yet, so it always checks live the first time.
- A **revoked** key may keep working for up to that 30-second window after revocation, before the next check catches up.

If you need to confirm whether a key change has actually taken effect, wait roughly 30 seconds and try again.
