"""Corpus manifest: build it from the pinned git snapshot, compare it with the approved RAG-M1
evidence, and verify a local cache against it.

Hash definitions are identical to RAG-M1 manifest §1.1 and §5.2:

- ``sha256`` of a file = sha256 of the git blob bytes.
- ``content_hash`` of a version = sha256 of the newline-joined, **sorted** lines
  ``"<repo path>\\t<file sha256>"`` for every ``*.md`` file under ``docs/<version>``.
- ``words`` = ``len(blob_bytes.split())`` summed over files (bytes split on ASCII whitespace,
  exactly as the RAG-M1 script computed it).

Release notes (S2): the manifest records each approved release's metadata and body hash, never
its text. It is built only from a cache that passed verification. The only timestamps are fixed
evidence values (publish time and the time the expected hash was first recorded), so building it
twice from the same inputs gives byte-identical output. Each fetch's own time is kept in the
ignored cache (``<tag>.fetch.json``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from version_aware_rag import __version__
from version_aware_rag.corpus.acquire import GitRepo
from version_aware_rag.corpus.config import CorpusConfig, DocsVersion, ReleaseNotesConfig
from version_aware_rag.corpus.parse import split_front_matter
from version_aware_rag.corpus.releases import (
    ReleaseVerifyReport,
    api_url,
    body_path,
    html_url,
    verify_releases,
)

SCHEMA_VERSION = 5  # 2: release_notes (S2); 3: release_posts (S3b); 4: per-version files split;
# 5: release-post titles removed (title_sha256; attribution without the title; owner 2026-11-23)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRecord(_Strict):
    source_id: str
    repo: str
    repo_url: str
    commit: str
    url_base: str
    licence: str
    licence_status: str
    copyright: str
    licence_file: str
    licence_file_git_blob: str
    licence_file_sha256: str


class VersionRecord(_Strict):
    version: DocsVersion
    docs_dir: str
    behaviour_package: str
    git_tree: str
    md_files: int
    words: int
    content_hash: str


class FileRecord(_Strict):
    version: DocsVersion
    path: str
    git_blob: str
    sha256: str
    bytes: int
    words: int


class ExcludedRecord(_Strict):
    version: DocsVersion
    path: str
    git_blob: str
    reason: str


class ReleaseRecord(_Strict):
    tag: str
    release_id: int
    version: DocsVersion
    introduced_in: str
    tag_commit: str
    published_at: str
    first_retrieved_at: str
    source_url: str
    api_url: str
    body_sha256: str
    body_bytes: int
    cache_path: str
    attribution: str
    licence: None
    licence_status: str
    redistributable: bool
    status: str
    parse_status: str
    closes_gap: str


class InvestigatedRecord(_Strict):
    tag: str
    release_id: int
    published_at: str
    source_url: str
    body_sha256: str
    body_bytes: int
    observed_at: str
    status: str
    reason: str


class ReleaseNotesSection(_Strict):
    source_id: str
    repo: str
    licence: None
    licence_status: str
    licence_note: str
    redistributable: bool
    retrieval_cleanup: str
    included: tuple[ReleaseRecord, ...]
    investigated_not_included: tuple[InvestigatedRecord, ...]


class ReleasePostRecord(_Strict):
    """A duckdb-web release post: a source type separate from docs pages and GitHub releases."""

    source_kind: str  # always "release_post"
    path: str
    release: str
    version: DocsVersion
    published_date: str
    canonical_url: str
    # Release-post titles are restricted text (owner, 2026-11-23): only their sha256 is kept, so the
    # title can be verified against the pinned source without being reproduced.
    title_sha256: str | None
    authors: tuple[str, ...]
    source_repo: str
    source_commit: str
    git_blob: str
    sha256: str
    bytes: int
    licence: None  # not established for website text (owner instruction, 2026-11-21)
    licence_status: str
    licence_applicability: str
    repository_licence: str
    repository_licence_file: str
    repository_licence_sha256: str
    repository_copyright: str
    copyright_date_discrepancy: bool
    copyright_note: str | None
    licence_basis: str
    resolution_evidence: tuple[str, ...]
    attribution: str
    redistributable: bool
    status: str


class CorpusManifest(_Strict):
    schema_version: int
    library: str
    config_sha256: str
    generator: str
    source: SourceRecord
    release_notes: ReleaseNotesSection
    release_posts: tuple[ReleasePostRecord, ...]
    versions: tuple[VersionRecord, ...]
    files: tuple[FileRecord, ...]
    excluded_files: tuple[ExcludedRecord, ...]


def content_hash(path_sha_pairs: list[tuple[str, str]]) -> str:
    """RAG-M1 version content hash (see module docstring)."""
    lines = sorted(f"{path}\t{sha}" for path, sha in path_sha_pairs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class UnverifiedReleaseCacheError(ValueError):
    """The release-note cache didn't pass verification, so no manifest is built from it."""


def build_release_section(notes: ReleaseNotesConfig, release_cache: Path) -> ReleaseNotesSection:
    """Release-note metadata and hashes, only from a cache that verifies (never the text)."""
    report = verify_releases(notes, release_cache)
    if not report.ok:
        bad = ", ".join(f"{c.tag}={c.status}" for c in report.checks if c.status != "verified")
        raise UnverifiedReleaseCacheError(
            f"release-note cache not verified ({bad}); run `vrag corpus releases fetch`"
        )
    included = tuple(
        ReleaseRecord(
            tag=rel.tag,
            release_id=rel.release_id,
            version=rel.version,
            introduced_in=rel.introduced_in,
            tag_commit=rel.tag_commit,
            published_at=rel.published_at,
            first_retrieved_at=notes.evidence_retrieved_at,
            source_url=html_url(notes, rel.tag),
            api_url=api_url(notes, rel),
            body_sha256=rel.body_sha256,
            body_bytes=rel.body_bytes,
            cache_path=body_path(notes.cache_dir, rel.tag).as_posix(),
            attribution=notes.attribution_template.format(tag=rel.tag),
            licence=None,
            licence_status=notes.licence_status,
            redistributable=False,
            status="included",
            parse_status="not_parsed",
            closes_gap=f"no duckdb-web release post exists for {rel.introduced_in}",
        )
        for rel in notes.included
    )
    investigated = tuple(
        InvestigatedRecord(
            tag=r.tag,
            release_id=r.release_id,
            published_at=r.published_at,
            source_url=html_url(notes, r.tag),
            body_sha256=r.body_sha256,
            body_bytes=r.body_bytes,
            observed_at=notes.evidence_retrieved_at,
            status="investigated_not_included",
            reason="owner decision Q4: not in the initial retrieval corpus",
        )
        for r in notes.investigated_not_included
    )
    return ReleaseNotesSection(
        source_id=notes.source_id,
        repo=notes.repo,
        licence=None,
        licence_status=notes.licence_status,
        licence_note=notes.licence_note,
        redistributable=False,
        retrieval_cleanup=notes.retrieval_cleanup,
        included=included,
        investigated_not_included=investigated,
    )


def _front_matter_authors(text: str) -> tuple[str, ...]:
    """Items of the top-level ``authors:`` list in front matter (plain ``- name`` lines only)."""
    if not text.startswith("---\n"):
        return ()
    header = text[4 : text.find("\n---", 3)]
    authors: list[str] = []
    in_list = False
    for line in header.split("\n"):
        if line.startswith("authors:"):
            in_list = True
        elif in_list and line.startswith("  - "):
            authors.append(line[len("  - ") :].strip())
        elif in_list and line and not line.startswith(" "):
            in_list = False
    return tuple(authors)


def build_release_posts(
    cfg: CorpusConfig, repo: GitRepo, licence_sha: str
) -> tuple[ReleasePostRecord, ...]:
    """Release-post records from git objects at the pinned commit."""
    src = cfg.docs_source
    posts_cfg = cfg.release_posts
    site_base = src.url_base.removesuffix("/docs")  # https://duckdb.org
    records = []
    for post in posts_cfg.posts:
        entries = repo.ls_tree(src.commit, post.path)
        if len(entries) != 1:
            raise ValueError(f"release post {post.path} not found at {src.commit}")
        blob = entries[0].oid
        data = repo.read_blobs([blob])[blob]
        text = data.decode("utf-8")
        title = split_front_matter(text).title
        discrepancy = int(post.published_date[:4]) > posts_cfg.repository_copyright_end_year
        authors = _front_matter_authors(text)
        records.append(
            ReleasePostRecord(
                source_kind="release_post",
                path=post.path,
                release=post.release,
                version=post.version,
                published_date=post.published_date,
                canonical_url=post.canonical_url(site_base, posts_cfg.url_pattern),
                title_sha256=hashlib.sha256(title.encode("utf-8")).hexdigest() if title else None,
                authors=authors,
                source_repo=src.repo,
                source_commit=src.commit,
                git_blob=blob,
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
                licence=None,
                licence_status=posts_cfg.licence_status,
                licence_applicability=posts_cfg.licence_applicability,
                repository_licence=posts_cfg.repository_licence,
                repository_licence_file=posts_cfg.repository_licence_file,
                repository_licence_sha256=licence_sha,
                repository_copyright=posts_cfg.repository_copyright,
                copyright_date_discrepancy=discrepancy,
                copyright_note=(
                    f"published {post.published_date[:4]}, after the repository copyright line "
                    f"('{posts_cfg.repository_copyright}')"
                    if discrepancy
                    else None
                ),
                licence_basis=posts_cfg.licence_basis,
                resolution_evidence=posts_cfg.resolution_evidence,
                attribution=f"DuckDB {post.release} release post, by "
                f"{', '.join(authors) or 'unknown'}, duckdb.org blog "
                f"({src.repo} @ {src.commit[:12]}); {posts_cfg.repository_copyright}",
                redistributable=posts_cfg.redistributable,
                status="included",
            )
        )
    return tuple(records)


def build_manifest(
    cfg: CorpusConfig, repo: GitRepo, config_sha256: str, release_cache: Path
) -> CorpusManifest:
    """Build the manifest from git objects at the pinned commit and the verified release cache."""
    commit = cfg.docs_source.commit
    src = cfg.docs_source

    licence_entries = repo.ls_tree(commit, src.licence_file)
    if len(licence_entries) != 1:
        raise ValueError(f"licence file {src.licence_file} not found at {commit}")
    licence_blob = licence_entries[0].oid
    licence_sha = hashlib.sha256(repo.read_blobs([licence_blob])[licence_blob]).hexdigest()

    versions: list[VersionRecord] = []
    files: list[FileRecord] = []
    excluded: list[ExcludedRecord] = []
    for spec in cfg.versions:
        entries = [e for e in repo.ls_tree(commit, spec.docs_dir) if e.kind == "blob"]
        md = sorted((e for e in entries if e.path.endswith(".md")), key=lambda e: e.path)
        other = sorted((e for e in entries if not e.path.endswith(".md")), key=lambda e: e.path)
        blobs = repo.read_blobs([e.oid for e in md])
        version_files = [
            FileRecord(
                version=spec.version,
                path=e.path,
                git_blob=e.oid,
                sha256=hashlib.sha256(blobs[e.oid]).hexdigest(),
                bytes=len(blobs[e.oid]),
                words=len(blobs[e.oid].split()),
            )
            for e in md
        ]
        files.extend(version_files)
        excluded.extend(
            ExcludedRecord(
                version=spec.version,
                path=e.path,
                git_blob=e.oid,
                reason="not a Markdown documentation page (asset or build file)",
            )
            for e in other
        )
        versions.append(
            VersionRecord(
                version=spec.version,
                docs_dir=spec.docs_dir,
                behaviour_package=spec.behaviour_package,
                git_tree=repo.rev_parse(f"{commit}:{spec.docs_dir}"),
                md_files=len(version_files),
                words=sum(f.words for f in version_files),
                content_hash=content_hash([(f.path, f.sha256) for f in version_files]),
            )
        )

    release_notes = build_release_section(cfg.release_notes, release_cache)
    release_posts = build_release_posts(cfg, repo, licence_sha)
    return CorpusManifest(
        schema_version=SCHEMA_VERSION,
        library=cfg.library,
        config_sha256=config_sha256,
        generator=f"version-aware-rag {__version__}",
        source=SourceRecord(
            source_id=src.source_id,
            repo=src.repo,
            repo_url=src.repo_url,
            commit=commit,
            url_base=src.url_base,
            licence=src.licence,
            licence_status=src.licence_status,
            copyright=src.copyright,
            licence_file=src.licence_file,
            licence_file_git_blob=licence_blob,
            licence_file_sha256=licence_sha,
        ),
        release_notes=release_notes,
        release_posts=release_posts,
        versions=tuple(versions),
        files=tuple(files),
        excluded_files=tuple(excluded),
    )


# ------------------------------------------------------------------ comparison with RAG-M1


@dataclass(frozen=True)
class Discrepancy:
    subject: str  # for example "1.2" or "LICENSE"
    field: str
    expected: str
    actual: str


def compare_with_m1(manifest: CorpusManifest, cfg: CorpusConfig) -> list[Discrepancy]:
    """Compare recomputed values with the approved evidence held in the config."""
    out: list[Discrepancy] = []
    src = cfg.docs_source
    checks = [
        ("source", "commit", src.commit, manifest.source.commit),
        ("licence", "git_blob", src.licence_file_git_blob, manifest.source.licence_file_git_blob),
        ("licence", "sha256", src.licence_file_sha256, manifest.source.licence_file_sha256),
    ]
    for subject, name, exp, act in checks:
        if exp != act:
            out.append(Discrepancy(subject, name, exp, act))
    by_version = {v.version: v for v in manifest.versions}
    for spec in cfg.versions:
        rec = by_version.get(spec.version)
        if rec is None:
            out.append(Discrepancy(spec.version, "presence", "present", "missing"))
            continue
        for name in ("git_tree", "md_files", "words", "content_hash"):
            exp, act = getattr(spec.expected, name), getattr(rec, name)
            if exp != act:
                out.append(Discrepancy(spec.version, name, str(exp), str(act)))
    by_path = {p.path: p for p in manifest.release_posts}
    for post in cfg.release_posts.posts:
        got = by_path.get(post.path)
        if got is None:
            out.append(Discrepancy(post.path, "presence", "present", "missing"))
            continue
        for name in ("git_blob", "sha256"):
            if getattr(post, name) != getattr(got, name):
                out.append(Discrepancy(post.path, name, getattr(post, name), getattr(got, name)))
    return out


# ------------------------------------------------------------------ cache verification


@dataclass
class VerifyReport:
    commit_ok: bool = True
    manifest_matches_snapshot: bool = True
    snapshot_differences: list[str] = field(default_factory=list)
    m1_discrepancies: list[Discrepancy] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    releases: ReleaseVerifyReport = field(default_factory=ReleaseVerifyReport)

    @property
    def ok(self) -> bool:
        return (
            self.commit_ok
            and self.manifest_matches_snapshot
            and not self.m1_discrepancies
            and not self.missing_files
            and not self.changed_files
            and self.releases.ok
        )


def diff_manifests(expected: CorpusManifest, actual: CorpusManifest) -> list[str]:
    """Human-readable differences, in deterministic order."""
    diffs: list[str] = []
    for name in (
        "schema_version",
        "library",
        "config_sha256",
        "source",
        "release_notes",
        "release_posts",
    ):
        if getattr(expected, name) != getattr(actual, name):
            diffs.append(f"{name}: {getattr(expected, name)!r} != {getattr(actual, name)!r}")
    exp_v = {v.version: v for v in expected.versions}
    act_v = {v.version: v for v in actual.versions}
    for ver in sorted(exp_v.keys() | act_v.keys()):
        if exp_v.get(ver) != act_v.get(ver):
            diffs.append(f"version {ver}: {exp_v.get(ver)} != {act_v.get(ver)}")
    exp_f = {(f.version, f.path): f for f in expected.files}
    act_f = {(f.version, f.path): f for f in actual.files}
    for key in sorted(exp_f.keys() - act_f.keys()):
        diffs.append(f"file missing from snapshot: {key[1]}")
    for key in sorted(act_f.keys() - exp_f.keys()):
        diffs.append(f"file not in committed manifest: {key[1]}")
    for key in sorted(exp_f.keys() & act_f.keys()):
        if exp_f[key] != act_f[key]:
            diffs.append(
                f"file changed: {key[1]} sha256 {exp_f[key].sha256} -> {act_f[key].sha256}"
            )
    if expected.excluded_files != actual.excluded_files:
        diffs.append("excluded_files differ")
    return diffs


def verify_cache(
    cfg: CorpusConfig,
    repo: GitRepo,
    committed: CorpusManifest,
    config_sha256: str,
    release_cache: Path,
) -> VerifyReport:
    """Check that the caches, the committed manifest and the RAG-M1 evidence all agree."""
    report = VerifyReport()
    report.commit_ok = repo.rev_parse("HEAD") == cfg.docs_source.commit
    report.releases = verify_releases(cfg.release_notes, release_cache)
    report.m1_discrepancies = compare_with_m1(committed, cfg)
    if not report.releases.ok:
        report.manifest_matches_snapshot = False
        report.snapshot_differences = ["release-note cache not verified; manifest not rebuilt"]
        return report
    rebuilt = build_manifest(cfg, repo, config_sha256, release_cache)
    report.snapshot_differences = diff_manifests(committed, rebuilt)
    report.manifest_matches_snapshot = not report.snapshot_differences
    # The checked-out files must be byte-identical to the git objects they came from.
    checked = [(r.path, r.sha256) for r in committed.files]
    checked += [(p.path, p.sha256) for p in committed.release_posts]
    for rel_path, sha in checked:
        path = repo.path / rel_path
        if not path.is_file():
            report.missing_files.append(rel_path)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            report.changed_files.append(rel_path)
    return report


# ------------------------------------------------------------------ serialisation


# Layout (schema 4, owner-approved split, 2026-11-21):
#   corpus_manifest.json   everything except the per-file docs records, plus `file_index`
#   files/<version>.jsonl  one FileRecord per line, sorted by path, one file per docs version
# Paths in `file_index` are relative to the directory holding corpus_manifest.json.

FILES_DIR = "files"


class FileIndexEntry(_Strict):
    version: DocsVersion
    behaviour_package: str
    path: str
    records: int
    sha256: str


class MainManifest(_Strict):
    """What ``corpus_manifest.json`` itself contains."""

    schema_version: int
    library: str
    config_sha256: str
    generator: str
    source: SourceRecord
    release_notes: ReleaseNotesSection
    release_posts: tuple[ReleasePostRecord, ...]
    versions: tuple[VersionRecord, ...]
    excluded_files: tuple[ExcludedRecord, ...]
    file_index: tuple[FileIndexEntry, ...]


class ManifestIntegrityError(ValueError):
    """The manifest on disk is incomplete or inconsistent; ``problems`` lists every finding."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def part_path(version: str) -> str:
    return f"{FILES_DIR}/{version}.jsonl"


def _jsonl(records: list[FileRecord]) -> bytes:
    lines = [
        json.dumps(r.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) for r in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _main_json(main: MainManifest) -> str:
    """Deterministic JSON: sorted keys; long lists one record per line; LF; final newline."""
    data = main.model_dump(mode="json")
    listed = ("excluded_files", "file_index", "release_posts")
    head = {k: v for k, v in data.items() if k not in listed}
    lines = ["{"]
    for key in sorted(head):
        lines.append(
            f"  {json.dumps(key)}: {json.dumps(head[key], sort_keys=True, ensure_ascii=False)},"
        )
    for key in listed:
        records = data[key]
        lines.append(f"  {json.dumps(key)}: [")
        for i, rec in enumerate(records):
            sep = "," if i < len(records) - 1 else ""
            lines.append("    " + json.dumps(rec, sort_keys=True, ensure_ascii=False) + sep)
        lines.append("  ]" if key == listed[-1] else "  ],")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_manifest(manifest: CorpusManifest) -> dict[str, bytes]:
    """Every part, keyed by path relative to the manifest directory, in deterministic order."""
    by_version: dict[str, list[FileRecord]] = {v.version: [] for v in manifest.versions}
    for rec in manifest.files:
        by_version[rec.version].append(rec)
    parts: dict[str, bytes] = {}
    index = []
    for v in manifest.versions:
        records = sorted(by_version[v.version], key=lambda r: r.path)
        data = _jsonl(records)
        parts[part_path(v.version)] = data
        index.append(
            FileIndexEntry(
                version=v.version,
                behaviour_package=v.behaviour_package,
                path=part_path(v.version),
                records=len(records),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    main = MainManifest(
        **{k: getattr(manifest, k) for k in MainManifest.model_fields if k != "file_index"},
        file_index=tuple(index),
    )
    parts["corpus_manifest.json"] = _main_json(main).encode("utf-8")
    return dict(sorted(parts.items()))


def dumps_manifest(manifest: CorpusManifest) -> str:
    """The main ``corpus_manifest.json`` text (the per-version parts come from render_manifest)."""
    return render_manifest(manifest)["corpus_manifest.json"].decode("utf-8")


def write_manifest(manifest: CorpusManifest, path: Path) -> None:
    """Write the main file and every per-version part. Files not in the index are left alone;
    verification reports them as unexpected."""
    for rel, data in render_manifest(manifest).items():
        target = path if rel == "corpus_manifest.json" else path.parent / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def stale_manifest_parts(manifest: CorpusManifest, path: Path) -> list[str]:
    """Parts on disk that differ from a fresh rendering (for ``manifest --check``)."""
    out = []
    for rel, data in render_manifest(manifest).items():
        target = path if rel == "corpus_manifest.json" else path.parent / rel
        if not target.is_file():
            out.append(f"missing: {rel}")
        elif target.read_bytes() != data:
            out.append(f"differs from a fresh build: {rel}")
    return out


def _load_part(
    entry: FileIndexEntry, base: Path, version: VersionRecord | None, problems: list[str]
) -> list[FileRecord]:
    target = base / entry.path
    if entry.path != part_path(entry.version):
        problems.append(
            f"{entry.path}: path does not follow the convention {part_path(entry.version)}"
        )
    if not target.is_file():
        problems.append(f"{entry.path}: missing")
        return []
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        problems.append(f"{entry.path}: modified (sha256 differs from the index)")
    records: list[FileRecord] = []
    seen: set[str] = set()
    prefix = f"docs/{entry.version}/"
    for n, line in enumerate(data.decode("utf-8", "replace").split("\n"), start=1):
        if not line:
            continue
        try:
            rec = FileRecord.model_validate_json(line)
        except ValueError as exc:
            problems.append(f"{entry.path}:{n}: invalid record ({type(exc).__name__})")
            continue
        if rec.path in seen:
            problems.append(f"{entry.path}:{n}: duplicate record for {rec.path}")
        seen.add(rec.path)
        if rec.version != entry.version or not rec.path.startswith(prefix):
            problems.append(
                f"{entry.path}:{n}: inconsistent version: record says {rec.version} for {rec.path}"
            )
        records.append(rec)
    if [r.path for r in records] != sorted(r.path for r in records):
        problems.append(f"{entry.path}: records are not sorted by path")
    if len(records) != entry.records:
        problems.append(f"{entry.path}: {len(records)} records, index says {entry.records}")
    if version is not None:
        if len(records) != version.md_files:
            problems.append(
                f"{entry.path}: {len(records)} records, version {version.version} says "
                f"{version.md_files} files"
            )
        if content_hash([(r.path, r.sha256) for r in records]) != version.content_hash:
            problems.append(f"{entry.path}: records do not reproduce the version content_hash")
        if sum(r.words for r in records) != version.words:
            problems.append(f"{entry.path}: records do not reproduce the version word count")
    return records


def load_manifest(path: Path) -> tuple[CorpusManifest | None, list[str]]:
    """Read the manifest and every part; return it with every integrity problem found."""
    problems: list[str] = []
    try:
        main = MainManifest.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        return None, [f"{path.name}: unreadable or invalid ({type(exc).__name__}: {exc})"]
    versions = {v.version: v for v in main.versions}
    indexed = [e.version for e in main.file_index]
    if indexed != [v.version for v in main.versions]:
        problems.append(
            f"file_index covers {indexed}, expected {[v.version for v in main.versions]}"
        )
    files: list[FileRecord] = []
    for entry in main.file_index:
        files.extend(_load_part(entry, path.parent, versions.get(entry.version), problems))
    files_dir = path.parent / FILES_DIR
    expected = {e.path for e in main.file_index}
    if files_dir.is_dir():
        for extra in sorted(files_dir.iterdir()):
            rel = f"{FILES_DIR}/{extra.name}"
            if rel not in expected:
                problems.append(f"{rel}: unexpected file (not in file_index)")
    paths = [f.path for f in files]
    for dup in sorted({x for x in paths if paths.count(x) > 1}):
        problems.append(f"duplicate record across parts: {dup}")
    manifest = CorpusManifest(
        **{k: getattr(main, k) for k in CorpusManifest.model_fields if k != "files"},
        files=tuple(files),
    )
    return manifest, problems


def read_manifest(path: Path) -> CorpusManifest:
    """Strict read: raises ManifestIntegrityError listing every problem."""
    manifest, problems = load_manifest(path)
    if manifest is None or problems:
        raise ManifestIntegrityError(problems)
    return manifest
