"""Open WebUI-backed API key authentication.

The service can accept personal API keys created in Open WebUI without managing
its own user store. A key is considered valid when it exists and has not
expired, and it is looked up via a short-lived in-memory cache.
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
    """A validated Open WebUI key record used for downstream authorization.

    Attributes
    ----------
    user_id : str
        The user identifier in Open WebUI.
    label : str
        A user-friendly label derived from the user email or name.

    """

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
    """Look up bearer tokens against the Open WebUI key database.

    Parameters
    ----------
    settings : Settings
        Runtime configuration containing the database connection details and
        cache TTL settings.

    Notes
    -----
    When unconfigured, the store acts as a no-op and returns ``None`` for all
    lookups.

    """

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
        """Return whether Open WebUI key validation is configured.

        Returns
        -------
        bool
            ``True`` when a database connection is configured.

        """
        return self._engine is not None

    async def close(self) -> None:
        """Dispose the underlying database engine, if present."""
        if self._engine is not None:
            await self._engine.dispose()

    async def ping(self) -> bool:
        """Check whether the Open WebUI database is reachable.

        Returns
        -------
        bool
            ``True`` when a lightweight database probe succeeds.

        Notes
        -----
        This method never raises; a database blip should not take down the whole
        service because static API keys remain viable without this dependency.

        """
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
        """Look up an API key in the cache or the Open WebUI database.

        Parameters
        ----------
        key : str
            Bearer token presented by the client.

        Returns
        -------
        OpenWebUIKeyRecord or None
            The user record associated with the key, or ``None`` if it is invalid.

        """
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
        """Query the backing database for a matching Open WebUI API key.

        Parameters
        ----------
        key : str
            The bearer token to validate.

        Returns
        -------
        OpenWebUIKeyRecord or None
            The matched key if it is still valid, otherwise ``None``.

        """
        assert self._engine is not None
        now_epoch = int(time.time())
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    _LOOKUP_QUERY, {"key": key, "now": now_epoch}
                )
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
        """Update the key's last-used timestamp when configured to do so.

        Parameters
        ----------
        key : str
            The key whose timestamp should be updated.
        now_epoch : int
            The current UNIX time in seconds.

        """
        assert self._engine is not None
        try:
            async with self._engine.begin() as conn:
                await conn.execute(_TOUCH_LAST_USED, {"now": now_epoch, "key": key})
        except Exception:
            # Non-fatal: auth already succeeded on the SELECT above. Most likely
            # cause is a read-only DB grant, which is the recommended setup.
            logger.warning(
                "Could not update last_used_at on Open WebUI api_key", exc_info=True
            )


def build_key_store(settings: Settings) -> OpenWebUIKeyStore:
    """Construct a configured Open WebUI key store.

    Parameters
    ----------
    settings : Settings
        Application settings to use when initializing the key store.

    Returns
    -------
    OpenWebUIKeyStore
        The initialized store, ready to validate bearer tokens.

    """
    return OpenWebUIKeyStore(settings)
