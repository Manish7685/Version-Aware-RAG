"""Whole-corpus chunk outputs, chunk manifest, report and validation (RAG-M3 S4c).

Design: ``docs/design/RAG-M3_S4_chunking.md`` §13a. Outputs:

- ``data/build/chunks/<version>.jsonl`` (5 files) and ``data/build/chunks/empty_sections.jsonl``:
  git-ignored, text-bearing, one record per line (sorted keys, UTF-8, LF, final newline),
  ordered by source path and then position in the page.
- ``data/manifest/chunk_manifest.json`` (committed): every output file's record count, size and
  sha256, the inputs' hashes and summary counts. No content.
- ``docs/reports/RAG-M3_S4_chunking.md`` (committed): statistics, the oversized-chunk and
  empty-section lists and samples. Release-post text, including headings, is withheld.

Not here (S5): ``unresolved.jsonl``, the build report, ``vrag corpus validate``, release notes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from version_aware_rag.corpus.chunk import (
    Chunk,
    ChunkedPage,
    approx_tokens,
    identity_key,
    make_chunk_id,
)
from version_aware_rag.corpus.config import APPROVED_VERSIONS, ParsingConfig
from version_aware_rag.corpus.inspect import WITHHELD
from version_aware_rag.corpus.manifest import CorpusManifest
from version_aware_rag.corpus.parse import ParsedDocument, SourceKind, parse_page
from version_aware_rag.corpus.sections import EmptySection, SectionTree

CHUNK_DIR = "data/build/chunks"
CHUNK_MANIFEST = "data/manifest/chunk_manifest.json"
CHUNK_REPORT = "docs/reports/RAG-M3_S4_chunking.md"
EMPTY_FILE = "empty_sections.jsonl"
VERSIONS = tuple(APPROVED_VERSIONS)
CHUNK_FILES = (*(f"{v}.jsonl" for v in VERSIONS), EMPTY_FILE)
# Files in the same directory that another stage writes and validates (S4-D11: the S4d
# release-note chunks). Not part of the S4c outputs; not reported as unexpected.
SIBLING_FILES = ("github_releases.jsonl",)
MANIFEST_SCHEMA = 1
EXCERPT_CHARS = 600

Pages = Sequence[tuple[ParsedDocument, SectionTree, ChunkedPage]]


@dataclass(frozen=True)
class BuildContext:
    source_commit: str
    parser_version: str
    chunker_version: str
    chunking_config_sha256: str
    corpus_manifest_sha256: str  # sha256 of the main corpus_manifest.json bytes


def parse_corpus_page(
    manifest: CorpusManifest, cache: Path, parsing: ParsingConfig, path: str
) -> ParsedDocument | None:
    """Parse one docs page or release post exactly as ``inspect_corpus`` does (hash-checked).
    Returns None if the path is not in the manifest."""
    rec = next((f for f in manifest.files if f.path == path), None)
    post = next((p for p in manifest.release_posts if p.path == path), None)
    kind: SourceKind
    if rec is not None:
        version, sha, kind, url = rec.version, rec.sha256, "docs_page", None
    elif post is not None:
        version, sha, kind, url = post.version, post.sha256, "release_post", post.canonical_url
    else:
        return None
    raw = (cache / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError(f"{path}: cached file differs from the manifest; run verify")
    return parse_page(
        raw=raw,
        version=version,
        path=path,
        source_sha256=sha,
        site_base=parsing.site_base,
        excluded=parsing.exclusions(),
        source_kind=kind,
        url=url,
    )


# ---------------------------------------------------------------- files


def _line(data: dict[str, object]) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def _jsonl(lines: Iterable[str]) -> bytes:
    text = "\n".join(lines)
    return (text + "\n" if text else "").encode("utf-8")


def ordered_chunks(pages: Pages) -> dict[str, list[Chunk]]:
    """Chunks per version file, ordered by source path then position in the page."""
    by_version: dict[str, list[tuple[str, int, int, Chunk]]] = {v: [] for v in VERSIONS}
    for page_no, (_, _, page) in enumerate(pages):
        for pos, c in enumerate(page.chunks):
            by_version[c.version].append((c.source_path, page_no, pos, c))
    return {v: [c for *_, c in sorted(rows, key=lambda r: r[:3])] for v, rows in by_version.items()}


def ordered_empty(pages: Pages) -> list[EmptySection]:
    rows = [e for _, _, page in pages for e in page.empty_sections]
    return sorted(rows, key=lambda e: (e.version, e.source_path, e.line_start))


def render_chunk_files(pages: Pages) -> dict[str, bytes]:
    files = {
        f"{v}.jsonl": _jsonl(_line(c.model_dump(mode="json")) for c in chunks)
        for v, chunks in ordered_chunks(pages).items()
    }
    files[EMPTY_FILE] = _jsonl(_line(e.model_dump(mode="json")) for e in ordered_empty(pages))
    return files


# ---------------------------------------------------------------- manifest


def _percentiles(values: list[int]) -> dict[str, int]:
    s = sorted(values)
    pick = {
        "min": 0.0,
        "p10": 0.1,
        "p25": 0.25,
        "median": 0.5,
        "p75": 0.75,
        "p90": 0.9,
        "p99": 0.99,
        "max": 1.0,
    }
    return {k: s[int(q * (len(s) - 1))] for k, q in pick.items()} if s else {}


def corpus_counts(pages: Pages) -> dict[str, object]:
    chunks = [c for _, _, page in pages for c in page.chunks]
    empties = [e for _, _, page in pages for e in page.empty_sections]
    merged = [c for c in chunks if c.unit_kind == "prose_merged"]
    split = Counter((c.source_path, c.unit_ordinal) for c in chunks if c.part_count > 1)
    return {
        "pages": len(pages),
        "chunks": len(chunks),
        "by_version": dict(sorted(Counter(c.version for c in chunks).items())),
        "by_source_kind": dict(sorted(Counter(c.source_kind for c in chunks).items())),
        "by_unit_kind": dict(sorted(Counter(c.unit_kind for c in chunks).items())),
        "api_units": len({(c.source_path, c.unit_ordinal) for c in chunks if c.api_signature}),
        "merged_chunks": len(merged),
        "merged_sections": sum(len(c.merged_section_paths) for c in merged),
        "split_units": len(split),
        "split_parts": sum(split.values()),
        "overlap_chunks": sum(1 for c in chunks if c.overlap_prev_blocks),
        "table_row_group_chunks": sum(1 for c in chunks if c.split_inside_block and c.has_table),
        "list_item_group_chunks": sum(
            1 for c in chunks if c.split_inside_block and not c.has_table
        ),
        "oversize_by_reason": dict(
            sorted(Counter(str(c.oversize_reason) for c in chunks if c.oversize).items())
        ),
        "empty_sections_by_reason": dict(sorted(Counter(e.reason for e in empties).items())),
        "railroad_records": {
            "on_chunks": sum(len(c.railroad_removal_lines) for c in chunks),
            "on_empty_sections": sum(len(e.railroad_removal_lines) for e in empties),
        },
        "not_redistributable_chunks": sum(1 for c in chunks if not c.redistributable),
        "token_percentiles": _percentiles([c.token_count_approx for c in chunks]),
    }


def render_chunk_manifest(pages: Pages, files: dict[str, bytes], ctx: BuildContext) -> bytes:
    records = {name: data.count(b"\n") for name, data in files.items()}
    body = {
        "schema_version": MANIFEST_SCHEMA,
        "generator": "vrag corpus chunk (RAG-M3 S4c)",
        "library": "duckdb",
        "source_commit": ctx.source_commit,
        "parser_version": ctx.parser_version,
        "chunker_version": ctx.chunker_version,
        "chunking_config_sha256": ctx.chunking_config_sha256,
        "corpus_manifest_sha256": ctx.corpus_manifest_sha256,
        "output_dir": CHUNK_DIR,
        "files": [
            {
                "path": name,
                "records": records[name],
                "bytes": len(files[name]),
                "sha256": hashlib.sha256(files[name]).hexdigest(),
            }
            for name in CHUNK_FILES
        ],
        "counts": corpus_counts(pages),
    }
    return (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------- validation


def chunk_file_problems(files: dict[str, bytes], manifest: dict[str, object]) -> list[str]:
    """Validate generated chunk files against the chunk manifest and the chunk rules."""
    problems: list[str] = []
    raw_files = manifest.get("files")
    entries = (
        {str(e["path"]): e for e in raw_files if isinstance(e, dict)}
        if isinstance(raw_files, list)
        else {}
    )
    for name in sorted(set(CHUNK_FILES) - set(files)):
        problems.append(f"{name}: missing")
    for name in sorted(set(files) - set(CHUNK_FILES) - set(SIBLING_FILES)):
        problems.append(f"{name}: unexpected file in {CHUNK_DIR}")
    seen: dict[str, str] = {}
    by_version: Counter[str] = Counter()
    for name in CHUNK_FILES:
        data = files.get(name)
        entry = entries.get(name)
        if data is None:
            continue
        if entry is None:
            problems.append(f"{name}: not in the chunk manifest")
        else:
            if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                problems.append(f"{name}: sha256 differs from the chunk manifest")
            if data.count(b"\n") != entry.get("records") or len(data) != entry.get("bytes"):
                problems.append(f"{name}: record count or size differs from the chunk manifest")
        lines = data.decode("utf-8", "replace").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        if name == EMPTY_FILE:
            for n, line in enumerate(lines, start=1):
                try:
                    EmptySection.model_validate_json(line)
                except ValidationError as exc:
                    problems.append(
                        f"{name}:{n}: invalid empty-section record ({exc.error_count()})"
                    )
            continue
        version = name.removesuffix(".jsonl")
        last_path = ""
        for n, line in enumerate(lines, start=1):
            tag = f"{name}:{n}"
            try:
                c = Chunk.model_validate_json(line)
            except ValidationError as exc:
                problems.append(f"{tag}: invalid chunk ({exc.error_count()} errors)")
                continue
            by_version[c.version] += 1
            if c.version != version:
                problems.append(f"{tag}: version {c.version} in the {version} file")
            if c.source_path < last_path:
                problems.append(f"{tag}: not ordered by source path")
            last_path = c.source_path
            if c.content_hash != hashlib.sha256(c.content.encode("utf-8")).hexdigest():
                problems.append(f"{tag}: content_hash does not match content")
            if c.token_count_approx != approx_tokens(c.content) or c.char_count != len(c.content):
                problems.append(f"{tag}: size fields do not match content")
            if c.chunk_id != make_chunk_id(identity_key(c)):
                problems.append(f"{tag}: chunk_id does not match its identity key")
            if c.chunk_id in seen:
                problems.append(
                    f"{tag}: duplicate chunk_id {c.chunk_id} (first at {seen[c.chunk_id]})"
                )
            seen.setdefault(c.chunk_id, tag)
            if c.token_count_approx > 512 and not c.oversize:
                problems.append(f"{tag}: over 512 tokens without oversize")
            if not c.redistributable and c.licence is not None:
                problems.append(f"{tag}: non-redistributable chunk with a licence")
            if c.source_kind == "release_post" and (
                c.redistributable or c.licence_applicability != "uncertain"
            ):
                problems.append(f"{tag}: release-post licence fields changed")
    counts = manifest.get("counts")
    if isinstance(counts, dict) and dict(sorted(by_version.items())) != counts.get("by_version"):
        problems.append("chunk counts per version differ from the chunk manifest")
    return problems


# ---------------------------------------------------------------- report


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = (str(c).replace("|", "\\|").replace("\n", " ") for c in row)
        out.append("| " + " | ".join(cells) + " |")
    return out


def _path_text(c_or_e: Chunk | EmptySection, path: Sequence[str]) -> str:
    """Section paths of release posts are post text: withheld (licence, S3b)."""
    if c_or_e.source_kind == "release_post":
        return WITHHELD
    return " > ".join(path)


def _sample(title: str, c: Chunk) -> list[str]:
    restricted = not c.redistributable
    text = WITHHELD if restricted else c.content
    if len(text) > EXCERPT_CHARS:
        text = text[:EXCERPT_CHARS] + " […]"
    meta: list[list[object]] = [
        ["chunk_id", f"`{c.chunk_id}`"],
        ["source", f"`{c.source_path}` ({c.source_kind}, version {c.version})"],
        ["section_path", _path_text(c, c.section_path)],
        ["unit_kind / part", f"{c.unit_kind}, part {c.part_ordinal} of {c.part_count}"],
        ["lines", f"{c.line_start}-{c.line_end}"],
        ["tokens", c.token_count_approx],
        [
            "flags",
            ", ".join(
                k
                for k in (
                    "oversize",
                    "split_inside_block",
                    "table_header_repeated",
                    "has_code",
                    "has_table",
                    "has_liquid_branches",
                )
                if getattr(c, k)
            )
            or "none",
        ],
        ["overlap", f"{c.overlap_prev_blocks} blocks, {c.overlap_prev_tokens} tokens"],
        ["licence", f"{c.licence} / {c.licence_status} / redistributable={c.redistributable}"],
    ]
    if c.merged_section_paths:
        merged = "; ".join(" > ".join(p) for p in c.merged_section_paths)
        meta.append(["merged_section_paths", WITHHELD if restricted else merged])
    return [
        "",
        f"### {title}",
        "",
        *_table(["field", "value"], meta),
        "",
        "````text",
        *[f"  {ln}".rstrip() for ln in text.split("\n")],
        "````",
    ]


def _samples(chunks: list[Chunk]) -> list[str]:
    def first(pred: object) -> Chunk | None:
        return next((c for c in chunks if pred(c)), None)  # type: ignore[operator]

    picks = [
        (
            "API entry (Markdown function heading)",
            first(
                lambda c: (
                    c.source_path == "docs/2.0/sql/functions/map.md" and c.unit_kind == "api_entry"
                )
            ),
        ),
        (
            "Merged short sections (R1)",
            first(
                lambda c: c.unit_kind == "prose_merged" and c.source_path.startswith("docs/1.2/")
            ),
        ),
        (
            "Table row group with the header repeated",
            first(
                lambda c: (
                    c.source_path == "docs/1.3/core_extensions/encodings.md"
                    and c.table_header_repeated
                )
            ),
        ),
        (
            "Split part with whole-block overlap",
            first(lambda c: c.overlap_prev_blocks and c.source_kind == "docs_page"),
        ),
        ("Oversized single list item", first(lambda c: c.oversize_reason == "list_item")),
        (
            "Python API entry (Sphinx)",
            first(
                lambda c: (
                    c.source_path == "docs/2.0/clients/python/reference/index.md"
                    and c.api_reason == "sphinx_dt"
                    and not c.oversize
                )
            ),
        ),
        ("Release post (structure only)", first(lambda c: c.source_kind == "release_post")),
    ]
    out: list[str] = []
    for title, c in picks:
        if c is None:
            out += ["", f"### {title}", "", "**No such chunk.**"]
        else:
            out += _sample(title, c)
    return out


def render_chunk_report(pages: Pages, files: dict[str, bytes], ctx: BuildContext) -> str:
    chunks = [c for v in ordered_chunks(pages).values() for c in v]
    empties = ordered_empty(pages)
    counts = corpus_counts(pages)
    lines = [
        "# RAG-M3 S4 chunking report (generated)",
        "",
        "Generated by `vrag corpus chunk` (RAG-M3 S4c). Release-post text, including headings and "
        "section paths, is withheld because its licence applicability is uncertain "
        "(`redistributable=false`). GitHub release notes are not processed yet (S4d). In content "
        "excerpts, trailing spaces are trimmed for display only.",
        "",
        "## 1. Inputs and outputs",
        "",
        *_table(
            ["input", "value"],
            [
                ["pinned commit", f"`{ctx.source_commit}`"],
                ["parser version", f"`{ctx.parser_version}`"],
                ["chunker version", f"`{ctx.chunker_version}`"],
                ["chunking config sha256", f"`{ctx.chunking_config_sha256}`"],
                ["corpus manifest sha256", f"`{ctx.corpus_manifest_sha256}`"],
            ],
        ),
        "",
        f"Outputs in `{CHUNK_DIR}/` (git-ignored; hashes also in `{CHUNK_MANIFEST}`):",
        "",
        *_table(
            ["file", "records", "bytes", "sha256"],
            [
                [
                    n,
                    files[n].count(b"\n"),
                    len(files[n]),
                    f"`{hashlib.sha256(files[n]).hexdigest()}`",
                ]
                for n in CHUNK_FILES
            ],
        ),
        "",
        "## 2. Counts",
        "",
    ]
    kinds = sorted({c.source_kind for c in chunks})
    per: Counter[tuple[str, str]] = Counter((str(c.version), str(c.source_kind)) for c in chunks)
    lines += _table(
        ["version", *kinds, "total"],
        [[v, *(per[(v, k)] for k in kinds), sum(per[(v, k)] for k in kinds)] for v in VERSIONS]
        + [["all", *(sum(per[(v, k)] for v in VERSIONS) for k in kinds), len(chunks)]],
    )
    lines += [
        "",
        f"Pages: {counts['pages']}. Unit kinds: {counts['by_unit_kind']}. API entries: "
        f"{counts['api_units']}. Merged chunks: {counts['merged_chunks']} "
        f"({counts['merged_sections']} sections). Split units: {counts['split_units']} "
        f"({counts['split_parts']} parts). Table row-group chunks: "
        f"{counts['table_row_group_chunks']}; list item-group chunks: "
        f"{counts['list_item_group_chunks']}. Chunks with overlap: {counts['overlap_chunks']}. "
        f"Not redistributable: {counts['not_redistributable_chunks']}.",
        "",
        "## 3. Size distribution (approximate tokens)",
        "",
    ]
    pct = counts["token_percentiles"]
    if not isinstance(pct, dict):
        raise TypeError("token_percentiles must be a mapping")
    lines += _table(list(pct), [list(pct.values())])
    edges = [0, 32, 64, 128, 256, 350, 512, 513, 10**9]
    pairs = list(itertools.pairwise(edges))
    labels = [f"{a}-{b - 1}" if b - a > 1 else f"{a}" for a, b in pairs]
    labels[-1] = ">512"
    hist = Counter(
        next(
            lab for lab, (a, b) in zip(labels, pairs, strict=True) if a <= c.token_count_approx < b
        )
        for c in chunks
    )
    lines += ["", *_table(labels, [[hist[lab] for lab in labels]])]
    oversize = [c for c in chunks if c.oversize]
    lines += [
        "",
        f"## 4. Oversized chunks ({len(oversize)})",
        "",
        "Kept whole and flagged (owner Q3 and S4-D3); never truncated. "
        f"By reason: {counts['oversize_by_reason']}.",
        "",
        *_table(
            ["version", "source", "lines", "tokens", "reason", "section path"],
            [
                [
                    c.version,
                    f"`{c.source_path}`",
                    f"{c.line_start}-{c.line_end}",
                    c.token_count_approx,
                    c.oversize_reason,
                    _path_text(c, c.section_path),
                ]
                for c in oversize
            ],
        ),
        "",
        f"## 5. Empty sections ({len(empties)})",
        "",
        "No chunk (S4-D2); recorded with their reason. The railroad records are S3's "
        f"`html_railroad_removed` lines (S4-D9). By reason: {counts['empty_sections_by_reason']}. "
        f"Railroad records: {counts['railroad_records']}.",
        "",
        *_table(
            ["version", "source", "lines", "reason", "railroad lines", "section path"],
            [
                [
                    e.version,
                    f"`{e.source_path}`",
                    f"{e.line_start}-{e.line_end}",
                    e.reason,
                    ", ".join(map(str, e.railroad_removal_lines)) or "-",
                    _path_text(e, e.section_path),
                ]
                for e in empties
            ],
        ),
        "",
        "## 6. Licence classes",
        "",
        *_table(
            [
                "source kind",
                "licence",
                "licence status",
                "applicability",
                "redistributable",
                "chunks",
            ],
            [
                [*k, n]
                for k, n in sorted(
                    Counter(
                        (
                            c.source_kind,
                            str(c.licence),
                            c.licence_status,
                            str(c.licence_applicability),
                            c.redistributable,
                        )
                        for c in chunks
                    ).items(),
                    key=str,
                )
            ],
        ),
        "",
        "## 7. Samples",
        "",
        "Each sample shows the chunk's metadata and the first "
        f"{EXCERPT_CHARS} characters of its content.",
        *_samples(chunks),
    ]
    return "\n".join(lines) + "\n"
