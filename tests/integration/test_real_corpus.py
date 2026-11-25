"""Checks against the real pinned DuckDB snapshot. Skipped unless the cache has been fetched
(`vrag corpus fetch`); the S6 CI job fetches it and runs these."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from version_aware_rag.corpus.acquire import GitRepo, open_cache
from version_aware_rag.corpus.config import CorpusConfig, load_config
from version_aware_rag.corpus.manifest import (
    build_manifest,
    compare_with_m1,
    dumps_manifest,
    read_manifest,
    stale_manifest_parts,
    verify_cache,
)
from version_aware_rag.corpus.releases import verify_releases

pytestmark = pytest.mark.corpus


Real = tuple[CorpusConfig, str, GitRepo, Path]


@pytest.fixture
def real(repo_root: Path) -> Real:
    cfg, sha = load_config(repo_root / "configs/corpus.toml")
    cache = cfg.cache_path(repo_root)
    if not (cache / ".git").is_dir():
        pytest.skip(f"corpus cache not fetched at {cache}")
    release_cache = repo_root / cfg.release_notes.cache_dir
    if not release_cache.is_dir():
        pytest.skip(f"release-note cache not fetched at {release_cache}")
    return cfg, sha, open_cache(cfg, cache), release_cache


def test_all_five_versions_match_rag_m1(real: Real) -> None:
    cfg, sha, repo, rel = real
    manifest = build_manifest(cfg, repo, sha, rel)
    assert compare_with_m1(manifest, cfg) == []
    assert sum(v.md_files for v in manifest.versions) == 2031


def test_committed_manifest_is_current_and_verified(real: Real, repo_root: Path) -> None:
    cfg, sha, repo, rel = real
    path = repo_root / cfg.paths.manifest
    assert path.read_bytes() == dumps_manifest(build_manifest(cfg, repo, sha, rel)).encode()
    assert stale_manifest_parts(build_manifest(cfg, repo, sha, rel), path) == []
    assert verify_cache(cfg, repo, read_manifest(path), sha, rel).ok


def test_real_release_notes_verify_and_text_not_in_manifest(real: Real, repo_root: Path) -> None:
    """The four cached bodies match their recorded hashes, and no line of their text (beyond
    trivially short lines) appears in the committed manifest."""
    cfg, _, _, rel = real
    report = verify_releases(cfg.release_notes, rel)
    assert report.ok, report.checks
    manifest_text = (repo_root / cfg.paths.manifest).read_text(encoding="utf-8")
    for r in cfg.release_notes.included:
        body = (rel / f"{r.tag}.md").read_text(encoding="utf-8")
        long_lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) > 40]
        assert long_lines, r.tag
        assert not any(ln in manifest_text for ln in long_lines), r.tag


# The S2 manifest (development commit 7e4125f, before S3b added release posts) as pinned digests:
# sha256 of the canonical JSON (sorted keys, compact separators, UTF-8) of each part compared below.
# Pinned rather than read with `git show`, so the check also runs on a checkout without that
# commit in its history (for example a single-snapshot release).
S2_MANIFEST_KEYS = frozenset(
    {"config_sha256", "excluded_files", "files", "generator", "library", "release_notes",
     "schema_version", "source", "versions"}
)  # fmt: skip
S2_MANIFEST_DIGESTS = {
    "versions": "adfbd0ecbc66570c251151d8103b93b6c5917629a5c692e13b9468a41857733e",
    "excluded_files": "65a6aef3da7986cb470273179312ddfb20e654af600248a5ffc826e1d4704433",
    "source": "94ed440b8bf302e9d72aaf20165a97959adb72fdf357dce7c89d6ec92fd35561",
    "release_notes": "23b44e8b98a826f3b722cb0f2e1f1394f42e6cfa60db0cc01f8e163926971407",
    "library": "123f2385fba5661c88eee6fcb890edeb293345f4916aefd2d3247959f136da68",
    "files": "f94d00cd7f2c1e9693c0ff7c2ab5bdd769e8db36d046704e9fc49d09f855b6af",
}


def _canonical_sha256(obj: object) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_versioned_docs_and_release_notes_unchanged_by_s3b(real: Real, repo_root: Path) -> None:
    """The five docs snapshots, LICENSE and the S2 release-note evidence in the committed manifest
    are identical to the S2 commit (7e4125f, pinned as digests above); S3b only adds
    release_posts."""
    cfg, _, _, _ = real
    new = json.loads((repo_root / cfg.paths.manifest).read_text(encoding="utf-8"))
    for key in ("versions", "excluded_files", "source", "release_notes", "library"):
        assert _canonical_sha256(new[key]) == S2_MANIFEST_DIGESTS[key], key
    # The per-file docs records now live in files/<version>.jsonl (S3 split): same records.
    split_files = [
        r.model_dump(mode="json") for r in read_manifest(repo_root / cfg.paths.manifest).files
    ]
    assert _canonical_sha256(split_files) == S2_MANIFEST_DIGESTS["files"]
    assert "release_posts" not in S2_MANIFEST_KEYS and len(new["release_posts"]) == 17
