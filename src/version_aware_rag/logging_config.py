"""Structured logging on the standard library (no extra dependency).

Usage::

    configure_logging(settings.log_level, settings.log_format)
    log = logging.getLogger(__name__)
    log.info("corpus_built", extra={"version": "1.5", "chunks": 1234})

In JSON mode every record is one line: ``ts`` (UTC, ISO 8601), ``level``, ``logger``, ``event``,
then any fields passed through ``extra``. Logs go to stderr so stdout stays free for command
output (for example ``vrag info --json``).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from version_aware_rag.settings import LogFormat, LogLevel

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}

_HANDLER_NAME = "vrag-root-handler"


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in _RESERVED and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    """Render a record as a single JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # default=str keeps logging from ever raising on an unserialisable field.
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line, with extra fields appended as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        line = f"{ts} {record.levelname:<8} {record.name}: {record.getMessage()}"
        extras = _extra_fields(record)
        if extras:
            line += " " + " ".join(f"{k}={v!r}" for k, v in sorted(extras.items()))
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(
    level: LogLevel = LogLevel.INFO,
    fmt: LogFormat = LogFormat.JSON,
    stream: TextIO | None = None,
) -> None:
    """Install one handler on the root logger. Safe to call repeatedly (it replaces its own)."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_NAME:
            root.removeHandler(existing)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(JsonFormatter() if fmt is LogFormat.JSON else ConsoleFormatter())
    root.addHandler(handler)
    root.setLevel(level.value)
