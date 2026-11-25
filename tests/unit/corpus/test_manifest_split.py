"""Manifest split (schema 4), release-post licence qualification and report redaction."""

from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.corpus.acquire import fetch_pinned
from version_aware_rag.corpus.config import CorpusConfig, ParsingConfig, SamplePage
from version_aware_rag.corpus.inspect import (
    WITHHELD,
    CorpusInspection,
    PageResult,
    render_inspection_report,
    render_samples,
)
from version_aware_rag.corpus.manifest import (
    CorpusManifest,
    ManifestIntegrityError,
    build_manifest,
    load_manifest,
    read_manifest,
    render_manifest,
    write_manifest,
)
from version_aware_rag.corpus.parse import parse_page

from .conftest import Upstream, make_config, write_release_cache

LIMIT = 512_000


def _write(up: Upstream, tmp_path: Path) -> tuple[CorpusManifest, Path]:
    cfg = make_config(up, up.commit1)
    repo = fetch_pinned(cfg, cfg.cache_path(tmp_path))
    manifest = build_manifest(cfg, repo, "0" * 64, write_release_cache(tmp_path / "rel"))
    target = tmp_path / "out" / "corpus_manifest.json"
    write_manifest(manifest, target)
    return manifest, target


def _problems(target: Path) -> list[str]:
    _, problems = load_manifest(target)
    return problems


def _rewrite_index(target: Path, version: str, part: Path) -> None:
    """Make the index agree with an edited part, so only the semantic problem remains."""
    import hashlib

    main = json.loads(target.read_text(encoding="utf-8"))
    lines = [ln for ln in part.read_text(encoding="utf-8").split("\n") if ln]
    for entry in main["file_index"]:
        if entry["version"] == version:
            entry["sha256"] = hashlib.sha256(part.read_bytes()).hexdigest()
            entry["records"] = len(lines)
    target.write_text(json.dumps(main), encoding="utf-8")


# ---------------------------------------------------------------- layout and determinism


def test_split_round_trips_and_is_deterministic(upstream: Upstream, tmp_path: Path) -> None:
    manifest, target = _write(upstream, tmp_path)
    assert read_manifest(target) == manifest
    parts = render_manifest(manifest)
    assert list(parts) == [
        "corpus_manifest.json",
        "files/1.2.jsonl",
        "files/1.3.jsonl",
        "files/1.4.jsonl",
        "files/1.5.jsonl",
        "files/2.0.jsonl",
    ]
    assert render_manifest(manifest) == parts  # byte-identical on repeat
    main = json.loads(parts["corpus_manifest.json"])
    assert "files" not in main
    for entry in main["file_index"]:
        data = parts[entry["path"]]
        records = [json.loads(ln) for ln in data.decode().splitlines()]
        assert entry["records"] == len(records) == 2
        assert [r["path"] for r in records] == sorted(r["path"] for r in records)
        assert all(r["version"] == entry["version"] for r in records)
        assert set(records[0]) == {"version", "path", "git_blob", "sha256", "bytes", "words"}
    assert _problems(target) == []


def test_missing_part_is_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    (target.parent / "files/1.3.jsonl").unlink()
    assert "files/1.3.jsonl: missing" in _problems(target)
    with pytest.raises(ManifestIntegrityError):
        read_manifest(target)


def test_modified_part_is_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    part = target.parent / "files/1.4.jsonl"
    part.write_text(
        part.read_text(encoding="utf-8").replace('"sha256": "', '"sha256": "0', 1)[:-1] + "\n",
        encoding="utf-8",
    )
    problems = _problems(target)
    assert "files/1.4.jsonl: modified (sha256 differs from the index)" in problems


def test_modified_record_that_keeps_the_index_hash_is_still_caught(
    upstream: Upstream, tmp_path: Path
) -> None:
    _, target = _write(upstream, tmp_path)
    part = target.parent / "files/1.4.jsonl"
    records = [json.loads(ln) for ln in part.read_text(encoding="utf-8").splitlines()]
    records[0]["sha256"] = "f" * 64
    part.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8"
    )
    _rewrite_index(target, "1.4", part)
    assert "files/1.4.jsonl: records do not reproduce the version content_hash" in _problems(target)


def test_unexpected_files_are_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    (target.parent / "files/1.1.jsonl").write_text("", encoding="utf-8")
    (target.parent / "files/notes.txt").write_text("x", encoding="utf-8")
    problems = _problems(target)
    assert "files/1.1.jsonl: unexpected file (not in file_index)" in problems
    assert "files/notes.txt: unexpected file (not in file_index)" in problems


def test_duplicate_records_are_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    part = target.parent / "files/1.2.jsonl"
    lines = part.read_text(encoding="utf-8").splitlines()
    part.write_text("\n".join([lines[0], lines[0], *lines[1:]]) + "\n", encoding="utf-8")
    _rewrite_index(target, "1.2", part)
    problems = _problems(target)
    assert any("duplicate record for docs/1.2/" in p for p in problems), problems


def test_inconsistent_version_assignment_is_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    src, dst = target.parent / "files/1.2.jsonl", target.parent / "files/1.3.jsonl"
    moved = src.read_text(encoding="utf-8").splitlines()[0]
    lines = sorted([*dst.read_text(encoding="utf-8").splitlines(), moved])
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _rewrite_index(target, "1.3", dst)
    problems = _problems(target)
    assert any("inconsistent version: record says 1.2 for docs/1.2/" in p for p in problems)


def test_unsorted_part_and_index_gaps_are_detected(upstream: Upstream, tmp_path: Path) -> None:
    _, target = _write(upstream, tmp_path)
    part = target.parent / "files/2.0.jsonl"
    part.write_text(
        "\n".join(reversed(part.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8"
    )
    _rewrite_index(target, "2.0", part)
    main = json.loads(target.read_text(encoding="utf-8"))
    main["file_index"] = [e for e in main["file_index"] if e["version"] != "1.5"]
    target.write_text(json.dumps(main), encoding="utf-8")
    problems = _problems(target)
    assert "files/2.0.jsonl: records are not sorted by path" in problems
    assert any(p.startswith("file_index covers") for p in problems)
    assert "files/1.5.jsonl: unexpected file (not in file_index)" in problems


def test_unreadable_main_manifest(tmp_path: Path) -> None:
    target = tmp_path / "corpus_manifest.json"
    target.write_text("{not json", encoding="utf-8")
    manifest, problems = load_manifest(target)
    assert manifest is None and problems[0].startswith("corpus_manifest.json: unreadable")


def test_committed_manifest_parts_fit_the_size_budget(repo_root: Path) -> None:
    root = repo_root / "data/manifest"
    sizes = {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in [root / "corpus_manifest.json", *sorted((root / "files").glob("*.jsonl"))]
    }
    assert len(sizes) == 6
    assert all(size <= LIMIT for size in sizes.values()), sizes
    hook = (repo_root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "--maxkb=500" in hook  # the budget itself is unchanged


# ---------------------------------------------------------------- licence qualification


def _raw(repo_root: Path) -> dict[str, Any]:
    return tomllib.loads((repo_root / "configs/corpus.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "changes",
    [{"licence_status": "verified"}, {"resolution_evidence": []}],
)
def test_uncertain_applicability_must_be_unverified_and_explained(
    repo_root: Path, changes: dict[str, Any]
) -> None:
    raw = _raw(repo_root)
    raw["release_posts"] = {**copy.deepcopy(raw["release_posts"]), **changes}
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


def test_committed_post_records_are_qualified(repo_root: Path) -> None:
    main = json.loads(
        (repo_root / "data/manifest/corpus_manifest.json").read_text(encoding="utf-8")
    )
    posts = main["release_posts"]
    assert len(posts) == 17
    for p in posts:
        assert p["licence"] is None
        assert (p["licence_status"], p["licence_applicability"]) == ("unverified", "uncertain")
        assert (p["repository_licence"], p["redistributable"]) == ("MIT", False)
        assert p["repository_copyright"] == "Copyright 2018-2025 Stichting DuckDB Foundation"
        assert len(p["resolution_evidence"]) == 3
        assert p["copyright_date_discrepancy"] == (p["published_date"][:4] > "2025")
    assert sum(p["copyright_date_discrepancy"] for p in posts) == 11  # 2026-01-26 (1.4.4) onwards


# ---------------------------------------------------------------- redaction of restricted text


def _page(redistributable: bool) -> PageResult:
    source = "---\ntitle: Secret Post\n---\n## Distinctive heading\n\nDistinctive sentence text.\n"
    doc = parse_page(
        raw=source.encode(),
        version="2.0",
        path="_posts/2026-01-01-x.md",
        source_sha256="s",
        site_base="https://duckdb.org",
        source_kind="release_post",
    )
    return PageResult(doc, source, 1.0, redistributable=redistributable)


def test_restricted_pages_show_structure_only_in_committed_reports() -> None:
    insp = CorpusInspection("c" * 40, "0" * 64, pages=[_page(redistributable=False)])
    parsing = ParsingConfig(
        site_base="https://duckdb.org",
        excluded_pages=(),
        samples=(SamplePage(path="_posts/2026-01-01-x.md", label="Post"),),
    )
    committed = render_samples(insp, parsing) + render_inspection_report(insp)
    assert "Distinctive" not in committed
    assert WITHHELD in committed and "- **heading** (lines 4-4, level 2)" in committed
    local = render_samples(insp, parsing, include_restricted=True)
    assert "Distinctive sentence text." in local


def test_redistributable_pages_are_shown_in_full() -> None:
    insp = CorpusInspection("c" * 40, "0" * 64, pages=[_page(redistributable=True)])
    parsing = ParsingConfig(
        site_base="https://duckdb.org",
        excluded_pages=(),
        samples=(SamplePage(path="_posts/2026-01-01-x.md", label="Post"),),
    )
    assert "Distinctive sentence text." in render_samples(insp, parsing)
