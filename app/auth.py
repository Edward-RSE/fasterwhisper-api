"""Authentication helpers for bearer-token validation.

A token is accepted if it matches either a static pre-shared key or a live,
non-expired Open WebUI personal API key. Static keys are checked first so the
common in-memory path remains fast and cheap.
"""

from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.openwebui_auth import OpenWebUIKeyStore, build_key_store

_bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache
def get_openwebui_key_store() -> OpenWebUIKeyStore:
    """Return the cached Open WebUI key store singleton.

    Returns
    -------
    OpenWebUIKeyStore
        The singleton helper used to validate Open WebUI-backed API keys.

    """
    return build_key_store(get_settings())


_bearer_dependency = Depends(_bearer_scheme)
_settings_dependency = Depends(get_settings)
_openwebui_key_store_dependency = Depends(get_openwebui_key_store)


async def get_api_key_label(
    credentials: HTTPAuthorizationCredentials | None = _bearer_dependency,
    settings: Settings = _settings_dependency,
    openwebui_keys: OpenWebUIKeyStore = _openwebui_key_store_dependency,
) -> str:
    """Resolve the label for a valid API key submitted on the request.

    Parameters
    ----------
    credentials : HTTPAuthorizationCredentials or None
        Bearer credentials provided by the client.
    settings : Settings
        Runtime configuration describing configured static and Open WebUI keys.
    openwebui_keys : OpenWebUIKeyStore
        Cache-backed lookup store for Open WebUI-derived API keys.

    Returns
    -------
    str
        The key label associated with the request, or ``auth-disabled`` when no
        authentication backend is configured.

    Raises
    ------
    HTTPException
        If the request lacks a valid bearer token.

    """
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
