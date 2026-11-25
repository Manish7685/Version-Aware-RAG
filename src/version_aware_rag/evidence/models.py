"""IR-2 evidence records (RAG-M4 S1a): ``DocClaim``, ``RuntimeObservation``, ``EvidenceConflict``.

The fields, types and enums are exactly the field-level schema approved as D14 in
``docs/design/RAG-M1_design.md`` §2.1. The validators here check what a single record can prove
on its own (ID formats and derivations, enum consistency, owner-only review fields). Checks that
need other data (the corpus manifest and cached sources, the wheel pins, the other records) are in
``verify.py``.

Rules from §2.1 that these models enforce:
- a documentation claim and a runtime observation are separate records; an ``EvidenceConflict``
  only links them and has no field that could change either one;
- only the owner can set a conflict to ``confirmed`` or ``dismissed`` (see ``review_conflict``);
- a ``V2`` claim records who verified it and when.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator

DocVersion = Literal["1.2", "1.3", "1.4", "1.5", "2.0"]
ClaimSourceKind = Literal["docs_page", "release_post", "migration_guide", "github_release"]
ClaimLevel = Literal["V0", "V1", "V2"]
ClaimCreator = Literal["owner", "script", "gemini_draft"]
ProbeKind = Literal["sql", "python_api", "control"]
ObservationDocVersion = Literal["1.2", "1.3", "1.4", "1.5", "2.0", "ref"]
ObservedStatus = Literal["ok", "error"]
ObservationLevel = Literal["auto_executed", "reproduced"]
ConflictDetector = Literal["script", "owner"]
ConflictStatus = Literal["suspected", "confirmed", "dismissed"]

# Source kinds whose text must never enter Git or a CI artifact (owner decisions Q8 and D22;
# RAG-M4 D1): their span text is stored only in the git-ignored local sidecar.
RESTRICTED_SOURCE_KINDS: frozenset[str] = frozenset({"release_post", "github_release"})
CHANGE_SOURCE_KINDS: frozenset[str] = frozenset(
    {"release_post", "migration_guide", "github_release"}
)
# The single reviewer of this project. Only this identity may confirm or dismiss a conflict.
OWNER = "owner"
# The obs_id platform tag: the platforms that have a pinned wheel (RAG-M1 manifest §2).
PLATFORM_TAGS: frozenset[str] = frozenset({"windows", "linux"})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SEMVER = re.compile(r"\d+\.\d+\.\d+")
_PROBE_ID = re.compile(r"[A-Z][A-Za-z0-9_]*")  # no "-": it separates the parts of obs_id
_OBS_ID = re.compile(r"ro-(?P<probe>[A-Za-z0-9_]+)-(?P<version>\d+\.\d+\.\d+)-(?P<platform>[a-z]+)")


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def doc_claim_id(source_path: str, span_text: str) -> str:
    """``dc-<sha256(source_path + span_text)[:12]>`` (D14)."""
    return f"dc-{_sha12(source_path + span_text)}"


def conflict_id(claim_id: str, obs_ids: list[str]) -> str:
    """``ec-<sha256(claim_id, then each obs_id, newline-separated)[:12]>``. D14 only says "Stable
    ID"; this derivation makes it stable and unique per claim and observation set."""
    return f"ec-{_sha12(chr(10).join([claim_id, *obs_ids]))}"


def observation_id(probe_id: str, package_version: str, platform_tag: str) -> str:
    """``ro-<probe_id>-<package_version>-<platform_tag>`` (D14)."""
    return f"ro-{probe_id}-{package_version}-{platform_tag}"


def _require(pattern: re.Pattern[str], value: str | None, what: str) -> None:
    if value is not None and not pattern.fullmatch(value):
        raise ValueError(f"{what} has the wrong format")


class DocClaim(_Record):
    """What a documentation or change-source passage says (D14)."""

    claim_id: str
    source_kind: ClaimSourceKind
    source_repo: str
    source_commit: str | None
    source_path: str
    source_sha256: str
    source_url: str
    heading_path: list[str]
    doc_version: DocVersion
    introduced_in: str | None
    span_text: str
    span_char_start: int
    span_char_end: int
    claim_summary: str
    claimed_value: str | None
    verification_level: ClaimLevel
    verified_by: str | None
    verified_at: AwareDatetime | None
    created_by: ClaimCreator

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require(_SHA256, self.source_sha256, "source_sha256")
        _require(_COMMIT, self.source_commit, "source_commit")
        _require(_SEMVER, self.introduced_in, "introduced_in")
        if self.source_kind == "github_release":
            if self.source_commit is not None:
                raise ValueError("source_commit must be null for github_release")
        elif self.source_commit is None:
            raise ValueError("source_commit is required (null only for github_release)")
        if self.introduced_in is not None and self.source_kind not in CHANGE_SOURCE_KINDS:
            raise ValueError("introduced_in is only for change sources")
        if not self.source_url.startswith("https://"):
            raise ValueError("source_url must be an https URL")
        if not self.span_text:
            raise ValueError("span_text must not be empty")
        if not 0 <= self.span_char_start < self.span_char_end:
            raise ValueError("span offsets must satisfy 0 <= start < end")
        if self.span_char_end - self.span_char_start != len(self.span_text):
            raise ValueError("span offsets do not match the length of span_text")
        if not self.claim_summary.strip():
            raise ValueError("claim_summary must not be empty")
        if self.claim_id != doc_claim_id(self.source_path, self.span_text):
            raise ValueError("claim_id is not dc-<sha256(source_path + span_text)[:12]>")
        verified = (self.verified_by is not None, self.verified_at is not None)
        if self.verification_level == "V2" and verified != (True, True):
            raise ValueError("a V2 claim needs verified_by and verified_at")
        if self.verification_level == "V0" and verified != (False, False):
            raise ValueError("a V0 (draft) claim has no verified_by or verified_at")
        if self.verified_by is not None and self.verified_at is None:
            raise ValueError("verified_by and verified_at are set together")
        return self


class RuntimeObservation(_Record):
    """What a pinned package returned for a probe (D14)."""

    obs_id: str
    probe_id: str
    probe_sha256: str
    probe_kind: ProbeKind
    package: Literal["duckdb"]
    package_version: str
    wheel_sha256: str
    applies_to_doc_version: ObservationDocVersion
    python_version: str
    platform: str
    observed_status: ObservedStatus
    observed_value: str
    run_at: AwareDatetime
    runner_commit: str
    verification_level: ObservationLevel

    @property
    def platform_tag(self) -> str:
        m = _OBS_ID.fullmatch(self.obs_id)
        if m is None:  # unreachable: the validator guarantees the format
            raise ValueError("obs_id has the wrong format")
        return m.group("platform")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _require(_PROBE_ID, self.probe_id, "probe_id")
        _require(_SEMVER, self.package_version, "package_version")
        _require(_SHA256, self.probe_sha256, "probe_sha256")
        _require(_SHA256, self.wheel_sha256, "wheel_sha256")
        _require(_COMMIT, self.runner_commit, "runner_commit")
        m = _OBS_ID.fullmatch(self.obs_id)
        if m is None or m.group("platform") not in PLATFORM_TAGS:
            raise ValueError("obs_id is not ro-<probe_id>-<package_version>-<windows|linux>")
        if self.obs_id != observation_id(self.probe_id, self.package_version, m.group("platform")):
            raise ValueError("obs_id does not match probe_id and package_version")
        expected_os = {"windows": "Windows", "linux": "Linux"}[m.group("platform")]
        if not self.platform.startswith(expected_os):
            raise ValueError("platform does not match the obs_id platform tag")
        if not self.observed_value:
            raise ValueError("observed_value must not be empty")
        return self


class EvidenceConflict(_Record):
    """Links a disagreeing claim and observation(s); never modifies either one (D14)."""

    conflict_id: str
    claim_id: str
    obs_ids: list[str]
    doc_version: DocVersion
    description: str
    detected_by: ConflictDetector
    status: ConflictStatus
    resolution_note: str | None
    reviewed_by: str | None
    reviewed_at: AwareDatetime | None

    @field_validator("obs_ids")
    @classmethod
    def _sorted_unique(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("obs_ids must not be empty")
        if value != sorted(set(value)):
            raise ValueError("obs_ids must be sorted and unique")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.conflict_id != conflict_id(self.claim_id, self.obs_ids):
            raise ValueError("conflict_id does not match claim_id and obs_ids")
        if not self.description.strip():
            raise ValueError("description must not be empty")
        reviewed = (self.reviewed_by is not None, self.reviewed_at is not None)
        if self.status == "suspected":
            if reviewed != (False, False):
                raise ValueError("a suspected conflict has no reviewer")
        else:
            if reviewed != (True, True) or self.reviewed_by != OWNER:
                raise ValueError(f"only the owner can set status {self.status}")
        return self


def new_suspected_conflict(
    claim_id: str, obs_ids: list[str], doc_version: str, description: str, detected_by: str
) -> EvidenceConflict:
    """A new conflict always starts as ``suspected``, whoever detected it."""
    ids = sorted(set(obs_ids))
    return EvidenceConflict.model_validate(
        {
            "conflict_id": conflict_id(claim_id, ids),
            "claim_id": claim_id,
            "obs_ids": ids,
            "doc_version": doc_version,
            "description": description,
            "detected_by": detected_by,
            "status": "suspected",
            "resolution_note": None,
            "reviewed_by": None,
            "reviewed_at": None,
        }
    )


class ReviewError(ValueError):
    """A conflict status change that the review rules do not allow."""


def review_conflict(
    conflict: EvidenceConflict,
    *,
    status: Literal["confirmed", "dismissed"],
    reviewer: str,
    reviewed_at: object,
    resolution_note: str | None = None,
) -> EvidenceConflict:
    """The owner's decision on a ``suspected`` conflict. Returns a new record; the input conflict,
    the claim and the observations are never modified (records are immutable)."""
    if reviewer != OWNER:
        raise ReviewError("only the owner can confirm or dismiss a conflict")
    if conflict.status != "suspected":
        raise ReviewError(f"conflict {conflict.conflict_id} is already {conflict.status}")
    if status not in ("confirmed", "dismissed"):
        raise ReviewError("a review sets the status to confirmed or dismissed")
    data = conflict.model_dump()
    data.update(
        status=status,
        reviewed_by=reviewer,
        reviewed_at=reviewed_at,
        resolution_note=resolution_note,
    )
    return EvidenceConflict.model_validate(data)
