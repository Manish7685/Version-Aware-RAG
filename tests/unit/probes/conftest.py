"""Fixtures for the IR-3 probe suite: the real config and a fake executor (no venvs, no network)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.probes.pins import PinnedPackage, ProbePins, load_pins
from version_aware_rag.probes.runner import PackageRun
from version_aware_rag.probes.suite import ProbeResult, ProbeSuite, load_suite

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs/probes.toml"
# A multi-line error message, as DuckDB raises them; it must survive byte for byte.
P09_ERROR = (
    "InvalidInputException: Invalid Input Error: More than one row returned by a subquery used "
    'as an expression - scalar subqueries can only return a single row.\n\nUse "SET '
    'scalar_subquery_error_on_multiple_rows=false" to revert to previous behavior of returning '
    "a random row."
)


@pytest.fixture
def pins() -> ProbePins:
    return load_pins(CONFIG)


@pytest.fixture
def suite() -> ProbeSuite:
    return load_suite(CONFIG)


def c2_value(version: str) -> str:
    v = f"v{version}"
    return f"[(1, 'a', None, '{v}'), (2, 'b', 7, '{v}')]"


def good_results(version: str, suite: ProbeSuite) -> dict[str, ProbeResult]:
    """A valid result set for one package; change probes vary by version, controls do not."""
    results = {p.id: ProbeResult("ok", f"'{p.id} on {version}'") for p in suite.probes}
    results["P09"] = ProbeResult("error", P09_ERROR)
    results["C1"] = ProbeResult("ok", "0")
    results["C2"] = ProbeResult("ok", c2_value(version))
    return results


def fake_executor(
    suite: ProbeSuite,
    edit: Callable[[str, dict[str, ProbeResult]], None] | None = None,
    run_over: dict[str, dict[str, Any]] | None = None,
) -> Callable[[PinnedPackage, ProbeSuite], PackageRun]:
    """An executor that returns ``good_results``, optionally edited per package version."""
    calls: list[str] = []

    def execute(pin: PinnedPackage, s: ProbeSuite) -> PackageRun:
        calls.append(pin.version)
        results = good_results(pin.version, suite)
        if edit is not None:
            edit(pin.version, results)
        fields: dict[str, Any] = {
            "package_version": pin.version,
            "duckdb": pin.version,
            "python": "3.12.16",
            "platform": "Windows-11-10.0.26200-SP0",
            "results": results,
        }
        fields.update((run_over or {}).get(pin.version, {}))
        return PackageRun(**fields)

    execute.calls = calls  # type: ignore[attr-defined]
    return execute
