"""
Structured (JSON) logging so logs are easy to query once collected by
whatever your cluster uses (Loki, ELK, CloudWatch, etc.). Falls back to
plain text if log_json is False, which is handy when running locally.
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
