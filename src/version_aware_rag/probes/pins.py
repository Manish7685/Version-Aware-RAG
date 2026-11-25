"""Pinned behavioural-test packages (``configs/probes.toml``; RAG-M4 owner decision D2).

Each package maps to the docs version it stands for (assumption A1) and to the sha256 of its cp312
wheel per platform, as pinned in the RAG-M1 version manifest §2. A ``RuntimeObservation`` must
carry the hash pinned for its package and platform.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

DEFAULT_PATH = Path("configs/probes.toml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WheelHashes(_Strict):
    windows: str
    linux: str

    @field_validator("windows", "linux")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("wheel sha256 must be 64 lowercase hex characters")
        return value


class PinnedPackage(_Strict):
    package: Literal["duckdb"]
    version: str
    applies_to_doc_version: Literal["1.2", "1.3", "1.4", "1.5", "2.0", "ref"]
    wheel_sha256: WheelHashes


class ProbePins(_Strict):
    python_version: str
    packages: tuple[PinnedPackage, ...]

    @model_validator(mode="after")
    def _unique(self) -> ProbePins:
        versions = [p.version for p in self.packages]
        doc_versions = [p.applies_to_doc_version for p in self.packages]
        if len(set(versions)) != len(versions) or len(set(doc_versions)) != len(doc_versions):
            raise ValueError("each package version and each docs version is pinned once")
        return self

    def package(self, version: str) -> PinnedPackage | None:
        return next((p for p in self.packages if p.version == version), None)


TOP_LEVEL_KEYS = frozenset({"python_version", "packages", "probes"})  # probes: the S1b suite


def load_pins(path: Path) -> ProbePins:
    """Load and validate the package pins of ``configs/probes.toml``; raises ``OSError`` or
    ``ValueError``. The probe suite in the same file is loaded by ``probes.suite``."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(data) - TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"unknown keys in {path.name}: {unknown}")
    return ProbePins.model_validate({k: v for k, v in data.items() if k != "probes"})
