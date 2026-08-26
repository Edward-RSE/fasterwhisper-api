"""
Bearer-token auth with no user accounts of its own. A token is accepted if
it's either:

1. one of the static pre-shared keys in API_KEYS_RAW (service accounts,
   internal tooling — anything that isn't an Open WebUI user), or
2. a live, unexpired API key in Open WebUI's own `api_key` table — so any
   Open WebUI user's personal API key works here automatically, with no key
   management on this side at all.

Static keys are checked first (in-memory, no I/O) before falling back to the
Open WebUI database lookup.
"""

from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.openwebui_auth import OpenWebUIKeyStore, build_key_store

_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_openwebui_key_store() -> OpenWebUIKeyStore:
    return build_key_store(get_settings())


_bearer_dependency = Depends(_bearer_scheme)
_settings_dependency = Depends(get_settings)
_openwebui_key_store_dependency = Depends(get_openwebui_key_store)


async def get_api_key_label(
    credentials: HTTPAuthorizationCredentials | None = _bearer_dependency,
    settings: Settings = _settings_dependency,
    openwebui_keys: OpenWebUIKeyStore = _openwebui_key_store_dependency,
) -> str:
    # If neither auth source is configured, auth is effectively disabled (local dev).
    if not settings.api_keys and not openwebui_keys.enabled:
        return "auth-disabled"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    static_label = settings.api_keys.get(token)
    if static_label is not None:
        return static_label

    record = await openwebui_keys.lookup(token)
    if record is not None:
        return record.label

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
