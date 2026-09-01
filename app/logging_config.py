"""Logging configuration helpers for the service.

The application emits structured JSON logs in production by default so they can
be ingested by cluster log collectors with minimal post-processing.
"""

import logging
import sys

from app.config import Settings

try:
    from pythonjsonlogger import jsonlogger

    _HAS_JSON_LOGGER = True
except ImportError:
    _HAS_JSON_LOGGER = False


def configure_logging(settings: Settings) -> None:
    """Configure the root logger for the current runtime environment.

    Parameters
    ----------
    settings : Settings
        Application settings, including the chosen log level and formatting mode.

    """
    handler = logging.StreamHandler(sys.stdout)

    if settings.log_json and _HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # Quieten noisy third-party loggers a touch.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
