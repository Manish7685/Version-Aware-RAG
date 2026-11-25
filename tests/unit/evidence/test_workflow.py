"""S1c workflow: claim creation, observation ingestion, stable suspected conflicts, owner-only
review, and the all-or-nothing write. Fixture sources and records only."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.evidence.store import (
    CLAIMS_FILE,
    OBSERVATIONS_FILE,
    SIDECAR_FILE,
    EvidenceSet,
    load_evidence,
    write_evidence,
)
from version_aware_rag.evidence.workflow import (
    WorkflowError,
    add_claim,
    add_conflict,
    apply_review,
    create_doc_claim,
    ingest_observations,
    manual_conflict,
    suggest_conflict,
    update_evidence,
)

from .conftest import FIXTURE_SOURCE, SPAN, WHEEL_LINUX_145, fixture_index, make_obs, sha

RELEASE_BODY = "## Changes\n\n- RESTRICTED-FIXTURE release-note line about date_trunc.\n"
RESTRICTED_SPAN = "RESTRICTED-FIXTURE release-note line about date_trunc."
WHEN = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


def _index() -> Any:
    index = fixture_index()
    index.update(
        fixture_index(
            text=RELEASE_BODY,
            kind="github_release",
            path="v1.3.2",
            repo="duckdb/duckdb",
            commit=None,
            sha256=sha(RELEASE_BODY),
            doc_version="1.3",
        )
    )
    return index


def _docs_claim(**over: Any) -> Any:
    start = FIXTURE_SOURCE.index(SPAN)
    values: dict[str, Any] = {
        "source_kind": "docs_page",
        "source_path": "docs/1.4/sql/functions/date.md",
        "span_char_start": start,
        "span_char_end": start + len(SPAN),
        "claim_summary": "date_trunc on DATE returns DATE",
        "claimed_value": "'DATE'",
        "source_url": "https://duckdb.org/docs/1.4/sql/functions/date.html",
    }
    values.update(over)
    return create_doc_claim(_index(), **values)


def _restricted_claim() -> Any:
    start = RELEASE_BODY.index(RESTRICTED_SPAN)
    return create_doc_claim(
        _index(),
        source_kind="github_release",
        source_path="v1.3.2",
        span_char_start=start,
        span_char_end=start + len(RESTRICTED_SPAN),
        claim_summary="a release-note claim",
        source_url="https://github.com/duckdb/duckdb/releases/tag/v1.3.2",
        introduced_in="1.3.2",
    )


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- claims


def test_claim_span_is_cut_from_the_verified_source() -> None:
    claim = _docs_claim()
    assert claim.span_text == SPAN and claim.verification_level == "V1"
    assert claim.source_sha256 == sha(FIXTURE_SOURCE) and claim.doc_version == "1.4"
    assert claim.verified_by is None and claim.created_by == "owner"
    assert _docs_claim(created_by="gemini_draft").verification_level == "V0"  # drafts stay V0


@pytest.mark.parametrize(
    ("over", "index_over", "message"),
    [
        ({"span_char_end": 10_000}, {}, "outside the source"),
        ({"span_char_start": 5, "span_char_end": 5}, {}, "outside the source"),
        ({"source_path": "docs/1.4/other.md"}, {}, "not in the corpus manifest"),
        ({}, {"text": None}, "cached source not available"),
        ({}, {"text": FIXTURE_SOURCE + "x"}, "does not match the manifest"),
        (
            {"introduced_in": "1.4.0"},
            {},
            "the claim is invalid",
        ),  # docs pages are not change sources
    ],
)
def test_claim_creation_refusals(
    over: dict[str, Any], index_over: dict[str, Any], message: str
) -> None:
    index = fixture_index(**index_over) if index_over else _index()
    start = FIXTURE_SOURCE.index(SPAN)
    values: dict[str, Any] = {
        "source_kind": "docs_page",
        "source_path": "docs/1.4/sql/functions/date.md",
        "span_char_start": start,
        "span_char_end": start + len(SPAN),
        "claim_summary": "s",
        "source_url": "https://duckdb.org/",
    }
    values.update(over)
    with pytest.raises(WorkflowError, match=message):
        create_doc_claim(index, **values)


def test_restricted_claim_text_goes_only_to_the_sidecar(tmp_path: Path, pins: Any) -> None:
    claim = _restricted_claim()
    update_evidence(tmp_path, lambda ev: add_claim(ev, claim), _index(), pins)
    committed = (tmp_path / CLAIMS_FILE).read_bytes()
    in_committed = RESTRICTED_SPAN.encode() in committed
    assert not in_committed, "restricted text reached the committed file"
    assert RESTRICTED_SPAN.encode() in (tmp_path / SIDECAR_FILE).read_bytes()
    assert load_evidence(tmp_path).evidence.claims == (claim,)


# ---------------------------------------------------------------- the all-or-nothing write


def test_update_refuses_broken_existing_evidence(tmp_path: Path, pins: Any) -> None:
    write_evidence(tmp_path, observations=[make_obs()])
    (tmp_path / OBSERVATIONS_FILE).write_bytes(b"{oops\n")
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowError, match="existing evidence has 1 problems"):
        update_evidence(tmp_path, lambda ev: add_claim(ev, _docs_claim()), _index(), pins)
    assert _snapshot(tmp_path) == before


def test_update_refuses_invalid_or_unverifiable_results(tmp_path: Path, pins: Any) -> None:
    bad = make_obs(python_version="3.11.0")
    with pytest.raises(WorkflowError, match="would be invalid"):
        update_evidence(tmp_path, lambda ev: EvidenceSet(observations=(bad,)), None, pins)
    assert _snapshot(tmp_path) == {}
    claim = _docs_claim()
    with pytest.raises(WorkflowError, match="cannot be fully verified"):  # cache missing
        update_evidence(tmp_path, lambda ev: add_claim(ev, claim), fixture_index(text=None), pins)
    assert _snapshot(tmp_path) == {}


# ---------------------------------------------------------------- conflicts


def test_conflict_ids_are_stable() -> None:
    claim = _docs_claim()
    a = make_obs(observed_value="'TIMESTAMP'")
    b = make_obs(probe_id="P03", observed_value="inf")
    first = suggest_conflict(claim, [a, b])
    second = suggest_conflict(claim, [b, a])  # order does not matter
    assert first is not None and second is not None
    assert first.conflict_id == second.conflict_id and first.obs_ids == sorted([a.obs_id, b.obs_id])
    only_a = suggest_conflict(claim, [a])
    assert only_a is not None and only_a.conflict_id != first.conflict_id  # different set, new id
    ev = EvidenceSet((claim,), (a, b), ())
    assert (
        manual_conflict(ev, claim.claim_id, [b.obs_id, a.obs_id], "differs").conflict_id
        == first.conflict_id
    )


def test_suggest_compares_exactly_and_only_suggests() -> None:
    claim = _docs_claim()  # claimed_value "'DATE'"
    agree = make_obs(observed_value="'DATE'")
    assert suggest_conflict(claim, [agree]) is None
    differ = make_obs(observed_value="'TIMESTAMP'")
    error = make_obs(probe_id="P03", observed_status="error", observed_value="'DATE'")
    conflict = suggest_conflict(claim, [agree, differ, error])
    assert conflict is not None
    assert conflict.status == "suspected" and conflict.detected_by == "script"
    assert conflict.obs_ids == sorted([differ.obs_id, error.obs_id])  # an error is not the claim
    assert conflict.reviewed_by is None
    with pytest.raises(WorkflowError, match="no claimed_value"):
        suggest_conflict(_docs_claim(claimed_value=None), [agree])


def _with_conflict(tmp_path: Path, pins: Any) -> tuple[Any, Any, Any]:
    claim, obs = _docs_claim(), make_obs(observed_value="'TIMESTAMP'")
    conflict = suggest_conflict(claim, [obs])
    assert conflict is not None
    update_evidence(
        tmp_path,
        lambda ev: add_conflict(EvidenceSet((claim,), (obs,), ()), conflict),
        _index(),
        pins,
    )
    return claim, obs, conflict


def test_only_the_owner_reviews_and_claims_and_observations_never_change(
    tmp_path: Path, pins: Any
) -> None:
    _claim, _obs, conflict = _with_conflict(tmp_path, pins)
    claims_before = (tmp_path / CLAIMS_FILE).read_bytes()
    obs_before = (tmp_path / OBSERVATIONS_FILE).read_bytes()
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowError, match="only the owner"):
        update_evidence(
            tmp_path,
            lambda ev: apply_review(
                ev,
                conflict.conflict_id,
                status="confirmed",
                reviewer="script",
                resolution_note=None,
            ),
            _index(),
            pins,
        )
    assert _snapshot(tmp_path) == before
    update_evidence(
        tmp_path,
        lambda ev: apply_review(
            ev,
            conflict.conflict_id,
            status="confirmed",
            reviewer="owner",
            resolution_note="the page describes a later release",
            reviewed_at=WHEN,
        ),
        _index(),
        pins,
    )
    reviewed = load_evidence(tmp_path).evidence.conflicts[0]
    assert (reviewed.status, reviewed.reviewed_by, reviewed.reviewed_at) == (
        "confirmed",
        "owner",
        WHEN,
    )
    assert (tmp_path / CLAIMS_FILE).read_bytes() == claims_before  # the claim is untouched
    assert (tmp_path / OBSERVATIONS_FILE).read_bytes() == obs_before  # so are the observations
    with pytest.raises(WorkflowError, match="already confirmed"):
        update_evidence(
            tmp_path,
            lambda ev: apply_review(
                ev, conflict.conflict_id, status="dismissed", reviewer="owner", resolution_note=None
            ),
            _index(),
            pins,
        )


def test_conflicts_are_never_added_already_reviewed_or_twice(tmp_path: Path, pins: Any) -> None:
    claim, obs, conflict = _with_conflict(tmp_path, pins)
    with pytest.raises(WorkflowError, match="already exists"):
        update_evidence(tmp_path, lambda ev: add_conflict(ev, conflict), _index(), pins)
    confirmed = conflict.model_copy(
        update={"status": "confirmed", "reviewed_by": "owner", "reviewed_at": WHEN}
    )
    with pytest.raises(WorkflowError, match="must be suspected"):
        add_conflict(EvidenceSet((claim,), (obs,), ()), confirmed)


def test_free_text_may_not_quote_restricted_sources(tmp_path: Path, pins: Any) -> None:
    claim = _restricted_claim()
    obs = make_obs(
        package_version="1.3.2",
        applies_to_doc_version="1.3",
        wheel_sha256="11af73963ae174aafd90ea45fb0317f1b2e28a7f1d9902819d47c67cc957d49c",
    )
    ev = EvidenceSet((claim,), (obs,), ())
    quoted = manual_conflict(ev, claim.claim_id, [obs.obs_id], f"note says: {RESTRICTED_SPAN}")
    with pytest.raises(WorkflowError, match="quotes restricted source text"):
        add_conflict(ev, quoted)
    clean = manual_conflict(ev, claim.claim_id, [obs.obs_id], "release note and package disagree")
    ev2 = add_conflict(ev, clean)
    with pytest.raises(WorkflowError, match="resolution note quotes restricted"):
        apply_review(
            ev2,
            clean.conflict_id,
            status="dismissed",
            reviewer="owner",
            resolution_note=f"because {RESTRICTED_SPAN}",
        )


# ---------------------------------------------------------------- observation ingestion


def test_ingestion_is_idempotent_and_refuses_conflicting_records(tmp_path: Path, pins: Any) -> None:
    obs = make_obs()
    exists = lambda sha: True  # noqa: E731
    once = update_evidence(tmp_path, lambda ev: ingest_observations(ev, [obs], exists), None, pins)
    again = update_evidence(tmp_path, lambda ev: ingest_observations(ev, [obs], exists), None, pins)
    assert once.evidence.observations == again.evidence.observations == (obs,)
    changed = obs.model_copy(update={"observed_value": "'TIMESTAMP'"})
    before = _snapshot(tmp_path)
    with pytest.raises(WorkflowError, match="different observation with this id"):
        update_evidence(tmp_path, lambda ev: ingest_observations(ev, [changed], exists), None, pins)
    assert _snapshot(tmp_path) == before


def test_ingestion_requires_a_known_runner_commit_and_d4(tmp_path: Path, pins: Any) -> None:
    obs = make_obs()
    with pytest.raises(WorkflowError, match="not a commit of this repository"):
        ingest_observations(EvidenceSet(), [obs], lambda sha: False)
    linux_kw = {
        "platform_tag": "linux",
        "platform": "Linux-6.8.0-x86_64",
        "wheel_sha256": WHEEL_LINUX_145,
    }
    lonely = make_obs(verification_level="reproduced", **linux_kw)
    with pytest.raises(WorkflowError, match="E-reproduced"):  # D4: no independent match
        update_evidence(
            tmp_path, lambda ev: ingest_observations(ev, [lonely], lambda s: True), None, pins
        )
    assert _snapshot(tmp_path) == {}
    windows = make_obs()
    update_evidence(
        tmp_path, lambda ev: ingest_observations(ev, [windows, lonely], lambda s: True), None, pins
    )
    assert len(load_evidence(tmp_path).evidence.observations) == 2  # the matching pair is accepted
