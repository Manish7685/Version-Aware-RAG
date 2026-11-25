"""``vrag probes run``: exit codes 0/1/2, and nothing is ever written unless the run is valid."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.cli import main
from version_aware_rag.evidence.store import load_evidence
from version_aware_rag.probes import commands, runner
from version_aware_rag.probes.runner import HarnessError
from version_aware_rag.probes.suite import ProbeResult

from .conftest import CONFIG, fake_executor

COMMIT = "b" * 40


def _snapshot(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    shutil.copy2(CONFIG, root / "configs/probes.toml")
    monkeypatch.setattr(commands, "platform_tag", lambda: "windows")
    return root


def _git(dirty: bool) -> Any:
    def git(root: Path, *args: str) -> str:
        return COMMIT + "\n" if args[0] == "rev-parse" else (" M x\n" if dirty else "")

    return git


def _use(monkeypatch: pytest.MonkeyPatch, executor: Any, *, dirty: bool = False) -> None:
    monkeypatch.setattr(commands, "VenvExecutor", lambda *a, **k: executor)
    monkeypatch.setattr(commands, "_git", _git(dirty))


def _run(capsys: pytest.CaptureFixture[str], root: Path, *extra: str) -> tuple[int, str]:
    code = main(["probes", "run", "--root", str(root), *extra])
    return code, capsys.readouterr().out


def test_a_valid_run_records_one_observation_per_probe_and_package(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from version_aware_rag.probes.suite import load_suite

    _use(monkeypatch, fake_executor(load_suite(CONFIG)))
    code, out = _run(capsys, repo)
    n = 6 * len(load_suite(CONFIG).probes)
    assert code == 0 and f"RESULT: RECORDED ({n} observations, platform windows" in out
    assert "controls passed: P09, C1, C2 identical in 6 packages" in out
    loaded = load_evidence(repo / "data/evidence")
    assert loaded.problems == [] and len(loaded.evidence.observations) == n


def test_an_invalid_run_writes_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from version_aware_rag.probes.suite import load_suite

    def edit(version: str, results: dict[str, ProbeResult]) -> None:
        if version == "2.0.1":
            results["P09"] = ProbeResult("error", "InvalidInputException: a different message")

    _use(monkeypatch, fake_executor(load_suite(CONFIG), edit))
    monkeypatch.setattr(runner, "write_evidence", _must_not_write)
    before = _snapshot(repo)
    code, out = _run(capsys, repo)
    assert code == 1 and "INVALID [P09] differs across packages" in out
    assert "RESULT: INVALID RUN (1 control failures; nothing written)" in out
    assert _snapshot(repo) == before and not (repo / "data/evidence").exists()


def test_an_invalid_run_leaves_existing_evidence_byte_identical(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from version_aware_rag.probes.suite import load_suite

    suite = load_suite(CONFIG)
    _use(monkeypatch, fake_executor(suite))
    assert _run(capsys, repo)[0] == 0
    evidence = _snapshot(repo / "data/evidence")

    def edit(version: str, results: dict[str, ProbeResult]) -> None:
        results["C2"] = ProbeResult(
            "ok", f"[(1, 'a', None, 'v{version}'), (2, 'b', 8, 'v{version}')]"
        )

    _use(monkeypatch, fake_executor(suite, edit))
    code, out = _run(capsys, repo)
    assert code == 1 and "INVALID [C2] not the declared expected result" in out
    assert _snapshot(repo / "data/evidence") == evidence


def _must_not_write(*args: object, **kwargs: object) -> None:
    raise AssertionError("write_evidence must not be called")


def test_harness_failures_write_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(pin: Any, suite: Any) -> Any:
        raise HarnessError(f"{pin.version}: hash-verified install failed (exit 1)")

    _use(monkeypatch, broken)
    monkeypatch.setattr(runner, "write_evidence", _must_not_write)
    code, out = _run(capsys, repo)
    assert code == 2 and "RESULT: INCOMPLETE (1.1.3: hash-verified install failed" in out
    assert not (repo / "data/evidence").exists()


def test_a_dirty_tree_refuses_repository_evidence(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from version_aware_rag.probes.suite import load_suite

    _use(monkeypatch, fake_executor(load_suite(CONFIG)), dirty=True)
    code, out = _run(capsys, repo)
    assert code == 2 and "uncommitted changes" in out
    code, out = _run(capsys, repo, "--allow-dirty")
    assert code == 2 and "may not write evidence inside the repository" in out
    assert not (repo / "data/evidence").exists()
    outside = tmp_path / "review-evidence"
    code, out = _run(capsys, repo, "--allow-dirty", "--evidence-dir", str(outside))
    assert (
        code == 0
        and "(dirty)" in out
        and len(load_evidence(outside).evidence.observations) == 6 * len(load_suite(CONFIG).probes)
    )


def test_an_unreadable_config_is_incomplete(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repo / "configs/probes.toml").write_bytes(b"not = [toml")
    code, out = _run(capsys, repo)
    assert code == 2 and "RESULT: INCOMPLETE (TOMLDecodeError; nothing written)" in out
