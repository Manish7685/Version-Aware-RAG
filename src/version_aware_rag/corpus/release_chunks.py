"""GitHub release-note chunks (RAG-M3 S4d).

Design: ``docs/design/RAG-M3_S4_chunking.md`` §11 (owner decisions S4-D5, S4-D10, S4-D11, S4-D12).

1. Verify before anything is read for processing: for every approved release, the cached body's
   sha256 and size against the S2 config, the manifest record (id, hash, size, commit) against the
   config, and the S2 fetch record (id, hash) against the manifest. Any problem stops the run.
2. Parse each body through S3's Markdown path (``source_kind = github_release``). A body that
   triggers a Liquid transformation, or is not a content page, stops the run.
3. Q7 retrieval cleanup, four rules, each with its own transformation code, applied outside code
   blocks and inline code spans, in order; every occurrence is recorded. The raw cache file is
   never modified.
4. Chunk with the approved S4 algorithm. Licence ``null`` / ``unverified`` / ``uncertain`` /
   ``redistributable = false``; ``copyright = null`` (none recorded).
5. Outputs: ``data/build/chunks/github_releases.jsonl`` (git-ignored, text-bearing), and a committed
   manifest and report that carry no release-note text and no contributor names.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from version_aware_rag.corpus.chunk import (
    Chunk,
    ChunkedPage,
    PageSource,
    approx_tokens,
    check_unique,
    chunk_page,
    identity_key,
    make_chunk_id,
)
from version_aware_rag.corpus.chunk_output import BuildContext
from version_aware_rag.corpus.config import ChunkingConfig, ParsingConfig, ReleaseNotesConfig
from version_aware_rag.corpus.inspect import WITHHELD
from version_aware_rag.corpus.manifest import ReleaseNotesSection, ReleaseRecord
from version_aware_rag.corpus.parse import Block, Issue, ParsedDocument, parse_page
from version_aware_rag.corpus.releases import body_path, fetch_record_path, verify_releases
from version_aware_rag.corpus.sections import SectionTree, build_tree

RELEASE_DIR = "data/build/chunks"
RELEASE_FILE = "github_releases.jsonl"
RELEASE_MANIFEST = "data/manifest/release_note_chunk_manifest.json"
RELEASE_REPORT = "docs/reports/RAG-M3_S4d_release_notes.md"
MANIFEST_SCHEMA = 1
REPOSITORY_LICENCE = "MIT"  # duckdb/duckdb repository licence (plan §7 L2); not the release text's


class ReleaseChunkError(ValueError):
    """Release-note inputs failed verification or cannot be processed; lists every problem."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


# ---------------------------------------------------------------- Q7 rules (S4-D10)


@dataclass(frozen=True)
class Q7Rule:
    code: str
    pattern: re.Pattern[str]
    replacement: str

    def detail(self, match: re.Match[str]) -> str:
        """What was recorded: PR numbers and version ranges only, never a contributor name."""
        if self.code == "q7_contributor_handle_masked":
            return "handle masked"
        if self.code == "q7_compare_url_shortened":
            return f"{match.group(1)}...{match.group(2)}"
        return f"#{match.group(1)}"


_HANDLE = r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_REPO = r"https://github\.com/duckdb/duckdb"
Q7_RULES: tuple[Q7Rule, ...] = (
    # Approved Q7: " by @user in <repo>/pull/<n>" -> " (#<n>)"
    Q7Rule(
        "q7_pr_attribution_shortened",
        re.compile(rf" by {_HANDLE} in {_REPO}/pull/(\d+)(?![\w/])"),
        r" (#\1)",
    ),
    # S4-D10: a bare PR URL of the duckdb/duckdb repository -> "#<n>"
    Q7Rule("q7_pr_url_shortened", re.compile(rf"(?<![\w/]){_REPO}/pull/(\d+)(?![\w/])"), r"#\1"),
    # S4-D10 refinement: a compare URL between two version tags -> the compact range
    Q7Rule(
        "q7_compare_url_shortened",
        re.compile(rf"(?<![\w/]){_REPO}/compare/(v\d+\.\d+\.\d+)\.\.\.(v\d+\.\d+\.\d+)(?![\w/.])"),
        r"\1...\2",
    ),
    # S4-D10: any remaining contributor handle -> "@[contributor]"
    Q7Rule(
        "q7_contributor_handle_masked",
        re.compile(rf"(?<![\w@/.\[]){_HANDLE}(?![\w-])"),
        "@[contributor]",
    ),
)
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")


def apply_q7_line(line: str) -> tuple[str, list[tuple[str, str]]]:
    """Apply the rules in order to one line, outside inline code spans.
    Returns the new line and (code, detail) for every occurrence."""
    applied: list[tuple[str, str]] = []
    out: list[str] = []
    pos = 0
    for span in [*_CODE_SPAN.finditer(line), None]:
        end = span.start() if span else len(line)
        text = line[pos:end]
        for rule in Q7_RULES:
            applied += [(rule.code, rule.detail(m)) for m in rule.pattern.finditer(text)]
            text = rule.pattern.sub(rule.replacement, text)
        out.append(text)
        if span:
            out.append(span.group(0))
            pos = span.end()
    return "".join(out), applied


@dataclass(frozen=True)
class Q7Result:
    doc: ParsedDocument
    line_codes: dict[int, list[set[str]]]  # id(new block) -> per text line, the codes applied
    counts: Counter[str]


def _maps_line_by_line(b: Block, n_lines: int, raw_lines: list[str] | None) -> bool:
    """True when the block's text lines are its first source lines, one to one: those raw
    lines are non-blank and the rest of its range is blank (a trailing blank line)."""
    span = b.line_end - b.line_start + 1
    if raw_lines is None or n_lines > span:
        return n_lines == span
    own = raw_lines[b.line_start - 1 : b.line_start - 1 + span]
    return (
        len(own) == span
        and all(x.strip() for x in own[:n_lines])
        and not any(x.strip() for x in own[n_lines:])
    )


def apply_q7(doc: ParsedDocument, raw_text: str | None = None) -> Q7Result:
    """Q7 on every non-code block; records each occurrence as a transformation, at its source
    line when the block maps line by line (given the raw body), otherwise at the block start."""
    raw_lines = raw_text.replace("\r\n", "\n").split("\n") if raw_text is not None else None
    blocks: list[Block] = []
    issues: list[Issue] = []
    line_codes: dict[int, list[set[str]]] = {}
    counts: Counter[str] = Counter()
    for b in doc.blocks:
        lines = b.text.split("\n")
        if b.kind == "code":
            blocks.append(b)
            line_codes[id(b)] = [set() for _ in lines]
            continue
        exact = _maps_line_by_line(b, len(lines), raw_lines)
        new_lines: list[str] = []
        codes: list[set[str]] = []
        for i, line in enumerate(lines):
            new, applied = apply_q7_line(line)
            new_lines.append(new)
            codes.append({c for c, _ in applied})
            for code, detail in applied:
                counts[code] += 1
                issues.append(
                    Issue(
                        code=code, line=b.line_start + i if exact else b.line_start, detail=detail
                    )
                )
        nb = b.model_copy(update={"text": "\n".join(new_lines)}) if new_lines != lines else b
        blocks.append(nb)
        line_codes[id(nb)] = codes
    new_doc = doc.model_copy(
        update={"blocks": tuple(blocks), "transformations": (*doc.transformations, *issues)}
    )
    return Q7Result(new_doc, line_codes, counts)


def _segment_codes(page: ChunkedPage, q7: Q7Result, segs_index: int) -> tuple[str, ...]:
    """The Q7 codes applied to the text of one chunk (overlap included)."""
    heading_codes = {
        b.line_start: set().union(*q7.line_codes.get(id(b), [set()]))
        for b in q7.doc.blocks
        if b.kind == "heading"
    }
    codes: set[str] = set()
    for s in page.segments[segs_index]:
        if s.block is None:
            codes |= heading_codes.get(s.line_start, set())
            continue
        per_line = q7.line_codes.get(id(s.block), [])
        if s.role == "block":
            for c in per_line:
                codes |= c
            continue
        # A row or item group: find its lines in the block (Q7 never changes line counts).
        block_lines = s.block.text.split("\n")
        body = "\n".join(s.body).split("\n")
        for start in range(len(block_lines) - len(body) + 1):
            if block_lines[start : start + len(body)] == body:
                for c in per_line[start : start + len(body)]:
                    codes |= c
                break
        for c in per_line[: len(s.header)]:
            codes |= c
    return tuple(sorted(codes))


# ---------------------------------------------------------------- verification and provenance


def verify_release_inputs(
    notes: ReleaseNotesConfig, section: ReleaseNotesSection, cache_dir: Path
) -> list[str]:
    """Every problem with the 4 approved release-note inputs (S4-D5 item 2; T35)."""
    problems: list[str] = []
    report = verify_releases(notes, cache_dir)
    problems += [
        f"{c.tag}: cached body {c.status}" for c in report.checks if c.status != "verified"
    ]
    records = {r.tag: r for r in section.included}
    for rel in notes.included:
        rec = records.get(rel.tag)
        if rec is None:
            problems.append(f"{rel.tag}: no record in the corpus manifest")
            continue
        for field_name in ("release_id", "body_sha256", "body_bytes", "tag_commit"):
            if getattr(rec, field_name) != getattr(rel, field_name):
                problems.append(f"{rel.tag}: manifest {field_name} differs from the config")
        fetch = fetch_record_path(cache_dir, rel.tag)
        if not fetch.is_file():
            problems.append(f"{rel.tag}: S2 fetch record missing")
            continue
        try:
            data = json.loads(fetch.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append(f"{rel.tag}: S2 fetch record unreadable")
            continue
        if data.get("release_id") != rec.release_id or data.get("body_sha256") != rec.body_sha256:
            problems.append(f"{rel.tag}: S2 fetch record id or hash differs from the manifest")
    if set(records) != {r.tag for r in notes.included}:
        problems.append("manifest release records differ from the approved releases")
    return problems


def release_source(
    rec: ReleaseRecord, section: ReleaseNotesSection, behaviour: Mapping[str, str]
) -> PageSource:
    return PageSource(
        version=rec.version,
        behaviour_package=behaviour[rec.version],
        source_type="changelog",
        source_kind="github_release",
        introduced_in=rec.introduced_in,
        source_path=f"releases/tag/{rec.tag}",
        source_url=rec.source_url,
        source_repo=section.repo,
        source_commit=rec.tag_commit,
        source_git_blob=None,
        source_content_hash=rec.body_sha256,
        release_id=rec.release_id,
        source_published_at=rec.published_at,
        source_retrieved_at=rec.first_retrieved_at,
        licence=None,
        licence_status=rec.licence_status,
        licence_applicability="uncertain",
        redistributable=False,
        repository_licence=REPOSITORY_LICENCE,
        licence_basis=section.licence_note,
        copyright=None,  # S4-D12: none recorded; never invented
        attribution=rec.attribution,
        copyright_date_discrepancy=None,
    )


@dataclass(frozen=True)
class ReleaseNotePage:
    record: ReleaseRecord
    doc: ParsedDocument  # after Q7
    tree: SectionTree
    page: ChunkedPage
    q7_counts: Counter[str]


def chunk_release_notes(
    notes: ReleaseNotesConfig,
    section: ReleaseNotesSection,
    behaviour: Mapping[str, str],
    cache_dir: Path,
    parsing: ParsingConfig,
    chunking: ChunkingConfig,
    chunking_sha256: str,
) -> list[ReleaseNotePage]:
    problems = verify_release_inputs(notes, section, cache_dir)
    if problems:
        raise ReleaseChunkError(problems)
    out: list[ReleaseNotePage] = []
    for rec in sorted(section.included, key=lambda r: r.tag):
        raw = body_path(cache_dir, rec.tag).read_bytes()
        doc = parse_page(
            raw=raw,
            version=rec.version,
            path=f"releases/tag/{rec.tag}",
            source_sha256=rec.body_sha256,
            site_base=parsing.site_base,
            source_kind="github_release",
            url=rec.source_url,
        )
        liquid = sorted({t.code for t in doc.transformations if t.code.startswith("liquid")})
        if liquid or not doc.include_in_corpus or doc.page_kind != "content":
            raise ReleaseChunkError(
                [f"{rec.tag}: unexpected parse result ({liquid or doc.page_kind})"]
            )
        q7 = apply_q7(doc, raw.decode("utf-8"))
        tree = build_tree(q7.doc, chunking)
        page = chunk_page(
            q7.doc, tree, release_source(rec, section, behaviour), chunking, chunking_sha256
        )
        chunks = tuple(
            c.model_copy(update={"transformations": _segment_codes(page, q7, i)})
            for i, c in enumerate(page.chunks)
        )
        page = ChunkedPage(chunks, page.segments, page.empty_sections)
        out.append(ReleaseNotePage(rec, q7.doc, tree, page, q7.counts))
    check_unique(c for r in out for c in r.page.chunks)
    for rec in section.included:  # the raw bodies are unchanged by processing
        raw = body_path(cache_dir, rec.tag).read_bytes()
        if hashlib.sha256(raw).hexdigest() != rec.body_sha256:
            raise ReleaseChunkError([f"{rec.tag}: cached body changed during processing"])
    return out


# ---------------------------------------------------------------- outputs


def render_release_file(pages: Sequence[ReleaseNotePage]) -> bytes:
    lines = [
        json.dumps(c.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        for r in pages
        for c in r.page.chunks
    ]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def release_counts(pages: Sequence[ReleaseNotePage]) -> dict[str, object]:
    chunks = [c for r in pages for c in r.page.chunks]
    q7_total: Counter[str] = Counter()
    for r in pages:
        q7_total.update(r.q7_counts)
    return {
        "releases": len(pages),
        "chunks": len(chunks),
        "by_version": dict(sorted(Counter(c.version for c in chunks).items())),
        "by_unit_kind": dict(sorted(Counter(c.unit_kind for c in chunks).items())),
        "split_inside_block_chunks": sum(c.split_inside_block for c in chunks),
        "oversize_by_reason": dict(
            sorted(Counter(str(c.oversize_reason) for c in chunks if c.oversize).items())
        ),
        "empty_sections": sum(len(r.page.empty_sections) for r in pages),
        "q7_transformations": dict(sorted(q7_total.items())),
        "chunks_with_q7": sum(1 for c in chunks if c.transformations),
        "not_redistributable_chunks": sum(1 for c in chunks if not c.redistributable),
    }


def render_release_manifest(
    pages: Sequence[ReleaseNotePage], data: bytes, ctx: BuildContext
) -> bytes:
    body = {
        "schema_version": MANIFEST_SCHEMA,
        "generator": "vrag corpus releases chunk (RAG-M3 S4d)",
        "library": "duckdb",
        "parser_version": ctx.parser_version,
        "chunker_version": ctx.chunker_version,
        "chunking_config_sha256": ctx.chunking_config_sha256,
        "corpus_manifest_sha256": ctx.corpus_manifest_sha256,
        "output": {
            "path": f"{RELEASE_DIR}/{RELEASE_FILE}",
            "records": data.count(b"\n"),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "releases": [
            {
                "tag": r.record.tag,
                "release_id": r.record.release_id,
                "version": r.record.version,
                "introduced_in": r.record.introduced_in,
                "body_sha256": r.record.body_sha256,
                "body_bytes": r.record.body_bytes,
                "verification": "verified",
                "chunks": len(r.page.chunks),
                "q7_transformations": dict(sorted(r.q7_counts.items())),
            }
            for r in pages
        ],
        "counts": release_counts(pages),
    }
    return (json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def release_file_problems(data: bytes, manifest: dict[str, object]) -> list[str]:
    """Validate the release-note chunk file against its manifest and the S4d rules."""
    problems: list[str] = []
    out = manifest.get("output")
    if not isinstance(out, dict):
        return ["release-note chunk manifest has no output entry"]
    if hashlib.sha256(data).hexdigest() != out.get("sha256"):
        problems.append(f"{RELEASE_FILE}: sha256 differs from the manifest")
    if data.count(b"\n") != out.get("records") or len(data) != out.get("bytes"):
        problems.append(f"{RELEASE_FILE}: record count or size differs from the manifest")
    seen: set[str] = set()
    for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), start=1):
        tag = f"{RELEASE_FILE}:{n}"
        try:
            c = Chunk.model_validate_json(line)
        except ValidationError as exc:
            problems.append(f"{tag}: invalid chunk ({exc.error_count()} errors)")
            continue
        if c.source_kind != "github_release" or c.source_type != "changelog":
            problems.append(f"{tag}: not a GitHub release-note chunk")
        if (c.licence, c.licence_status, c.licence_applicability, c.redistributable) != (
            None,
            "unverified",
            "uncertain",
            False,
        ) or c.copyright is not None:
            problems.append(f"{tag}: licence fields differ from S4-D5 / S4-D12")
        if c.content_hash != hashlib.sha256(c.content.encode("utf-8")).hexdigest():
            problems.append(f"{tag}: content_hash does not match content")
        if c.token_count_approx != approx_tokens(c.content):
            problems.append(f"{tag}: token count does not match content")
        if c.chunk_id != make_chunk_id(identity_key(c)) or c.chunk_id in seen:
            problems.append(f"{tag}: chunk_id wrong or duplicated")
        seen.add(c.chunk_id)
        if any(rule.pattern.search(c.content) for rule in Q7_RULES[:3]):
            problems.append(f"{tag}: a PR attribution or GitHub URL survived Q7")
    return problems


def _table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return out


def render_release_report(pages: Sequence[ReleaseNotePage], data: bytes, ctx: BuildContext) -> str:
    counts = release_counts(pages)
    codes = [rule.code for rule in Q7_RULES]
    lines = [
        "# RAG-M3 S4d release-note chunks (generated)",
        "",
        "Generated by `vrag corpus releases chunk` (RAG-M3 S4d). GitHub release text has no "
        "established licence (owner Q8): **no release-note text, heading or contributor name "
        f"appears in this report**; text-bearing output stays in `{RELEASE_DIR}/{RELEASE_FILE}` "
        f"(git-ignored). Section paths below that would quote release text show {WITHHELD}.",
        "",
        "## 1. Inputs verified before processing",
        "",
        "Each cached body's sha256 and size against the S2 config; the manifest record's id, "
        "hash, size and commit against the config; the S2 fetch record's id and hash against the "
        "manifest. The bodies were re-hashed after processing and are unchanged.",
        "",
        *_table(
            ["tag", "release id", "body sha256", "bytes", "version", "introduced_in", "status"],
            [
                [
                    r.record.tag,
                    r.record.release_id,
                    f"`{r.record.body_sha256}`",
                    r.record.body_bytes,
                    r.record.version,
                    r.record.introduced_in,
                    "verified",
                ]
                for r in pages
            ],
        ),
        "",
        "## 2. Output",
        "",
        *_table(
            ["file", "records", "bytes", "sha256"],
            [
                [
                    f"`{RELEASE_DIR}/{RELEASE_FILE}`",
                    data.count(b"\n"),
                    len(data),
                    f"`{hashlib.sha256(data).hexdigest()}`",
                ]
            ],
        ),
        "",
        f"Parser `{ctx.parser_version}`, chunker `{ctx.chunker_version}`, chunking config "
        f"`{ctx.chunking_config_sha256}`, corpus manifest `{ctx.corpus_manifest_sha256}`.",
        "",
        "## 3. Q7 retrieval cleanup (S4-D10)",
        "",
        "Applied to chunk text only, outside code blocks and inline code spans, in this order; "
        "every occurrence is recorded. `q7_pr_attribution_shortened`: ` by @user in <PR URL>` "
        "becomes ` (#n)`. `q7_pr_url_shortened`: a bare duckdb/duckdb PR URL becomes `#n`. "
        "`q7_compare_url_shortened`: a compare URL between two version tags becomes the compact "
        "range (`vA...vB`). `q7_contributor_handle_masked`: any remaining handle becomes "
        "`@[contributor]`.",
        "",
        *_table(
            ["tag", *codes, "total"],
            [
                [r.record.tag, *(r.q7_counts[c] for c in codes), sum(r.q7_counts.values())]
                for r in pages
            ],
        ),
        "",
        "## 4. Chunks",
        "",
        f"{counts['chunks']} chunks; by unit kind {counts['by_unit_kind']}; split inside a "
        f"block: {counts['split_inside_block_chunks']}; oversize: "
        f"{counts['oversize_by_reason'] or 'none'}; "
        f"empty sections: {counts['empty_sections']}; chunks with a Q7 transformation: "
        f"{counts['chunks_with_q7']}.",
        "",
        *_table(
            ["chunk_id", "tag", "part", "unit kind", "lines", "tokens", "flags", "transformations"],
            [
                [
                    f"`{c.chunk_id}`",
                    r.record.tag,
                    f"{c.part_ordinal}/{c.part_count}",
                    c.unit_kind,
                    f"{c.line_start}-{c.line_end}",
                    c.token_count_approx,
                    ", ".join(k for k in ("split_inside_block", "oversize") if getattr(c, k))
                    or "-",
                    ", ".join(c.transformations) or "-",
                ]
                for r in pages
                for c in r.page.chunks
            ],
        ),
        "",
        "## 5. Licence",
        "",
        "Every chunk: `licence = null`, `licence_status = unverified`, "
        "`licence_applicability = uncertain`, `redistributable = false`, `copyright = null` "
        "(no copyright recorded for GitHub release text; S4-D12), `repository_licence = MIT` (the "
        'duckdb/duckdb repository, not the release text). Attribution: "DuckDB release notes, '
        'github.com/duckdb/duckdb, release <tag>".',
    ]
    return "\n".join(lines) + "\n"
