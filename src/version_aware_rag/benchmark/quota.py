"""Persistent request and token limits for Gemini drafting (RAG-M4 S3).

Every HTTP attempt, retries included, passes through ``QuotaLedger.acquire`` first. The ledger
keeps one event per attempt (time and input tokens) in a JSON file and enforces three rolling
windows:

- requests per minute and input tokens per minute (the last 60 seconds);
- requests per day, over the **last 24 hours**. Google resets daily quotas at midnight Pacific
  time; a rolling 24-hour window keeps every calendar day under the limit, whatever the time zone.

The file is re-read before every attempt and written atomically after it, so restarting the
application never resets the counters. An unreadable ledger stops the run (fail closed) instead of
starting from zero. ``ProcessLock`` keeps a second run from sending at the same time.

Input tokens are estimated conservatively before sending (``estimate_input_tokens``) and replaced
by the API's own count when it reports a larger one (``settle``).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

MINUTE = 60.0
DAY = 24 * 60 * 60.0
STALE_LOCK_SECONDS = 15 * 60.0


class QuotaError(RuntimeError):
    """The run must stop; messages never contain prompt text or the key."""


class DailyLimitReached(QuotaError):
    """The rolling 24-hour request limit is used up; the run can resume later."""


def estimate_input_tokens(prompt: str) -> int:
    """A deliberately high estimate: one token per 3 UTF-8 bytes, plus request overhead."""
    return math.ceil(len(prompt.encode("utf-8")) / 3) + 64


@dataclass(frozen=True)
class QuotaLimits:
    requests_per_minute: int
    input_tokens_per_minute: int
    requests_per_day: int


class QuotaLedger:
    def __init__(
        self,
        path: Path,
        limits: QuotaLimits,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat: Callable[[], None] | None = None,
    ) -> None:
        self.path, self.limits, self._clock, self._sleep = path, limits, clock, sleep
        self._heartbeat = heartbeat  # e.g. ProcessLock.refresh, called while waiting

    # -- persistence -------------------------------------------------------------------------

    def _load(self) -> list[list[float]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            events = [[float(t), float(n)] for t, n in data["events"]]
        except (OSError, ValueError, KeyError, TypeError):
            raise QuotaError(
                f"quota ledger {self.path.name} is unreadable; not resetting it"
            ) from None
        return events

    def _save(self, events: list[list[float]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({"events": events}, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        tmp.replace(self.path)

    def _recent(self) -> tuple[float, list[list[float]]]:
        now = self._clock()
        # Events "in the future" (a clock moved back) are kept: counting them is conservative.
        return now, [e for e in self._load() if now - e[0] < DAY]

    # -- public API ----------------------------------------------------------------------------

    def usage(self) -> dict[str, int]:
        now, events = self._recent()
        minute = [e for e in events if now - e[0] < MINUTE]
        return {
            "requests_last_minute": len(minute),
            "input_tokens_last_minute": int(sum(e[1] for e in minute)),
            "requests_last_24h": len(events),
        }

    def acquire(self, tokens: int) -> None:
        """Wait until one more request of ``tokens`` input tokens fits every window, then record
        it. Raises DailyLimitReached when the 24-hour limit is used up, and QuotaError when the
        request could never fit the per-minute token limit."""
        lim = self.limits
        if tokens > lim.input_tokens_per_minute:
            raise QuotaError("request exceeds the per-minute input-token limit on its own")
        while True:
            if self._heartbeat is not None:
                self._heartbeat()
            now, events = self._recent()
            if len(events) >= lim.requests_per_day:
                raise DailyLimitReached(
                    f"daily request limit of {lim.requests_per_day} (rolling 24 h) reached; "
                    "run again later to resume"
                )
            minute = [e for e in events if now - e[0] < MINUTE]
            used = sum(e[1] for e in minute)
            if (
                len(minute) < lim.requests_per_minute
                and used + tokens <= lim.input_tokens_per_minute
            ):
                events.append([now, float(tokens)])
                self._save(events)
                return
            oldest = min(e[0] for e in minute)
            self._sleep(max(MINUTE - (now - oldest), 0.0) + 0.05)

    def settle(self, actual_tokens: int | None) -> None:
        """Raise the last event's token count to the API-reported one, if that is higher."""
        if actual_tokens is None:
            return
        events = self._load()
        if events and actual_tokens > events[-1][1]:
            events[-1][1] = float(actual_tokens)
            self._save(events)


class ProcessLock:
    """One drafting run at a time. A lock untouched for STALE_LOCK_SECONDS is treated as left
    behind by a crashed run and replaced; ``refresh`` keeps a live run's lock fresh."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path, self._clock = path, clock

    def __enter__(self) -> ProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = self._clock() - self.path.stat().st_mtime
            if age < STALE_LOCK_SECONDS:
                raise QuotaError("another drafting run holds the lock; not sending") from None
            self.path.unlink()
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def refresh(self) -> None:
        os.utime(self.path)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.path.unlink(missing_ok=True)
