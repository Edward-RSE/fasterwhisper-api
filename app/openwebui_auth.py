"""
Lets Open WebUI's own personal API keys authenticate against this service —
no key management here, no shared secret to distribute. Reads Open WebUI's
api_key table directly (joined to "user" for an email/name label):

    CREATE TABLE api_key (
        id TEXT PRIMARY KEY,
        user_id TEXT REFERENCES "user"(id) ON DELETE CASCADE,
        key TEXT UNIQUE NOT NULL,
        data JSON,
        expires_at BIGINT,     -- epoch seconds, NULL = never expires
        last_used_at BIGINT,
        created_at BIGINT NOT NULL,
        updated_at BIGINT NOT NULL
    );

There's no `is_active`/`enabled` column — a key counts as active if it exists
and isn't expired. We treat that as the definition of "active" here too.

Currently connects via the shared app role on Open WebUI's database (see
k8s/secret.yaml), which can read/write everything in that database — not
just api_key. Worth narrowing later: grant a dedicated read-only role SELECT
on a single purpose-built view instead of the raw tables, e.g.

    CREATE VIEW fasterwhisper_key_lookup AS
    SELECT ak.key, ak.user_id, ak.expires_at, u.email, u.name
    FROM api_key ak
    LEFT JOIN "user" u ON u.id = ak.user_id;

    GRANT CONNECT ON DATABASE openwebui TO fasterwhisper_ro;
    GRANT USAGE ON SCHEMA public TO fasterwhisper_ro;
    GRANT SELECT ON fasterwhisper_key_lookup TO fasterwhisper_ro;

Postgres views run with the view owner's privileges by default, so a role
scoped to the view would never need direct access to api_key or "user" at
all — the view's column list becomes the entire contract, immune to whatever
else Open WebUI's schema does over time. If you switch to this, swap
_LOOKUP_QUERY below to select from the view instead of joining the tables.

This is a separate database (and, per the recommended setup, a separate CNPG
cluster) from this service's own `database_url`, which just holds
transcription_requests. Only a SELECT-level grant is required unless
openwebui_update_last_used is enabled.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings

logger = logging.getLogger("fasterwhisper")


@dataclass
class OpenWebUIKeyRecord:
    user_id: str
    label: str  # e.g. "openwebui:jane@example.com"


_LOOKUP_QUERY = text(
    """
    SELECT ak.user_id AS user_id, u.email AS email, u.name AS name
    FROM api_key ak
    LEFT JOIN "user" u ON u.id = ak.user_id
    WHERE ak.key = :key
      AND (ak.expires_at IS NULL OR ak.expires_at > :now)
    """
)

_TOUCH_LAST_USED = text("UPDATE api_key SET last_used_at = :now WHERE key = :key")


class OpenWebUIKeyStore:
    """Looks up bearer tokens against Open WebUI's api_key table, with a short
    in-memory TTL cache so steady traffic doesn't hit that database on every
    single request. A no-op (lookup always returns None) when unconfigured."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._engine = (
            create_async_engine(
                settings.openwebui_database_url_async,
                pool_size=settings.openwebui_db_pool_size,
                pool_pre_ping=True,
            )
            if settings.openwebui_database_url
            else None
        )
        # key -> (cache_expiry_monotonic, record_or_None). Negative results
        # (unknown/expired keys) are cached too, briefly, so a bad key doesn't
        # cause a DB round-trip on every retry.
        self._cache: dict[str, tuple[float, OpenWebUIKeyRecord | None]] = {}

    @property
    def enabled(self) -> bool:
        return self._engine is not None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def ping(self) -> bool:
        """Connectivity check for the health endpoint / startup log. Never
        raises — a database blip here shouldn't take the whole service down,
        since static API keys (if any) still work independently of this."""
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            logger.warning("Open WebUI database is not reachable", exc_info=True)
            return False

    async def lookup(self, key: str) -> OpenWebUIKeyRecord | None:
        if self._engine is None:
            return None

        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

        record = await self._query(key)
        self._cache[key] = (now + self._settings.openwebui_key_cache_seconds, record)
        return record

    async def _query(self, key: str) -> OpenWebUIKeyRecord | None:
        assert self._engine is not None
        now_epoch = int(time.time())
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(_LOOKUP_QUERY, {"key": key, "now": now_epoch})
                row = result.mappings().first()
        except Exception:
            logger.exception("Error querying Open WebUI database for API key lookup")
            return None

        if row is None or row["user_id"] is None:
            return None

        if self._settings.openwebui_update_last_used:
            await self._touch_last_used(key, now_epoch)

        label = row["email"] or row["name"] or row["user_id"]
        return OpenWebUIKeyRecord(user_id=row["user_id"], label=f"openwebui:{label}")

    async def _touch_last_used(self, key: str, now_epoch: int) -> None:
        assert self._engine is not None
        try:
            async with self._engine.begin() as conn:
                await conn.execute(_TOUCH_LAST_USED, {"now": now_epoch, "key": key})
        except Exception:
            # Non-fatal: auth already succeeded on the SELECT above. Most likely
            # cause is a read-only DB grant, which is the recommended setup.
            logger.warning("Could not update last_used_at on Open WebUI api_key", exc_info=True)


def build_key_store(settings: Settings) -> OpenWebUIKeyStore:
    return OpenWebUIKeyStore(settings)
