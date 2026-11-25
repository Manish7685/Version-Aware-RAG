from __future__ import annotations

import io
import json
import logging
from datetime import datetime

from version_aware_rag.logging_config import configure_logging
from version_aware_rag.settings import LogFormat, LogLevel


def _capture(fmt: LogFormat, level: LogLevel = LogLevel.INFO) -> io.StringIO:
    stream = io.StringIO()
    configure_logging(level, fmt, stream=stream)
    return stream


def test_json_record_has_required_fields_and_extras() -> None:
    stream = _capture(LogFormat.JSON)
    logging.getLogger("vrag.test").info("corpus_built", extra={"version": "1.5", "chunks": 12})
    record = json.loads(stream.getvalue().strip())
    assert record["event"] == "corpus_built"
    assert record["level"] == "INFO"
    assert record["logger"] == "vrag.test"
    assert record["version"] == "1.5"
    assert record["chunks"] == 12
    ts = datetime.fromisoformat(record["ts"])
    assert ts.utcoffset() is not None
    assert ts.utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_json_is_one_line_per_record_and_handles_unserialisable_values() -> None:
    stream = _capture(LogFormat.JSON)
    log = logging.getLogger("vrag.test")
    log.info("first\nwith newline", extra={"obj": object()})
    log.info("second")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "first\nwith newline"
    assert json.loads(lines[0])["obj"].startswith("<object object")


def test_exception_is_included() -> None:
    stream = _capture(LogFormat.JSON)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("vrag.test").exception("failed")
    record = json.loads(stream.getvalue())
    assert "ValueError: boom" in record["exc_info"]


def test_level_filtering() -> None:
    stream = _capture(LogFormat.JSON, LogLevel.WARNING)
    log = logging.getLogger("vrag.test")
    log.info("hidden")
    log.warning("shown")
    events = [json.loads(line)["event"] for line in stream.getvalue().splitlines()]
    assert events == ["shown"]


def test_console_format() -> None:
    stream = _capture(LogFormat.CONSOLE)
    logging.getLogger("vrag.test").info("hello", extra={"b": 2, "a": "x"})
    line = stream.getvalue().strip()
    assert "INFO" in line
    assert "vrag.test: hello" in line
    assert line.endswith("a='x' b=2")


def test_reconfiguring_does_not_duplicate_handlers() -> None:
    first = _capture(LogFormat.JSON)
    second = _capture(LogFormat.JSON)
    logging.getLogger("vrag.test").info("once")
    assert first.getvalue() == ""
    assert len(second.getvalue().splitlines()) == 1
