"""IR-2 models: the approved D14 schema, field rules and owner-only conflict review."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.evidence.models import (
    DocClaim,
    EvidenceConflict,
    ReviewError,
    RuntimeObservation,
    conflict_id,
    new_suspected_conflict,
    review_conflict,
)

from .conftest import (
    ROOT,
    WHEN,
    claim_data,
    conflict_data,
    make_claim,
    make_conflict,
    make_obs,
    obs_data,
)


def _design_fields(record: str) -> list[str]:
    """The field names of one D14 table in RAG-M1_design.md §2.1, in order."""
    text = (ROOT / "docs/design/RAG-M1_design.md").read_text(encoding="utf-8")
    start = text.index(f"**`{record}`**")
    table = text[start : text.index("\n\n", text.index("| Field |", start))]
    names: list[str] = []
    rows = [r for r in table.splitlines() if r.startswith("|")]
    for row in rows[2:]:  # skip the header and the |---| separator
        cell = row.split("|")[1].replace("★", "").strip()
        names += [n.strip(" `") for n in cell.split(",")]
    return names


@pytest.mark.parametrize(
    ("record", "model"),
    [
        ("DocClaim", DocClaim),
        ("RuntimeObservation", RuntimeObservation),
        ("EvidenceConflict", EvidenceConflict),
    ],
)
def test_models_have_exactly_the_approved_d14_fields_in_order(record: str, model: Any) -> None:
    assert list(model.model_fields) == _design_fields(record)


def test_valid_records() -> None:
    claim, obs = make_claim(), make_obs()
    conflict = make_conflict(claim, [obs])
    assert claim.claim_id.startswith("dc-") and obs.obs_id == "ro-P02-1.4.5-windows"
    assert conflict.status == "suspected" and conflict.conflict_id.startswith("ec-")


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"claim_id": "dc-000000000000"}, "claim_id"),
        ({"span_char_end": 5}, "offsets"),
        ({"span_char_start": 9, "span_char_end": 9}, "start < end"),
        ({"source_kind": "github_release", "source_path": "v1.2.1"}, "null for github_release"),
        ({"source_commit": None}, "source_commit is required"),
        ({"introduced_in": "1.4.0"}, "only for change sources"),
        ({"source_kind": "release_post", "introduced_in": "1.4"}, "introduced_in"),
        ({"source_url": "http://duckdb.org/x"}, "https"),
        ({"source_sha256": "ABC"}, "source_sha256"),
        ({"claim_summary": "  "}, "claim_summary"),
        ({"verification_level": "V2"}, "V2 claim needs"),
        ({"verification_level": "V0", "verified_by": "owner", "verified_at": WHEN}, "V0"),
        ({"verified_by": "owner"}, "set together"),
        ({"doc_version": "1.1"}, "doc_version"),
        ({"created_by": "assistant"}, "created_by"),
        ({"surprise": 1}, "surprise"),
        ({"span_char_start": "0"}, "span_char_start"),
    ],
)
def test_doc_claim_rejects(over: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        DocClaim.model_validate(claim_data(**over))


def test_v2_claim_and_change_source_claim() -> None:
    v2 = make_claim(verification_level="V2", verified_by="owner", verified_at=WHEN)
    assert v2.verification_level == "V2"
    post = make_claim(
        source_kind="release_post",
        source_path="_posts/2025-09-16-announcing-duckdb-140.md",
        introduced_in="1.4.0",
    )
    assert post.introduced_in == "1.4.0"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"obs_id": "ro-P02-1.4.5-linux"}, "platform does not match"),
        ({"platform_tag": "macos"}, "windows|linux"),
        ({"obs_id": "ro-P03-1.4.5-windows"}, "does not match probe_id"),
        ({"probe_id": "P-02", "obs_id": "ro-P-02-1.4.5-windows"}, "probe_id"),
        ({"package_version": "1.4"}, "package_version"),
        ({"wheel_sha256": "0" * 63}, "wheel_sha256"),
        ({"runner_commit": "abc"}, "runner_commit"),
        ({"observed_value": ""}, "observed_value"),
        ({"observed_status": "skipped"}, "observed_status"),
        ({"applies_to_doc_version": "1.1"}, "applies_to_doc_version"),
        ({"verification_level": "verified"}, "verification_level"),
        ({"package": "pandas"}, "package"),
    ],
)
def test_runtime_observation_rejects(over: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        RuntimeObservation.model_validate(obs_data(**over))


def test_run_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="run_at"):
        RuntimeObservation.model_validate_json(
            make_obs().model_dump_json().replace("2026-11-23T10:00:00Z", "2026-11-23T10:00:00")
        )


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"conflict_id": "ec-000000000000"}, "conflict_id"),
        ({"obs_ids": []}, "must not be empty"),
        ({"status": "confirmed"}, "only the owner"),
        ({"status": "confirmed", "reviewed_by": "script", "reviewed_at": WHEN}, "only the owner"),
        ({"status": "dismissed", "reviewed_by": "owner"}, "only the owner"),
        ({"reviewed_by": "owner", "reviewed_at": WHEN}, "suspected conflict has no reviewer"),
        ({"description": " "}, "description"),
        ({"status": "resolved"}, "status"),
    ],
)
def test_evidence_conflict_rejects(over: dict[str, Any], message: str) -> None:
    claim, obs = make_claim(), make_obs()
    with pytest.raises(ValidationError, match=re.escape(message)):
        EvidenceConflict.model_validate(conflict_data(claim, [obs], **over))


def test_obs_ids_must_be_sorted_and_unique() -> None:
    claim = make_claim()
    a, b = make_obs(), make_obs(probe_id="P03", obs_id="ro-P03-1.4.5-windows")
    data = conflict_data(claim, [a, b])
    data["obs_ids"] = list(reversed(data["obs_ids"]))
    with pytest.raises(ValidationError, match="sorted and unique"):
        EvidenceConflict.model_validate(data)
    duplicated = conflict_data(claim, [a, b])
    duplicated["obs_ids"] = sorted([a.obs_id, a.obs_id, b.obs_id])
    with pytest.raises(ValidationError, match="sorted and unique"):
        EvidenceConflict.model_validate(duplicated)


def test_conflict_id_canonical_encoding() -> None:
    """``ec-`` + the first 12 hex characters of sha256 over the UTF-8 text: claim_id, then the
    sorted, unique obs_ids, newline-separated. Checked against a hand-built string."""
    claim = make_claim()
    a = make_obs(probe_id="P03", obs_id="ro-P03-1.4.5-windows")
    b = make_obs()  # ro-P02-1.4.5-windows, which sorts first
    encoded = f"{claim.claim_id}\nro-P02-1.4.5-windows\nro-P03-1.4.5-windows"
    expected = "ec-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]
    from_unsorted_input = new_suspected_conflict(
        claim.claim_id, [a.obs_id, b.obs_id, a.obs_id], "1.4", "d", "script"
    )
    assert from_unsorted_input.conflict_id == expected
    assert from_unsorted_input.obs_ids == ["ro-P02-1.4.5-windows", "ro-P03-1.4.5-windows"]
    assert conflict_id(claim.claim_id, ["ro-P02-1.4.5-windows", "ro-P03-1.4.5-windows"]) == expected
    assert re.fullmatch(r"ec-[0-9a-f]{12}", expected)


def test_new_conflicts_are_always_suspected() -> None:
    claim, obs = make_claim(), make_obs()
    for detector in ("script", "owner"):
        c = new_suspected_conflict(claim.claim_id, [obs.obs_id], "1.4", "differs", detector)
        assert c.status == "suspected" and c.reviewed_by is None


def test_only_the_owner_reviews_and_records_are_never_modified() -> None:
    claim, obs = make_claim(), make_obs()
    conflict = make_conflict(claim, [obs])
    snapshot = (claim.model_dump(), obs.model_dump(), conflict.model_dump())
    confirmed = review_conflict(
        conflict,
        status="confirmed",
        reviewer="owner",
        reviewed_at=WHEN,
        resolution_note="the page describes a later release",
    )
    assert confirmed.status == "confirmed" and confirmed.reviewed_by == "owner"
    assert (claim.model_dump(), obs.model_dump(), conflict.model_dump()) == snapshot
    dismissed = review_conflict(conflict, status="dismissed", reviewer="owner", reviewed_at=WHEN)
    assert dismissed.status == "dismissed"
    with pytest.raises(ReviewError, match="only the owner"):
        review_conflict(conflict, status="confirmed", reviewer="script", reviewed_at=WHEN)
    with pytest.raises(ReviewError, match="already confirmed"):
        review_conflict(confirmed, status="dismissed", reviewer="owner", reviewed_at=WHEN)
    with pytest.raises(ValidationError):  # records are immutable
        conflict.status = "confirmed"  # type: ignore[misc]
