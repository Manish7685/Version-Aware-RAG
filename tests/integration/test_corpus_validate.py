"""RAG-M3 S5b on the real corpus: ``validate_corpus`` read-only, all rules, pinned warnings.

The leak scan applies the owner's S5b review policy: lines shared with the approved versioned
docs are not restricted, and the four documented quotations in immutable files are covered by
exact line fingerprints. The counts are pinned so any change is noticed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from version_aware_rag.corpus.build import ALL_OUTPUTS
from version_aware_rag.corpus.commands import _committable_files
from version_aware_rag.corpus.config import (
    load_chunking_config,
    load_config,
    load_parsing_config,
)
from version_aware_rag.corpus.validate import ValidateInputs, ValidationReport, validate_corpus

pytestmark = pytest.mark.corpus

REPO = Path(__file__).resolve().parents[2]
# Release-post lines over 25 characters: 1,262, of which 121 also occur in the versioned docs:
# 118 identical lines and 3 code lines contained token-aligned in docs code (the 1.3 FTS query
# and two MERGE clauses), leaving 1,141 restricted.
POST_LINES = 1141
POST_LINES_SHARED = 121
RELEASE_LINES_SHARED = 0


def _digest(paths: list[Path]) -> dict[str, str]:
    return {
        p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
        if p.is_file()
    }


def _watched(cache: Path) -> list[Path]:
    return [
        *(REPO / p for p in ALL_OUTPUTS),
        *(REPO / "data/build").rglob("*"),
        *(cache / ".git").rglob("*"),
    ]


@pytest.fixture(scope="module")
def run() -> tuple[ValidationReport, bool]:
    cfg, cfg_sha = load_config(REPO / "configs/corpus.toml")
    cache = cfg.cache_path(REPO)
    if not (cache / ".git").is_dir() or not (REPO / "data/build/chunks/1.2.jsonl").is_file():
        pytest.skip("corpus cache or generated outputs not available in this checkout")
    parsing, parsing_sha = load_parsing_config(REPO / "configs/parsing.toml")
    chunking, chunking_sha = load_chunking_config(REPO / "configs/chunking.toml")
    committable = _committable_files(REPO)  # the same list `vrag corpus validate` uses
    assert committable is not None
    inputs = ValidateInputs(
        root=REPO,
        cfg=cfg,
        cfg_sha256=cfg_sha,
        manifest_path=REPO / cfg.paths.manifest,
        parsing=parsing,
        parsing_sha256=parsing_sha,
        chunking=chunking,
        chunking_sha256=chunking_sha,
        cache=cache,
        release_cache=REPO / cfg.release_notes.cache_dir,
        committable_files=committable,
    )
    before = _digest(_watched(cache))
    report = validate_corpus(inputs)
    unchanged = _digest(_watched(cache)) == before
    return report, unchanged


def test_validation_is_read_only(run: tuple[ValidationReport, bool]) -> None:
    _, unchanged = run
    assert unchanged  # outputs, data/build tree and the cache's .git byte-identical


def test_every_rule_passes(run: tuple[ValidationReport, bool]) -> None:
    report, _ = run
    assert report.missing == []
    assert Counter(f.rule for f in report.errors) == {}, [f.message for f in report.errors]
    assert report.exit_code == 0


def test_leak_scan_policy_on_the_real_repository(run: tuple[ValidationReport, bool]) -> None:
    report, _ = run
    leak = report.counts["leak_scan"]
    assert isinstance(leak, dict)
    assert leak["exception_occurrences"] == 0  # no exceptions remain (owner, 2026-11-23)
    assert leak["post_lines_shared_with_docs"] == POST_LINES_SHARED
    assert leak["release_lines_shared_with_docs"] == RELEASE_LINES_SHARED
    assert leak["post_lines"] == POST_LINES and leak["release_lines"] == 305
    quotes = report.counts["quote_scan"]  # R7e: short quotations of release-post text
    assert isinstance(quotes, dict)
    assert (quotes["release_posts"], quotes["findings"]) == (17, 0)
    assert leak["handles"] == 34


def test_pinned_warnings_and_counts(run: tuple[ValidationReport, bool]) -> None:
    report, _ = run
    assert sorted(f.rule for f in report.warnings) == ["R11", "R3w", "W-duplicates", "W-oversize"]
    counts = report.counts
    assert counts["chunks"] == 31680 and counts["unresolved"] == 0
    assert counts["version_status_resolved"] == 31680
    assert counts["oversize"] == {"api_entry_block": 4, "code_block": 164, "list_item": 3}
    assert counts["near_empty_pages"] == 6  # S5-D7
    assert counts["section_path_fallback"] == 22  # the release-note chunks (no title, S4d)
    assert counts["cross_version_duplicates"] == 6283
    assert counts["coverage"] == {
        "blocks_split": 174,
        "blocks_verbatim": 79228,
        "excluded_pages": 26,
        "headings": 36578,
        "pages": 2048,
        "q7_transformations": 305,
        "release_blocks_split": 4,
        "release_blocks_verbatim": 8,
    }
