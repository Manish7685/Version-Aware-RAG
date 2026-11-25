"""Deterministic JSONL storage for the evidence records (D14: ``data/evidence/``; RAG-M4 D1).

Layout under the evidence directory:

- ``doc_claims.jsonl``, ``runtime_observations.jsonl``, ``evidence_conflicts.jsonl``: committed.
  One record per line, sorted by ID, ``json.dumps(..., sort_keys=True, ensure_ascii=False)``,
  UTF-8, LF line endings, a final newline. Writing the same records always gives the same bytes,
  and loading checks that every file is in exactly this canonical form.
- ``local/doc_claim_spans.jsonl``: **git-ignored**. Owner decision D1: the text of release-note
  and release-post claims (``RESTRICTED_SOURCE_KINDS``) never enters Git or a CI artifact. Their
  committed line carries every ``DocClaim`` field except ``span_text``; the text is kept in this
  local sidecar as ``{"claim_id", "span_text"}`` and merged back on load, so the in-memory record
  is always the complete, approved ``DocClaim``. A restricted line that contains ``span_text``,
  or a restricted claim whose sidecar text is missing, is a load problem. The merged text is then
  verified against the source hash and offsets (``verify.py``).

Load problems name the file, the line and the field, never a record value, so no restricted text
can reach a log through an error message.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from version_aware_rag.evidence.models import (
    RESTRICTED_SOURCE_KINDS,
    DocClaim,
    EvidenceConflict,
    RuntimeObservation,
)

CLAIMS_FILE = "doc_claims.jsonl"
OBSERVATIONS_FILE = "runtime_observations.jsonl"
CONFLICTS_FILE = "evidence_conflicts.jsonl"
SIDECAR_FILE = "local/doc_claim_spans.jsonl"
COMMITTED_FILES = (CLAIMS_FILE, OBSERVATIONS_FILE, CONFLICTS_FILE)


class EvidenceStoreError(ValueError):
    """Records that cannot be written (for example a duplicate ID)."""


@dataclass(frozen=True)
class LoadProblem:
    file: str
    line: int | None
    message: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line is not None else self.file
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class EvidenceSet:
    claims: tuple[DocClaim, ...] = ()
    observations: tuple[RuntimeObservation, ...] = ()
    conflicts: tuple[EvidenceConflict, ...] = ()


@dataclass
class LoadResult:
    evidence: EvidenceSet
    problems: list[LoadProblem] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)  # committed files that do not exist yet


def dumps_line(data: dict[str, Any]) -> str:
    """The canonical serialization of one record."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n"


def _is_restricted(claim: DocClaim) -> bool:
    return claim.source_kind in RESTRICTED_SOURCE_KINDS


def committed_claim_data(claim: DocClaim) -> dict[str, Any]:
    """The committed form of a claim: without ``span_text`` when its source is restricted."""
    data = claim.model_dump(mode="json")
    if _is_restricted(claim):
        del data["span_text"]
    return data


def _check_unique(ids: Sequence[str], kind: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise EvidenceStoreError(f"duplicate {kind} id {i}")
        seen.add(i)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    tmp.replace(path)


def write_evidence(
    evidence_dir: Path,
    *,
    claims: Iterable[DocClaim] = (),
    observations: Iterable[RuntimeObservation] = (),
    conflicts: Iterable[EvidenceConflict] = (),
) -> None:
    """Write all three committed files and the local sidecar, each sorted by ID."""
    claims = sorted(claims, key=lambda c: c.claim_id)
    observations = sorted(observations, key=lambda o: o.obs_id)
    conflicts = sorted(conflicts, key=lambda c: c.conflict_id)
    _check_unique([c.claim_id for c in claims], "claim")
    _check_unique([o.obs_id for o in observations], "observation")
    _check_unique([c.conflict_id for c in conflicts], "conflict")
    _write_atomic(
        evidence_dir / CLAIMS_FILE, "".join(dumps_line(committed_claim_data(c)) for c in claims)
    )
    _write_atomic(
        evidence_dir / OBSERVATIONS_FILE,
        "".join(dumps_line(o.model_dump(mode="json")) for o in observations),
    )
    _write_atomic(
        evidence_dir / CONFLICTS_FILE,
        "".join(dumps_line(c.model_dump(mode="json")) for c in conflicts),
    )
    sidecar = [
        {"claim_id": c.claim_id, "span_text": c.span_text} for c in claims if _is_restricted(c)
    ]
    _write_atomic(evidence_dir / SIDECAR_FILE, "".join(dumps_line(s) for s in sidecar))


def _errors(exc: ValidationError) -> str:
    """Field locations and messages only: no input values (they could be restricted text)."""
    parts = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(x) for x in err["loc"]) or "record"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _read_lines(path: Path, name: str, problems: list[LoadProblem]) -> list[str] | None:
    """The lines of a JSONL file, or None if it cannot be read as canonical UTF-8 LF text."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        problems.append(LoadProblem(name, None, f"unreadable ({type(exc).__name__})"))
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(LoadProblem(name, None, "not UTF-8"))
        return None
    if "\r" in text:
        problems.append(LoadProblem(name, None, "contains CR characters (LF line endings only)"))
        return None
    if text and not text.endswith("\n"):
        problems.append(LoadProblem(name, None, "does not end with a newline"))
        return None
    return text.split("\n")[:-1] if text else []


def _parse_object(
    line: str, name: str, n: int, problems: list[LoadProblem]
) -> dict[str, Any] | None:
    try:
        data = json.loads(line)
    except ValueError:
        problems.append(LoadProblem(name, n, "not valid JSON"))
        return None
    if not isinstance(data, dict):
        problems.append(LoadProblem(name, n, "not a JSON object"))
        return None
    return data


def _validate[M: BaseModel](
    model: type[M], data: dict[str, Any], name: str, n: int, problems: list[LoadProblem]
) -> M | None:
    try:
        return model.model_validate_json(json.dumps(data))
    except ValidationError as exc:
        problems.append(LoadProblem(name, n, f"invalid record ({_errors(exc)})"))
        return None


def _check_canonical(
    lines: list[str], records: list[tuple[int, str, str]], name: str, problems: list[LoadProblem]
) -> None:
    """Every line must be the canonical serialization, sorted by ID, with unique IDs."""
    ids = [rid for _, rid, _ in records]
    for n, rid, canonical in records:
        if lines[n - 1] + "\n" != canonical:
            problems.append(LoadProblem(name, n, f"{rid} is not in canonical form"))
    seen: set[str] = set()
    for n, rid, _ in records:
        if rid in seen:
            problems.append(LoadProblem(name, n, f"duplicate id {rid}"))
        seen.add(rid)
    if ids != sorted(ids):
        problems.append(LoadProblem(name, None, "records are not sorted by id"))


def _load_sidecar(evidence_dir: Path, problems: list[LoadProblem]) -> dict[str, str]:
    path = evidence_dir / SIDECAR_FILE
    if not path.is_file():
        return {}
    lines = _read_lines(path, SIDECAR_FILE, problems)
    spans: dict[str, str] = {}
    for n, line in enumerate(lines or [], start=1):
        data = _parse_object(line, SIDECAR_FILE, n, problems)
        if data is None:
            continue
        cid, text = data.get("claim_id"), data.get("span_text")
        if (
            set(data) != {"claim_id", "span_text"}
            or not isinstance(cid, str)
            or not isinstance(text, str)
        ):
            problems.append(LoadProblem(SIDECAR_FILE, n, "entry must be {claim_id, span_text}"))
        elif cid in spans:
            problems.append(LoadProblem(SIDECAR_FILE, n, f"duplicate entry for {cid}"))
        else:
            spans[cid] = text
    return spans


def load_evidence(evidence_dir: Path) -> LoadResult:
    """Load and validate every record; all problems are collected, none is raised."""
    result = LoadResult(EvidenceSet())
    problems = result.problems
    spans = _load_sidecar(evidence_dir, problems)
    used_spans: set[str] = set()
    loaded: dict[str, list[Any]] = {}
    for name, model, id_field in (
        (CLAIMS_FILE, DocClaim, "claim_id"),
        (OBSERVATIONS_FILE, RuntimeObservation, "obs_id"),
        (CONFLICTS_FILE, EvidenceConflict, "conflict_id"),
    ):
        loaded[name] = []
        path = evidence_dir / name
        if not path.exists():
            result.absent.append(name)
            continue
        lines = _read_lines(path, name, problems)
        if lines is None:
            continue
        canonical: list[tuple[int, str, str]] = []
        for n, line in enumerate(lines, start=1):
            data = _parse_object(line, name, n, problems)
            if data is None:
                continue
            if model is DocClaim and data.get("source_kind") in RESTRICTED_SOURCE_KINDS:
                if "span_text" in data:
                    problems.append(
                        LoadProblem(
                            name, n, "restricted span_text must not be in the committed file"
                        )
                    )
                    continue
                cid = data.get("claim_id")
                if not isinstance(cid, str) or cid not in spans:
                    problems.append(
                        LoadProblem(
                            name, n, "restricted span text is missing from the local sidecar"
                        )
                    )
                    continue
                used_spans.add(cid)
                data = {**data, "span_text": spans[cid]}
            record = _validate(model, data, name, n, problems)
            if record is None:
                continue
            loaded[name].append(record)
            committed = (
                committed_claim_data(record)
                if isinstance(record, DocClaim)
                else record.model_dump(mode="json")
            )
            canonical.append((n, getattr(record, id_field), dumps_line(committed)))
        _check_canonical(lines, canonical, name, problems)
    claim_kinds = {c.claim_id: c.source_kind for c in loaded[CLAIMS_FILE]}
    for cid in sorted(set(spans) - used_spans):
        kind = claim_kinds.get(cid)
        reason = "no such claim" if kind is None else "its source is not restricted"
        problems.append(LoadProblem(SIDECAR_FILE, None, f"unexpected entry {cid} ({reason})"))
    result.evidence = EvidenceSet(
        claims=tuple(loaded[CLAIMS_FILE]),
        observations=tuple(loaded[OBSERVATIONS_FILE]),
        conflicts=tuple(loaded[CONFLICTS_FILE]),
    )
    return result
