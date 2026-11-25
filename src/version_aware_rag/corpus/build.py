"""Deterministic corpus build (RAG-M3 S5a): ``vrag corpus build [--check]``.

Design: ``docs/design/RAG-M3_S5_build.md`` (owner decisions S5-D1 to S5-D10). The authoritative
path to the final corpus (S5-D6); it reuses the approved S1-S4 functions unchanged.

1. **Verify every input before anything is written:** the docs cache at the pinned commit, the
   corpus manifest's integrity, S1's ``verify_cache`` (pinned commit, release cache, per-version
   RAG-M1 ``content_hash`` = IR-6, the manifest rebuilt from the cache, every checked-out file),
   S4d's release-note verification, and the integrity of any existing S4c/S4d outputs.
2. **Build in memory** with the S3 parser and the S4 chunkers; detect unresolved sources
   (S5-D2); validate everything in memory.
3. **Write** (S5-D5): every changed output is first written to a staging folder; a journal
   lists the replacement set; then each existing file is moved aside (backup) and its replacement
   moved in. **Removing the journal is the commit point**: backups are kept until it is gone.
   Any exception during replacement (including ``KeyboardInterrupt``) rolls every file back.

Recovery: a normal build first restores the previous output set if a journal is present (an
interrupted transaction), and only then verifies its inputs, so a build that then fails
verification leaves the previous consistent set in place. With no journal, a normal build removes
stale staging and backup folders. ``--check`` never recovers or cleans up, and runs with Git lazy
fetching disabled (``GIT_NO_LAZY_FETCH=1``) so it cannot write to the Git cache.

Atomicity, stated precisely: each single file replacement is an atomic rename within one
filesystem. A *set* of files in several folders cannot be renamed atomically, so the set-level
guarantee is all-or-rollback within the process plus journal-based restoration of the previous set
on the next run after an interruption. Directory entries are not fsynced, so durability across
power loss depends on the filesystem.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from version_aware_rag.corpus.acquire import GitError, GitRepo, open_cache
from version_aware_rag.corpus.chunk import Chunk, ChunkingError, check_unique, chunk_documents
from version_aware_rag.corpus.chunk_output import (
    CHUNK_DIR,
    CHUNK_FILES,
    CHUNK_MANIFEST,
    CHUNK_REPORT,
    BuildContext,
    chunk_file_problems,
    corpus_counts,
    render_chunk_files,
    render_chunk_manifest,
    render_chunk_report,
)
from version_aware_rag.corpus.config import (
    APPROVED_RELEASE_POSTS,
    ChunkingConfig,
    CorpusConfig,
    ParsingConfig,
)
from version_aware_rag.corpus.inspect import inspect_corpus
from version_aware_rag.corpus.manifest import CorpusManifest, load_manifest, verify_cache
from version_aware_rag.corpus.parse import PARSER_VERSION
from version_aware_rag.corpus.release_chunks import (
    RELEASE_DIR,
    RELEASE_FILE,
    RELEASE_MANIFEST,
    RELEASE_REPORT,
    ReleaseChunkError,
    ReleaseNotePage,
    chunk_release_notes,
    release_counts,
    release_file_problems,
    render_release_file,
    render_release_manifest,
    render_release_report,
    verify_release_inputs,
)
from version_aware_rag.corpus.report_stats import (
    cross_version_duplicates,
    include_records,
    near_empty_pages,
    oversize_chunks,
    parse_warning_records,
    section_path_fallbacks,
    token_histogram,
)

UNRESOLVED_FILE = "data/build/unresolved.jsonl"
BUILD_REPORT = "data/build/build_report.json"
BUILD_MANIFEST = "data/manifest/build_manifest.json"
STAGING_DIR = "data/build/.build-staging"
BACKUP_DIR = "data/build/.build-backup"
JOURNAL = "data/build/.build-journal.json"
JOURNAL_TMP = "data/build/.build-journal.tmp"
SCHEMA = 1  # build manifest (unchanged by S5c, S5c-D6)
REPORT_SCHEMA = 2  # build report: S5c added the extended sections (S5c-D6)

S4C_OUTPUTS = (*(f"{CHUNK_DIR}/{n}" for n in CHUNK_FILES), CHUNK_MANIFEST, CHUNK_REPORT)
S4D_OUTPUTS = (f"{RELEASE_DIR}/{RELEASE_FILE}", RELEASE_MANIFEST, RELEASE_REPORT)
# S5-D1: the logical corpus is the five version files plus the separate release-note file.
LOGICAL_CORPUS = (
    *(f"{CHUNK_DIR}/{n}" for n in CHUNK_FILES if n != "empty_sections.jsonl"),
    f"{RELEASE_DIR}/{RELEASE_FILE}",
)
ALL_OUTPUTS = (*S4C_OUTPUTS, *S4D_OUTPUTS, UNRESOLVED_FILE, BUILD_REPORT, BUILD_MANIFEST)

# Indirections so tests can inject failures or interruptions; production behaviour is the default.
_REPLACE: Callable[[Path, Path], None] = os.replace


def _rmtree_quiet(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _no_checkpoint(name: str) -> None:
    """Production: nothing. Tests replace it to simulate an interruption at a named point."""


def _remove_journal(path: Path) -> None:
    path.unlink()


_RMTREE: Callable[[Path], None] = _rmtree_quiet
_CHECKPOINT: Callable[[str], None] = _no_checkpoint
_REMOVE_JOURNAL: Callable[[Path], None] = _remove_journal  # the commit-point deletion


class BuildError(Exception):
    """The build stopped; ``problems`` lists every finding.

    ``outputs_unchanged`` is False only when outputs may be partly changed (a failed rollback or
    recovery; the journal and backups are then kept). ``recovered`` lists what a preceding recovery
    of an interrupted build restored."""

    exit_code = 1

    def __init__(
        self,
        stage: str,
        problems: list[str],
        *,
        outputs_unchanged: bool = True,
        recovered: list[str] | None = None,
    ) -> None:
        self.stage = stage
        self.problems = problems
        self.outputs_unchanged = outputs_unchanged
        self.recovered = list(recovered or [])
        super().__init__(f"{stage}: " + "; ".join(problems))


class BuildEnvironmentError(BuildError):
    """An input is absent (no cache, unreadable manifest): exit code 2."""

    exit_code = 2


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UnresolvedSource(_Strict):
    """S5-D2: a source that cannot be assigned a version; never chunked."""

    kind: Literal["release_post", "github_release"]
    path: str
    sha256: str
    reason: Literal["unmapped_release_post", "unapproved_release_body"]
    detecting_step: str


@dataclass(frozen=True)
class BuildInputs:
    root: Path  # where outputs are written (the repository root)
    cfg: CorpusConfig
    cfg_sha256: str
    cache: Path  # docs cache (git checkout)
    release_cache: Path
    manifest_path: Path  # committed corpus manifest
    parsing: ParsingConfig
    parsing_sha256: str
    chunking: ChunkingConfig
    chunking_sha256: str


@dataclass
class BuildResult:
    outputs: dict[str, bytes]
    checks: list[str]
    written: list[str] = field(default_factory=list)  # replaced or created
    unchanged: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # --check differences
    recovered: list[str] = field(default_factory=list)
    counts: dict[str, object] = field(default_factory=dict)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- verification


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_PROBLEM_FILE = re.compile(r"^([\w.-]+\.jsonl)\b")


def existing_output_problems(root: Path) -> list[str]:
    """Integrity of S4c/S4d outputs already on disk (absent outputs mean a fresh build)."""
    problems: list[str] = []
    chunk_dir = root / CHUNK_DIR
    if chunk_dir.is_dir():
        present = {p.name: p.read_bytes() for p in sorted(chunk_dir.iterdir()) if p.is_file()}
        if any(name in present for name in CHUNK_FILES):
            committed = _read_json(root / CHUNK_MANIFEST)
            if committed is None:
                problems.append(
                    f"S4c chunk files exist but {CHUNK_MANIFEST} is missing or unreadable"
                )
            else:
                problems += [
                    f"existing S4c output: {p}" for p in chunk_file_problems(present, committed)
                ]
    rel = root / RELEASE_DIR / RELEASE_FILE
    if rel.is_file():
        committed = _read_json(root / RELEASE_MANIFEST)
        if committed is None:
            problems.append(
                f"{RELEASE_FILE} exists but {RELEASE_MANIFEST} is missing or unreadable"
            )
        else:
            problems += [
                f"existing S4d output: {p}"
                for p in release_file_problems(rel.read_bytes(), committed)
            ]
    return problems


def existing_output_diagnostics(problems: list[str]) -> list[str]:
    """Explain a mismatch between generated outputs and their committed manifests (no remedy is
    applied automatically)."""
    files = sorted(
        {
            f"{CHUNK_DIR}/{m.group(1)}"
            for p in problems
            if (m := _PROBLEM_FILE.match(p.split(": ", 1)[-1]))
        }
    )
    listed = ", ".join(files) if files else "see the problems above"
    return [
        f"stale or inconsistent generated outputs: {listed}",
        f"these files do not match their committed manifests ({CHUNK_MANIFEST}, "
        f"{RELEASE_MANIFEST}); the build stopped and changed nothing",
        "safe manual recovery: (1) run `git status` and `git diff -- data/manifest docs/reports` "
        "to see whether the committed manifests changed; (2) move the listed generated files out "
        f"of {CHUNK_DIR}/ yourself (for example into a dated folder outside data/build); "
        "(3) run `vrag corpus build` again. Nothing is deleted or overwritten automatically.",
    ]


@dataclass(frozen=True)
class Verified:
    repo: GitRepo
    manifest: CorpusManifest
    checks: list[str]


def verify_build_inputs(inp: BuildInputs) -> Verified:
    """Every input check; raises ``BuildError`` listing all failures before anything is built."""
    try:
        repo = open_cache(inp.cfg, inp.cache)
    except GitError as exc:
        raise BuildEnvironmentError("verify inputs", [str(exc)]) from exc
    manifest, integrity = load_manifest(inp.manifest_path)
    if manifest is None:
        raise BuildEnvironmentError("verify inputs", integrity)
    checks = ["docs cache at the pinned commit"]
    problems = [f"corpus manifest: {p}" for p in integrity]
    if not problems:
        checks.append("corpus manifest parts intact")
    report = verify_cache(inp.cfg, repo, manifest, inp.cfg_sha256, inp.release_cache)
    if not report.commit_ok:
        problems.append("cache HEAD is not the pinned commit")
    if report.m1_discrepancies:
        problems += [f"RAG-M1 evidence (IR-6): {d}" for d in report.m1_discrepancies]
    else:
        checks.append("per-version content_hash equals RAG-M1 evidence (IR-6)")
    if not report.releases.ok:
        problems += [
            f"release cache: {c.tag} {c.status}"
            for c in report.releases.checks
            if c.status != "verified"
        ]
    if not report.manifest_matches_snapshot:
        problems += [f"manifest vs cache snapshot: {d}" for d in report.snapshot_differences]
    else:
        checks.append("committed manifest equals a rebuild from the cache (config hash included)")
    problems += [f"missing cache file: {p}" for p in report.missing_files]
    problems += [f"changed cache file: {p}" for p in report.changed_files]
    # verify_cache skips the per-file check when the release cache fails; report it only if it ran.
    file_check_ran = report.releases.ok
    if file_check_ran and not report.missing_files and not report.changed_files:
        checks.append("every checked-out source file matches its manifest sha256")
    rel = verify_release_inputs(inp.cfg.release_notes, manifest.release_notes, inp.release_cache)
    problems += [f"release notes: {p}" for p in rel]
    if not rel and report.releases.ok:
        checks.append("release-note bodies, records and fetch records verified (S2/S4d)")
    existing = existing_output_problems(inp.root)
    problems += existing
    if existing:
        problems += existing_output_diagnostics(existing)
    else:
        checks.append("existing S4c/S4d outputs intact (or absent)")
    if problems:
        raise BuildError("verify inputs", problems)
    return Verified(repo, manifest, checks)


# ---------------------------------------------------------------- unresolved sources (S5-D2)


def detect_unresolved(
    cfg: CorpusConfig, cache: Path, release_cache: Path
) -> list[UnresolvedSource]:
    """Sources present in the caches that no approved mapping covers."""
    found: list[UnresolvedSource] = []
    approved_posts = set(APPROVED_RELEASE_POSTS)
    posts = cache / "_posts"
    if posts.is_dir():
        for f in sorted(posts.glob("*.md")):
            rel = f"_posts/{f.name}"
            if rel not in approved_posts:
                found.append(
                    UnresolvedSource(
                        kind="release_post",
                        path=rel,
                        sha256=_sha(f.read_bytes()),
                        reason="unmapped_release_post",
                        detecting_step="build.scan_cache_posts",
                    )
                )
    approved_tags = {r.tag for r in cfg.release_notes.included}
    if release_cache.is_dir():
        for f in sorted(release_cache.glob("*.md")):
            if f.stem not in approved_tags:
                found.append(
                    UnresolvedSource(
                        kind="github_release",
                        path=f"releases/tag/{f.stem}",
                        sha256=_sha(f.read_bytes()),
                        reason="unapproved_release_body",
                        detecting_step="build.scan_release_cache",
                    )
                )
    return found


# ---------------------------------------------------------------- build in memory


def _file_entry(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "records": data.count(b"\n") if path.endswith(".jsonl") else None,
        "bytes": len(data),
        "sha256": _sha(data),
    }


def _json(body: dict[str, object]) -> bytes:
    return (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def render_outputs(
    inp: BuildInputs, verified: Verified
) -> tuple[dict[str, bytes], dict[str, object]]:
    """Every output, built and validated in memory. Raises ``BuildError`` on any problem."""
    manifest = verified.manifest
    insp = inspect_corpus(manifest, inp.cache, inp.parsing, inp.parsing_sha256)
    pages = chunk_documents(
        [p.doc for p in insp.pages], manifest, inp.chunking, inp.chunking_sha256
    )
    ctx = BuildContext(
        source_commit=manifest.source.commit,
        parser_version=PARSER_VERSION,
        chunker_version=inp.chunking.chunker_version,
        chunking_config_sha256=inp.chunking_sha256,
        corpus_manifest_sha256=_sha(inp.manifest_path.read_bytes()),
    )
    chunk_files = render_chunk_files(pages)
    chunk_manifest = render_chunk_manifest(pages, chunk_files, ctx)
    behaviour = {str(v.version): v.behaviour_package for v in manifest.versions}
    releases: list[ReleaseNotePage] = chunk_release_notes(
        inp.cfg.release_notes,
        manifest.release_notes,
        behaviour,
        inp.release_cache,
        inp.parsing,
        inp.chunking,
        inp.chunking_sha256,
    )
    release_data = render_release_file(releases)
    release_manifest = render_release_manifest(releases, release_data, ctx)
    unresolved = detect_unresolved(inp.cfg, inp.cache, inp.release_cache)

    # In-memory validation before anything is written.
    problems = [f"S4c: {p}" for p in chunk_file_problems(chunk_files, json.loads(chunk_manifest))]
    problems += [
        f"S4d: {p}" for p in release_file_problems(release_data, json.loads(release_manifest))
    ]
    version_chunks: list[Chunk] = [c for _, _, pg in pages for c in pg.chunks]
    release_chunks: list[Chunk] = [c for r in releases for c in r.page.chunks]
    all_chunks = version_chunks + release_chunks
    try:
        check_unique(all_chunks)
    except ValueError as exc:
        problems.append(f"logical corpus: {exc}")
    chunked_paths = {c.source_path for c in all_chunks}
    problems += [
        f"unresolved source was chunked: {u.path}" for u in unresolved if u.path in chunked_paths
    ]
    if problems:
        raise BuildError("validate in memory", problems)

    outputs: dict[str, bytes] = {f"{CHUNK_DIR}/{n}": d for n, d in chunk_files.items()}
    outputs[CHUNK_MANIFEST] = chunk_manifest
    outputs[CHUNK_REPORT] = render_chunk_report(pages, chunk_files, ctx).encode("utf-8")
    outputs[f"{RELEASE_DIR}/{RELEASE_FILE}"] = release_data
    outputs[RELEASE_MANIFEST] = release_manifest
    outputs[RELEASE_REPORT] = render_release_report(releases, release_data, ctx).encode("utf-8")
    outputs[UNRESOLVED_FILE] = (
        "".join(json.dumps(u.model_dump(mode="json"), sort_keys=True) + "\n" for u in unresolved)
    ).encode("utf-8")

    corpus = corpus_counts(pages)
    rel_counts = release_counts(releases)
    inputs = {
        "source_commit": manifest.source.commit,
        "corpus_config_sha256": inp.cfg_sha256,
        "parsing_config_sha256": inp.parsing_sha256,
        "chunking_config_sha256": inp.chunking_sha256,
        "corpus_manifest_sha256": ctx.corpus_manifest_sha256,
        "parser_version": PARSER_VERSION,
        "chunker_version": inp.chunking.chunker_version,
        "release_bodies": {r.tag: r.body_sha256 for r in manifest.release_notes.included},
    }
    logical_total = int(str(corpus["chunks"])) + int(str(rel_counts["chunks"]))
    # S5c extended content: identifiers, codes, paths, versions, counts and lines only.
    parsed = [pr.doc for pr in insp.pages] + [r.doc for r in releases]
    duplicates = cross_version_duplicates(all_chunks)
    report = {
        "schema_version": REPORT_SCHEMA,
        "generator": "vrag corpus build (RAG-M3 S5a)",
        "library": "duckdb",
        "inputs": inputs,
        "verification": [{"check": c, "result": "passed"} for c in verified.checks],
        "logical_corpus": list(LOGICAL_CORPUS),
        "outputs": [
            _file_entry(p, outputs[p]) for p in (*S4C_OUTPUTS, *S4D_OUTPUTS, UNRESOLVED_FILE)
        ],
        "counts": {
            "corpus": corpus,
            "release_notes": rel_counts,
            "logical_corpus_chunks": logical_total,
            "unresolved": len(unresolved),
            "unresolved_by_reason": dict(sorted(Counter(u.reason for u in unresolved).items())),
        },
        "warnings": {
            "oversize": oversize_chunks(all_chunks),
            "near_empty_pages": near_empty_pages(all_chunks),
            "section_path_fallback": section_path_fallbacks(all_chunks),
            "cross_version_duplicates": {"count": duplicates["count"]},
        },
        "duplicates": duplicates,
        "sizes": {
            "token_histogram": {
                "version_files": token_histogram(version_chunks),
                "release_notes": token_histogram(release_chunks),
                "logical_corpus": token_histogram(all_chunks),
            }
        },
        "parse_warnings": parse_warning_records(parsed),
        "includes": include_records(parsed),
    }
    outputs[BUILD_REPORT] = _json(report)
    summary: dict[str, object] = {
        "pages": corpus["pages"],
        "version_file_chunks": corpus["chunks"],
        "by_version": corpus["by_version"],
        "release_note_chunks": rel_counts["chunks"],
        "logical_corpus_chunks": logical_total,
        "empty_sections": sum(len(pg.empty_sections) for _, _, pg in pages),
        "unresolved": len(unresolved),
    }
    build_manifest = {
        "schema_version": SCHEMA,
        "generator": "vrag corpus build (RAG-M3 S5a)",
        "library": "duckdb",
        "inputs": inputs,
        "logical_corpus": list(LOGICAL_CORPUS),
        "outputs": [
            _file_entry(p, outputs[p])
            for p in (*S4C_OUTPUTS, *S4D_OUTPUTS, UNRESOLVED_FILE, BUILD_REPORT)
        ],
        "counts": summary,
    }
    outputs[BUILD_MANIFEST] = _json(build_manifest)
    return {p: outputs[p] for p in ALL_OUTPUTS}, summary


# ---------------------------------------------------------------- writing (S5-D5)


def _fsync_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _remove_stale_transaction_data(root: Path) -> list[str]:
    """Only called when no journal exists: leftover staging, backups or a journal draft."""
    removed = []
    for rel in (STAGING_DIR, BACKUP_DIR):
        if (root / rel).exists():
            _RMTREE(root / rel)
            removed.append(f"removed stale transaction data {rel}")
    if (root / JOURNAL_TMP).is_file():
        (root / JOURNAL_TMP).unlink()
        removed.append(f"removed stale transaction data {JOURNAL_TMP}")
    return removed


def recover_interrupted(root: Path) -> list[str]:
    """Normal builds only. With a journal: restore the previous output set (idempotent; the journal
    is removed only after every file is restored). Without one: remove stale transaction data."""
    journal = root / JOURNAL
    if not journal.is_file():
        return _remove_stale_transaction_data(root)
    record = _read_json(journal)
    if record is None:
        raise BuildError(
            "recover",
            [f"the journal {JOURNAL} is unreadable; it and {BACKUP_DIR} were left untouched"],
            outputs_unchanged=False,
        )
    raw_replace, raw_created = record.get("replace"), record.get("created")
    replace = [str(r) for r in raw_replace] if isinstance(raw_replace, list) else []
    created = {str(r) for r in raw_created} if isinstance(raw_created, list) else set()
    messages: list[str] = []
    try:
        for rel in replace:
            target, backup = root / rel, root / BACKUP_DIR / rel
            if backup.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                _REPLACE(backup, target)
                messages.append(f"restored {rel}")
            elif rel in created and target.is_file():
                target.unlink()
                messages.append(f"removed {rel} (created by the interrupted build)")
    except OSError as exc:
        raise BuildError(
            "recover",
            [
                f"recovery of the interrupted build failed ({exc})",
                f"the journal {JOURNAL} and the remaining backups in {BACKUP_DIR} are kept; "
                "run `vrag corpus build` again to retry the recovery",
            ],
            outputs_unchanged=False,
            recovered=messages,
        ) from exc
    journal.unlink()  # recovery complete: the previous set is back in place
    _RMTREE(root / STAGING_DIR)
    _RMTREE(root / BACKUP_DIR)
    return messages


def write_outputs(root: Path, outputs: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Replace every changed output, or none of them. Returns (written, unchanged)."""
    journal = root / JOURNAL
    if journal.is_file():
        raise BuildError(
            "write outputs",
            [
                f"an active transaction journal {JOURNAL} exists; nothing was written. "
                "Run `vrag corpus build` (without --check) to recover the interrupted build first"
            ],
        )
    changed = [
        r for r, d in outputs.items() if not ((root / r).is_file() and (root / r).read_bytes() == d)
    ]
    unchanged = [r for r in outputs if r not in changed]
    if not changed:
        return [], unchanged
    staging, backups, journal_tmp = root / STAGING_DIR, root / BACKUP_DIR, root / JOURNAL_TMP
    _RMTREE(staging)  # safe: no journal exists, so these are not recovery data
    _RMTREE(backups)
    created = [r for r in changed if not (root / r).is_file()]
    try:
        for rel in changed:
            _fsync_write(staging / rel, outputs[rel])
        _fsync_write(
            journal_tmp, json.dumps({"replace": changed, "created": created}, indent=2).encode()
        )
        _REPLACE(journal_tmp, journal)  # transaction begins
    except OSError as exc:
        _RMTREE(staging)
        journal_tmp.unlink(missing_ok=True)
        raise BuildError(
            "write outputs", [f"staging failed ({exc}); no output was replaced"]
        ) from exc
    done: list[str] = []
    try:
        for rel in changed:
            target = root / rel
            if target.is_file():
                (backups / rel).parent.mkdir(parents=True, exist_ok=True)
                _REPLACE(target, backups / rel)
            done.append(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            _REPLACE(staging / rel, target)
    except BaseException as exc:  # includes KeyboardInterrupt: roll back, then re-raise
        try:
            for rel in reversed(done):
                target, backup = root / rel, backups / rel
                if backup.is_file():
                    backup.replace(target)  # real rename, never the injectable _REPLACE
                elif rel in created and target.is_file():
                    target.unlink()
        except BaseException as rollback_exc:
            raise BuildError(
                "write outputs",
                [
                    f"replacement failed: {type(exc).__name__}: {exc}",
                    f"rollback incomplete: {type(rollback_exc).__name__}: {rollback_exc}",
                    f"the journal {JOURNAL} and the remaining backups in {BACKUP_DIR} are kept; "
                    "run `vrag corpus build` again to recover the previous output set",
                ],
                outputs_unchanged=False,
            ) from rollback_exc
        journal.unlink(missing_ok=True)  # rolled back: the previous set is in place
        _RMTREE(staging)
        _RMTREE(backups)
        if isinstance(exc, OSError):
            raise BuildError(
                "write outputs", [f"replacement failed ({exc}); every output rolled back"]
            ) from exc
        raise
    _CHECKPOINT("replacements_done")
    try:
        _REMOVE_JOURNAL(journal)  # COMMIT POINT: from here on the new set is the committed set
    except OSError as exc:
        # Not committed. Keep the journal, every backup and the staging data; clean nothing.
        raise BuildError(
            "commit",
            [
                f"journal removal failed ({exc}); the transaction could not be committed",
                f"the journal {JOURNAL}, the backups in {BACKUP_DIR} and the staging data in "
                f"{STAGING_DIR} were kept; the next normal `vrag corpus build` will restore the "
                "previous consistent output set",
            ],
            outputs_unchanged=False,
        ) from exc
    _CHECKPOINT("journal_removed")
    _RMTREE(backups)
    _CHECKPOINT("backups_removed")
    _RMTREE(staging)
    return changed, unchanged


def check_outputs(root: Path, outputs: dict[str, bytes]) -> list[str]:
    """``--check``: differences between a fresh build and the files on disk; writes nothing."""
    problems = []
    if (root / JOURNAL).is_file():
        problems.append(f"an interrupted build left {JOURNAL}; run `vrag corpus build` to restore")
    for rel, data in outputs.items():
        target = root / rel
        if not target.is_file():
            problems.append(f"{rel}: missing")
        elif target.read_bytes() != data:
            problems.append(f"{rel}: differs from a fresh build")
    return problems


@contextmanager
def _no_lazy_fetch() -> Iterator[None]:
    """Check mode: git must not fetch missing objects into the cache (git >= 2.44)."""
    previous = os.environ.get("GIT_NO_LAZY_FETCH")
    os.environ["GIT_NO_LAZY_FETCH"] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GIT_NO_LAZY_FETCH", None)
        else:
            os.environ["GIT_NO_LAZY_FETCH"] = previous


def _verify_and_render(inp: BuildInputs) -> tuple[Verified, dict[str, bytes], dict[str, object]]:
    """Verification and the in-memory build, with expected failures turned into build errors.
    Unexpected exceptions (programming errors) propagate unchanged."""
    try:
        verified = verify_build_inputs(inp)
        outputs, counts = render_outputs(inp, verified)
    except GitError as exc:
        raise BuildEnvironmentError("git", [str(exc)]) from exc
    except ReleaseChunkError as exc:
        raise BuildError("release notes", exc.problems) from exc
    except ChunkingError as exc:
        raise BuildError("chunking", [str(exc)]) from exc
    except OSError as exc:
        raise BuildEnvironmentError("read inputs", [f"{type(exc).__name__}: {exc}"]) from exc
    return verified, outputs, counts


def run_build(inp: BuildInputs, *, check: bool) -> BuildResult:
    if check:
        # No recovery, no cleanup, no lazy fetch: check mode writes nothing anywhere.
        with _no_lazy_fetch():
            verified, outputs, counts = _verify_and_render(inp)
        return BuildResult(
            outputs=outputs,
            checks=verified.checks,
            counts=counts,
            problems=check_outputs(inp.root, outputs),
        )
    # Normal build: restore an interrupted transaction first, then verify (documented order).
    recovered = recover_interrupted(inp.root)
    try:
        verified, outputs, counts = _verify_and_render(inp)
    except BuildError as exc:
        exc.recovered = recovered + exc.recovered
        raise
    written, unchanged = write_outputs(inp.root, outputs)
    return BuildResult(
        outputs=outputs,
        checks=verified.checks,
        written=written,
        unchanged=unchanged,
        recovered=recovered,
        counts=counts,
    )


def output_entries(
    outputs: dict[str, bytes], names: Sequence[str] = ALL_OUTPUTS
) -> list[dict[str, object]]:
    return [_file_entry(n, outputs[n]) for n in names]
