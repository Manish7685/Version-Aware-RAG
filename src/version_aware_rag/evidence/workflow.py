"""Evidence workflow (RAG-M4 S1c): create claims, ingest observations, create and review
conflicts. Every change goes through ``update_evidence``: load (a load problem refuses), apply,
run the complete S1a validation, and write once, atomically. Nothing invalid or unverifiable is
ever written.

Rules (D14, ``RAG-M1_design.md`` §2.1; RAG-M4 D1, D4, D5):
- A claim's ``span_text`` is cut from the hash-verified cached source at the given offsets, so it
  is verbatim by construction. Source fields come from the corpus manifest. A claim is ``V1``
  only after those checks; ``gemini_draft`` claims are ``V0``; nothing here sets ``V2``.
- Restricted span text (release notes, release posts) goes only to the git-ignored sidecar
  (``store.py``, D1), and free text (descriptions, notes) may not quote it.
- Observations are ingested as recorded; they keep their verification level, and ``reproduced``
  must still pass the D4 independence rule. Claims and observations stay separate records.
- A conflict always starts ``suspected`` (``new_suspected_conflict``); only a review recorded
  with the reviewer identity ``owner`` confirms or dismisses it (``review_conflict``). That is a
  workflow convention, not authentication: no automated path can set either status, and every
  review is recorded in ``reviewed_by`` and ``reviewed_at``. The claim and observations are never
  modified.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from version_aware_rag.evidence.models import (
    RESTRICTED_SOURCE_KINDS,
    DocClaim,
    EvidenceConflict,
    ReviewError,
    RuntimeObservation,
    doc_claim_id,
    new_suspected_conflict,
    review_conflict,
)
from version_aware_rag.evidence.store import EvidenceSet, load_evidence, write_evidence
from version_aware_rag.evidence.verify import ClaimSource, validate_evidence
from version_aware_rag.probes.pins import ProbePins


class WorkflowError(ValueError):
    """A workflow step that is refused; nothing is written. Messages never quote source text."""


Index = dict[tuple[str, str], ClaimSource]


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _source_text(src: ClaimSource) -> str:
    data = src.read_bytes()
    if data is None:
        raise WorkflowError(f"{src.kind} {src.path}: cached source not available")
    if hashlib.sha256(data).hexdigest() != src.sha256:
        raise WorkflowError(f"{src.kind} {src.path}: cached source does not match the manifest")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkflowError(f"{src.kind} {src.path}: cached source is not UTF-8") from None


def create_doc_claim(
    index: Index,
    *,
    source_kind: str,
    source_path: str,
    span_char_start: int,
    span_char_end: int,
    claim_summary: str,
    source_url: str,
    heading_path: Sequence[str] = (),
    claimed_value: str | None = None,
    introduced_in: str | None = None,
    created_by: Literal["owner", "script", "gemini_draft"] = "owner",
) -> DocClaim:
    """A claim whose span is cut from the verified cached source. ``V1`` for owner and script
    claims, ``V0`` for Gemini drafts; never ``V2``."""
    src = index.get((source_kind, source_path))
    if src is None:
        raise WorkflowError(f"{source_kind} {source_path} is not in the corpus manifest")
    text = _source_text(src)
    if not 0 <= span_char_start < span_char_end <= len(text):
        raise WorkflowError("span offsets are outside the source")
    span = text[span_char_start:span_char_end]
    try:
        return DocClaim.model_validate(
            {
                "claim_id": doc_claim_id(source_path, span),
                "source_kind": src.kind,
                "source_repo": src.repo,
                "source_commit": src.commit,
                "source_path": src.path,
                "source_sha256": src.sha256,
                "source_url": source_url,
                "heading_path": list(heading_path),
                "doc_version": src.doc_version,
                "introduced_in": introduced_in,
                "span_text": span,
                "span_char_start": span_char_start,
                "span_char_end": span_char_end,
                "claim_summary": claim_summary,
                "claimed_value": claimed_value,
                "verification_level": "V0" if created_by == "gemini_draft" else "V1",
                "verified_by": None,
                "verified_at": None,
                "created_by": created_by,
            }
        )
    except ValueError as exc:  # pydantic ValidationError: report types and fields only
        raise WorkflowError(f"the claim is invalid ({_error_summary(exc)})") from None


def _error_summary(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        items = errors(include_url=False, include_input=False, include_context=False)
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['type']}" for e in items)
    return type(exc).__name__


def _quotes_restricted_text(text: str | None, claims: Sequence[DocClaim]) -> bool:
    """True if ``text`` contains a restricted claim's span, or any 30+ character line of it."""
    if not text:
        return False
    for claim in claims:
        if claim.source_kind not in RESTRICTED_SOURCE_KINDS:
            continue
        pieces = [claim.span_text, *(ln.strip() for ln in claim.span_text.splitlines())]
        if any(len(p) >= 30 and p in text for p in pieces) or claim.span_text in text:
            return True
    return False


def suggest_conflict(
    claim: DocClaim, observations: Sequence[RuntimeObservation]
) -> EvidenceConflict | None:
    """Script-assisted detection: a suspected conflict when the claim's ``claimed_value`` differs
    exactly from the ``observed_value`` of a linked observation; None when there is nothing to
    compare or every linked observation agrees. Never confirms anything."""
    if claim.claimed_value is None:
        raise WorkflowError(f"{claim.claim_id} has no claimed_value to compare")
    if not observations:
        raise WorkflowError("at least one observation is needed")
    differing = sorted(
        o.obs_id
        for o in observations
        if (o.observed_status, o.observed_value) != ("ok", claim.claimed_value)
    )
    if not differing:
        return None
    return new_suspected_conflict(
        claim.claim_id,
        differing,
        claim.doc_version,
        f"claimed_value of {claim.claim_id} differs from the observed value of "
        f"{len(differing)} observation(s): {', '.join(differing)}",
        "script",
    )


Mutation = Callable[[EvidenceSet], EvidenceSet]


@dataclass(frozen=True)
class UpdateResult:
    evidence: EvidenceSet
    warnings: int


def update_evidence(
    evidence_dir: Path, mutate: Mutation, index: Index | None, pins: ProbePins
) -> UpdateResult:
    """Load, apply ``mutate``, validate everything, then write once. Refused (nothing written)
    on any load problem, validation error or check that could not be run."""
    loaded = load_evidence(evidence_dir)
    if loaded.problems:
        raise WorkflowError(f"the existing evidence has {len(loaded.problems)} problems")
    new = mutate(loaded.evidence)
    report = validate_evidence(new, [], index, pins)
    if report.errors:
        first = report.errors[0]
        count = len(report.errors)
        raise WorkflowError(
            f"the result would be invalid ({count} errors; first [{first.code}] {first.message})"
        )
    if report.incomplete:
        raise WorkflowError(f"the result cannot be fully verified ({report.incomplete[0]})")
    write_evidence(
        evidence_dir, claims=new.claims, observations=new.observations, conflicts=new.conflicts
    )
    return UpdateResult(new, len(report.warnings))


def add_claim(evidence: EvidenceSet, claim: DocClaim) -> EvidenceSet:
    if any(c.claim_id == claim.claim_id for c in evidence.claims):
        raise WorkflowError(f"claim {claim.claim_id} already exists")
    return EvidenceSet((*evidence.claims, claim), evidence.observations, evidence.conflicts)


def add_conflict(evidence: EvidenceSet, conflict: EvidenceConflict) -> EvidenceSet:
    if conflict.status != "suspected":
        raise WorkflowError("a new conflict must be suspected")
    if any(c.conflict_id == conflict.conflict_id for c in evidence.conflicts):
        raise WorkflowError(f"conflict {conflict.conflict_id} already exists")
    if _quotes_restricted_text(conflict.description, evidence.claims):
        raise WorkflowError("the description quotes restricted source text (D1)")
    return EvidenceSet(evidence.claims, evidence.observations, (*evidence.conflicts, conflict))


def manual_conflict(
    evidence: EvidenceSet, claim_id: str, obs_ids: Sequence[str], description: str
) -> EvidenceConflict:
    """The owner's own suspected conflict (``detected_by = owner``); still only suspected."""
    claim = next((c for c in evidence.claims if c.claim_id == claim_id), None)
    if claim is None:
        raise WorkflowError(f"claim {claim_id} does not exist")
    try:
        return new_suspected_conflict(
            claim_id, list(obs_ids), claim.doc_version, description, "owner"
        )
    except ValueError as exc:
        raise WorkflowError(f"the conflict is invalid ({_error_summary(exc)})") from None


def apply_review(
    evidence: EvidenceSet,
    conflict_id: str,
    *,
    status: Literal["confirmed", "dismissed"],
    reviewer: str,
    resolution_note: str | None,
    reviewed_at: datetime | None = None,
) -> EvidenceSet:
    """The owner's decision on one suspected conflict; claims and observations are untouched."""
    target = next((c for c in evidence.conflicts if c.conflict_id == conflict_id), None)
    if target is None:
        raise WorkflowError(f"conflict {conflict_id} does not exist")
    if _quotes_restricted_text(resolution_note, evidence.claims):
        raise WorkflowError("the resolution note quotes restricted source text (D1)")
    try:
        reviewed = review_conflict(
            target,
            status=status,
            reviewer=reviewer,
            reviewed_at=reviewed_at or _now(),
            resolution_note=resolution_note,
        )
    except ReviewError as exc:  # not the owner, or not suspected
        raise WorkflowError(str(exc)) from None
    except ValueError as exc:  # a validation error: fields and types only
        raise WorkflowError(_error_summary(exc)) from None
    conflicts = tuple(reviewed if c.conflict_id == conflict_id else c for c in evidence.conflicts)
    return EvidenceSet(evidence.claims, evidence.observations, conflicts)


def ingest_observations(
    evidence: EvidenceSet,
    incoming: Sequence[RuntimeObservation],
    commit_exists: Callable[[str], bool],
) -> EvidenceSet:
    """Add recorded observations as they are. Identical duplicates are accepted (idempotent);
    a different record under an existing obs_id is refused; ``runner_commit`` must be a commit of
    this repository. Validation (pins, D4) runs in ``update_evidence``."""
    existing = {o.obs_id: o for o in evidence.observations}
    added: dict[str, RuntimeObservation] = {}
    for obs in incoming:
        if not commit_exists(obs.runner_commit):
            raise WorkflowError(f"{obs.obs_id}: runner_commit is not a commit of this repository")
        prior = existing.get(obs.obs_id) or added.get(obs.obs_id)
        if prior is not None and prior != obs:
            raise WorkflowError(
                f"{obs.obs_id}: a different observation with this id already exists"
            )
        added[obs.obs_id] = obs
    merged = {**existing, **added}
    return EvidenceSet(evidence.claims, tuple(merged.values()), evidence.conflicts)
