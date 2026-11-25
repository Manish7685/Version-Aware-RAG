"""Cross-record validation: claims against their source, observations against the pins (D2),
references, and the independence rule for reproduced observations (D4)."""

from __future__ import annotations

from typing import Any

import pytest

from version_aware_rag.evidence.store import EvidenceSet, LoadProblem
from version_aware_rag.evidence.verify import validate_evidence

from .conftest import (
    COMMIT,
    FIXTURE_SOURCE,
    WHEEL_LINUX_145,
    fixture_index,
    make_claim,
    make_conflict,
    make_obs,
    sha,
)


def _codes(report: Any) -> list[str]:
    return [f"{f.severity}:{f.code}" for f in report.findings]


def test_a_valid_claim_passes(pins: Any) -> None:
    report = validate_evidence(EvidenceSet(claims=(make_claim(),)), [], fixture_index(), pins)
    assert report.findings == [] and report.incomplete == [] and report.exit_code == 0


@pytest.mark.parametrize(
    ("index_over", "code"),
    [
        ({"path": "docs/1.4/other.md"}, "error:E-source"),
        ({"kind": "migration_guide"}, "error:E-source"),
        ({"repo": "someone/else"}, "error:E-manifest"),
        ({"commit": "f" * 40}, "error:E-manifest"),
        ({"sha256": sha("other")}, "error:E-manifest"),
        ({"doc_version": "1.5"}, "error:E-manifest"),
    ],
)
def test_claim_must_match_the_manifest_record(
    pins: Any, index_over: dict[str, Any], code: str
) -> None:
    report = validate_evidence(
        EvidenceSet(claims=(make_claim(),)), [], fixture_index(**index_over), pins
    )
    assert code in _codes(report) and report.exit_code == 1


def test_tampered_cache_is_an_error(pins: Any) -> None:
    index = fixture_index(text=FIXTURE_SOURCE.replace("Intro", "Edited"))
    report = validate_evidence(EvidenceSet(claims=(make_claim(),)), [], index, pins)
    assert _codes(report) == ["error:E-cache"]


def test_missing_cache_makes_the_check_incomplete(pins: Any) -> None:
    report = validate_evidence(
        EvidenceSet(claims=(make_claim(),)), [], fixture_index(text=None), pins
    )
    assert report.findings == [] and len(report.incomplete) == 1 and report.exit_code == 2


def test_claims_without_a_manifest_are_incomplete(pins: Any) -> None:
    report = validate_evidence(EvidenceSet(claims=(make_claim(),)), [], None, pins)
    assert report.exit_code == 2


@pytest.mark.parametrize(("level", "severity"), [("V1", "error"), ("V0", "warning")])
def test_span_not_at_its_offsets(pins: Any, level: str, severity: str) -> None:
    claim = make_claim(
        span_char_start=3, span_char_end=3 + len(make_claim().span_text), verification_level=level
    )
    report = validate_evidence(EvidenceSet(claims=(claim,)), [], fixture_index(), pins)
    assert _codes(report) == [f"{severity}:E-span"]
    assert "occurs elsewhere in the source" in report.findings[0].message
    assert claim.span_text not in report.findings[0].message  # never quoted


def test_span_absent_from_the_source(pins: Any) -> None:
    claim = make_claim(
        span_text="Text that is not in the fixture source.",
        span_char_start=0,
        span_char_end=len("Text that is not in the fixture source."),
    )
    report = validate_evidence(EvidenceSet(claims=(claim,)), [], fixture_index(), pins)
    assert _codes(report) == ["error:E-span"]
    assert "does not occur in the source" in report.findings[0].message


def test_restricted_claim_is_verified_from_its_sidecar_text(pins: Any) -> None:
    body = "## Changes\n\n- Fixture release-note line.\n"
    span = "Fixture release-note line."
    start = body.index(span)
    claim = make_claim(
        source_kind="github_release",
        source_repo="duckdb/duckdb",
        source_commit=None,
        source_path="v1.3.2",
        source_url="https://github.com/duckdb/duckdb/releases/tag/v1.3.2",
        source_sha256=sha(body),
        doc_version="1.3",
        span_text=span,
        span_char_start=start,
        span_char_end=start + len(span),
    )
    index = fixture_index(
        text=body,
        kind="github_release",
        path="v1.3.2",
        repo="duckdb/duckdb",
        commit=None,
        sha256=sha(body),
        doc_version="1.3",
    )
    assert validate_evidence(EvidenceSet(claims=(claim,)), [], index, pins).exit_code == 0
    moved = claim.model_copy(
        update={"span_char_start": start + 1, "span_char_end": start + 1 + len(span)}
    )
    assert _codes(validate_evidence(EvidenceSet(claims=(moved,)), [], index, pins)) == [
        "error:E-span"
    ]


def test_a_valid_observation_passes(pins: Any) -> None:
    report = validate_evidence(EvidenceSet(observations=(make_obs(),)), [], None, pins)
    assert report.findings == [] and report.exit_code == 0


@pytest.mark.parametrize(
    "over",
    [
        {"package_version": "1.4.4"},
        {"applies_to_doc_version": "1.5"},
        {"wheel_sha256": WHEEL_LINUX_145},  # the Linux wheel on a Windows observation
        {"python_version": "3.12.15"},
    ],
)
def test_observation_must_match_the_pins(pins: Any, over: dict[str, Any]) -> None:
    report = validate_evidence(EvidenceSet(observations=(make_obs(**over),)), [], None, pins)
    assert "error:E-pin" in _codes(report)


def test_linux_observation_uses_the_linux_wheel(pins: Any) -> None:
    linux = make_obs(
        platform_tag="linux",
        platform="Linux-6.8.0-x86_64-with-glibc2.39",
        wheel_sha256=WHEEL_LINUX_145,
    )
    assert validate_evidence(EvidenceSet(observations=(linux,)), [], None, pins).findings == []


def test_reproduced_needs_a_matching_independent_run(pins: Any) -> None:
    win = make_obs()
    linux_kw = {
        "platform_tag": "linux",
        "platform": "Linux-6.8.0-x86_64",
        "wheel_sha256": WHEEL_LINUX_145,
    }
    alone = make_obs(verification_level="reproduced", **linux_kw)
    report = validate_evidence(EvidenceSet(observations=(alone,)), [], None, pins)
    assert _codes(report) == ["error:E-reproduced"]
    differs = make_obs(verification_level="reproduced", observed_value="'TIMESTAMP'", **linux_kw)
    report = validate_evidence(EvidenceSet(observations=(win, differs)), [], None, pins)
    assert _codes(report) == ["error:E-reproduced"]
    matches = make_obs(verification_level="reproduced", **linux_kw)
    assert (
        validate_evidence(EvidenceSet(observations=(win, matches)), [], None, pins).findings == []
    )


def test_conflict_references(pins: Any) -> None:
    claim, obs = make_claim(), make_obs()
    ok = make_conflict(claim, [obs])
    report = validate_evidence(EvidenceSet((claim,), (obs,), (ok,)), [], fixture_index(), pins)
    assert report.findings == []
    report = validate_evidence(EvidenceSet((), (obs,), (ok,)), [], fixture_index(), pins)
    assert "does not exist" in report.findings[0].message
    report = validate_evidence(EvidenceSet((claim,), (), (ok,)), [], fixture_index(), pins)
    assert any(f"observation {obs.obs_id} does not exist" in f.message for f in report.findings)
    other = make_obs(
        package_version="1.5.6",
        applies_to_doc_version="1.5",
        wheel_sha256="09ff51b230219f0d8b47fc8a1e17fb595ba9fab0c3d96a6de4d00b8ff86b3cf1",
    )
    wrong = make_conflict(claim, [other])
    report = validate_evidence(EvidenceSet((claim,), (other,), (wrong,)), [], fixture_index(), pins)
    assert any("another docs version" in f.message for f in report.findings)


def test_load_problems_are_errors_and_errors_win_over_incomplete(pins: Any) -> None:
    problem = LoadProblem("doc_claims.jsonl", 3, "not valid JSON")
    report = validate_evidence(
        EvidenceSet(claims=(make_claim(),)), [problem], fixture_index(text=None), pins
    )
    assert report.incomplete and report.exit_code == 1
    assert report.findings[0].message == "doc_claims.jsonl:3: not valid JSON"


def test_commit_constant_is_the_pinned_docs_commit() -> None:
    assert COMMIT == "d01484462710d49206816c9eeaeb822361ebe81c"


# D1: for restricted sources the span check verifies the local sidecar text, so a mismatch is an
# error at every verification level; only unrestricted V0 drafts are downgraded to a warning.

RELEASE_BODY = "## Changes\n\n- Fixture release-note line one.\n- Fixture release-note line two.\n"
RELEASE_INDEX_FIELDS: dict[str, Any] = {
    "kind": "github_release",
    "path": "v1.3.2",
    "repo": "duckdb/duckdb",
    "commit": None,
    "sha256": sha(RELEASE_BODY),
    "doc_version": "1.3",
}


def _restricted_v0(span: str, start: int) -> Any:
    """A V0 release-note claim whose claim_id and offset length are consistent with its text."""
    return make_claim(
        source_kind="github_release",
        source_repo="duckdb/duckdb",
        source_commit=None,
        source_path="v1.3.2",
        source_url="https://github.com/duckdb/duckdb/releases/tag/v1.3.2",
        source_sha256=sha(RELEASE_BODY),
        doc_version="1.3",
        span_text=span,
        span_char_start=start,
        span_char_end=start + len(span),
        verification_level="V0",
    )


def test_restricted_v0_claim_at_the_wrong_offsets_is_an_error(pins: Any) -> None:
    span = "Fixture release-note line two."
    claim = _restricted_v0(span, RELEASE_BODY.index(span) - 2)  # internally consistent, misplaced
    index = fixture_index(text=RELEASE_BODY, **RELEASE_INDEX_FIELDS)
    report = validate_evidence(EvidenceSet(claims=(claim,)), [], index, pins)
    assert _codes(report) == ["error:E-span"] and report.exit_code == 1
    assert "occurs elsewhere in the source" in report.findings[0].message


def test_restricted_v0_claim_with_text_absent_from_the_source_is_an_error(pins: Any) -> None:
    claim = _restricted_v0("Text that this fixture release body never says.", 0)
    index = fixture_index(text=RELEASE_BODY, **RELEASE_INDEX_FIELDS)
    report = validate_evidence(EvidenceSet(claims=(claim,)), [], index, pins)
    assert _codes(report) == ["error:E-span"] and report.exit_code == 1
    assert "does not occur in the source" in report.findings[0].message


def test_unrestricted_v0_mismatch_stays_a_warning(pins: Any) -> None:
    span = make_claim().span_text
    claim = make_claim(span_char_start=3, span_char_end=3 + len(span), verification_level="V0")
    report = validate_evidence(EvidenceSet(claims=(claim,)), [], fixture_index(), pins)
    assert _codes(report) == ["warning:E-span"] and report.exit_code == 0
