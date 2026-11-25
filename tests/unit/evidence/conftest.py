"""Factories for valid evidence records. All text is fixture text written for these tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.evidence.models import (
    DocClaim,
    EvidenceConflict,
    RuntimeObservation,
    conflict_id,
    doc_claim_id,
    observation_id,
)
from version_aware_rag.evidence.verify import ClaimSource
from version_aware_rag.probes.pins import load_pins

ROOT = Path(__file__).resolve().parents[3]
COMMIT = "d01484462710d49206816c9eeaeb822361ebe81c"
WHEEL_WIN_145 = "6b8d992d957c89e83d697756f6c5b5aea910d6bf16e2666da4c508f891932ae2"  # 1.4.5
WHEEL_LINUX_145 = "aa294d028c149ca21110e366eaffcb4fc9ab11d7d203d50f7bc49a07ab34b960"
FIXTURE_SOURCE = "# Title\n\nIntro line.\n\n`date_trunc` on DATE returns DATE here.\nEnd.\n"
SPAN = "`date_trunc` on DATE returns DATE here."
WHEN = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def claim_data(**over: Any) -> dict[str, Any]:
    path = over.get("source_path", "docs/1.4/sql/functions/date.md")
    span = over.get("span_text", SPAN)
    start = FIXTURE_SOURCE.find(span) if span in FIXTURE_SOURCE else 0
    data: dict[str, Any] = {
        "claim_id": doc_claim_id(path, span),
        "source_kind": "docs_page",
        "source_repo": "duckdb/duckdb-web",
        "source_commit": COMMIT,
        "source_path": path,
        "source_sha256": sha(FIXTURE_SOURCE),
        "source_url": "https://duckdb.org/docs/1.4/sql/functions/date.html",
        "heading_path": ["Title"],
        "doc_version": "1.4",
        "introduced_in": None,
        "span_text": span,
        "span_char_start": start,
        "span_char_end": start + len(span),
        "claim_summary": "`date_trunc` on DATE returns DATE",
        "claimed_value": "DATE",
        "verification_level": "V1",
        "verified_by": None,
        "verified_at": None,
        "created_by": "script",
    }
    data.update(over)
    if "claim_id" not in over:
        data["claim_id"] = doc_claim_id(data["source_path"], data["span_text"])
    return data


def make_claim(**over: Any) -> DocClaim:
    return DocClaim.model_validate(claim_data(**over))


def obs_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "probe_id": "P02",
        "probe_sha256": sha("SELECT typeof(date_trunc('month', DATE '2026-03-27'))"),
        "probe_kind": "sql",
        "package": "duckdb",
        "package_version": "1.4.5",
        "wheel_sha256": WHEEL_WIN_145,
        "applies_to_doc_version": "1.4",
        "python_version": "3.12.16",
        "platform": "Windows-11-10.0.26200-SP0",
        "observed_status": "ok",
        "observed_value": "'DATE'",
        "run_at": WHEN,
        "runner_commit": "0" * 40,
        "verification_level": "auto_executed",
    }
    platform_tag = over.pop("platform_tag", "windows")
    data.update(over)
    data.setdefault(
        "obs_id", observation_id(data["probe_id"], data["package_version"], platform_tag)
    )
    return data


def make_obs(**over: Any) -> RuntimeObservation:
    return RuntimeObservation.model_validate(obs_data(**over))


def conflict_data(claim: DocClaim, obs: list[RuntimeObservation], **over: Any) -> dict[str, Any]:
    ids = sorted(o.obs_id for o in obs)
    data: dict[str, Any] = {
        "conflict_id": conflict_id(claim.claim_id, ids),
        "claim_id": claim.claim_id,
        "obs_ids": ids,
        "doc_version": claim.doc_version,
        "description": "page says DATE; the package returns another type",
        "detected_by": "script",
        "status": "suspected",
        "resolution_note": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }
    data.update(over)
    return data


def make_conflict(claim: DocClaim, obs: list[RuntimeObservation], **over: Any) -> EvidenceConflict:
    return EvidenceConflict.model_validate(conflict_data(claim, obs, **over))


def fixture_index(
    text: str | None = FIXTURE_SOURCE, **over: Any
) -> dict[tuple[str, str], ClaimSource]:
    """A one-source index for the docs page used by ``make_claim``."""
    data = text.encode("utf-8") if text is not None else None
    fields: dict[str, Any] = {
        "kind": "docs_page",
        "path": "docs/1.4/sql/functions/date.md",
        "repo": "duckdb/duckdb-web",
        "commit": COMMIT,
        "sha256": sha(FIXTURE_SOURCE),
        "doc_version": "1.4",
        "read_bytes": lambda: data,
    }
    fields.update(over)
    return {(fields["kind"], fields["path"]): ClaimSource(**fields)}


@pytest.fixture
def pins():  # type: ignore[no-untyped-def]
    return load_pins(ROOT / "configs/probes.toml")
