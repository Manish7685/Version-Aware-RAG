from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from version_aware_rag.settings import LogFormat, LogLevel, get_settings


def test_defaults() -> None:
    s = get_settings()
    assert s.log_level is LogLevel.INFO
    assert s.log_format is LogFormat.JSON
    assert s.data_dir == Path("data")
    assert s.results_dir == Path("results")


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VRAG_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("VRAG_LOG_FORMAT", "console")
    s = get_settings()
    assert s.log_level is LogLevel.DEBUG
    assert s.log_format is LogFormat.CONSOLE


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    # conftest chdirs into tmp_path, so this is the working directory's .env
    (tmp_path / ".env").write_text("VRAG_LOG_LEVEL=WARNING\n", encoding="utf-8")
    assert get_settings().log_level is LogLevel.WARNING


def test_env_example_is_valid(repo_root: Path, tmp_path: Path) -> None:
    """The committed .env.example (inline comments included) must load cleanly."""
    (tmp_path / ".env").write_text(
        (repo_root / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    s = get_settings()
    assert s.log_level is LogLevel.INFO
    assert s.log_format is LogFormat.JSON


def test_invalid_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VRAG_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        get_settings()


def test_settings_are_immutable() -> None:
    s = get_settings()
    with pytest.raises(ValidationError):
        s.log_level = LogLevel.DEBUG  # type: ignore[misc]
