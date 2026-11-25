"""The IR-3 probe suite (RAG-M4 S1b): definitions, probe hashes and the per-run control rule.

Definitions live in ``configs/probes.toml`` (``[[probes]]``). This module is pure: it loads and
validates the definitions, hashes each probe, and decides whether one run over all pinned
packages is valid. Running the packages is ``runner.py``.

**Control rule** (RAG-M3_IR3_report.md §3; owner decision D3). A run is valid only if, for every
control probe:
- its result is identical in every package of the run (status and value), and
- where an expected result is declared (C1, C2), it equals that expected result.

The comparison value is the raw observed value, except for a control with
``normalize = "package_version"`` (C2), whose comparison value replaces exact occurrences of the
package's own version identifier (``"v" + package version``, as ``version()`` returns it) with
``VERSION_PLACEHOLDER``. Nothing else is normalised: no other value, number, error or field. The
observations a valid run stores always keep the raw value.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

VERSION_PLACEHOLDER = "<duckdb-version>"


class ProbeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: Literal["sql", "python_api", "control"]
    language: Literal["sql", "python"]
    capture: Literal["first_value", "all_rows", "repr"]
    code: str
    rag_m1_name: str | None = None
    normalize: Literal["package_version"] | None = None
    expected_status: Literal["ok", "error"] | None = None
    expected_value: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.language == "python") != (self.capture == "repr"):
            raise ValueError(f"{self.id}: python probes capture repr; sql probes capture rows")
        if (self.expected_status is None) != (self.expected_value is None):
            raise ValueError(f"{self.id}: expected_status and expected_value are set together")
        if self.kind != "control" and (self.expected_status or self.normalize):
            raise ValueError(f"{self.id}: only controls declare expected results or normalise")
        if self.normalize and self.expected_value is None:
            raise ValueError(f"{self.id}: a normalised control needs a declared expected value")
        return self

    @property
    def executable(self) -> dict[str, str]:
        """What the executor runs: the exact probe code and how its result is captured."""
        return {"capture": self.capture, "code": self.code, "language": self.language}

    @property
    def sha256(self) -> str:
        """``probe_sha256``: sha256 of the canonical JSON of ``executable``."""
        text = json.dumps(self.executable, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProbeSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    probes: tuple[ProbeDefinition, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [p.id for p in self.probes]
        if len(set(ids)) != len(ids) or not ids:
            raise ValueError("probe ids must be unique and the suite must not be empty")
        if not any(p.kind == "control" for p in self.probes):
            raise ValueError("the suite needs at least one control probe")
        return self

    @property
    def controls(self) -> tuple[ProbeDefinition, ...]:
        return tuple(p for p in self.probes if p.kind == "control")


def load_suite(path: Path) -> ProbeSuite:
    """The ``[[probes]]`` of ``configs/probes.toml``; raises ``OSError`` or ``ValueError``."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ProbeSuite.model_validate({"probes": data.get("probes", [])})


@dataclass(frozen=True)
class ProbeResult:
    status: Literal["ok", "error"]
    value: str


def version_identifier(package_version: str) -> str:
    """The package's own version identifier, as DuckDB's ``version()`` returns it."""
    return f"v{package_version}"


def comparison_value(probe: ProbeDefinition, result: ProbeResult, package_version: str) -> str:
    """The value a control is compared on. Only C2-style controls replace the version identifier,
    and only standalone occurrences of that exact string (not inside a longer token such as
    ``v1.4.50`` or ``xv1.4.5``); every other value is compared exactly as observed."""
    if probe.normalize == "package_version":
        ident = re.escape(version_identifier(package_version))
        return re.sub(rf"(?<![\w.]){ident}(?![\w.])", VERSION_PLACEHOLDER, result.value)
    return result.value


@dataclass(frozen=True)
class ControlFailure:
    probe_id: str
    reason: str


def evaluate_controls(
    suite: ProbeSuite, results: Mapping[str, Mapping[str, ProbeResult]]
) -> list[ControlFailure]:
    """``results`` maps package version -> probe id -> result, for every package of one run.
    Returns every control failure; an empty list means the run is valid."""
    failures: list[ControlFailure] = []
    for probe in suite.controls:
        groups: dict[tuple[str, str], list[str]] = {}
        for version, by_probe in sorted(results.items()):
            result = by_probe[probe.id]
            key = (result.status, comparison_value(probe, result, version))
            groups.setdefault(key, []).append(version)
        if len(groups) > 1:
            parts = [f"{status} in {', '.join(vs)}" for (status, _), vs in groups.items()]
            failures.append(
                ControlFailure(
                    probe.id,
                    f"differs across packages ({len(groups)} distinct results: "
                    + "; ".join(parts)
                    + ")",
                )
            )
        if probe.expected_status is not None:
            expected = (probe.expected_status, probe.expected_value)
            wrong = sorted(v for key, vs in groups.items() if key != expected for v in vs)
            if wrong:
                failures.append(
                    ControlFailure(
                        probe.id, f"not the declared expected result in {', '.join(wrong)}"
                    )
                )
    return failures
