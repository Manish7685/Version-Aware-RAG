"""S3b (owner decision L2): duckdb-web release posts as a separate, hash-verified source type."""

from __future__ import annotations

import copy
import hashlib
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.corpus.acquire import fetch_pinned, sparse_patterns
from version_aware_rag.corpus.config import APPROVED_RELEASE_POSTS, CorpusConfig, load_config
from version_aware_rag.corpus.manifest import build_manifest, compare_with_m1, verify_cache
from version_aware_rag.corpus.parse import parse_page

from .conftest import Upstream, make_config, post_file, write_release_cache

CFG_SHA = "0" * 64


def _raw(repo_root: Path) -> dict[str, Any]:
    return tomllib.loads((repo_root / "configs/corpus.toml").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- configuration


def test_posts_equal_rag_m1_manifest_evidence(repo_root: Path) -> None:
    text = (repo_root / "docs/design/RAG-M1_version_manifest.md").read_text(encoding="utf-8")
    m1 = {
        path: (blob, sha)
        for path, blob, sha in re.findall(
            r"^\| `(_posts/[^`]+)` \| `([0-9a-f]{40})` \| `([0-9a-f]{64})` \|$", text, flags=re.M
        )
    }
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    assert sorted(m1) == list(APPROVED_RELEASE_POSTS)
    assert {p.path: (p.git_blob, p.sha256) for p in cfg.release_posts.posts} == m1


def test_post_metadata_is_derived_from_explicit_fields(repo_root: Path) -> None:
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    post = next(p for p in cfg.release_posts.posts if p.release == "1.4.5")
    assert post.path == "_posts/2026-06-17-announcing-duckdb-145.md"
    assert (post.version, post.published_date) == ("1.4", "2026-06-17")
    url = post.canonical_url("https://duckdb.org", cfg.release_posts.url_pattern)
    assert url == "https://duckdb.org/2026/06/17/announcing-duckdb-145.html"
    assert cfg.release_posts.repository_licence == "MIT"
    assert cfg.release_posts.licence_applicability == "uncertain"
    assert cfg.release_posts.licence_status == "unverified"
    assert cfg.release_posts.redistributable is False
    assert len(cfg.release_posts.resolution_evidence) == 3


@pytest.mark.parametrize(
    "mutate",
    [
        lambda posts: posts.pop(),  # one approved post missing
        lambda posts: posts.append(
            {**posts[0], "path": "_posts/2024-09-09-announcing-duckdb-110.md"}
        ),
        lambda posts: posts.reverse(),  # order matters
        lambda posts: posts[0].update(release="1.2"),  # not x.y.z
        lambda posts: posts[0].update(sha256="F" * 64),  # not lowercase hex
    ],
)
def test_post_config_rejects_unapproved_changes(repo_root: Path, mutate: Any) -> None:
    raw = _raw(repo_root)
    posts = copy.deepcopy(raw["release_posts"]["posts"])
    mutate(posts)
    raw["release_posts"]["posts"] = posts
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


def test_sparse_checkout_lists_exactly_the_approved_posts(repo_root: Path) -> None:
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    post_patterns = [p for p in sparse_patterns(cfg) if p.startswith("/_posts/")]
    assert post_patterns == [f"/{p}" for p in APPROVED_RELEASE_POSTS]  # no wildcard over _posts/


# ---------------------------------------------------------------- manifest records (fixture)


def _built(up: Upstream, tmp_path: Path, **overrides: Any):  # type: ignore[no-untyped-def]
    cfg = make_config(up, up.commit1)
    if overrides:
        cfg = cfg.model_copy(update=overrides)
    repo = fetch_pinned(cfg, cfg.cache_path(tmp_path))
    rel = write_release_cache(tmp_path / "release-cache")
    return cfg, repo, rel, build_manifest(cfg, repo, CFG_SHA, rel)


def test_manifest_records_post_metadata_and_provenance(upstream: Upstream, tmp_path: Path) -> None:
    cfg, _, _, manifest = _built(upstream, tmp_path)
    assert [p.path for p in manifest.release_posts] == list(APPROVED_RELEASE_POSTS)
    post = next(p for p in manifest.release_posts if p.release == "2.0.1")
    assert post.source_kind == "release_post"
    fixture_title = "Announcing DuckDB 2.0.1"  # synthetic fixture front matter
    assert post.title_sha256 == hashlib.sha256(fixture_title.encode("utf-8")).hexdigest()
    assert post.attribution.startswith("DuckDB 2.0.1 release post, by ")
    # Schema 5: the title is restricted text and appears nowhere in the manifest.
    assert fixture_title not in manifest.model_dump_json()
    assert post.authors == ("The DuckDB team",)
    assert (post.version, post.published_date) == ("2.0", "2026-11-20")
    assert post.canonical_url == "https://example.invalid/2026/11/20/announcing-duckdb-201.html"
    assert post.source_commit == upstream.commit1 and post.source_repo == "fixture/docs"
    assert post.bytes == len(post_file(post.path))
    assert post.licence is None  # applicability to website text not established
    assert (post.licence_status, post.licence_applicability) == ("unverified", "uncertain")
    assert (post.repository_licence, post.repository_licence_file) == ("MIT", "LICENSE")
    assert post.redistributable is False
    assert post.copyright_date_discrepancy is True  # 2026 post, copyright line ends 2025
    assert post.copyright_note is not None and "2026" in post.copyright_note
    assert post.resolution_evidence == ("an explicit content licence",)
    assert "The DuckDB team" in post.attribution and "Copyright 2018-2025" in post.attribution
    assert compare_with_m1(manifest, cfg) == []
    # Separation: posts never appear among the versioned documentation files.
    assert not any(f.path.startswith("_posts/") for f in manifest.files)


def test_post_hash_mismatch_with_evidence_is_reported(upstream: Upstream, tmp_path: Path) -> None:
    cfg, _, _, manifest = _built(upstream, tmp_path)
    posts = list(cfg.release_posts.posts)
    posts[3] = posts[3].model_copy(update={"sha256": "a" * 64})
    stale = cfg.model_copy(
        update={"release_posts": cfg.release_posts.model_copy(update={"posts": tuple(posts)})}
    )
    found = compare_with_m1(manifest, stale)
    assert [(d.subject, d.field) for d in found] == [(posts[3].path, "sha256")]


def test_verify_detects_a_tampered_or_missing_post(upstream: Upstream, tmp_path: Path) -> None:
    cfg, repo, rel, manifest = _built(upstream, tmp_path)
    assert verify_cache(cfg, repo, manifest, CFG_SHA, rel).ok
    (repo.path / APPROVED_RELEASE_POSTS[0]).write_bytes(b"edited\n")
    (repo.path / APPROVED_RELEASE_POSTS[1]).unlink()
    report = verify_cache(cfg, repo, manifest, CFG_SHA, rel)
    assert not report.ok
    assert report.changed_files == [APPROVED_RELEASE_POSTS[0]]
    assert report.missing_files == [APPROVED_RELEASE_POSTS[1]]


# ---------------------------------------------------------------- parsing


def _parse_post(path: str = APPROVED_RELEASE_POSTS[-1]):  # type: ignore[no-untyped-def]
    return parse_page(
        raw=post_file(path),
        version="2.0",
        path=path,
        source_sha256="s",
        site_base="https://duckdb.org",
        source_kind="release_post",
        url="https://duckdb.org/2026/11/20/announcing-duckdb-201.html",
    )


def test_posts_parse_as_release_posts_with_their_own_url() -> None:
    doc = _parse_post()
    assert doc.source_kind == "release_post"
    assert doc.url == "https://duckdb.org/2026/11/20/announcing-duckdb-201.html"
    assert doc.title == "Announcing DuckDB 2.0.1" and doc.page_kind == "content"
    assert [b.kind for b in doc.blocks] == ["paragraph", "heading", "list"]
    assert doc.blocks[2].text == "- One fix see docs."


def test_post_parsing_is_deterministic_and_docs_default_to_docs_page() -> None:
    assert _parse_post().model_dump_json() == _parse_post().model_dump_json()
    docs = parse_page(
        raw=b"---\ntitle: X\n---\nText\n",
        version="1.2",
        path="docs/1.2/x.md",
        source_sha256="s",
        site_base="https://duckdb.org",
    )
    assert docs.source_kind == "docs_page"
    assert docs.url == "https://duckdb.org/docs/1.2/x.html"
