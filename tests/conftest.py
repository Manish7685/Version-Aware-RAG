"""Shared fixtures."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Run every test without VRAG_* variables, away from any developer .env file,
    and restore the root logger afterwards."""
    for key in [k for k in os.environ if k.startswith("VRAG_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
