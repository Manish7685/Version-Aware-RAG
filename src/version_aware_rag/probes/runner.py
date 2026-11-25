"""Run the IR-3 probe suite on every pinned package and record ``RuntimeObservation``s (RAG-M4
S1b; RAG-M1 manifest §5.1 method; owner decisions D2, D3, D4).

1. Each pinned DuckDB package gets its own venv with the pinned Python, installed from
   ``scripts/probes/requirements/duckdb-<version>.txt`` with ``--require-hashes``. The requirements
   file must pin this platform's wheel sha256 from ``configs/probes.toml`` (D2).
2. ``scripts/probes/run_probe_suite.py`` runs the whole suite in that venv (``python -I``).
3. The run is checked: every package reports its pinned version and Python, and every probe.
   Any mismatch is a harness error.
4. The control rule (``suite.evaluate_controls``) decides validity for the whole run.
5. Only a valid run produces observations (``auto_executed``, D4: never ``reproduced``). They
   are validated together with the existing evidence and written in one atomic step that
   replaces only this platform's earlier observations. An invalid run, or any harness error,
   writes nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from version_aware_rag.evidence.models import RuntimeObservation, observation_id
from version_aware_rag.evidence.store import load_evidence, write_evidence
from version_aware_rag.evidence.verify import EvidenceReport, verify_observation, verify_references
from version_aware_rag.probes.pins import PinnedPackage, ProbePins
from version_aware_rag.probes.suite import (
    ControlFailure,
    ProbeResult,
    ProbeSuite,
    evaluate_controls,
)

EXECUTOR = Path("scripts/probes/run_probe_suite.py")
REQUIREMENTS = Path("scripts/probes/requirements")


class HarnessError(RuntimeError):
    """The run could not be carried out or checked; nothing is recorded."""


@dataclass(frozen=True)
class PackageRun:
    package_version: str
    duckdb: str
    python: str
    platform: str
    results: dict[str, ProbeResult]


Executor = Callable[[PinnedPackage, ProbeSuite], PackageRun]


def platform_tag(platform_name: str | None = None) -> str:
    name = platform_name or sys.platform
    if name.startswith("win"):
        return "windows"
    if name.startswith("linux"):
        return "linux"
    raise HarnessError(f"no pinned wheel for platform {name}")


def parse_executor_output(stdout: str, package_version: str) -> PackageRun:
    try:
        data = json.loads(stdout)
        results = {
            pid: ProbeResult(status=r["status"], value=r["value"])
            for pid, r in data["results"].items()
        }
        if any(
            r.status not in ("ok", "error") or not isinstance(r.value, str)
            for r in results.values()
        ):
            raise ValueError("bad result")
        return PackageRun(
            package_version, data["duckdb"], data["python"], data["platform"], results
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HarnessError(
            f"{package_version}: malformed executor output ({type(exc).__name__})"
        ) from None


class VenvExecutor:
    """Runs the suite in one hash-pinned venv per package (the RAG-M1 manifest §5.1 method)."""

    def __init__(
        self, root: Path, uv: Sequence[str], envs_dir: Path, python_version: str, tag: str
    ) -> None:
        self.root, self.uv, self.envs_dir = root, list(uv), envs_dir
        self.python_version, self.tag = python_version, tag

    def _run(
        self, args: list[str], what: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", check=False, **kwargs
        )  # type: ignore[call-overload]
        if proc.returncode != 0:
            raise HarnessError(f"{what} failed (exit {proc.returncode})")
        return proc  # type: ignore[no-any-return]

    def __call__(self, pin: PinnedPackage, suite: ProbeSuite) -> PackageRun:
        req = self.root / REQUIREMENTS / f"duckdb-{pin.version}.txt"
        pinned_wheel = getattr(pin.wheel_sha256, self.tag)
        try:
            req_text = req.read_text(encoding="utf-8")
        except OSError as exc:
            raise HarnessError(
                f"{pin.version}: requirements file unreadable ({type(exc).__name__})"
            ) from None
        if f"--hash=sha256:{pinned_wheel}" not in req_text or not req_text.startswith(
            f"duckdb=={pin.version} "
        ):
            raise HarnessError(
                f"{pin.version}: requirements file does not pin the {self.tag} wheel (D2)"
            )
        env = self.envs_dir / f"duckdb-{pin.version}"
        python = env / ("Scripts/python.exe" if self.tag == "windows" else "bin/python")
        self._run(
            [*self.uv, "venv", "-q", "--allow-existing", "--python", self.python_version, str(env)],
            f"{pin.version}: venv",
        )
        self._run(
            [
                *self.uv,
                "pip",
                "install",
                "-q",
                "--python",
                str(python),
                "--require-hashes",
                "-r",
                str(req),
            ],
            f"{pin.version}: hash-verified install",
        )
        payload = json.dumps({"probes": [{"id": p.id, **p.executable} for p in suite.probes]})
        proc = self._run(
            [str(python), "-I", str(self.root / EXECUTOR)],
            f"{pin.version}: executor",
            input=payload,
        )
        return parse_executor_output(proc.stdout, pin.version)


@dataclass
class SuiteRun:
    runs: dict[str, PackageRun] = field(default_factory=dict)  # package version -> run
    failures: list[ControlFailure] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.failures


def run_suite(pins: ProbePins, suite: ProbeSuite, executor: Executor) -> SuiteRun:
    """Run every pinned package, check each run, then apply the control rule to the whole run."""
    result = SuiteRun()
    probe_ids = {p.id for p in suite.probes}
    for pin in pins.packages:
        run = executor(pin, suite)
        if run.duckdb != pin.version:
            raise HarnessError(f"{pin.version}: the venv reports duckdb {run.duckdb}")
        if run.python != pins.python_version:
            raise HarnessError(
                f"{pin.version}: the venv runs Python {run.python}, not {pins.python_version}"
            )
        if set(run.results) != probe_ids:
            raise HarnessError(
                f"{pin.version}: the executor did not return exactly the suite's probes"
            )
        result.runs[pin.version] = run
    result.failures = evaluate_controls(suite, {v: r.results for v, r in result.runs.items()})
    return result


def build_observations(
    pins: ProbePins,
    suite: ProbeSuite,
    run: SuiteRun,
    tag: str,
    runner_commit: str,
    run_at: datetime,
) -> list[RuntimeObservation]:
    """The observations of a **valid** run, with raw (never normalised) values."""
    if not run.valid:
        raise HarnessError("an invalid run has no observations")
    observations = []
    for pin in pins.packages:
        package_run = run.runs[pin.version]
        for probe in suite.probes:
            res = package_run.results[probe.id]
            observations.append(
                RuntimeObservation.model_validate(
                    {
                        "obs_id": observation_id(probe.id, pin.version, tag),
                        "probe_id": probe.id,
                        "probe_sha256": probe.sha256,
                        "probe_kind": probe.kind,
                        "package": pin.package,
                        "package_version": pin.version,
                        "wheel_sha256": getattr(pin.wheel_sha256, tag),
                        "applies_to_doc_version": pin.applies_to_doc_version,
                        "python_version": package_run.python,
                        "platform": package_run.platform,
                        "observed_status": res.status,
                        "observed_value": res.value,
                        "run_at": run_at,
                        "runner_commit": runner_commit,
                        "verification_level": "auto_executed",
                    }
                )
            )
    return observations


def record_observations(
    evidence_dir: Path, observations: list[RuntimeObservation], pins: ProbePins, tag: str
) -> int:
    """Validate the new observations with the existing evidence, then write everything in one
    step; this platform's earlier observations are replaced. Returns the observation count."""
    loaded = load_evidence(evidence_dir)
    if loaded.problems:
        raise HarnessError(
            f"the existing evidence has {len(loaded.problems)} problems; nothing written"
        )
    kept = [o for o in loaded.evidence.observations if o.platform_tag != tag]
    merged = sorted([*kept, *observations], key=lambda o: o.obs_id)
    report = EvidenceReport()
    for obs in merged:
        verify_observation(obs, pins, report)
    new_set = type(loaded.evidence)(
        loaded.evidence.claims, tuple(merged), loaded.evidence.conflicts
    )
    verify_references(new_set, report)
    if report.errors:
        raise HarnessError(
            f"the new observations fail validation ({len(report.errors)} errors); nothing written"
        )
    write_evidence(
        evidence_dir, claims=new_set.claims, observations=merged, conflicts=new_set.conflicts
    )
    return len(observations)
