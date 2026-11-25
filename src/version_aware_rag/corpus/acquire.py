"""Acquire the pinned documentation snapshot with git (shallow, blob-less, sparse).

Only the pinned commit is fetched (``--depth 1``), file contents are downloaded only for the
sparse paths (the five docs directories' Markdown files and the licence file), and nothing is
ever fetched from a branch tip. The cache directory is keyed by commit (see
``CorpusConfig.cache_path``), so a cache for one commit is never reused for another.

All content hashing elsewhere reads **git objects** (``read_blobs``), the canonical bytes, exactly
as the RAG-M1 manifest did. The working tree is only checked for consistency.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from version_aware_rag.corpus.config import CorpusConfig

log = logging.getLogger(__name__)


class GitError(RuntimeError):
    """A git command failed, or the repository is not in the expected state."""


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str  # "blob", "tree", "commit"
    oid: str
    path: str


def _git_executable() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitError("git executable not found on PATH")
    return exe


class GitRepo:
    """Thin wrapper over the git CLI for one repository directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._git = _git_executable()

    def run(self, *args: str, input_bytes: bytes | None = None) -> bytes:
        proc = subprocess.run(
            [self._git, "-C", str(self.path), *args],
            input=input_bytes,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc.stdout

    def rev_parse(self, rev: str) -> str:
        return self.run("rev-parse", "--verify", "--end-of-options", rev).decode().strip()

    def ls_tree(self, commit: str, path: str) -> list[TreeEntry]:
        """Recursive listing of ``path`` at ``commit``. Needs no file contents."""
        out = self.run("ls-tree", "-r", "-z", "--full-tree", commit, "--", path)
        entries = []
        for record in out.split(b"\0"):
            if not record:
                continue
            meta, _, name = record.partition(b"\t")
            mode, kind, oid = meta.decode().split(" ")
            entries.append(TreeEntry(mode, kind, oid, name.decode("utf-8")))
        return entries

    def read_blobs(self, oids: Sequence[str]) -> dict[str, bytes]:
        """Read blob contents with one ``git cat-file --batch`` call."""
        unique = list(dict.fromkeys(oids))  # identical files share one blob
        if not unique:
            return {}
        out = self.run("cat-file", "--batch", input_bytes=("\n".join(unique) + "\n").encode())
        blobs: dict[str, bytes] = {}
        pos = 0
        for oid in unique:
            end = out.index(b"\n", pos)
            header = out[pos:end].decode().split(" ")
            if len(header) != 3 or header[1] != "blob":
                raise GitError(f"object {oid} not available as a blob: {' '.join(header)}")
            size = int(header[2])
            start = end + 1
            blobs[header[0]] = out[start : start + size]
            pos = start + size + 1  # skip the trailing newline
        if set(blobs) != set(unique):
            raise GitError("cat-file returned an unexpected set of objects")
        return blobs


def sparse_patterns(cfg: CorpusConfig) -> list[str]:
    """Non-cone sparse-checkout patterns: Markdown under each approved docs dir, LICENSE, and
    the 17 approved release posts (exact paths, nothing else from _posts/)."""
    patterns = [f"/{v.docs_dir}/**/*.md" for v in cfg.versions]
    patterns.append(f"/{cfg.docs_source.licence_file}")
    patterns.extend(f"/{post.path}" for post in cfg.release_posts.posts)
    return patterns


def _ensure_repo(cache: Path, repo_url: str) -> GitRepo:
    if not (cache / ".git").is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        repo = GitRepo(cache)
        repo.run("init", "-q")
        repo.run("remote", "add", "origin", repo_url)
    repo = GitRepo(cache)
    # Never let line-ending conversion change checked-out bytes.
    repo.run("config", "core.autocrlf", "false")
    repo.run("config", "core.eol", "lf")
    current = repo.run("remote", "get-url", "origin").decode().strip()
    if current != repo_url:
        raise GitError(
            f"cache {cache} points to {current!r}, expected {repo_url!r}; refusing to reuse it"
        )
    return repo


def fetch_pinned(cfg: CorpusConfig, cache: Path) -> GitRepo:
    """Fetch exactly the pinned commit and check out only the sparse paths. Idempotent."""
    commit = cfg.docs_source.commit
    repo = _ensure_repo(cache, cfg.docs_source.repo_url)
    log.info("fetch_start", extra={"repo": cfg.docs_source.repo_url, "commit": commit})
    repo.run("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
    repo.run("sparse-checkout", "set", "--no-cone", *sparse_patterns(cfg))
    repo.run("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", commit)
    head = repo.rev_parse("HEAD")
    if head != commit:
        raise GitError(f"checked-out HEAD {head} is not the pinned commit {commit}")
    log.info("fetch_done", extra={"commit": head, "cache": str(cache)})
    return repo


def open_cache(cfg: CorpusConfig, cache: Path) -> GitRepo:
    """Open an existing cache without network access; fail clearly if it isn't usable."""
    if not (cache / ".git").is_dir():
        raise GitError(f"no corpus cache at {cache}; run `vrag corpus fetch` first")
    repo = GitRepo(cache)
    head = repo.rev_parse("HEAD")
    if head != cfg.docs_source.commit:
        raise GitError(f"cache HEAD {head} is not the pinned commit {cfg.docs_source.commit}")
    return repo
