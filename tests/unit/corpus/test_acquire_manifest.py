"""Acquisition, manifest build, M1 comparison and cache verification against a local upstream."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from version_aware_rag.corpus.acquire import GitError, GitRepo, fetch_pinned, open_cache
from version_aware_rag.corpus.config import APPROVED_RELEASE_POSTS, CorpusConfig
from version_aware_rag.corpus.manifest import (
    CorpusManifest,
    build_manifest,
    compare_with_m1,
    content_hash,
    dumps_manifest,
    read_manifest,
    verify_cache,
    write_manifest,
)

from .conftest import Upstream, git, make_config, write_release_cache

CFG_SHA = "0" * 64


def _rel(tmp_path: Path) -> Path:
    """A verified fixture release-note cache (S2), required to build a manifest."""
    return write_release_cache(tmp_path / "release-cache")


def _fetch(up: Upstream, tmp_path: Path, commit: str | None = None) -> tuple[GitRepo, Path]:
    cfg = make_config(up, commit or up.commit1)
    cache = cfg.cache_path(tmp_path)
    return fetch_pinned(cfg, cache), cache


# ---------------------------------------------------------------- hash definitions


def test_content_hash_known_answer() -> None:
    expected = hashlib.sha256(b"a.md\t1\nb.md\t2").hexdigest()
    assert content_hash([("b.md", "2"), ("a.md", "1")]) == expected  # order-independent


def test_words_use_bytes_split_like_rag_m1() -> None:
    # bytes.split() only splits on ASCII whitespace; str.split() also splits on U+00A0.
    data = "a\u00a0b c".encode()
    assert len(data.split()) == 2
    assert len(data.decode().split()) == 3


# ---------------------------------------------------------------- acquisition


def test_fetch_checks_out_only_docs_licence_and_approved_posts(
    upstream: Upstream, tmp_path: Path
) -> None:
    repo, cache = _fetch(upstream, tmp_path)
    assert repo.rev_parse("HEAD") == upstream.commit1
    checked_out = sorted(
        p.relative_to(cache).as_posix()
        for p in cache.rglob("*")
        if p.is_file() and ".git" not in p.parts
    )
    assert "LICENSE" in checked_out
    posts = [p for p in checked_out if p.startswith("_posts/")]
    assert posts == sorted(APPROVED_RELEASE_POSTS)  # exact paths only
    assert "_posts/2024-09-09-announcing-duckdb-110.md" not in checked_out  # not approved
    docs = [p for p in checked_out if p not in posts and p != "LICENSE"]
    assert all(p.startswith("docs/") and p.endswith(".md") for p in docs)
    assert not any(p.endswith(".js") for p in checked_out)
    assert "README.md" not in checked_out


def test_fetch_is_idempotent(upstream: Upstream, tmp_path: Path) -> None:
    _fetch(upstream, tmp_path)
    repo, _ = _fetch(upstream, tmp_path)
    assert repo.rev_parse("HEAD") == upstream.commit1


def test_cache_with_other_remote_is_refused(upstream: Upstream, tmp_path: Path) -> None:
    _, cache = _fetch(upstream, tmp_path)
    git(cache, "remote", "set-url", "origin", "https://example.invalid/other.git")
    with pytest.raises(GitError, match="refusing to reuse"):
        _fetch(upstream, tmp_path)


def test_open_cache_errors(upstream: Upstream, tmp_path: Path) -> None:
    cfg = make_config(upstream, upstream.commit1)
    with pytest.raises(GitError, match="no corpus cache"):
        open_cache(cfg, tmp_path / "absent")
    _, cache = _fetch(upstream, tmp_path)
    with pytest.raises(GitError, match="not the pinned commit"):
        open_cache(make_config(upstream, upstream.commit2), cache)


def test_read_blobs_handles_duplicate_ids(upstream: Upstream, tmp_path: Path) -> None:
    repo, _ = _fetch(upstream, tmp_path)
    oid = repo.rev_parse(f"{upstream.commit1}:docs/1.2/sql/functions/map.md")
    same = repo.rev_parse(f"{upstream.commit1}:docs/1.3/sql/functions/map.md")
    assert oid == same  # identical text in two versions is one blob
    assert list(repo.read_blobs([oid, same, oid])) == [oid]


# ---------------------------------------------------------------- manifest


def test_manifest_matches_independent_expectations(upstream: Upstream, tmp_path: Path) -> None:
    cfg = make_config(upstream, upstream.commit1)
    repo = fetch_pinned(cfg, cfg.cache_path(tmp_path))
    manifest = build_manifest(cfg, repo, CFG_SHA, _rel(tmp_path))
    assert compare_with_m1(manifest, cfg) == []
    assert [v.version for v in manifest.versions] == ["1.2", "1.3", "1.4", "1.5", "2.0"]
    assert len(manifest.files) == 10
    assert {e.path.rsplit("/", 1)[-1] for e in manifest.excluded_files} == {"app.js"}
    assert len(manifest.excluded_files) == 5
    assert all(e.reason for e in manifest.excluded_files)
    for rec in manifest.files:
        blob = repo.read_blobs([rec.git_blob])[rec.git_blob]
        assert rec.sha256 == hashlib.sha256(blob).hexdigest()
        assert rec.bytes == len(blob)


def test_manifest_serialisation_is_deterministic_and_round_trips(
    upstream: Upstream, tmp_path: Path
) -> None:
    cfg = make_config(upstream, upstream.commit1)
    a = build_manifest(cfg, fetch_pinned(cfg, tmp_path / "a"), CFG_SHA, _rel(tmp_path))
    b = build_manifest(cfg, fetch_pinned(cfg, tmp_path / "b"), CFG_SHA, _rel(tmp_path))
    assert dumps_manifest(a) == dumps_manifest(b)  # two independent caches, same bytes
    text = dumps_manifest(a)
    assert text.endswith("\n") and "\r" not in text
    target = tmp_path / "out" / "manifest.json"
    write_manifest(a, target)
    assert read_manifest(target) == a


def test_wrong_expected_value_is_a_discrepancy(upstream: Upstream, tmp_path: Path) -> None:
    cfg = make_config(upstream, upstream.commit1, **{"1.3": {"content_hash": "f" * 64}})
    repo = fetch_pinned(cfg, cfg.cache_path(tmp_path))
    found = compare_with_m1(build_manifest(cfg, repo, CFG_SHA, _rel(tmp_path)), cfg)
    assert [(d.subject, d.field) for d in found] == [("1.3", "content_hash")]


def test_pinning_ignores_later_upstream_commits(upstream: Upstream, tmp_path: Path) -> None:
    """The upstream has a newer commit changing 1.4; the pinned commit's content is unchanged."""
    cfg = make_config(upstream, upstream.commit1)
    manifest = build_manifest(
        cfg, fetch_pinned(cfg, cfg.cache_path(tmp_path)), CFG_SHA, _rel(tmp_path)
    )
    assert compare_with_m1(manifest, cfg) == []


def test_changed_upstream_content_is_detected(upstream: Upstream, tmp_path: Path) -> None:
    """Pinning the newer commit while keeping the old approved evidence must be flagged."""
    old = make_config(upstream, upstream.commit1)
    new = make_config(upstream, upstream.commit2)
    expected_old = {v.version: v.expected for v in old.versions}
    stale = new.model_copy(
        update={
            "versions": tuple(
                v.model_copy(update={"expected": expected_old[v.version]}) for v in new.versions
            )
        }
    )
    manifest = build_manifest(
        stale, fetch_pinned(stale, stale.cache_path(tmp_path)), CFG_SHA, _rel(tmp_path)
    )
    found = {(d.subject, d.field) for d in compare_with_m1(manifest, stale)}
    assert found == {("1.4", "git_tree"), ("1.4", "content_hash"), ("1.4", "words")}


# ---------------------------------------------------------------- verification


def _verified(up: Upstream, tmp_path: Path) -> tuple[CorpusConfig, GitRepo, CorpusManifest]:
    cfg = make_config(up, up.commit1)
    repo = fetch_pinned(cfg, cfg.cache_path(tmp_path))
    committed = build_manifest(cfg, repo, CFG_SHA, _rel(tmp_path))
    return cfg, repo, committed


def test_verify_clean_cache(upstream: Upstream, tmp_path: Path) -> None:
    cfg, repo, committed = _verified(upstream, tmp_path)
    assert verify_cache(cfg, repo, committed, CFG_SHA, _rel(tmp_path)).ok


def test_verify_detects_changed_and_missing_files(upstream: Upstream, tmp_path: Path) -> None:
    cfg, repo, committed = _verified(upstream, tmp_path)
    (repo.path / "docs/1.5/index.md").write_bytes(b"tampered\n")
    (repo.path / "docs/2.0/sql/functions/map.md").unlink()
    report = verify_cache(cfg, repo, committed, CFG_SHA, _rel(tmp_path))
    assert not report.ok
    assert report.changed_files == ["docs/1.5/index.md"]
    assert report.missing_files == ["docs/2.0/sql/functions/map.md"]


def test_verify_detects_edited_committed_manifest(upstream: Upstream, tmp_path: Path) -> None:
    cfg, repo, committed = _verified(upstream, tmp_path)
    first = committed.files[0]
    edited = committed.model_copy(
        update={"files": (first.model_copy(update={"sha256": "e" * 64}), *committed.files[1:])}
    )
    report = verify_cache(cfg, repo, edited, CFG_SHA, _rel(tmp_path))
    assert not report.manifest_matches_snapshot
    assert any(first.path in d and "file changed" in d for d in report.snapshot_differences)


def test_verify_detects_config_change(upstream: Upstream, tmp_path: Path) -> None:
    cfg, repo, committed = _verified(upstream, tmp_path)
    report = verify_cache(cfg, repo, committed, "1" * 64, _rel(tmp_path))
    assert any(d.startswith("config_sha256") for d in report.snapshot_differences)
