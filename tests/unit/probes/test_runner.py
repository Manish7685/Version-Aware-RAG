"""Running the suite, building observations, and the all-or-nothing write (IR-3, D4)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.evidence.store import (
    OBSERVATIONS_FILE,
    load_evidence,
    write_evidence,
)
from version_aware_rag.probes import runner
from version_aware_rag.probes.pins import ProbePins
from version_aware_rag.probes.runner import (
    HarnessError,
    build_observations,
    parse_executor_output,
    platform_tag,
    record_observations,
    run_suite,
)
from version_aware_rag.probes.suite import ProbeResult, ProbeSuite

from .conftest import P09_ERROR, c2_value, fake_executor

COMMIT = "a" * 40
WHEN = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def test_a_valid_run_covers_all_six_packages(pins: ProbePins, suite: ProbeSuite) -> None:
    execute = fake_executor(suite)
    result = run_suite(pins, suite, execute)
    assert result.valid and execute.calls == [p.version for p in pins.packages]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"duckdb": "1.4.4"}, "reports duckdb 1.4.4"),
        ({"python": "3.12.15"}, "runs Python 3.12.15"),
    ],
)
def test_harness_mismatches_are_errors(
    pins: ProbePins, suite: ProbeSuite, over: dict[str, Any], message: str
) -> None:
    with pytest.raises(HarnessError, match=message):
        run_suite(pins, suite, fake_executor(suite, run_over={"1.4.5": over}))


def test_a_missing_or_extra_probe_is_a_harness_error(pins: ProbePins, suite: ProbeSuite) -> None:
    for edit in (lambda v, r: r.pop("P05"), lambda v, r: r.update(Z9=ProbeResult("ok", "1"))):
        with pytest.raises(HarnessError, match="exactly the suite's probes"):
            run_suite(pins, suite, fake_executor(suite, edit))


def test_observations_keep_raw_values_and_full_errors(pins: ProbePins, suite: ProbeSuite) -> None:
    result = run_suite(pins, suite, fake_executor(suite))
    obs = build_observations(pins, suite, result, "windows", COMMIT, WHEN)
    assert len(obs) == len(pins.packages) * len(suite.probes) == 6 * 28
    by_id = {o.obs_id: o for o in obs}
    c2 = by_id["ro-C2-1.4.5-windows"]
    assert c2.observed_value == c2_value("1.4.5")  # raw: the version string, not the placeholder
    p09 = by_id["ro-P09-2.0.1-windows"]
    assert (p09.observed_status, p09.observed_value) == ("error", P09_ERROR)  # multi-line, intact
    probe = next(p for p in suite.probes if p.id == "P02")
    p02 = by_id["ro-P02-1.1.3-windows"]
    assert p02.probe_sha256 == probe.sha256 and p02.probe_kind == "sql"
    assert p02.applies_to_doc_version == "ref" and p02.verification_level == "auto_executed"
    assert p02.wheel_sha256 == pins.package("1.1.3").wheel_sha256.windows  # type: ignore[union-attr]
    assert {o.run_at for o in obs} == {WHEN} and {o.runner_commit for o in obs} == {COMMIT}
    assert {o.verification_level for o in obs} == {"auto_executed"}  # D4: never reproduced here


def test_an_invalid_run_has_no_observations(pins: ProbePins, suite: ProbeSuite) -> None:
    def edit(version: str, results: dict[str, ProbeResult]) -> None:
        if version == "1.3.2":
            results["C1"] = ProbeResult("ok", "1")

    result = run_suite(pins, suite, fake_executor(suite, edit))
    assert not result.valid
    with pytest.raises(HarnessError, match="no observations"):
        build_observations(pins, suite, result, "windows", COMMIT, WHEN)


def test_record_writes_and_replaces_only_this_platform(
    pins: ProbePins, suite: ProbeSuite, tmp_path: Path
) -> None:
    result = run_suite(pins, suite, fake_executor(suite))
    windows = build_observations(pins, suite, result, "windows", COMMIT, WHEN)
    n = len(pins.packages) * len(suite.probes)
    assert record_observations(tmp_path, windows, pins, "windows") == n
    loaded = load_evidence(tmp_path)
    assert loaded.problems == [] and len(loaded.evidence.observations) == n
    linux_platform = {"platform": "Linux-6.8.0-x86_64-with-glibc2.39"}
    linux_over = {p.version: linux_platform for p in pins.packages}
    linux_run = run_suite(pins, suite, fake_executor(suite, run_over=linux_over))
    linux = build_observations(pins, suite, linux_run, "linux", COMMIT, WHEN)
    record_observations(tmp_path, linux, pins, "linux")
    assert len(load_evidence(tmp_path).evidence.observations) == 2 * n
    later = WHEN.replace(hour=13)
    record_observations(
        tmp_path, build_observations(pins, suite, result, "windows", COMMIT, later), pins, "windows"
    )
    observations = load_evidence(tmp_path).evidence.observations
    assert len(observations) == 2 * n  # windows replaced, linux kept
    assert {o.run_at for o in observations if o.platform_tag == "windows"} == {later}


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def test_record_refuses_when_existing_evidence_is_broken(
    pins: ProbePins, suite: ProbeSuite, tmp_path: Path
) -> None:
    result = run_suite(pins, suite, fake_executor(suite))
    obs = build_observations(pins, suite, result, "windows", COMMIT, WHEN)
    write_evidence(tmp_path, observations=obs[:1])
    (tmp_path / OBSERVATIONS_FILE).write_bytes(b"{oops\n")
    before = _snapshot(tmp_path)
    with pytest.raises(HarnessError, match="existing evidence has 1 problems"):
        record_observations(tmp_path, obs, pins, "windows")
    assert _snapshot(tmp_path) == before


def test_record_refuses_observations_that_fail_validation(
    pins: ProbePins, suite: ProbeSuite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run_suite(pins, suite, fake_executor(suite))
    obs = build_observations(pins, suite, result, "windows", COMMIT, WHEN)
    bad = [obs[0].model_copy(update={"python_version": "3.11.0"}), *obs[1:]]
    monkeypatch.setattr(runner, "write_evidence", _must_not_write)
    with pytest.raises(HarnessError, match="fail validation"):
        record_observations(tmp_path, bad, pins, "windows")
    assert _snapshot(tmp_path) == {}


def _must_not_write(*args: object, **kwargs: object) -> None:
    raise AssertionError("write_evidence must not be called")


def test_platform_tags() -> None:
    assert platform_tag("win32") == "windows" and platform_tag("linux") == "linux"
    with pytest.raises(HarnessError, match="no pinned wheel"):
        platform_tag("darwin")


@pytest.mark.parametrize(
    "stdout",
    [
        "not json",
        "[]",
        '{"duckdb": "1.4.5"}',
        '{"duckdb": "x", "python": "y", "platform": "z", '
        '"results": {"P01": {"status": "maybe", "value": "1"}}}',
    ],
)
def test_malformed_executor_output_is_a_harness_error(stdout: str) -> None:
    with pytest.raises(HarnessError, match="malformed executor output"):
        parse_executor_output(stdout, "1.4.5")


def test_venv_executor_requires_the_platform_wheel_pin_before_running(
    pins: ProbePins, suite: ProbeSuite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D2: the requirements file must pin this platform's wheel; checked before any subprocess."""

    def no_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("no subprocess may run when the requirements check fails")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    pin = pins.package("1.4.5")
    assert pin is not None
    executor = runner.VenvExecutor(tmp_path, ["uv"], tmp_path / "envs", "3.12.16", "windows")
    with pytest.raises(HarnessError, match="requirements file unreadable"):
        executor(pin, suite)
    req = tmp_path / "scripts/probes/requirements/duckdb-1.4.5.txt"
    req.parent.mkdir(parents=True)
    req.write_bytes(f"duckdb==1.4.5 --hash=sha256:{pin.wheel_sha256.linux}\n".encode())
    with pytest.raises(HarnessError, match="does not pin the windows wheel"):
        executor(pin, suite)
    req.write_bytes(f"duckdb==1.4.4 --hash=sha256:{pin.wheel_sha256.windows}\n".encode())
    with pytest.raises(HarnessError, match="does not pin the windows wheel"):
        executor(pin, suite)
