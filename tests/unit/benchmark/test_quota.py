"""The persistent quota ledger and the run lock (no network; a fake clock whose sleep advances)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from version_aware_rag.benchmark.quota import (
    STALE_LOCK_SECONDS,
    DailyLimitReached,
    ProcessLock,
    QuotaError,
    QuotaLedger,
    QuotaLimits,
    estimate_input_tokens,
)


class FakeTime:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _ledger(
    path: Path, ft: FakeTime, rpm: int = 10, tpm: int = 200_000, rpd: int = 400
) -> QuotaLedger:
    return QuotaLedger(path, QuotaLimits(rpm, tpm, rpd), clock=ft.clock, sleep=ft.sleep)


def test_requests_per_minute_are_enforced(tmp_path: Path) -> None:
    ft = FakeTime()
    ledger = _ledger(tmp_path / "l.json", ft)
    for _ in range(10):
        ledger.acquire(100)
    assert ft.slept == []
    ledger.acquire(100)  # the 11th within a minute waits for the oldest to leave the window
    assert sum(ft.slept) >= 60.0
    assert ledger.usage()["requests_last_minute"] <= 10


def test_input_tokens_per_minute_are_enforced(tmp_path: Path) -> None:
    ft = FakeTime()
    ledger = _ledger(tmp_path / "l.json", ft)
    ledger.acquire(150_000)
    ledger.acquire(40_000)
    assert ft.slept == []
    ledger.acquire(20_000)  # 210,000 would exceed 200,000 within the minute
    assert sum(ft.slept) >= 60.0
    assert ledger.usage()["input_tokens_last_minute"] <= 200_000


def test_a_request_larger_than_the_token_limit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(QuotaError, match="per-minute input-token limit"):
        _ledger(tmp_path / "l.json", FakeTime()).acquire(200_001)


def test_the_daily_limit_is_a_persistent_rolling_24_hours(tmp_path: Path) -> None:
    ft = FakeTime()
    path = tmp_path / "l.json"
    ledger = _ledger(path, ft, rpm=1000, rpd=3)
    for _ in range(3):
        ledger.acquire(10)
    with pytest.raises(DailyLimitReached):
        ledger.acquire(10)
    # A restart (a new object, same file) does not reset the counter.
    with pytest.raises(DailyLimitReached):
        _ledger(path, ft, rpm=1000, rpd=3).acquire(10)
    ft.now += 24 * 3600 - 1
    with pytest.raises(DailyLimitReached):
        _ledger(path, ft, rpm=1000, rpd=3).acquire(10)  # still inside 24 hours
    ft.now += 2
    _ledger(path, ft, rpm=1000, rpd=3).acquire(10)  # the oldest left the window
    assert _ledger(path, ft, rpm=1000, rpd=3).usage()["requests_last_24h"] == 1


def test_an_unreadable_ledger_stops_instead_of_resetting(tmp_path: Path) -> None:
    path = tmp_path / "l.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(QuotaError, match="unreadable"):
        _ledger(path, FakeTime()).acquire(10)
    assert path.read_text(encoding="utf-8") == "{not json"  # untouched


def test_settle_raises_but_never_lowers_the_recorded_tokens(tmp_path: Path) -> None:
    ft = FakeTime()
    path = tmp_path / "l.json"
    ledger = _ledger(path, ft)
    ledger.acquire(1000)
    ledger.settle(400)
    assert ledger.usage()["input_tokens_last_minute"] == 1000
    ledger.settle(5000)
    ledger.settle(None)
    assert ledger.usage()["input_tokens_last_minute"] == 5000
    assert json.loads(path.read_text(encoding="utf-8"))["events"][-1][1] == 5000


def test_the_heartbeat_runs_while_waiting(tmp_path: Path) -> None:
    ft = FakeTime()
    beats: list[int] = []
    ledger = QuotaLedger(
        tmp_path / "l.json",
        QuotaLimits(1, 1000, 10),
        clock=ft.clock,
        sleep=ft.sleep,
        heartbeat=lambda: beats.append(1),
    )
    ledger.acquire(10)
    ledger.acquire(10)
    assert len(beats) >= 3  # once per check, including after the wait


def test_the_token_estimate_is_conservative() -> None:
    text = "word " * 1000  # about 1,000-1,250 real tokens
    assert estimate_input_tokens(text) >= len(text) // 3
    assert estimate_input_tokens("") == 64


def test_only_one_run_holds_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    # The first lock is held while the second must fail; then the first is released.
    with (
        ProcessLock(path),
        pytest.raises(QuotaError, match="another drafting run"),
        ProcessLock(path),
    ):
        pass
    assert not path.exists()  # released


def test_a_stale_lock_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text("12345", encoding="utf-8")
    old = path.stat().st_mtime - STALE_LOCK_SECONDS - 1
    os.utime(path, (old, old))
    with ProcessLock(path):
        assert path.read_text(encoding="utf-8") == str(os.getpid())
