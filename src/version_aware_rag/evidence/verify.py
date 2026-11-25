"""Cross-record validation of the evidence (RAG-M4 S1a; D14 rules, owner decisions D1, D2, D4).

- **Claims against their source.** The source must be a corpus-manifest file of the stated kind,
  with the manifest's repository, commit, version and sha256. The cached source bytes must hash
  to that sha256, and ``span_text`` must occur at exactly ``[span_char_start, span_char_end)`` of
  the decoded source text (Python ``str`` indices, Unicode code points). For restricted sources
  the span text comes from the local sidecar (D1), so this verifies the sidecar against the hash
  and offsets: for restricted sources a mismatch is always an error, at every level. For
  unrestricted sources ``V1`` and ``V2`` claims must pass, and a ``V0`` draft that does not is a
  warning.
- **Observations against the pins (D2).** The package version must be pinned, with the pinned
  docs version, the wheel sha256 pinned for the observation's platform, and the pinned Python.
- **References.** A conflict's claim and observations must exist and share its ``doc_version``.
  A ``reproduced`` observation needs an independent match (D4): an observation of the same probe
  code and package on the other platform with the same status and value.

Findings name records by ID and fields by name; they never quote span text or observed values.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from version_aware_rag.corpus.config import ChunkingConfig, CorpusConfig
from version_aware_rag.corpus.manifest import CorpusManifest
from version_aware_rag.corpus.releases import body_path
from version_aware_rag.evidence.models import (
    RESTRICTED_SOURCE_KINDS,
    DocClaim,
    EvidenceConflict,
    RuntimeObservation,
)
from version_aware_rag.evidence.store import EvidenceSet, LoadProblem
from version_aware_rag.probes.pins import ProbePins

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class ClaimSource:
    """One citable source as recorded in the corpus manifest."""

    kind: str
    path: str
    repo: str
    commit: str | None
    sha256: str
    doc_version: str
    read_bytes: Callable[[], bytes | None]  # None when the cached copy is not available


def _reader(path: Path) -> Callable[[], bytes | None]:
    def read() -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    return read


def build_source_index(
    root: Path, cfg: CorpusConfig, chunking: ChunkingConfig, manifest: CorpusManifest
) -> dict[tuple[str, str], ClaimSource]:
    """Every citable source, keyed by (source_kind, source_path), from the committed manifest."""
    cache = cfg.cache_path(root)
    release_cache = root / cfg.release_notes.cache_dir
    guides = {g.path for g in chunking.migration_guides}
    index: dict[tuple[str, str], ClaimSource] = {}
    for f in manifest.files:
        kind = "migration_guide" if f.path in guides else "docs_page"
        index[(kind, f.path)] = ClaimSource(
            kind,
            f.path,
            manifest.source.repo,
            manifest.source.commit,
            f.sha256,
            f.version,
            _reader(cache / f.path),
        )
    for p in manifest.release_posts:
        index[("release_post", p.path)] = ClaimSource(
            "release_post",
            p.path,
            p.source_repo,
            p.source_commit,
            p.sha256,
            p.version,
            _reader(cache / p.path),
        )
    for r in manifest.release_notes.included:
        index[("github_release", r.tag)] = ClaimSource(
            "github_release",
            r.tag,
            cfg.release_notes.repo,
            None,
            r.body_sha256,
            r.version,
            _reader(body_path(release_cache, r.tag)),
        )
    return index


@dataclass
class EvidenceReport:
    findings: list[Finding] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)  # checks that could not be run
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, severity: Severity, code: str, message: str) -> None:
        self.findings.append(Finding(severity, code, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def exit_code(self) -> int:
        """0 valid; 1 invalid evidence; 2 valid so far, but some checks could not be run."""
        if self.errors:
            return 1
        return 2 if self.incomplete else 0


def verify_claim(
    claim: DocClaim, index: dict[tuple[str, str], ClaimSource], report: EvidenceReport
) -> None:
    cid = claim.claim_id
    src = index.get((claim.source_kind, claim.source_path))
    if src is None:
        report.add(
            "error",
            "E-source",
            f"{cid}: {claim.source_kind} {claim.source_path} is not in the corpus manifest",
        )
        return
    for name, have, want in (
        ("source_repo", claim.source_repo, src.repo),
        ("source_commit", claim.source_commit, src.commit),
        ("source_sha256", claim.source_sha256, src.sha256),
        ("doc_version", claim.doc_version, src.doc_version),
    ):
        if have != want:
            report.add("error", "E-manifest", f"{cid}: {name} differs from the corpus manifest")
    data = src.read_bytes()
    if data is None:
        report.incomplete.append(f"{cid}: cached source not available")
        return
    if hashlib.sha256(data).hexdigest() != src.sha256:
        report.add("error", "E-cache", f"{cid}: cached source does not match the manifest sha256")
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        report.add("error", "E-cache", f"{cid}: cached source is not UTF-8")
        return
    if text[claim.span_char_start : claim.span_char_end] != claim.span_text:
        located = (
            "occurs elsewhere in the source"
            if claim.span_text in text
            else "does not occur in the source"
        )
        # D1: for restricted sources this verifies the local sidecar text, so a mismatch is
        # always an error. Only an unrestricted V0 draft (D14: unchecked) is downgraded.
        draft = claim.verification_level == "V0"
        restricted = claim.source_kind in RESTRICTED_SOURCE_KINDS
        severity: Severity = "warning" if draft and not restricted else "error"
        report.add(severity, "E-span", f"{cid}: span_text is not at its offsets ({located})")


def verify_observation(obs: RuntimeObservation, pins: ProbePins, report: EvidenceReport) -> None:
    oid = obs.obs_id
    pin = pins.package(obs.package_version)
    if pin is None or pin.package != obs.package:
        report.add(
            "error", "E-pin", f"{oid}: {obs.package} {obs.package_version} is not a pinned package"
        )
        return
    if obs.applies_to_doc_version != pin.applies_to_doc_version:
        report.add(
            "error", "E-pin", f"{oid}: applies_to_doc_version differs from the pinned package"
        )
    pinned_wheel = getattr(pin.wheel_sha256, obs.platform_tag)
    if obs.wheel_sha256 != pinned_wheel:
        report.add(
            "error",
            "E-pin",
            f"{oid}: wheel_sha256 is not the {obs.platform_tag} wheel pinned for "
            f"{obs.package_version}",
        )
    if obs.python_version != pins.python_version:
        report.add(
            "error", "E-pin", f"{oid}: python_version is not the pinned {pins.python_version}"
        )


def verify_references(evidence: EvidenceSet, report: EvidenceReport) -> None:
    claims = {c.claim_id: c for c in evidence.claims}
    observations = {o.obs_id: o for o in evidence.observations}
    for conflict in evidence.conflicts:
        _verify_conflict(conflict, claims, observations, report)
    for obs in evidence.observations:
        if obs.verification_level != "reproduced":
            continue
        matches = [
            other
            for other in evidence.observations
            if other.platform_tag != obs.platform_tag
            and (other.probe_id, other.probe_sha256, other.package, other.package_version)
            == (obs.probe_id, obs.probe_sha256, obs.package, obs.package_version)
            and (other.observed_status, other.observed_value)
            == (obs.observed_status, obs.observed_value)
        ]
        if not matches:
            report.add(
                "error",
                "E-reproduced",
                f"{obs.obs_id}: reproduced, but no matching observation from an independent "
                "run on another platform",
            )


def _verify_conflict(
    conflict: EvidenceConflict,
    claims: dict[str, DocClaim],
    observations: dict[str, RuntimeObservation],
    report: EvidenceReport,
) -> None:
    cid = conflict.conflict_id
    claim = claims.get(conflict.claim_id)
    if claim is None:
        report.add("error", "E-ref", f"{cid}: claim {conflict.claim_id} does not exist")
    elif claim.doc_version != conflict.doc_version:
        report.add("error", "E-ref", f"{cid}: doc_version differs from claim {claim.claim_id}")
    for oid in conflict.obs_ids:
        obs = observations.get(oid)
        if obs is None:
            report.add("error", "E-ref", f"{cid}: observation {oid} does not exist")
        elif obs.applies_to_doc_version != conflict.doc_version:
            report.add("error", "E-ref", f"{cid}: observation {oid} is for another docs version")


def validate_evidence(
    evidence: EvidenceSet,
    problems: list[LoadProblem],
    index: dict[tuple[str, str], ClaimSource] | None,
    pins: ProbePins,
) -> EvidenceReport:
    """Every check on a loaded evidence set. ``index`` may be None only when there are no claims."""
    report = EvidenceReport()
    for p in problems:
        report.add("error", "E-load", str(p))
    if evidence.claims:
        if index is None:
            report.incomplete.append("claims present but the corpus manifest is not available")
        else:
            for claim in evidence.claims:
                verify_claim(claim, index, report)
    for obs in evidence.observations:
        verify_observation(obs, pins, report)
    verify_references(evidence, report)
    report.counts = {
        "doc_claims": len(evidence.claims),
        "runtime_observations": len(evidence.observations),
        "evidence_conflicts": len(evidence.conflicts),
    }
    return report
