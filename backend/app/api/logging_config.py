"""Process-wide logging configuration.

Two output formats are supported:

- ``LOG_FORMAT=text`` (default) — human-readable single-line records,
  used in local development.
- ``LOG_FORMAT=json``           — one JSON object per line, used in
  hosted deployments where logs are shipped to a structured backend.

In both modes every record is augmented with a ``request_id`` field
pulled from :data:`app.api.request_id.request_id_var`. Records emitted
outside a request scope (startup, background workers) carry an empty
string so downstream log parsers see a stable field set.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.api.config import settings
from app.api.request_id import request_id_var


class RequestIdFilter(logging.Filter):
    """Inject ``request_id`` from the request-scoped ContextVar."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# Standard LogRecord attributes; anything else on a record is treated
# as a structured "extra" and merged into the JSON output.
_LOGRECORD_RESERVED: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter.

    Emits the fields requested by the deployment readiness audit
    (timestamp, level, logger, message, request_id, plus request
    metadata when present) without pulling in a third-party logging
    library.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", ""),
        }
        for attr in ("path", "method", "status_code"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        # Any other custom attributes attached via ``logger.x(..., extra=...)``
        # ride along under their own key so structured fields aren't lost.
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_RESERVED or key in payload:
                continue
            if key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except TypeError:
                value = repr(value)
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter that includes the request id when set."""

    default_fmt = (
        "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
    )

    def __init__(self) -> None:
        super().__init__(fmt=self.default_fmt)


_CONFIGURED = False


def configure_logging() -> None:
    """Install handlers/formatters on the root logger.

    Idempotent: safe to call from both :func:`create_app` and any
    ``__main__`` entry point. Honors ``LOG_FORMAT`` / ``LOG_LEVEL``
    from :class:`app.api.config.Settings`.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(RequestIdFilter())

    if settings.log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    # Clear any handlers a previous configure call (or pytest capture
    # plugin) installed so we don't double-log.
    for existing in list(root.handlers):
        if getattr(existing, "_tckdb_request_id_handler", False):
            root.removeHandler(existing)
    handler._tckdb_request_id_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    _CONFIGURED = True
