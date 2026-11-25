"""The pilot change inventory (RAG-M4 S2; ``RAG-M1_design.md`` §3.4 step 2 and §3.5 step 1).

One record per behaviour change between consecutive versions, with the §3.5 fields
(``change_id, version, feature, before, after, source_path, probe_sql?``) extended so that verified
facts and open items are never confused:

- ``sources``: where the change is documented, by source kind, path and line range. Restricted
  sources (release posts, release notes) are cited by reference only; ``feature``, ``before``,
  ``after`` and ``status_reason`` are this project's own words and may not quote them.
- ``probe_ids``: the suite probes (``configs/probes.toml``) that observe the change; their code is
  the ``probe_sql`` of §3.5.
- ``runtime_evidence``: ``rag_m1_verified`` (recorded in ``RAG-M1_research_evidence.md`` §4),
  ``scratch_only`` (an S2 exploration run that is **not** canonical evidence), or ``none``. It is
  the **weakest** provenance among the record's probes: ``rag_m1_verified`` only when every probe
  is a RAG-M1 probe (``rag_m1_name`` set in the suite). Each probe's own provenance stays
  machine-readable in the suite, and ``status_reason`` states it when a record mixes probes.
- ``canonical_observations``: always ``pending`` until a canonical ``vrag probes run`` is committed.
- ``status``: ``pilot_candidate``, ``unresolved`` (evidence disagrees or is incomplete) or
  ``excluded`` (with the reason); ``owner_review`` starts ``pending``.

Nothing here is a ``DocClaim``, ``RuntimeObservation`` or ``EvidenceConflict``; those are created
only through the evidence workflow.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from version_aware_rag.evidence.models import RESTRICTED_SOURCE_KINDS
from version_aware_rag.evidence.verify import ClaimSource

VERSIONS = ("ref", "1.2", "1.3", "1.4", "1.5", "2.0")
CORPUS_BOUNDARIES = (("1.2", "1.3"), ("1.3", "1.4"), ("1.4", "1.5"), ("1.5", "2.0"))
DEFAULT_PATH = Path("benchmark/inventory/pilot_change_inventory.jsonl")
_CHANGE_ID = re.compile(r"chg-(?P<version>\d\.\d)-(?P<n>\d{2})")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRef(_Strict):
    source_kind: Literal["docs_page", "release_post", "migration_guide", "github_release"]
    source_path: str
    line_start: int
    line_end: int
    note: str

    @model_validator(mode="after")
    def _lines(self) -> Self:
        if not 1 <= self.line_start <= self.line_end:
            raise ValueError("line range must satisfy 1 <= line_start <= line_end")
        if not self.note.strip():
            raise ValueError("note must not be empty")
        return self


class ChangeRecord(_Strict):
    change_id: str
    version: Literal["1.2", "1.3", "1.4", "1.5", "2.0"]
    previous_version: Literal["ref", "1.2", "1.3", "1.4", "1.5"]
    feature: str
    before: str
    after: str
    sources: tuple[SourceRef, ...]
    probe_ids: tuple[str, ...]
    runtime_evidence: Literal["rag_m1_verified", "scratch_only", "none"]
    canonical_observations: Literal["pending"]
    status: Literal["pilot_candidate", "unresolved", "excluded"]
    status_reason: str
    owner_review: Literal["pending"]

    @field_validator("feature", "before", "after", "status_reason")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        m = _CHANGE_ID.fullmatch(self.change_id)
        if m is None or m.group("version") != self.version:
            raise ValueError("change_id must be chg-<version>-<nn> for this record's version")
        if VERSIONS.index(self.previous_version) != VERSIONS.index(self.version) - 1:
            raise ValueError("previous_version must be the version just before version")
        if not self.sources:
            raise ValueError("at least one source is required")
        if list(self.probe_ids) != sorted(set(self.probe_ids)):
            raise ValueError("probe_ids must be sorted and unique")
        if self.status == "pilot_candidate" and not self.probe_ids:
            raise ValueError("a pilot candidate needs at least one probe")
        if self.status == "pilot_candidate" and self.runtime_evidence == "none":
            raise ValueError("a pilot candidate needs runtime evidence (RAG-M1 or scratch)")
        if self.runtime_evidence != "none" and not self.probe_ids:
            raise ValueError("runtime evidence must come from at least one suite probe")
        return self


@dataclass
class InventoryReport:
    records: list[ChangeRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors:
            return 1
        return 2 if self.incomplete else 0

    def coverage(self) -> dict[str, int]:
        """Pilot candidates per corpus boundary (``"1.4->1.5": n``), plus the reference one."""
        counts: dict[str, int] = {f"{a}->{b}": 0 for a, b in (("ref", "1.2"), *CORPUS_BOUNDARIES)}
        for r in self.records:
            if r.status == "pilot_candidate":
                counts[f"{r.previous_version}->{r.version}"] += 1
        return counts


def dumps_record(record: ChangeRecord) -> str:
    return json.dumps(record.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n"


def load_inventory(path: Path, report: InventoryReport) -> None:
    """Parse the JSONL strictly: canonical form, sorted by change_id, unique ids."""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.incomplete.append(f"{path.name} cannot be read ({type(exc).__name__})")
        return
    if "\r" in text or (text and not text.endswith("\n")):
        report.errors.append(f"{path.name}: LF line endings and a final newline are required")
        return
    for n, line in enumerate(text.split("\n")[:-1] if text else [], start=1):
        try:
            record = ChangeRecord.model_validate_json(line)
        except ValidationError as exc:
            types = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['type']}"
                for e in exc.errors(include_url=False, include_input=False, include_context=False)
            )
            report.errors.append(f"{path.name}:{n}: invalid record ({types})")
            continue
        if dumps_record(record) != line + "\n":
            report.errors.append(f"{path.name}:{n}: {record.change_id} is not in canonical form")
        report.records.append(record)
    ids = [r.change_id for r in report.records]
    if len(set(ids)) != len(ids):
        report.errors.append(f"{path.name}: duplicate change_id")
    if ids != sorted(ids):
        report.errors.append(f"{path.name}: records are not sorted by change_id")


def _quotes(text: str, source_lines: Sequence[str]) -> bool:
    return any(len(ln) >= 30 and ln in text for ln in source_lines)


_HEADING = re.compile(r"^(#{1,6})\s")


def crossed_heading(lines: Sequence[str], line_start: int, line_end: int) -> int | None:
    """The first line inside ``(line_start, line_end]`` whose Markdown heading leaves the cited
    section: one of the same or a higher level than a heading at ``line_start``, or any heading
    when the range does not start at a heading. None when the range stays in one section."""
    first = _HEADING.match(lines[line_start - 1]) if line_start <= len(lines) else None
    level = len(first.group(1)) if first else None
    for n in range(line_start + 1, min(line_end, len(lines)) + 1):
        m = _HEADING.match(lines[n - 1])
        if m and (level is None or len(m.group(1)) <= level):
            return n
    return None


@dataclass(frozen=True)
class SuiteFacts:
    """What validation needs from the probe suite and the pins."""

    probe_ids: frozenset[str]
    rag_m1_probe_ids: frozenset[str]  # probes carried forward from RAG-M1 (``rag_m1_name`` set)
    packages: dict[str, str]  # version ("ref", "1.2", ...) -> package version ("1.1.3", ...)


def _check_evidence(r: ChangeRecord, facts: SuiteFacts, report: InventoryReport) -> None:
    for pid in r.probe_ids:
        if pid not in facts.probe_ids:
            report.errors.append(f"{r.change_id}: probe {pid} is not in the probe suite")
    if r.runtime_evidence == "rag_m1_verified":
        # The label is the weakest provenance among the record's probes: every probe must be a
        # RAG-M1 probe, so scratch-only evidence can never be presented as RAG-M1-verified.
        not_carried = sorted(set(r.probe_ids) - facts.rag_m1_probe_ids)
        if not_carried:
            report.errors.append(
                f"{r.change_id}: rag_m1_verified, but probe(s) {', '.join(not_carried)} have no "
                "RAG-M1 evidence (label the record scratch_only)"
            )
        if "RAG-M1" not in r.status_reason:
            report.errors.append(
                f"{r.change_id}: rag_m1_verified, but status_reason does not cite RAG-M1"
            )
    if r.runtime_evidence == "scratch_only":
        if r.probe_ids and set(r.probe_ids) <= facts.rag_m1_probe_ids:
            report.errors.append(
                f"{r.change_id}: scratch_only, but every probe has RAG-M1 evidence"
            )
        if "not canonical evidence" not in r.status_reason:
            report.errors.append(
                f"{r.change_id}: scratch_only, but status_reason does not say it is "
                "not canonical evidence"
            )
    if r.status == "pilot_candidate":  # unresolved and excluded records describe open evidence
        before_pkg = facts.packages.get(r.previous_version)
        after_pkg = facts.packages.get(r.version)
        if before_pkg is None or before_pkg not in r.before:
            report.errors.append(
                f"{r.change_id}: 'before' does not name the {r.previous_version} "
                f"package {before_pkg}"
            )
        if after_pkg is None or after_pkg not in r.after:
            report.errors.append(
                f"{r.change_id}: 'after' does not name the {r.version} package {after_pkg}"
            )


def validate_inventory(
    report: InventoryReport,
    index: dict[tuple[str, str], ClaimSource],
    facts: SuiteFacts,
) -> None:
    """Check every record against the corpus manifest, the cached sources, the probe suite and the
    package pins."""
    for r in report.records:
        _check_evidence(r, facts, report)
        for ref in r.sources:
            src = index.get((ref.source_kind, ref.source_path))
            if src is None:
                report.errors.append(
                    f"{r.change_id}: {ref.source_kind} {ref.source_path} "
                    "is not in the corpus manifest"
                )
                continue
            if src.doc_version not in (r.version, r.previous_version):
                report.errors.append(
                    f"{r.change_id}: {ref.source_path} belongs to docs {src.doc_version}"
                )
            data = src.read_bytes()
            if data is None:
                report.incomplete.append(f"{r.change_id}: cached {ref.source_path} not available")
                continue
            raw_lines = data.decode("utf-8", "replace").splitlines()
            lines = [ln.strip() for ln in raw_lines]
            if ref.line_end > len(lines):
                report.errors.append(
                    f"{r.change_id}: {ref.source_path} has only {len(lines)} lines"
                )
            crossed = crossed_heading(raw_lines, ref.line_start, ref.line_end)
            if crossed is not None:
                report.errors.append(
                    f"{r.change_id}: {ref.source_path} {ref.line_start}-{ref.line_end} runs into "
                    f"another section at line {crossed}"
                )
            if ref.source_kind in RESTRICTED_SOURCE_KINDS:
                own_text = "\n".join([r.feature, r.before, r.after, r.status_reason, ref.note])
                if _quotes(own_text, lines):
                    report.errors.append(
                        f"{r.change_id}: a text field quotes restricted {ref.source_path}"
                    )
    missing = [b for b, n in report.coverage().items() if n == 0 and not b.startswith("ref")]
    for boundary in missing:
        report.errors.append(f"no pilot candidate covers the corpus boundary {boundary}")
