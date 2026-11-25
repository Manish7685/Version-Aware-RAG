"""A local git repository standing in for duckdb-web, so acquisition runs offline."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.corpus.config import (
    APPROVED_RELEASE_POSTS,
    APPROVED_RELEASE_TAGS,
    APPROVED_VERSIONS,
    CorpusConfig,
)

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> str:
    assert GIT is not None
    proc = subprocess.run(
        [GIT, "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


# Two pages per version, one shared verbatim across versions (like real version confusion),
# one non-Markdown asset that must be recorded as excluded and never checked out.
def fixture_files(version: str) -> dict[str, bytes]:
    return {
        f"docs/{version}/index.md": f"---\ntitle: Docs {version}\n---\n# Docs {version}\n".encode(),
        # \xc2\xa0 is a UTF-8 no-break space: bytes.split() keeps it inside a word (RAG-M1 rule)
        f"docs/{version}/sql/functions/map.md": (
            b"# Map\n\n`map_extract(map, key)`\xc2\xa0returns a value.\n"
        ),
        f"docs/{version}/tutorial/app.js": b"console.log('asset');\n",
    }


LICENCE = b"MIT licence text for the fixture\n"


def release_body(tag: str) -> str:
    """Synthetic release text with CRLF and non-ASCII, to prove bytes are kept exactly."""
    n = tag.replace("v", "").replace(".", "")
    return (
        f"Bug fix release {tag}.\r\n\r\n## What's Changed\r\n"
        f"* Fix café ✓ parsing by @alice in https://github.com/duckdb/duckdb/pull/1{n}\r\n"
        f"* Faster joins by @bob in https://github.com/duckdb/duckdb/pull/2{n}\r\n"
    )


RELEASE_IDS = {tag: 1000 + i for i, tag in enumerate(APPROVED_RELEASE_TAGS)}


def release_notes_config(**overrides: Any) -> dict[str, Any]:
    """A release_notes section whose expected hashes match ``release_body``."""
    included = []
    for tag in APPROVED_RELEASE_TAGS:
        body = release_body(tag).encode("utf-8")
        rec: dict[str, Any] = {
            "tag": tag,
            "release_id": RELEASE_IDS[tag],
            "version": tag[1:4],
            "introduced_in": tag[1:],
            "tag_commit": "a" * 40,
            "published_at": "2025-03-05T18:03:02Z",
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body),
        }
        rec.update(overrides.get(tag, {}))
        included.append(rec)
    return {
        "source_id": "fixture-releases",
        "repo": "duckdb/duckdb",
        "api_base": "https://api.example.invalid",
        "cache_dir": "data/cache/github_releases",
        "evidence_retrieved_at": "2026-11-20T17:48:36Z",
        "licence_status": "unverified",
        "licence_note": "not established",
        "attribution_template": "DuckDB release notes, github.com/duckdb/duckdb, release {tag}",
        "retrieval_cleanup": "drop @user and URL, keep (#n)",
        "investigated_not_included": [
            {
                "tag": "v1.2.0",
                "release_id": 999,
                "published_at": "2025-02-05T13:57:07Z",
                "body_sha256": "b" * 64,
                "body_bytes": 10,
            }
        ],
        "included": included,
    }


def write_release_cache(cache: Path) -> Path:
    """Put verified fixture bodies in a release cache (as a successful fetch would)."""
    cache.mkdir(parents=True, exist_ok=True)
    for tag in APPROVED_RELEASE_TAGS:
        (cache / f"{tag}.md").write_bytes(release_body(tag).encode("utf-8"))
    return cache


@dataclass
class Upstream:
    path: Path
    url: str
    commit1: str
    commit2: str  # changes one 1.4 page


def m1_content_hash(files: dict[str, bytes]) -> str:
    """Independent re-implementation of the RAG-M1 definition (not imported from src)."""
    lines = sorted(
        f"{p}\t{hashlib.sha256(b).hexdigest()}" for p, b in files.items() if p.endswith(".md")
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


@pytest.fixture(scope="session")
def upstream(tmp_path_factory: pytest.TempPathFactory) -> Upstream:
    if GIT is None:
        pytest.skip("git not available")
    root = tmp_path_factory.mktemp("upstream")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "uploadpack.allowFilter", "true")
    git(root, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(root, "config", "core.autocrlf", "false")
    files: dict[str, bytes] = {"LICENSE": LICENCE, "README.md": b"site readme, not docs\n"}
    for v in APPROVED_VERSIONS:
        files.update(fixture_files(v))
    for post in APPROVED_RELEASE_POSTS:
        files[post] = post_file(post)
    files["_posts/2024-09-09-announcing-duckdb-110.md"] = post_file("x-110.md")  # not approved
    for rel, data in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    commit1 = git(root, "rev-parse", "HEAD")
    (root / "docs/1.4/sql/functions/map.md").write_bytes(b"# Map\n\nChanged upstream.\n")
    git(root, "commit", "-q", "-am", "two")
    commit2 = git(root, "rev-parse", "HEAD")
    return Upstream(root, root.as_uri(), commit1, commit2)


def post_release(path: str) -> str:
    """Release written out for a fixture post path (``..-announcing-duckdb-145.md`` -> 1.4.5)."""
    digits = path.rsplit("-", 1)[1].removesuffix(".md")
    return ".".join(digits)


def post_file(path: str) -> bytes:
    release = post_release(path)
    return (
        f'---\nlayout: post\ntitle: "Announcing DuckDB {release}"\nauthors:\n'
        f"  - The DuckDB team\ntag: release\n---\n\nDuckDB {release} is out.\n\n"
        "## Fixes\n\n- One fix [see docs]({% link docs/1.2/sql/functions/map.md %}).\n"
    ).encode()


def release_posts_config(up: Upstream, commit: str) -> dict[str, Any]:
    posts = []
    for path in APPROVED_RELEASE_POSTS:
        posts.append(
            {
                "path": path,
                "release": post_release(path),
                "git_blob": git(up.path, "rev-parse", f"{commit}:{path}"),
                "sha256": hashlib.sha256(post_file(path)).hexdigest(),
            }
        )
    return {
        "repository_licence": "MIT",
        "repository_licence_file": "LICENSE",
        "repository_copyright": "Copyright 2018-2025 Fixture",
        "repository_copyright_end_year": 2025,
        "licence_applicability": "uncertain",
        "licence_status": "unverified",
        "licence_basis": "fixture: repository licence present, scope over website text unknown",
        "evidence_inspected": ["fixture LICENSE"],
        "resolution_evidence": ["an explicit content licence"],
        "url_pattern": "{site}/{yyyy}/{mm}/{dd}/{slug}.html",
        "posts": posts,
    }


def make_config(up: Upstream, commit: str, **expected_overrides: Any) -> CorpusConfig:
    licence_blob = git(up.path, "rev-parse", f"{commit}:LICENSE")
    versions = []
    for v, pkg in APPROVED_VERSIONS.items():
        files = fixture_files(v)
        if commit == up.commit2 and v == "1.4":
            files[f"docs/{v}/sql/functions/map.md"] = b"# Map\n\nChanged upstream.\n"
        md = {p: b for p, b in files.items() if p.endswith(".md")}
        expected = {
            "git_tree": git(up.path, "rev-parse", f"{commit}:docs/{v}"),
            "md_files": len(md),
            "words": sum(len(b.split()) for b in md.values()),
            "content_hash": m1_content_hash(files),
        }
        expected.update(expected_overrides.get(v, {}))
        versions.append(
            {"version": v, "docs_dir": f"docs/{v}", "behaviour_package": pkg, "expected": expected}
        )
    return CorpusConfig.model_validate(
        {
            "library": "duckdb",
            "docs_source": {
                "source_id": "fixture",
                "repo": "fixture/docs",
                "repo_url": up.url,
                "commit": commit,
                "url_base": "https://example.invalid/docs",
                "licence": "MIT",
                "licence_status": "verified",
                "copyright": "fixture",
                "licence_file": "LICENSE",
                "licence_file_git_blob": licence_blob,
                "licence_file_sha256": hashlib.sha256(LICENCE).hexdigest(),
            },
            "paths": {"cache_dir": "data/cache/fixture", "manifest": "data/manifest/m.json"},
            "release_notes": release_notes_config(),
            "release_posts": release_posts_config(up, commit),
            "versions": versions,
        }
    )
