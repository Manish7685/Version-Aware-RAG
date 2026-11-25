"""IR-2 evidence checks against the real corpus manifest and cache (RAG-M4 S1a).

Claims are built on the fly from the cached sources, so no source text appears in this file, and
everything is written to temporary directories. Skipped unless the caches have been fetched; the
CI corpus-rebuild job fetches them and runs these.

Restricted text (release notes, release posts) must never reach test output either: pytest
prints the operands of a failing ``assert``. So every assertion that involves real source text
asserts a precomputed boolean or count, never an expression or object containing the text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.cli import main
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.models import DocClaim, doc_claim_id
from version_aware_rag.evidence.store import (
    CLAIMS_FILE,
    SIDECAR_FILE,
    EvidenceSet,
    load_evidence,
    write_evidence,
)
from version_aware_rag.evidence.verify import ClaimSource, build_source_index, validate_evidence
from version_aware_rag.evidence.workflow import add_claim, create_doc_claim, update_evidence
from version_aware_rag.probes.pins import load_pins

pytestmark = pytest.mark.corpus

MIGRATION_GUIDE = "docs/2.0/clients/python/migration.md"
DOCS_PAGE = "docs/1.4/sql/functions/date.md"
RELEASE_TAG = "v1.3.2"


@pytest.fixture
def real(repo_root: Path) -> dict[tuple[str, str], ClaimSource]:
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    chunking, _ = load_chunking_config(repo_root / "configs/chunking.toml")
    if not (cfg.cache_path(repo_root) / ".git").is_dir():
        pytest.skip("corpus cache not fetched")
    if not (repo_root / cfg.release_notes.cache_dir).is_dir():
        pytest.skip("release-note cache not fetched")
    manifest, problems = load_manifest(repo_root / cfg.paths.manifest)
    assert manifest is not None and problems == []
    return build_source_index(repo_root, cfg, chunking, manifest)


def _claim_from_source(src: ClaimSource, **over: Any) -> DocClaim:
    """A V1 claim whose span is the first substantial line of the cached source."""
    data = src.read_bytes()
    assert data is not None, f"cached source missing for {src.kind} {src.path}"
    text = data.decode("utf-8")
    line = next(ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 30)
    start = text.index(line)
    values: dict[str, Any] = {
        "claim_id": doc_claim_id(src.path, line),
        "source_kind": src.kind,
        "source_repo": src.repo,
        "source_commit": src.commit,
        "source_path": src.path,
        "source_sha256": src.sha256,
        "source_url": "https://duckdb.org/",
        "heading_path": [],
        "doc_version": src.doc_version,
        "introduced_in": None,
        "span_text": line,
        "span_char_start": start,
        "span_char_end": start + len(line),
        "claim_summary": "fixture claim built from the cached source",
        "claimed_value": None,
        "verification_level": "V1",
        "verified_by": None,
        "verified_at": None,
        "created_by": "script",
    }
    values.update(over)
    try:
        return DocClaim.model_validate(values)
    except ValidationError as exc:
        # A ValidationError prints its input values, which here are restricted source text.
        # Re-raise with field locations and error types only (no messages, inputs or context),
        # and without the chained original.
        fields = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'record'}: {e['type']}"
            for e in exc.errors(include_url=False, include_input=False, include_context=False)
        )
        raise AssertionError(
            f"fixture claim for {src.kind} {src.path} is invalid: {fields}"
        ) from None


def _codes(report: Any) -> list[str]:
    return [f.code for f in report.findings]  # findings carry ids and field names only


def test_real_docs_and_migration_guide_claims_verify(real: Any, repo_root: Path) -> None:
    pins = load_pins(repo_root / "configs/probes.toml")
    docs = _claim_from_source(real[("docs_page", DOCS_PAGE)])
    guide = _claim_from_source(real[("migration_guide", MIGRATION_GUIDE)], introduced_in="2.0.0")
    report = validate_evidence(EvidenceSet(claims=(docs, guide)), [], real, pins)
    assert _codes(report) == [] and report.incomplete == [] and report.exit_code == 0
    offsets = {"span_char_start": docs.span_char_start + 1, "span_char_end": docs.span_char_end + 1}
    report = validate_evidence(
        EvidenceSet(claims=(docs.model_copy(update=offsets),)), [], real, pins
    )
    assert _codes(report) == ["E-span"]
    quoted = docs.span_text in report.findings[0].message
    assert not quoted, "a finding quoted the span text"
    # Migration guides are their own kind: the same path as docs_page is not in the manifest.
    wrong_kind = guide.model_copy(update={"source_kind": "docs_page", "introduced_in": None})
    assert _codes(validate_evidence(EvidenceSet(claims=(wrong_kind,)), [], real, pins)) == [
        "E-source"
    ]


def _sidecar_texts(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {d["claim_id"]: d["span_text"] for d in map(json.loads, lines)}


def test_real_restricted_claims_round_trip_through_the_local_sidecar(
    real: Any, repo_root: Path, tmp_path: Path
) -> None:
    pins = load_pins(repo_root / "configs/probes.toml")
    note = _claim_from_source(real[("github_release", RELEASE_TAG)], introduced_in="1.3.2")
    post = _claim_from_source(real[next(k for k in real if k[0] == "release_post")])
    evidence = tmp_path / "evidence"
    write_evidence(evidence, claims=[note, post])
    committed = (evidence / CLAIMS_FILE).read_text(encoding="utf-8")
    committed_json = json.dumps(committed)  # also catch JSON-escaped forms
    leaked = any(
        t in committed or json.dumps(t)[1:-1] in committed_json
        for t in (note.span_text, post.span_text)
    )
    assert not leaked, "restricted span text reached the committed file (D1)"
    expected = {note.claim_id: note.span_text, post.claim_id: post.span_text}
    in_sidecar = _sidecar_texts(evidence / SIDECAR_FILE) == expected
    assert in_sidecar, "the local sidecar does not hold exactly the restricted span texts"
    loaded = load_evidence(evidence)
    assert loaded.problems == []
    report = validate_evidence(loaded.evidence, loaded.problems, real, pins)
    assert _codes(report) == [] and report.exit_code == 0
    # D1 at every level: a V0 restricted claim whose claim_id and offset length are consistent
    # with its real sidecar text, but whose offsets are wrong, is an error, not a draft warning.
    misplaced = note.model_copy(
        update={
            "verification_level": "V0",
            "span_char_start": note.span_char_start + 1,
            "span_char_end": note.span_char_end + 1,
        }
    )
    draft_dir = tmp_path / "draft"
    write_evidence(draft_dir, claims=[misplaced])
    draft = load_evidence(draft_dir)
    report = validate_evidence(draft.evidence, draft.problems, real, pins)
    restricted_v0_is_error = (
        draft.problems == []
        and _codes(report) == ["E-span"]
        and report.exit_code == 1
        and report.findings[0].severity == "error"
    )
    assert restricted_v0_is_error, (
        f"restricted V0 mismatch: {_codes(report)}, exit {report.exit_code}"
    )
    # S1c workflow on the real source: the claim is cut from the verified cached release note at
    # the given offsets, and writing it keeps the text in the local sidecar only (D1).
    created = create_doc_claim(
        real,
        source_kind="github_release",
        source_path=RELEASE_TAG,
        span_char_start=note.span_char_start,
        span_char_end=note.span_char_end,
        claim_summary="fixture claim built from the cached source",
        source_url="https://duckdb.org/",
        introduced_in="1.3.2",
    )
    workflow_dir = tmp_path / "workflow"
    update_evidence(workflow_dir, lambda ev: add_claim(ev, created), real, pins)
    workflow_ok = (
        created.claim_id == note.claim_id
        and created.span_text == note.span_text
        and created.verification_level == "V1"
        and created.span_text not in (workflow_dir / CLAIMS_FILE).read_text(encoding="utf-8")
        and load_evidence(workflow_dir).evidence.claims == (created,)
    )
    assert workflow_ok, "workflow claim creation on the real release note did not round-trip"
    # The sidecar is verified against the source hash and offsets: a same-length edit fails.
    texts = _sidecar_texts(evidence / SIDECAR_FILE)
    original = texts[note.claim_id]
    texts[note.claim_id] = original[:-1] + ("x" if original[-1] != "x" else "y")
    lines = [
        json.dumps({"claim_id": k, "span_text": v}, sort_keys=True, ensure_ascii=False)
        for k, v in sorted(texts.items())
    ]
    (evidence / SIDECAR_FILE).write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    problems = [str(p) for p in load_evidence(evidence).problems]
    flagged = sum("claim_id" in p for p in problems)
    quoted = any(original in p or texts[note.claim_id] in p for p in problems)
    assert flagged == 1 and not quoted, f"{flagged} claim_id problems; text quoted: {quoted}"
    (evidence / SIDECAR_FILE).unlink()
    problems = [str(p) for p in load_evidence(evidence).problems]
    assert sum("missing from the local sidecar" in p for p in problems) == 2


def test_cli_validates_real_claims_without_printing_their_text(
    real: Any, repo_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    note = _claim_from_source(real[("github_release", RELEASE_TAG)], introduced_in="1.3.2")
    docs = _claim_from_source(real[("docs_page", DOCS_PAGE)])
    evidence = tmp_path / "evidence"
    write_evidence(evidence, claims=[note, docs])
    code = main(["evidence", "validate", "--root", str(repo_root), "--evidence-dir", str(evidence)])
    out = capsys.readouterr()
    passed = code == 0 and "RESULT: PASSED (0 warnings)" in out.out and "doc_claims=2" in out.out
    printed = any(t in s for t in (note.span_text, docs.span_text) for s in (out.out, out.err))
    assert passed and not printed, f"exit {code}; span text printed: {printed}"
