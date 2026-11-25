from __future__ import annotations

import json
from pathlib import Path

import pytest

from version_aware_rag import __version__
from version_aware_rag.cli import main
from version_aware_rag.repro.probe_check import ProbeRun


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"vrag {__version__}"


def test_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_info_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["version"] == __version__
    assert info["settings"]["log_format"] == "json"


def test_log_level_override(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--log-level", "DEBUG", "--log-format", "console", "info", "--json"]) == 0
    settings = json.loads(capsys.readouterr().out)["settings"]
    assert settings == {**settings, "log_level": "DEBUG", "log_format": "console"}


def test_invalid_env_config_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VRAG_LOG_FORMAT", "xml")
    assert main(["info"]) == 2
    assert "invalid configuration" in capsys.readouterr().err


def _jsonl(path: Path, results: dict[str, str]) -> Path:
    run = ProbeRun(duckdb="1.5.6", python="3.12.16", platform="p", results=results)
    path.write_text(run.model_dump_json() + "\n", encoding="utf-8")
    return path


def test_probes_compare_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exp = _jsonl(tmp_path / "e.jsonl", {"P02": "'TIMESTAMP'"})
    same = _jsonl(tmp_path / "a.jsonl", {"P02": "'TIMESTAMP'"})
    diff = _jsonl(tmp_path / "b.jsonl", {"P02": "'DATE'"})
    assert main(["probes", "compare", "--expected", str(exp), "--actual", str(same)]) == 0
    assert "RESULT: MATCH" in capsys.readouterr().out
    assert main(["probes", "compare", "--expected", str(exp), "--actual", str(diff)]) == 1
    assert "RESULT: MISMATCH" in capsys.readouterr().out


def test_probes_compare_bad_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exp = _jsonl(tmp_path / "e.jsonl", {"P02": "x"})
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n", encoding="utf-8")
    assert main(["probes", "compare", "--expected", str(exp), "--actual", str(bad)]) == 2
    assert main(["probes", "compare", "--expected", str(exp), "--actual", "missing.jsonl"]) == 2
    err = capsys.readouterr().err
    assert err.count("probe_input_invalid") == 2
