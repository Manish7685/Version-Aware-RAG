from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from version_aware_rag.repro.probe_check import (
    DuplicateVersionError,
    ProbeRun,
    compare_runs,
    load_runs,
)


def _run(version: str, platform: str = "Linux-x", **results: str) -> ProbeRun:
    return ProbeRun(duckdb=version, python="3.12.16", platform=platform, results=results)


def _write(path: Path, *runs: ProbeRun) -> Path:
    path.write_text("\n".join(r.model_dump_json() for r in runs) + "\n", encoding="utf-8")
    return path


def test_identical_results_match_even_if_platform_differs() -> None:
    expected = {"1.5.6": _run("1.5.6", "Windows-11", P02="'TIMESTAMP'")}
    actual = {"1.5.6": _run("1.5.6", "Linux-6", P02="'TIMESTAMP'")}
    report = compare_runs(expected, actual)
    assert report.ok
    assert report.compared_versions == ["1.5.6"]
    assert "RESULT: MATCH" in report.render()


def test_changed_result_is_a_difference() -> None:
    report = compare_runs(
        {"2.0.1": _run("2.0.1", P04="ERROR x")}, {"2.0.1": _run("2.0.1", P04="None")}
    )
    assert not report.ok
    (diff,) = report.differences
    assert (diff.duckdb, diff.probe, diff.expected, diff.actual) == (
        "2.0.1",
        "P04",
        "ERROR x",
        "None",
    )
    assert "RESULT: MISMATCH" in report.render()


def test_missing_and_extra_probes_are_differences() -> None:
    report = compare_runs(
        {"1.4.5": _run("1.4.5", P01="'v'", P07="99")},
        {"1.4.5": _run("1.4.5", P01="'v'", P99="1")},
    )
    assert [(d.probe, d.expected, d.actual) for d in report.differences] == [
        ("P07", "99", None),
        ("P99", None, "1"),
    ]


def test_missing_and_unexpected_versions_fail() -> None:
    report = compare_runs(
        {"1.2.2": _run("1.2.2"), "1.3.2": _run("1.3.2")},
        {"1.3.2": _run("1.3.2"), "9.9.9": _run("9.9.9")},
    )
    assert report.missing_versions == ["1.2.2"]
    assert report.unexpected_versions == ["9.9.9"]
    assert not report.ok


def test_empty_actual_run_fails() -> None:
    assert not compare_runs({"1.2.2": _run("1.2.2")}, {}).ok


def test_load_runs_round_trip_and_blank_lines(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.jsonl", _run("1.1.3", P01="['v']"), _run("1.2.2", P01="'v'"))
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    runs = load_runs(path)
    assert sorted(runs) == ["1.1.3", "1.2.2"]
    assert runs["1.1.3"].results == {"P01": "['v']"}


def test_load_runs_rejects_duplicates(tmp_path: Path) -> None:
    path = _write(tmp_path / "r.jsonl", _run("1.2.2"), _run("1.2.2"))
    with pytest.raises(DuplicateVersionError):
        load_runs(path)


def test_load_runs_rejects_unknown_fields(tmp_path: Path) -> None:
    line = {"duckdb": "1", "python": "3", "platform": "p", "results": {}, "extra": 1}
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps(line), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_runs(path)


def test_expected_rag_m1_file_matches_itself(repo_root: Path) -> None:
    expected = load_runs(repo_root / "scripts/probes/expected/rag_m1_windows_probe_results.jsonl")
    assert sorted(expected) == ["1.1.3", "1.2.2", "1.3.2", "1.4.5", "1.5.6", "2.0.1"]
    assert all(len(r.results) == 10 for r in expected.values())
    assert compare_runs(expected, expected).ok
