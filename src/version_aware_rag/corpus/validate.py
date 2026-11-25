"""Read-only corpus validation (RAG-M3 S5b): ``vrag corpus validate``.

Design: ``docs/design/RAG-M3_S5_build.md`` §3 (owner decisions S5-D4, S5-D7, S5-D8, S5b-D1 to
S5b-D8). Validation reads the generated outputs, the committed manifests and the configuration;
it never writes, never rebuilds, and never runs the S4 chunker.

1. **Required outputs** come from ``build_manifest.json`` (its schema, paths, record and byte
   counts and hashes) plus the fixed S5a output set. A missing output is exit 2.
2. **Integrity** reuses the S4c and S4d validators.
3. **Per-chunk rules** R1-R7, R12; **corpus rules** R8 (S4 hierarchy and chunking invariants
   provable from records), R9 (manifest completeness, RAG-M1 evidence), cross-file IDs and
   unresolved sources.
4. **Cache tier** (S5b-D1, S5b-D2): source coverage through the S3 parser (and S4d's Q7 for
   release notes), and the leak scan; both are warnings ("skipped") when a cache is unavailable.
5. **Warnings** (S5-D7 and the S5b plan): oversize chunks, near-empty pages, section-path
   fallbacks, cross-version duplicate content, skipped checks.

Exit codes (S5-D4): 0 no errors (warnings allowed), 1 errors, 2 missing outputs or bad usage.
Messages never quote release-note or release-post text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from version_aware_rag.corpus.build import (
    ALL_OUTPUTS,
    BUILD_MANIFEST,
    LOGICAL_CORPUS,
    UNRESOLVED_FILE,
    UnresolvedSource,
)
from version_aware_rag.corpus.chunk import LIST_ITEM, TABLE_DELIMITER, Chunk
from version_aware_rag.corpus.chunk_output import (
    CHUNK_DIR,
    CHUNK_FILES,
    CHUNK_MANIFEST,
    EMPTY_FILE,
    chunk_file_problems,
    parse_corpus_page,
)
from version_aware_rag.corpus.compat import is_compatible, series_of
from version_aware_rag.corpus.config import (
    APPROVED_MIGRATION_GUIDES,
    ChunkingConfig,
    CorpusConfig,
    ParsingConfig,
)
from version_aware_rag.corpus.manifest import CorpusManifest, compare_with_m1, load_manifest
from version_aware_rag.corpus.parse import ParsedDocument, page_url, parse_page
from version_aware_rag.corpus.release_chunks import (
    RELEASE_DIR,
    RELEASE_FILE,
    RELEASE_MANIFEST,
    apply_q7,
    release_file_problems,
)
from version_aware_rag.corpus.releases import body_path, html_url
from version_aware_rag.corpus.sections import EmptySection

Severity = Literal["error", "warning"]
VERSION_FILES = tuple(n for n in CHUNK_FILES if n != EMPTY_FILE)
OVERSIZE_REASONS = {"code_block", "table_row", "list_item", "api_entry_block"}
NEAR_EMPTY_WORDS = 20  # S5-D7
LEAK_LINE_CHARS = 25  # S5b-D5
LEAK_HANDLE_CHARS = 4  # S5b-D5
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_HANDLE = re.compile(r"@([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)")
_RELEASE_PATH = re.compile(r"^releases/tag/(v\d+\.\d+\.\d+)$")
_FRONT_MATTER = ("title:", "layout:", "author:", "excerpt:", "---", "{%", "```")
_TOKEN = re.compile(r"\w+|[^\w\s]+")  # leak scan: token boundaries for code containment


class ValidationUsageError(Exception):
    """A required input cannot be loaded at all (exit 2)."""


@dataclass(frozen=True)
class Finding:
    severity: Severity
    rule: str
    message: str


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    counts: dict[str, object] = field(default_factory=dict)

    def error(self, rule: str, message: str) -> None:
        self.findings.append(Finding("error", rule, message))

    def warn(self, rule: str, message: str) -> None:
        self.findings.append(Finding("warning", rule, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def exit_code(self) -> int:
        if self.missing:
            return 2
        return 1 if self.errors else 0


@dataclass(frozen=True)
class ValidateInputs:
    root: Path
    cfg: CorpusConfig
    cfg_sha256: str
    manifest_path: Path
    parsing: ParsingConfig
    parsing_sha256: str
    chunking: ChunkingConfig
    chunking_sha256: str
    cache: Path | None  # docs cache (optional: cache-tier checks are skipped without it)
    release_cache: Path | None
    committable_files: Sequence[Path] | None  # leak scan (None: skipped with a warning)


@dataclass(frozen=True)
class LoadedChunk:
    chunk: Chunk
    file: str
    line: int

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_file(path: Path, report: ValidationReport, rule: str) -> dict[str, object] | None:
    """S5b-D7: present but unparseable is an error (exit 1)."""
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        report.error(rule, f"{path.name}: unreadable or not JSON ({type(exc).__name__})")
        return None
    if not isinstance(data, dict):
        report.error(rule, f"{path.name}: not a JSON object")
        return None
    return data


# ---------------------------------------------------------------- 1. build manifest and outputs


def check_build_manifest(inp: ValidateInputs, report: ValidationReport) -> None:
    """Schema, paths, records, bytes and hashes of every output the build manifest lists."""
    path = inp.root / BUILD_MANIFEST
    expected_outputs = [p for p in ALL_OUTPUTS if p != BUILD_MANIFEST]
    if not path.is_file():
        report.missing.append(BUILD_MANIFEST)
        return
    manifest = _json_file(path, report, "R9c")
    if manifest is None:
        return
    if manifest.get("schema_version") != 1:
        report.error("R9c", "build manifest: schema_version is not 1")
    if manifest.get("library") != "duckdb" or not isinstance(manifest.get("generator"), str):
        report.error("R9c", "build manifest: library or generator missing or wrong")
    if manifest.get("logical_corpus") != list(LOGICAL_CORPUS):
        report.error(
            "R9c", "build manifest: logical_corpus differs from S5-D1 (5 files + releases)"
        )
    if not isinstance(manifest.get("counts"), dict):
        report.error("R9c", "build manifest: counts missing")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        report.error("R9c", "build manifest: inputs missing")
    else:
        expected_inputs = {
            "corpus_config_sha256": inp.cfg_sha256,
            "parsing_config_sha256": inp.parsing_sha256,
            "chunking_config_sha256": inp.chunking_sha256,
            "corpus_manifest_sha256": _sha(inp.manifest_path.read_bytes())
            if inp.manifest_path.is_file()
            else None,
            "source_commit": inp.cfg.docs_source.commit,
        }
        for key, value in expected_inputs.items():
            if inputs.get(key) != value:
                report.error("R9c", f"build manifest: input {key} differs from the current one")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list):
        report.error("R9c", "build manifest: outputs missing")
        return
    seen: set[str] = set()
    for i, entry in enumerate(outputs):
        if not isinstance(entry, dict) or set(entry) != {"path", "records", "bytes", "sha256"}:
            report.error("R9c", f"build manifest: output entry {i} has the wrong fields")
            continue
        rel = entry["path"]
        if (
            not isinstance(rel, str)
            or rel.startswith(("/", "\\"))
            or ".." in rel.split("/")
            or ":" in rel
            or "\\" in rel
        ):
            report.error("R9c", f"build manifest: output entry {i} has an unsafe path {rel!r}")
            continue
        if rel in seen:
            report.error("R9c", f"build manifest: {rel} listed twice")
            continue
        seen.add(rel)
        if rel not in expected_outputs:
            report.error("R9c", f"build manifest: unknown output {rel}")
        target = inp.root / rel
        if not target.is_file():
            report.missing.append(rel)  # listed but missing: exit 2
            continue
        data = target.read_bytes()
        if _sha(data) != entry["sha256"]:
            report.error("R9c", f"{rel}: sha256 differs from the build manifest")
        if len(data) != entry["bytes"]:
            report.error("R9c", f"{rel}: byte count differs from the build manifest")
        records = data.count(b"\n") if rel.endswith(".jsonl") else None
        if records != entry["records"]:
            report.error("R9c", f"{rel}: record count differs from the build manifest")
    for rel in expected_outputs:
        if rel not in seen:
            report.error("R9c", f"build manifest does not list {rel}")


def check_required_outputs(inp: ValidateInputs, report: ValidationReport) -> None:
    for rel in ALL_OUTPUTS:
        if not (inp.root / rel).is_file() and rel not in report.missing:
            report.missing.append(rel)


# ---------------------------------------------------------------- 2. integrity (reused)


def check_integrity(inp: ValidateInputs, report: ValidationReport) -> None:
    chunk_manifest = _json_file(inp.root / CHUNK_MANIFEST, report, "R12")
    if chunk_manifest is not None:
        chunk_dir = inp.root / CHUNK_DIR
        files = {p.name: p.read_bytes() for p in sorted(chunk_dir.iterdir()) if p.is_file()}
        for problem in chunk_file_problems(files, chunk_manifest):
            report.error("R12", f"S4c output: {problem}")
    release_manifest = _json_file(inp.root / RELEASE_MANIFEST, report, "R12")
    if release_manifest is not None:
        data = (inp.root / RELEASE_DIR / RELEASE_FILE).read_bytes()
        for problem in release_file_problems(data, release_manifest):
            report.error("R12", f"S4d output: {problem}")


# ---------------------------------------------------------------- 3. loading records


def load_chunks(root: Path) -> list[LoadedChunk]:
    """Every parseable chunk of the logical corpus (unparseable lines are integrity errors)."""
    loaded: list[LoadedChunk] = []
    for name in (*VERSION_FILES, RELEASE_FILE):
        rel = f"{CHUNK_DIR}/{name}" if name != RELEASE_FILE else f"{RELEASE_DIR}/{RELEASE_FILE}"
        for n, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            try:
                loaded.append(LoadedChunk(Chunk.model_validate_json(line), name, n))
            except ValidationError:
                continue
    return loaded


def load_empty_sections(root: Path, report: ValidationReport) -> list[EmptySection]:
    out = []
    for n, line in enumerate((root / CHUNK_DIR / EMPTY_FILE).read_text("utf-8").splitlines(), 1):
        try:
            out.append(EmptySection.model_validate_json(line))
        except ValidationError:
            report.error("R12", f"{EMPTY_FILE}:{n}: invalid empty-section record")
    return out


def load_unresolved(root: Path, report: ValidationReport) -> list[UnresolvedSource]:
    out = []
    for n, line in enumerate((root / UNRESOLVED_FILE).read_text("utf-8").splitlines(), 1):
        try:
            out.append(UnresolvedSource.model_validate_json(line))
        except ValidationError:
            report.error("R1c", f"unresolved.jsonl:{n}: invalid unresolved-source record")
    return out


# ---------------------------------------------------------------- 4. per-chunk rules


@dataclass(frozen=True)
class _Context:
    manifest: CorpusManifest
    cfg: CorpusConfig
    parsing: ParsingConfig
    chunking: ChunkingConfig
    behaviour: dict[str, str]

    @property
    def site(self) -> str:
        return self.manifest.source.url_base.removesuffix("/docs")


def _check_docs_like(c: Chunk, where: str, ctx: _Context, report: ValidationReport) -> None:
    files = {f.path: f for f in ctx.manifest.files}
    rec = files.get(c.source_path)
    if rec is None:
        report.error("R2a", f"{where}: source_path {c.source_path} is not a manifest file")
        return
    guide = c.source_path in APPROVED_MIGRATION_GUIDES
    if (c.source_kind == "migration_guide") != guide:
        report.error("R5", f"{where}: source_kind {c.source_kind} wrong for {c.source_path}")
    if c.source_path in ctx.parsing.exclusions():
        report.error("R8e", f"{where}: chunk from an excluded page {c.source_path}")
    if rec.version != c.version or not c.source_path.startswith(f"docs/{c.version}/"):
        report.error("R5", f"{where}: version {c.version} does not match {c.source_path}")
    if c.source_url != page_url(c.source_path, ctx.parsing.site_base):
        report.error("R2b", f"{where}: source_url is not the canonical page URL")
    src = ctx.manifest.source
    if (c.source_content_hash, c.source_git_blob) != (rec.sha256, rec.git_blob):
        report.error("R2a", f"{where}: source hash or blob differs from the manifest")
    if (c.source_commit, c.source_repo) != (src.commit, src.repo):
        report.error("R2a", f"{where}: source commit or repository differs from the manifest")
    expected_type = "changelog" if guide else "documentation"
    if c.source_type != expected_type:
        report.error("R5", f"{where}: source_type {c.source_type}, expected {expected_type}")
    if guide:
        wanted = {g.path: g.introduced_in for g in ctx.chunking.migration_guides}[c.source_path]
        if c.introduced_in != wanted:
            report.error("R6", f"{where}: introduced_in {c.introduced_in}, expected {wanted}")
    lic = (c.licence, c.licence_status, c.licence_applicability, c.redistributable)
    if lic != (src.licence, src.licence_status, None, True):
        report.error("R7c", f"{where}: licence fields differ from the docs classification (S1)")


def _check_post(c: Chunk, where: str, ctx: _Context, report: ValidationReport) -> None:
    posts = {p.path: p for p in ctx.manifest.release_posts}
    rec = posts.get(c.source_path)
    if rec is None:
        report.error("R2a", f"{where}: {c.source_path} is not a manifest release post")
        return
    cfg_post = {p.path: p for p in ctx.cfg.release_posts.posts}[c.source_path]
    canonical = cfg_post.canonical_url(ctx.site, ctx.cfg.release_posts.url_pattern)
    if not (c.source_url == rec.canonical_url == canonical):
        report.error("R2b", f"{where}: source_url is not the post's canonical URL")
    if (c.version, c.introduced_in) != (rec.version, rec.release):
        report.error("R5", f"{where}: version or introduced_in differs from the post record")
    if c.source_type != "changelog" or c.source_content_hash != rec.sha256:
        report.error("R5", f"{where}: source_type or source hash differs from the post record")
    lic = (c.licence, c.licence_status, c.licence_applicability, c.redistributable)
    if lic != (rec.licence, rec.licence_status, rec.licence_applicability, False) or (
        c.attribution != rec.attribution
        or c.repository_licence != rec.repository_licence
        or c.copyright != rec.repository_copyright
    ):
        report.error("R7c", f"{where}: licence fields differ from the release-post record (D22)")


def _check_release(c: Chunk, where: str, ctx: _Context, report: ValidationReport) -> None:
    m = _RELEASE_PATH.match(c.source_path)
    releases = {r.tag: r for r in ctx.manifest.release_notes.included}
    rec = releases.get(m.group(1)) if m else None
    if rec is None:
        report.error("R2a", f"{where}: {c.source_path} is not an approved release")
        return
    if not (c.source_url == rec.source_url == html_url(ctx.cfg.release_notes, rec.tag)):
        report.error("R2b", f"{where}: source_url is not the release's canonical URL")
    if (c.version, c.introduced_in) != (rec.version, rec.introduced_in):
        report.error("R5", f"{where}: version or introduced_in differs from the release record")
    identity = (c.release_id, c.source_commit, c.source_content_hash, c.source_type)
    if identity != (rec.release_id, rec.tag_commit, rec.body_sha256, "changelog"):
        report.error("R5", f"{where}: release id, commit, hash or type differs from S2")
    lic = (c.licence, c.licence_status, c.licence_applicability, c.redistributable)
    if lic != (None, "unverified", "uncertain", False) or c.copyright is not None:
        report.error("R7c", f"{where}: release-note licence fields differ from S4-D5/S4-D12")
    if c.attribution != rec.attribution:
        report.error("R7a", f"{where}: attribution differs from the S2 record")


def check_chunk(lc: LoadedChunk, ctx: _Context, report: ValidationReport) -> None:
    c, where = lc.chunk, lc.where
    if lc.file != RELEASE_FILE and c.version != lc.file.removesuffix(".jsonl"):
        report.error("R1a", f"{where}: version {c.version} in the {lc.file} file")
    if c.behaviour_package != ctx.behaviour.get(c.version):
        report.error(
            "R5", f"{where}: behaviour_package {c.behaviour_package} wrong for {c.version}"
        )
    if c.source_kind in ("docs_page", "migration_guide"):
        _check_docs_like(c, where, ctx, report)
    elif c.source_kind == "release_post":
        _check_post(c, where, ctx, report)
    else:
        _check_release(c, where, ctx, report)
        if lc.file != RELEASE_FILE:
            report.error("R1a", f"{where}: a GitHub release chunk outside {RELEASE_FILE}")
    # R6: introduced_in exactly for change sources, within the chunk's series.
    if (c.introduced_in is not None) != (c.source_type == "changelog"):
        report.error("R6", f"{where}: introduced_in must be set exactly for change sources")
    if c.introduced_in is not None:
        try:
            in_series = series_of(c.introduced_in) == c.version
            compatible = is_compatible(
                c.version, c.source_type, c.introduced_in, c.version, ctx.behaviour
            )
        except ValueError:
            report.error("R6", f"{where}: introduced_in {c.introduced_in!r} is not X.Y.Z")
        else:
            if not in_series:
                report.error("R6", f"{where}: introduced_in {c.introduced_in} outside {c.version}")
            elif not compatible:
                report.error("R6", f"{where}: introduced_in {c.introduced_in} after the package")
    # R3
    if not c.section_path or c.section_path[0] != c.doc_title:
        report.error("R3", f"{where}: section_path empty or not rooted at the page title")
    # R7a/R7b
    if not c.attribution.strip() or not c.licence_status.strip():
        report.error("R7a", f"{where}: attribution or licence_status empty")
    if (c.licence is not None) != (c.licence_status == "verified"):
        report.error("R7b", f"{where}: licence must be set exactly when licence_status is verified")
    # R8b and unit kinds
    if c.unit_kind == "prose_merged":
        n = len(c.section_path)
        if (
            len(c.merged_section_paths) < 2
            or c.part_count != 1
            or len(set(c.merged_section_paths)) != len(c.merged_section_paths)
            or any(len(p) != n + 1 or p[:n] != c.section_path for p in c.merged_section_paths)
        ):
            report.error("R8b", f"{where}: merged chunk does not reconstruct its sections")
        if c.api_signature is not None:
            report.error("R8a", f"{where}: a merged chunk carries an API signature")
    else:
        if c.merged_section_paths:
            report.error("R8b", f"{where}: unmerged chunk lists merged paths")
        if (c.unit_kind == "api_entry") != (c.api_signature is not None):
            report.error("R8a", f"{where}: api_signature must be set exactly for API entries")
    # R8d: S4 size, oversize and overlap rules provable from the record
    cfg = ctx.chunking
    if c.token_count_approx > cfg.hard_limit_tokens and not c.oversize:
        report.error("R8d", f"{where}: {c.token_count_approx} tokens without oversize")
    if c.oversize != (c.oversize_reason is not None) or (
        c.oversize_reason is not None and c.oversize_reason not in OVERSIZE_REASONS
    ):
        report.error("R8d", f"{where}: oversize flag and reason inconsistent")
    if c.overlap_prev_tokens > cfg.overlap_ratio * cfg.target_tokens:
        report.error("R8d", f"{where}: overlap {c.overlap_prev_tokens} tokens over the budget")
    if c.overlap_prev_blocks and (c.part_ordinal == 1 or c.oversize or c.split_inside_block):
        report.error("R8d", f"{where}: overlap where the S4 rules allow none")
    if (c.overlap_line_start is None) != (c.overlap_prev_blocks == 0):
        report.error("R8d", f"{where}: overlap line range inconsistent with overlap blocks")


# ---------------------------------------------------------------- 5. corpus-level rules


def check_corpus(
    chunks: list[LoadedChunk],
    empties: list[EmptySection],
    unresolved: list[UnresolvedSource],
    ctx: _Context,
    report: ValidationReport,
) -> None:
    # R12a: IDs unique across the whole logical corpus.
    first: dict[str, str] = {}
    for lc in chunks:
        other = first.setdefault(lc.chunk.chunk_id, lc.where)
        if other != lc.where:
            report.error("R12a", f"{lc.where}: chunk_id {lc.chunk.chunk_id} also at {other}")
    # R1c: unresolved sources never chunked.
    chunked = {lc.chunk.source_path for lc in chunks}
    for u in unresolved:
        if u.path in chunked:
            report.error("R1c", f"unresolved source {u.path} appears in a chunk file")
    # R8a/R8c: parts of one unit are complete and consistent.
    units: dict[tuple[str, str, int], list[LoadedChunk]] = defaultdict(list)
    for lc in chunks:
        c = lc.chunk
        units[(c.version, c.source_path, c.unit_ordinal)].append(lc)
    api_paths: dict[tuple[str, str, tuple[str, ...], int], str] = {}
    for (version, path, ordinal), parts in units.items():
        cs = [p.chunk for p in parts]
        head = cs[0]
        where = f"{path} unit {ordinal} ({version})"
        if sorted(c.part_ordinal for c in cs) != list(range(1, head.part_count + 1)):
            report.error("R8c", f"{where}: parts are not exactly 1..{head.part_count}")
        same = {
            (
                c.section_path,
                c.merged_section_paths,
                c.unit_kind,
                c.api_signature,
                c.occurrence,
                c.part_count,
            )
            for c in cs
        }
        if len(same) != 1:
            report.error("R8c", f"{where}: parts disagree on path, kind, signature or count")
        if head.unit_kind == "api_entry":
            key = (version, path, head.section_path, head.occurrence)
            if key in api_paths:
                report.error("R8a", f"{where}: API entry {head.section_path} split over units")
            api_paths[key] = where
    for lc in chunks:
        c = lc.chunk
        for merged in c.merged_section_paths:
            for occurrence in range(c.occurrence + 2):
                if (c.version, c.source_path, merged, occurrence) in api_paths:
                    report.error("R8a", f"{lc.where}: merged chunk contains API entry {merged}")
    # Empty-section records belong to manifest pages of their version.
    known = {(f.version, f.path) for f in ctx.manifest.files}
    known |= {(p.version, p.path) for p in ctx.manifest.release_posts}
    for e in empties:
        if (e.version, e.source_path) not in known:
            report.error("R8e", f"empty-section record for unknown page {e.source_path}")


# ---------------------------------------------------------------- 6. manifest completeness


def check_manifest(inp: ValidateInputs, report: ValidationReport) -> CorpusManifest:
    manifest, problems = load_manifest(inp.manifest_path)
    if manifest is None:
        raise ValidationUsageError("; ".join(problems) or "corpus manifest unreadable")
    for p in problems:
        report.error("R9a", f"corpus manifest: {p}")
    for d in compare_with_m1(manifest, inp.cfg):
        report.error("R9a", f"RAG-M1 evidence: {d}")
    for f in manifest.files:
        if not (_SHA256.match(f.sha256) and _GIT_SHA.match(f.git_blob) and f.bytes >= 0):
            report.error("R9b", f"manifest file {f.path}: hash, blob or size missing")
    for x in manifest.excluded_files:
        if not x.reason.strip():
            report.error("R9b", f"excluded file {x.path} has no reason")
    src = manifest.source
    if not (src.licence and src.licence_status and src.copyright):
        report.error("R9b", "manifest source: licence, licence_status or copyright missing")
    for post in manifest.release_posts:
        if not (post.licence_status and post.licence_applicability and post.attribution):
            report.error("R9b", f"release post {post.path}: licence fields or attribution missing")
    for r in manifest.release_notes.included:
        if not (r.licence_status and r.attribution) or r.redistributable:
            report.error("R9b", f"release {r.tag}: licence status or attribution wrong")
    return manifest


# ---------------------------------------------------------------- 7. cache tier


@dataclass
class Coverage:
    verbatim: int = 0
    split: int = 0
    headings: int = 0
    problems: list[str] = field(default_factory=list)


def _pieces(block_text: str, kind: str) -> list[str]:
    """Rows of a table (header included) or top-level items of a list: the units S4 may split."""
    lines = block_text.split("\n")
    if kind == "table":
        return [ln for ln in lines if ln.strip()]
    if kind == "list":
        items: list[list[str]] = []
        for line in lines:
            if LIST_ITEM.match(line) or not items:
                items.append([line])
            else:
                items[-1].append(line)
        return ["\n".join(it) for it in items]
    return []


def source_coverage(
    doc: ParsedDocument,
    chunks: Sequence[Chunk],
    empties: Sequence[EmptySection],
    line_count: int | None = None,
) -> Coverage:
    """S5b-D1 option B: every S3 block of the page is represented in the page's chunks.

    Legitimate S4 transformations are accounted for: overlap repeats whole blocks (substring
    tests tolerate repetition), split tables and lists are checked row by row and item by item
    (repeated headers included), and headings live in section paths. The chunker is not run."""
    cov = Coverage()
    text = "\n\n".join(c.content for c in chunks)
    path_elements = {e for c in chunks for e in c.section_path}
    path_elements |= {e for c in chunks for p in c.merged_section_paths for e in p}
    path_elements |= {e for x in empties for e in x.section_path}
    for b in doc.blocks:
        span = f"{doc.path}:{b.line_start}-{b.line_end}"
        if b.kind == "heading":
            if b.text in path_elements or b.text.strip() == (doc.title or "").strip():
                cov.headings += 1
            else:
                cov.problems.append(f"{span}: heading not represented in any section path")
            continue
        if b.text in text:
            cov.verbatim += 1
            continue
        pieces = _pieces(b.text, b.kind)
        if b.kind == "table":
            lines = b.text.split("\n")
            header_ok = (
                len(lines) < 2
                or not TABLE_DELIMITER.match(lines[1])
                or ("\n".join(lines[:2]) in text)
            )
            pieces_ok = header_ok and all(p in text for p in pieces)
        else:
            pieces_ok = bool(pieces) and all(p in text for p in pieces)
        if pieces_ok:
            cov.split += 1
        else:
            missing = sum(p not in text for p in pieces) if pieces else 1
            cov.problems.append(
                f"{span}: {b.kind} block not represented in the page's chunks "
                f"({missing} of {max(len(pieces), 1)} parts missing)"
            )
    ordered = sorted(chunks, key=lambda c: (c.unit_ordinal, c.part_ordinal))
    last = 0
    for c in ordered:
        if c.line_start > c.line_end or c.line_start < 1:
            cov.problems.append(f"{doc.path}: chunk {c.chunk_id} has an invalid line range")
        if line_count is not None and c.line_end > line_count:
            cov.problems.append(f"{doc.path}: chunk {c.chunk_id} ends after the last source line")
        if c.overlap_line_end is not None and c.overlap_line_end >= c.line_start:
            cov.problems.append(f"{doc.path}: chunk {c.chunk_id} overlap is not before its lines")
        if c.line_start < last:
            cov.problems.append(f"{doc.path}: chunk {c.chunk_id} is out of source order")
        last = max(last, c.line_start)
    return cov


def check_source_coverage(
    inp: ValidateInputs,
    manifest: CorpusManifest,
    chunks: list[LoadedChunk],
    empties: list[EmptySection],
    report: ValidationReport,
) -> None:
    by_page: dict[tuple[str, str], list[Chunk]] = defaultdict(list)
    for lc in chunks:
        by_page[(lc.chunk.version, lc.chunk.source_path)].append(lc.chunk)
    empty_by_page: dict[tuple[str, str], list[EmptySection]] = defaultdict(list)
    for e in empties:
        empty_by_page[(e.version, e.source_path)].append(e)
    totals = Counter[str]()
    if inp.cache is None or not (inp.cache / ".git").is_dir():
        report.warn("R8e", "source coverage skipped for docs and posts: docs cache unavailable")
    else:
        pages = [(f.version, f.path) for f in manifest.files]
        pages += [(p.version, p.path) for p in manifest.release_posts]
        for version, path in pages:
            try:
                doc = parse_corpus_page(manifest, inp.cache, inp.parsing, path)
            except (OSError, ValueError) as exc:
                report.error("R8e", f"{path}: cannot be read for coverage ({exc})")
                continue
            if doc is None:
                continue
            key = (version, path)
            totals["pages"] += 1
            if not doc.include_in_corpus:
                totals["excluded_pages"] += 1
                if by_page.get(key):
                    report.error("R8e", f"{path}: excluded page has chunks")
                continue
            if not by_page.get(key) and any(b.kind != "heading" for b in doc.blocks):
                report.error("R8e", f"{path}: included page with content has no chunks")
                continue
            lines = (inp.cache / path).read_bytes().count(b"\n") + 1
            cov = source_coverage(doc, by_page.get(key, []), empty_by_page.get(key, []), lines)
            totals["blocks_verbatim"] += cov.verbatim
            totals["blocks_split"] += cov.split
            totals["headings"] += cov.headings
            for problem in cov.problems:
                report.error("R8e", problem)
    if inp.release_cache is None or not inp.release_cache.is_dir():
        report.warn("R8e", "source coverage skipped for release notes: release cache unavailable")
    else:
        for rec in manifest.release_notes.included:
            body = body_path(inp.release_cache, rec.tag)
            if not body.is_file() or _sha(body.read_bytes()) != rec.body_sha256:
                report.error("R8e", f"{rec.tag}: release body missing or differs from S2")
                continue
            raw = body.read_bytes()
            doc = parse_page(
                raw=raw,
                version=rec.version,
                path=f"releases/tag/{rec.tag}",
                source_sha256=rec.body_sha256,
                site_base=inp.parsing.site_base,
                source_kind="github_release",
                url=rec.source_url,
            )
            q7 = apply_q7(doc, raw.decode("utf-8"))  # the approved S4d transformation (not S4)
            totals["q7_transformations"] += sum(q7.counts.values())
            key = (rec.version, f"releases/tag/{rec.tag}")
            cov = source_coverage(
                q7.doc, by_page.get(key, []), empty_by_page.get(key, []), raw.count(b"\n") + 1
            )
            totals["release_blocks_verbatim"] += cov.verbatim
            totals["release_blocks_split"] += cov.split
            for problem in cov.problems:
                report.error("R8e", problem)
    report.counts["coverage"] = dict(sorted(totals.items()))


@dataclass(frozen=True)
class LeakException:
    """An owner-approved quotation of restricted text in an immutable committed file.

    It covers exactly one line: the file path, the 1-based line number, the sha256 of that whole
    line (UTF-8, without its line ending) and the sha256 of the restricted line it quotes. Any
    edit or move of the line, or another restricted line on it, is no longer covered."""

    path: str
    line: int
    line_sha256: str
    matched_sha256: str
    reason: str


# Heading fingerprints. Lines 107 and 109 of RAG-M1_research_evidence.md now cite these sections as
# "release-post section A" (1.3.0 post, line 18) and "B" (1.5.0 post, line 282) by path and line
# only (owner 2026-11-23); tests check that neither heading is back on those lines.
_POST_130_HEADING = "24e78d30f71bdb38d838555558bdfe5835dd71ee850c679d7c5f241fd087c814"
_POST_150_HEADING = "3bd26a905ea5f0c26cd003140f65fe1fca1520590a684ed80c8dc053b90b0e5d"
# Owner 2026-11-23: no exceptions remain; every heading locator in a committed file was replaced
# by a neutral section identifier (path and line only). Add one only with owner approval.
LEAK_EXCEPTIONS: tuple[LeakException, ...] = ()


def release_restricted_lines(text: str) -> set[str]:
    """Normalised release-note lines longer than the S5b-D5 threshold."""
    return {
        s for ln in text.splitlines() if len(s := ln.strip().lstrip("*#").strip()) > LEAK_LINE_CHARS
    }


def post_restricted_lines(text: str) -> set[str]:
    """Normalised release-post lines longer than the threshold, front matter excluded."""
    return {
        s
        for ln in text.splitlines()
        if len(s := ln.strip().lstrip("#").strip()) > LEAK_LINE_CHARS
        and not s.startswith(_FRONT_MATTER)
    }


def docs_corpus_lines(text: str) -> set[str]:
    """A docs page's lines under both normalisations above (the approved MIT corpus)."""
    lines = text.splitlines()
    return {ln.strip().lstrip("*#").strip() for ln in lines} | {
        ln.strip().lstrip("#").strip() for ln in lines
    }


def fenced_code_lines(text: str) -> set[str]:
    """The stripped, non-empty lines inside fenced code blocks (``` or ~~~), fences excluded."""
    lines: set[str] = set()
    inside = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith(("```", "~~~")):
            inside = not inside
        elif inside and s:
            lines.add(s)
    return lines


def token_aligned_in(s: str, line: str) -> bool:
    """``s`` occurs in ``line`` (plain substring, whitespace significant) starting and ending on
    token boundaries of ``line``; a token is a run of word characters or of other non-space
    characters, so ``10`` does not match inside ``100`` nor a border inside a wider one."""
    spans = [m.span() for m in _TOKEN.finditer(line)]
    starts, ends = {a for a, _ in spans}, {b for _, b in spans}
    i = line.find(s)
    while i != -1:
        if i in starts and i + len(s) in ends:
            return True
        i = line.find(s, i + 1)
    return False


def shared_with_docs(
    restricted: set[str], restricted_code: set[str], docs_lines: set[str], docs_code: set[str]
) -> set[str]:
    """Restricted lines whose text also occurs in the approved docs corpus (owner, S5b review):

    - equal to a normalised docs line, in any context; or
    - a line inside a fenced code block of its source (``restricted_code``) that occurs,
      token-aligned and with identical whitespace, in a docs line inside a fenced code block
      (``docs_code``): a post may wrap a code line that a docs example continues.

    Prose and headings are never matched by containment."""
    shared = restricted & docs_lines
    candidates = sorted(s for s in restricted - shared if s in restricted_code)
    if candidates:
        code = sorted(docs_code)
        blob = "\n".join(code)
        shared |= {
            s
            for s in candidates
            if s in blob and any(token_aligned_in(s, d) for d in code if s in d)
        }
    return shared


def count_leaks(
    rel: str, text: str, restricted: set[str], exceptions: tuple[LeakException, ...]
) -> tuple[int, int]:
    """For one file: (restricted lines with at least one occurrence not covered by an exception,
    occurrences covered by an exception)."""
    hits = [s for s in restricted if s in text]
    if not hits:
        return 0, 0
    lines = text.splitlines()
    allowed = {(e.line, e.line_sha256, e.matched_sha256) for e in exceptions if e.path == rel}
    leaked = covered = 0
    for s in hits:
        s_sha = _sha(s.encode("utf-8"))
        occurrences = [(i, ln) for i, ln in enumerate(lines, 1) if s in ln]
        ok = sum((i, _sha(ln.encode("utf-8")), s_sha) in allowed for i, ln in occurrences)
        covered += ok
        if not occurrences or ok < len(occurrences):
            leaked += 1
    return leaked, covered


def check_leaks(inp: ValidateInputs, manifest: CorpusManifest, report: ValidationReport) -> None:
    """S5b-D5: committable files must not contain release-note or release-post text, or
    contributor handles. Findings name the file only; the text itself is never printed.

    A line that also occurs in the approved versioned docs corpus (the hash-checked manifest
    pages, MIT) is not restricted text (``shared_with_docs``). Owner-approved quotations in
    immutable committed files are matched exactly by ``LEAK_EXCEPTIONS``; everything else stays
    an error."""
    if inp.committable_files is None:
        report.warn("R7d", "leak scan skipped: the committable file list is unavailable")
        return
    docs_lines: set[str] = set()
    docs_code: set[str] = set()
    if inp.cache is not None and (inp.cache / ".git").is_dir():
        for file_rec in manifest.files:
            page = inp.cache / file_rec.path
            if page.is_file() and _sha(raw := page.read_bytes()) == file_rec.sha256:
                docs_lines |= docs_corpus_lines(page_text := raw.decode("utf-8"))
                docs_code |= fenced_code_lines(page_text)
    release_lines: set[str] = set()
    release_code: set[str] = set()
    handles: set[str] = set()
    if inp.release_cache is not None and inp.release_cache.is_dir():
        for rec in manifest.release_notes.included:
            body = body_path(inp.release_cache, rec.tag)
            if body.is_file():
                text = body.read_text(encoding="utf-8")
                handles |= {h for h in _HANDLE.findall(text) if len(h) >= LEAK_HANDLE_CHARS}
                release_lines |= release_restricted_lines(text)
                release_code |= fenced_code_lines(text)
    else:
        report.warn("R7d", "leak scan for release notes skipped: release cache unavailable")
    post_lines: set[str] = set()
    post_code: set[str] = set()
    if inp.cache is not None and (inp.cache / "_posts").is_dir():
        for post in manifest.release_posts:
            path = inp.cache / post.path
            if path.is_file():
                post_lines |= post_restricted_lines(post_text := path.read_text(encoding="utf-8"))
                post_code |= fenced_code_lines(post_text)
    else:
        report.warn("R7d", "leak scan for release posts skipped: docs cache unavailable")
    release_shared = shared_with_docs(release_lines, release_code, docs_lines, docs_code)
    post_shared = shared_with_docs(post_lines, post_code, docs_lines, docs_code)
    release_lines -= release_shared
    post_lines -= post_shared
    handle_res = [re.compile(rf"@{re.escape(h)}\b") for h in sorted(handles)]
    scanned = covered_total = 0
    for f in inp.committable_files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        rel = f.relative_to(inp.root).as_posix() if f.is_relative_to(inp.root) else str(f)
        n_release, c_release = count_leaks(rel, text, release_lines, LEAK_EXCEPTIONS)
        n_post, c_post = count_leaks(rel, text, post_lines, LEAK_EXCEPTIONS)
        covered_total += c_release + c_post
        n_handle = sum(1 for r in handle_res if r.search(text))
        if n_release:
            report.error("R7d", f"{rel}: contains {n_release} release-note line(s)")
        if n_post:
            report.error("R7d", f"{rel}: contains {n_post} release-post line(s)")
        if n_handle:
            report.error("R7d", f"{rel}: contains {n_handle} contributor handle(s)")
    report.counts["leak_scan"] = {
        "files_scanned": scanned,
        "release_lines": len(release_lines),
        "post_lines": len(post_lines),
        "handles": len(handles),
        "release_lines_shared_with_docs": len(release_shared),
        "post_lines_shared_with_docs": len(post_shared),
        "exception_occurrences": covered_total,
    }


# ---------------------------------------------------------------- 8. warnings and orchestration


def add_warnings(chunks: list[LoadedChunk], report: ValidationReport) -> None:
    cs = [lc.chunk for lc in chunks]
    oversize = Counter(str(c.oversize_reason) for c in cs if c.oversize)
    if oversize:
        report.warn("W-oversize", f"{sum(oversize.values())} oversize chunks {dict(oversize)}")
    words: Counter[tuple[str, str]] = Counter()
    for c in cs:
        words[(c.version, c.source_path)] += len(c.content.split())
    near_empty = sorted(
        f"{p} ({v}, {n} words)" for (v, p), n in words.items() if n < NEAR_EMPTY_WORDS
    )
    if near_empty:
        report.warn("R11", f"{len(near_empty)} near-empty pages: " + "; ".join(near_empty))
    fallback = sum(c.section_path_fallback for c in cs)
    if fallback:
        report.warn("R3w", f"{fallback} chunks use the section-path fallback")
    versions: dict[str, set[str]] = defaultdict(set)
    for c in cs:
        if c.source_kind != "github_release":
            versions[c.content_hash].add(c.version)
    duplicates = sum(1 for v in versions.values() if len(v) > 1)
    if duplicates:
        report.warn("W-duplicates", f"{duplicates} chunk contents appear in two or more versions")
    report.counts.update(
        {
            "oversize": dict(oversize),
            "near_empty_pages": len(near_empty),
            "section_path_fallback": fallback,
            "cross_version_duplicates": duplicates,
        }
    )


# ---------------------------------------------------------------------------------------------
# R7e: short quotations of release-post text (owner, 2026-11-23)

QUOTE_MIN_WORDS = 4
_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
_QUOTE_EDGE = " .,;:!?()[]{}'`*_"


def quote_key(text: str) -> str:
    """Comparison form of a phrase or a source text: backticks removed, lower case, whitespace
    collapsed, punctuation trimmed from both ends (so a quotation mark or full stop attached to
    the first or last word does not hide a match)."""
    return " ".join(text.replace("`", "").lower().split()).strip(_QUOTE_EDGE)


def quoted_phrases(line: str) -> list[str]:
    """Comparison forms of the double-quoted phrases of at least QUOTE_MIN_WORDS words."""
    keys = (quote_key(m.group(1) or m.group(2)) for m in _QUOTED.finditer(line))
    return [k for k in keys if len(k.split()) >= QUOTE_MIN_WORDS]


def contains_phrase(haystack: str, key: str) -> bool:
    """``key`` occurs in ``haystack`` (both comparison forms) at word boundaries."""
    start = 0
    while (i := haystack.find(key, start)) != -1:
        j = i + len(key)
        if (i == 0 or not haystack[i - 1].isalnum()) and (
            j == len(haystack) or not haystack[j].isalnum()
        ):
            return True
        start = i + 1
    return False


def restricted_quotes(text: str, restricted: str, permitted: str) -> list[tuple[int, int]]:
    """(line, words) for each quoted phrase in ``text`` that occurs in the restricted text and not
    in the permitted text (MIT docs and confirmed DuckDB runtime output). Both haystacks are
    comparison forms. Never returns the phrase itself."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for key in quoted_phrases(line):
            if contains_phrase(restricted, key) and not contains_phrase(permitted, key):
                found.append((n, len(key.split())))
    return found


def _runtime_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _runtime_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _runtime_strings(v)]
    return []


def check_short_quotes(
    inp: ValidateInputs, manifest: CorpusManifest, report: ValidationReport
) -> None:
    """R7e: committable files must not quote release-post text in short double-quoted phrases
    (at least QUOTE_MIN_WORDS words), which the line-based R7d scan cannot see. Phrases that also
    occur in the hash-verified MIT docs or in the confirmed DuckDB runtime output of the pinned
    RAG-M1 probe results are permitted. Fails closed: if a release post cannot be loaded and
    verified, the scan is an error, not a skip. Findings give file, line and word count only."""
    if inp.committable_files is None:
        report.error(
            "R7e", "short-quotation scan cannot run: the committable file list is unavailable"
        )
        return
    if manifest.release_posts and (inp.cache is None or not (inp.cache / "_posts").is_dir()):
        report.error("R7e", "short-quotation scan cannot run: release posts unavailable")
        return
    restricted_parts: list[str] = []
    for post in manifest.release_posts:
        data: bytes | None = None
        if inp.cache is not None:
            try:
                data = (inp.cache / post.path).read_bytes()
            except OSError:
                data = None
        if data is None or _sha(data) != post.sha256:
            report.error(
                "R7e",
                f"short-quotation scan cannot run: release post {post.path} missing or changed",
            )
            return
        restricted_parts.append(quote_key(data.decode("utf-8")))
    if not restricted_parts:
        report.counts["quote_scan"] = {"files_scanned": 0, "release_posts": 0, "findings": 0}
        return
    permitted_parts: list[str] = []
    if inp.cache is not None:
        for file_rec in manifest.files:
            page = inp.cache / file_rec.path
            if page.is_file() and _sha(raw := page.read_bytes()) == file_rec.sha256:
                permitted_parts.append(quote_key(raw.decode("utf-8")))
    for runtime in sorted((inp.root / "scripts/probes/expected").glob("*.jsonl")):
        for line in runtime.read_text(encoding="utf-8").splitlines():
            if line.strip():
                permitted_parts.extend(quote_key(s) for s in _runtime_strings(json.loads(line)))
    restricted = " \x00 ".join(restricted_parts)
    permitted = " \x00 ".join(permitted_parts)
    scanned = findings = 0
    for f in inp.committable_files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        rel = f.relative_to(inp.root).as_posix() if f.is_relative_to(inp.root) else str(f)
        for n, words in restricted_quotes(text, restricted, permitted):
            findings += 1
            report.error(
                "R7e", f"{rel}:{n}: quoted phrase of {words} words matches release-post text"
            )
    report.counts["quote_scan"] = {
        "files_scanned": scanned,
        "release_posts": len(restricted_parts),
        "findings": findings,
    }


def validate_corpus(inp: ValidateInputs) -> ValidationReport:
    """Run every check; read-only. Raises ``ValidationUsageError`` if the manifest won't load."""
    report = ValidationReport()
    check_build_manifest(inp, report)
    check_required_outputs(inp, report)
    if report.missing:
        return report  # exit 2: nothing else can be checked reliably
    manifest = check_manifest(inp, report)
    ctx = _Context(
        manifest=manifest,
        cfg=inp.cfg,
        parsing=inp.parsing,
        chunking=inp.chunking,
        behaviour={str(v.version): v.behaviour_package for v in manifest.versions},
    )
    check_integrity(inp, report)
    chunks = load_chunks(inp.root)
    empties = load_empty_sections(inp.root, report)
    unresolved = load_unresolved(inp.root, report)
    for lc in chunks:
        check_chunk(lc, ctx, report)
    check_corpus(chunks, empties, unresolved, ctx, report)
    check_source_coverage(inp, manifest, chunks, empties, report)
    check_leaks(inp, manifest, report)
    check_short_quotes(inp, manifest, report)
    add_warnings(chunks, report)
    report.counts.update(
        {
            "chunks": len(chunks),
            "by_version": dict(sorted(Counter(lc.chunk.version for lc in chunks).items())),
            "by_source_kind": dict(sorted(Counter(lc.chunk.source_kind for lc in chunks).items())),
            "empty_sections": len(empties),
            "unresolved": len(unresolved),
            "version_status_resolved": sum(lc.chunk.version_status == "resolved" for lc in chunks),
        }
    )
    return report
