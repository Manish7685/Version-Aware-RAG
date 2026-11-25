"""Compare behavioural-probe outputs between two runs (RAG-M1 assumption A2: Linux = Windows).

Each input is the JSONL written by ``scripts/probes/rag_m1_probes.py``: one object per DuckDB
package with the keys ``duckdb``, ``python``, ``platform`` and ``results``.

Only ``duckdb`` (the key) and ``results`` are compared. ``python`` and ``platform`` are expected
to differ between machines and are reported for information only. The comparison is strict:
every expected package must be present exactly once, with exactly the same probe names and
result strings. Anything else is a mismatch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class ProbeRun(BaseModel):
    """One line of probe output (one DuckDB package)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duckdb: str
    python: str
    platform: str
    results: dict[str, str]


@dataclass(frozen=True)
class ProbeDifference:
    """One disagreement between the expected and the actual run."""

    duckdb: str
    probe: str
    expected: str | None
    actual: str | None


@dataclass
class ComparisonReport:
    """Outcome of comparing an actual run with the expected run."""

    differences: list[ProbeDifference] = field(default_factory=list)
    missing_versions: list[str] = field(default_factory=list)
    unexpected_versions: list[str] = field(default_factory=list)
    compared_versions: list[str] = field(default_factory=list)
    actual_platforms: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.differences or self.missing_versions or self.unexpected_versions)

    def render(self) -> str:
        """Plain-text summary suitable for CI logs."""
        lines = [f"compared versions: {', '.join(self.compared_versions) or '(none)'}"]
        for version, plat in sorted(self.actual_platforms.items()):
            lines.append(f"  actual {version}: {plat}")
        lines.extend(f"MISSING version in actual run: {v}" for v in self.missing_versions)
        lines.extend(f"UNEXPECTED version in actual run: {v}" for v in self.unexpected_versions)
        for d in self.differences:
            lines.append(
                f"DIFF duckdb={d.duckdb} probe={d.probe}\n"
                f"  expected: {d.expected}\n  actual:   {d.actual}"
            )
        lines.append("RESULT: MATCH" if self.ok else "RESULT: MISMATCH")
        return "\n".join(lines)


class DuplicateVersionError(ValueError):
    """The same DuckDB version appears twice in one file."""


def load_runs(path: Path) -> dict[str, ProbeRun]:
    """Load a probe JSONL file keyed by DuckDB version. Blank lines are ignored."""
    runs: dict[str, ProbeRun] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        run = ProbeRun.model_validate(json.loads(raw))
        if run.duckdb in runs:
            raise DuplicateVersionError(f"{path}:{lineno}: duckdb {run.duckdb} appears twice")
        runs[run.duckdb] = run
    return runs


def compare_runs(expected: dict[str, ProbeRun], actual: dict[str, ProbeRun]) -> ComparisonReport:
    """Strictly compare ``results`` for every version; order of output is deterministic."""
    report = ComparisonReport(
        missing_versions=sorted(expected.keys() - actual.keys()),
        unexpected_versions=sorted(actual.keys() - expected.keys()),
    )
    for version in sorted(expected.keys() & actual.keys()):
        report.compared_versions.append(version)
        report.actual_platforms[version] = (
            f"{actual[version].platform} / py{actual[version].python}"
        )
        exp, act = expected[version].results, actual[version].results
        for probe in sorted(exp.keys() | act.keys()):
            if exp.get(probe) != act.get(probe):
                report.differences.append(
                    ProbeDifference(version, probe, exp.get(probe), act.get(probe))
                )
    return report
