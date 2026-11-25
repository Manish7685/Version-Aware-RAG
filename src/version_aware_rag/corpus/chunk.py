"""Chunker and stable chunk IDs (RAG-M3 S4b).

Consumes S3's ``ParsedDocument`` and S4a's ``SectionTree`` only. Design:
``docs/design/RAG-M3_S4_chunking.md`` §5-§9 (owner decisions S4-D1 to S4-D9).

1. Units: every ``content`` node, in source order (heading line, or first block line).
2. R1 merge: consecutive non-API units under the same parent, each under ``merge_below`` tokens,
   with no other section between them, while the joined content stays within ``target``.
3. A unit within ``hard_limit`` is one chunk. A longer unit is cut into pieces at block
   boundaries; over-limit tables become row groups (header repeated), over-limit lists become
   top-level item groups, over-limit code and Sphinx API blocks stay whole (``oversize``).
4. Pieces are packed greedily (close at ``target``, never over ``hard_limit``); groups and
   oversize pieces are parts of their own. Whole-block overlap (at most ``overlap_ratio`` x
   ``target`` tokens) is added between consecutive ordinary parts of one unit.
5. Chunk IDs: sha256 of a canonical JSON identity key, 20 hex characters (S4-D1).

Block text is never edited. A chunk's content is its segments joined by blank lines; the
segments are kept with each chunk (``ChunkedPage.segments``) so that content preservation can be
checked exactly (``reconstruction_problems``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict

from version_aware_rag.corpus.config import ChunkingConfig
from version_aware_rag.corpus.manifest import CorpusManifest
from version_aware_rag.corpus.parse import Block, ParsedDocument
from version_aware_rag.corpus.sections import (
    RAILROAD_RULE,
    EmptySection,
    SectionNode,
    SectionTree,
    build_tree,
)

TOKEN = re.compile(r"\w+|[^\w\s]")
SITE_MARKER = re.compile(r"\[site:[^\]\s]+\]")
LIST_ITEM = re.compile(r"^(?:[-*+] |\d+[.)] )")
TABLE_DELIMITER = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
LIBRARY = "duckdb"
DOCS_LICENCE_BASIS = (
    "duckdb-web LICENSE (MIT) at the pinned commit. The S1 classification of the versioned "
    "documentation, retained as an approved project assumption; not a new legal determination."
)

SourceType = Literal["documentation", "changelog"]
ChunkSourceKind = Literal["docs_page", "release_post", "migration_guide", "github_release"]
UnitKind = Literal["api_entry", "prose", "prose_merged"]
OversizeReason = Literal["code_block", "table_row", "list_item", "api_entry_block"]
SegmentRole = Literal["heading", "block", "part"]


def approx_tokens(text: str) -> int:
    """Dependency-free deterministic token estimate (plan §4.2): ``\\w+|[^\\w\\s]`` pieces."""
    return len(TOKEN.findall(text))


class ChunkingError(ValueError):
    """A page cannot be chunked under the approved rules; never silently skipped."""


class ChunkIdCollision(ChunkingError):
    """Two different chunks have the same ID."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PageSource(_Model):
    """Provenance and licence of one page, from the corpus manifest."""

    version: str
    behaviour_package: str
    source_type: SourceType
    source_kind: ChunkSourceKind
    introduced_in: str | None
    source_path: str
    source_url: str
    source_repo: str
    source_commit: str
    source_git_blob: str | None
    source_content_hash: str
    release_id: int | None
    source_published_at: str | None
    source_retrieved_at: str | None
    licence: str | None
    licence_status: str
    licence_applicability: str | None
    redistributable: bool
    repository_licence: str
    licence_basis: str
    copyright: str | None  # None when no copyright is recorded (GitHub release notes, S4-D12)
    attribution: str
    copyright_date_discrepancy: bool | None


class Chunk(_Model):
    # identity
    chunk_id: str
    library: Literal["duckdb"]
    version: Literal["1.2", "1.3", "1.4", "1.5", "2.0"]
    behaviour_package: str
    version_status: Literal["resolved"]
    # source
    source_type: SourceType
    source_kind: ChunkSourceKind
    introduced_in: str | None
    source_path: str
    source_url: str
    source_repo: str
    source_commit: str
    source_git_blob: str | None
    source_content_hash: str
    release_id: int | None
    source_published_at: str | None
    source_retrieved_at: str | None
    # structure
    doc_title: str
    section_path: tuple[str, ...]
    section_path_fallback: bool
    heading_level: int
    unit_kind: UnitKind
    merged_section_paths: tuple[tuple[str, ...], ...]
    unit_ordinal: int
    occurrence: int
    part_ordinal: int
    part_count: int
    line_start: int
    line_end: int
    # API
    api_signature: str | None
    api_id: str | None
    api_reason: str | None
    # content
    content: str
    content_hash: str
    token_count_approx: int
    char_count: int
    # split, overlap, oversize
    split_inside_block: bool
    table_header_repeated: bool
    overlap_prev_blocks: int
    overlap_prev_tokens: int
    overlap_line_start: int | None
    overlap_line_end: int | None
    oversize: bool
    oversize_reason: OversizeReason | None
    # audit
    has_code: bool
    has_table: bool
    has_unrendered_site_variable: bool
    has_liquid_branches: bool
    extraction: Literal["markdown", "sphinx_html"]
    transformations: tuple[str, ...]
    railroad_removal_lines: tuple[int, ...]  # S4-D9: each S3 record on exactly one chunk
    # licence
    licence: str | None
    licence_status: str
    licence_applicability: str | None
    redistributable: bool
    repository_licence: str
    licence_basis: str
    copyright: str | None  # None when no copyright is recorded (GitHub release notes, S4-D12)
    attribution: str
    copyright_date_discrepancy: bool | None
    # reproducibility
    parser_version: str
    chunker_version: str
    chunking_config_sha256: str


@dataclass(frozen=True)
class Segment:
    """One piece of a chunk's content, kept for exact preservation checks."""

    text: str
    tokens: int
    role: SegmentRole  # heading line, whole block, or a row/item group of one block
    unit: int  # unit index in source order
    block: Block | None
    line_start: int
    line_end: int
    header: tuple[str, ...] = ()  # table header lines carried by a row group
    body: tuple[str, ...] = ()  # rows or item lines of a group
    header_repeated: bool = False
    overlap: bool = False


@dataclass(frozen=True)
class ChunkedPage:
    chunks: tuple[Chunk, ...]
    segments: tuple[tuple[Segment, ...], ...]  # parallel to chunks
    empty_sections: tuple[EmptySection, ...]


# ---------------------------------------------------------------- provenance


def page_source(doc: ParsedDocument, manifest: CorpusManifest, cfg: ChunkingConfig) -> PageSource:
    """Provenance for one page from the manifest; refuses a page whose hash differs."""
    behaviour = {v.version: v.behaviour_package for v in manifest.versions}
    if doc.source_kind == "release_post":
        posts = [p for p in manifest.release_posts if p.path == doc.path]
        if len(posts) != 1:
            raise ChunkingError(f"{doc.path}: not exactly one release-post record in the manifest")
        p = posts[0]
        if p.sha256 != doc.source_sha256:
            raise ChunkingError(f"{doc.path}: parsed bytes differ from the manifest sha256")
        return PageSource(
            version=p.version,
            behaviour_package=behaviour[p.version],
            source_type="changelog",
            source_kind="release_post",
            introduced_in=p.release,
            source_path=p.path,
            source_url=p.canonical_url,
            source_repo=p.source_repo,
            source_commit=p.source_commit,
            source_git_blob=p.git_blob,
            source_content_hash=p.sha256,
            release_id=None,
            source_published_at=p.published_date,
            source_retrieved_at=None,
            licence=p.licence,
            licence_status=p.licence_status,
            licence_applicability=p.licence_applicability,
            redistributable=p.redistributable,
            repository_licence=p.repository_licence,
            licence_basis=p.licence_basis,
            copyright=p.repository_copyright,
            attribution=p.attribution,
            copyright_date_discrepancy=p.copyright_date_discrepancy,
        )
    files = [f for f in manifest.files if f.path == doc.path and f.version == doc.version]
    if len(files) != 1:
        raise ChunkingError(f"{doc.path}: not exactly one file record in the manifest")
    f = files[0]
    if f.sha256 != doc.source_sha256:
        raise ChunkingError(f"{doc.path}: parsed bytes differ from the manifest sha256")
    src = manifest.source
    guide = next((g for g in cfg.migration_guides if g.path == doc.path), None)
    return PageSource(
        version=f.version,
        behaviour_package=behaviour[f.version],
        source_type="changelog" if guide else "documentation",
        source_kind="migration_guide" if guide else "docs_page",
        introduced_in=guide.introduced_in if guide else None,
        source_path=f.path,
        source_url=doc.url,
        source_repo=src.repo,
        source_commit=src.commit,
        source_git_blob=f.git_blob,
        source_content_hash=f.sha256,
        release_id=None,
        source_published_at=None,
        source_retrieved_at=None,
        licence=src.licence,
        licence_status=src.licence_status,
        licence_applicability=None,
        redistributable=True,
        repository_licence=src.licence,
        licence_basis=DOCS_LICENCE_BASIS,
        copyright=src.copyright,
        attribution=(
            f"{doc.title or f.path}, DuckDB documentation {f.version}, duckdb.org "
            f"({src.repo} @ {src.commit[:12]}); {src.copyright}"
        ),
        copyright_date_discrepancy=None,
    )


# ---------------------------------------------------------------- IDs (S4-D1)


def identity_key(chunk: Chunk) -> list[object]:
    return [
        chunk.library,
        chunk.version,
        chunk.source_kind,
        chunk.source_path,
        list(chunk.section_path),
        [list(p) for p in chunk.merged_section_paths],
        chunk.occurrence,
        chunk.part_ordinal,
        chunk.content_hash,
    ]


def canonical_json(key: Sequence[object]) -> bytes:
    """Design §6: compact JSON, no ASCII escaping, no NaN; strings used exactly as produced."""
    return json.dumps(list(key), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


def make_chunk_id(key: Sequence[object], hex_chars: int = 20) -> str:
    return "c-" + hashlib.sha256(canonical_json(key)).hexdigest()[:hex_chars]


def check_unique(chunks: Iterable[Chunk]) -> None:
    """Fail on any corpus-wide ID collision, naming both chunks."""
    seen: dict[str, Chunk] = {}
    for c in chunks:
        other = seen.get(c.chunk_id)
        if other is not None:
            raise ChunkIdCollision(
                f"chunk id {c.chunk_id} collides: {other.source_path} {list(other.section_path)} "
                f"part {other.part_ordinal} and {c.source_path} {list(c.section_path)} "
                f"part {c.part_ordinal}"
            )
        seen[c.chunk_id] = c


# ---------------------------------------------------------------- units and pieces


@dataclass
class _Unit:
    node: SectionNode
    index: int
    position: int  # in the source-ordered list of all nodes
    segments: list[Segment]

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.segments)

    @property
    def is_api(self) -> bool:
        return self.node.api is not None


@dataclass
class _Piece:
    segments: list[Segment]
    kind: Literal["ordinary", "group", "oversize"] = "ordinary"
    reason: OversizeReason | None = None
    has_heading: bool = False

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.segments)


def _seg(
    text: str,
    role: SegmentRole,
    unit: int,
    block: Block | None,
    a: int,
    b: int,
    *,
    header: tuple[str, ...] = (),
    body: tuple[str, ...] = (),
    header_repeated: bool = False,
) -> Segment:
    return Segment(
        text=text,
        tokens=approx_tokens(text),
        role=role,
        unit=unit,
        block=block,
        line_start=a,
        line_end=b,
        header=header,
        body=body,
        header_repeated=header_repeated,
    )


def _block_of(seg: Segment) -> Block:
    if seg.block is None:
        raise ChunkingError(f"segment at line {seg.line_start} has no block")
    return seg.block


def _units(tree: SectionTree) -> list[_Unit]:
    nodes = list(tree.nodes())
    ordered = sorted(range(len(nodes)), key=lambda i: (nodes[i].line_start, i))
    position = {id(nodes[i]): pos for pos, i in enumerate(ordered)}
    units: list[_Unit] = []
    for i in ordered:
        n = nodes[i]
        if n.state != "content":
            continue
        idx = len(units)
        segs: list[Segment] = []
        if n.kind == "heading" and n.heading is not None:
            segs.append(
                _seg(
                    f"{'#' * n.level} {n.heading}", "heading", idx, None, n.line_start, n.line_start
                )
            )
        elif n.kind == "api_entry" and n.heading is not None:
            first = n.blocks[0]
            segs.append(
                _seg(
                    f"{'#' * min(n.level, 6)} {n.heading}",
                    "heading",
                    idx,
                    None,
                    first.line_start,
                    first.line_start,
                )
            )
        for b in n.blocks:
            segs.append(_seg(b.text, "block", idx, b, b.line_start, b.line_end))
        units.append(_Unit(node=n, index=idx, position=position[id(n)], segments=segs))
    return units


def _table_parts(seg: Segment, prefix: int, cfg: ChunkingConfig) -> list[_Piece]:
    b = _block_of(seg)
    lines = b.text.split("\n")
    has_header = (
        b.meta.get("header") != "false"
        and len(lines) >= 2
        and bool(TABLE_DELIMITER.match(lines[1]))
    )
    header = tuple(lines[:2]) if has_header else ()
    rows = lines[len(header) :]
    exact = b.line_end - b.line_start + 1 == len(lines)
    ht = sum(approx_tokens(h) for h in header)
    pieces: list[_Piece] = []
    current: list[int] = []
    cur_t = 0

    def emit(sel: list[int], oversize: bool) -> None:
        first = not pieces
        body = tuple(rows[i] for i in sel)
        if exact:
            a = b.line_start if first else b.line_start + len(header) + sel[0]
            z = b.line_start + len(header) + sel[-1]
        else:
            a, z = b.line_start, b.line_end
        text = "\n".join((*header, *body))
        part = _seg(
            text,
            "part",
            seg.unit,
            b,
            a,
            z,
            header=header,
            body=body,
            header_repeated=bool(header) and not first,
        )
        pieces.append(
            _Piece([part], "oversize" if oversize else "group", "table_row" if oversize else None)
        )

    for i, row in enumerate(rows):
        rt = approx_tokens(row)
        pre = prefix if not pieces and not current else 0
        if current and cur_t + rt <= cfg.target_tokens:
            current.append(i)
            cur_t += rt
            continue
        if current:
            emit(current, False)
            current, pre = [], 0
        if ht + rt + pre > cfg.hard_limit_tokens:
            emit([i], True)
        else:
            current, cur_t = [i], ht + rt + pre
    if current:
        emit(current, False)
    return pieces


def _list_parts(seg: Segment, prefix: int, cfg: ChunkingConfig) -> list[_Piece]:
    b = _block_of(seg)
    items: list[list[str]] = []
    for line in b.text.split("\n"):
        if LIST_ITEM.match(line) or not items:
            items.append([line])
        else:
            items[-1].append(line)
    texts = ["\n".join(it) for it in items]
    pieces: list[_Piece] = []
    current: list[int] = []
    cur_t = 0

    def emit(sel: list[int], oversize: bool) -> None:
        body = tuple(texts[i] for i in sel)
        part = _seg("\n".join(body), "part", seg.unit, b, b.line_start, b.line_end, body=body)
        pieces.append(
            _Piece([part], "oversize" if oversize else "group", "list_item" if oversize else None)
        )

    for i, text in enumerate(texts):
        t = approx_tokens(text)
        pre = prefix if not pieces and not current else 0
        if current and cur_t + t <= cfg.target_tokens:
            current.append(i)
            cur_t += t
            continue
        if current:
            emit(current, False)
            current, pre = [], 0
        if t + pre > cfg.hard_limit_tokens:
            emit([i], True)
        else:
            current, cur_t = [i], t + pre
    if current:
        emit(current, False)
    return pieces


def _pieces(unit: _Unit, cfg: ChunkingConfig, path: str) -> list[_Piece]:
    heading = unit.segments[0] if unit.segments[0].role == "heading" else None
    blocks = unit.segments[1:] if heading else unit.segments
    pieces: list[_Piece] = []
    pending = heading
    for seg in blocks:
        b = _block_of(seg)
        pre = pending.tokens if pending else 0
        if seg.tokens + pre <= cfg.hard_limit_tokens:
            new = [_Piece([seg])]
        elif b.kind == "table":
            new = _table_parts(seg, pre, cfg)
        elif b.kind == "list":
            new = _list_parts(seg, pre, cfg)
        elif b.kind == "code":
            new = [_Piece([seg], "oversize", "code_block")]
        elif b.kind == "api_entry":
            new = [_Piece([seg], "oversize", "api_entry_block")]
        else:
            raise ChunkingError(
                f"{path}:{b.line_start}: a {b.kind} block over {cfg.hard_limit_tokens} tokens has "
                "no approved rule"
            )
        if pending is not None:
            new[0].segments.insert(0, pending)
            new[0].has_heading = True
            pending = None
        pieces.extend(new)
    return pieces


def _pack(pieces: list[_Piece], cfg: ChunkingConfig) -> list[list[_Piece]]:
    parts: list[list[_Piece]] = []
    current: list[_Piece] = []
    for p in pieces:
        if p.kind != "ordinary":
            if current:
                parts.append(current)
                current = []
            parts.append([p])
            continue
        cur_t = sum(x.tokens for x in current)
        if current and (cur_t >= cfg.target_tokens or cur_t + p.tokens > cfg.hard_limit_tokens):
            parts.append(current)
            current = []
        current.append(p)
    if current:
        parts.append(current)
    return parts


def _overlap(prev: list[_Piece], part: list[_Piece], cfg: ChunkingConfig) -> list[Segment]:
    """Trailing whole blocks of the previous ordinary part (design §8 rule 6)."""
    if prev[0].kind != "ordinary" or part[0].kind != "ordinary":
        return []
    budget = cfg.overlap_ratio * cfg.target_tokens
    chosen: list[Segment] = []
    total = 0
    for p in reversed(prev):
        if p.has_heading or len(p.segments) != 1:
            break
        s = p.segments[0]
        if total + s.tokens > budget:
            break
        chosen.insert(0, s)
        total += s.tokens
    own = sum(p.tokens for p in part)
    while chosen and own + sum(s.tokens for s in chosen) > cfg.hard_limit_tokens:
        chosen.pop(0)
    return [replace(s, overlap=True) for s in chosen]


# ---------------------------------------------------------------- chunks


@dataclass
class _Draft:
    segments: list[Segment]
    units: list[_Unit]
    part: int = 1
    parts: int = 1
    kind: Literal["ordinary", "group", "oversize"] = "ordinary"
    reason: OversizeReason | None = None
    railroad: tuple[int, ...] = field(default_factory=tuple)


def _merge_runs(units: list[_Unit], cfg: ChunkingConfig) -> list[list[_Unit]]:
    runs: list[list[_Unit]] = []
    open_run = False
    for u in units:
        mergeable = not u.is_api and len(u.node.path) > 1 and u.tokens < cfg.merge_below_tokens
        if mergeable and open_run:
            last = runs[-1][-1]
            if (
                u.node.path[:-1] == last.node.path[:-1]
                and u.position == last.position + 1
                and sum(x.tokens for x in runs[-1]) + u.tokens <= cfg.target_tokens
            ):
                runs[-1].append(u)
                continue
        runs.append([u])
        open_run = mergeable
    return runs


def _drafts(units: list[_Unit], cfg: ChunkingConfig, path: str) -> list[_Draft]:
    drafts: list[_Draft] = []
    for run in _merge_runs(units, cfg):
        if len(run) > 1 or run[0].tokens <= cfg.hard_limit_tokens:
            segs = [s for u in run for s in u.segments]
            lines = tuple(line for u in run for line in u.node.railroad_removal_lines)
            drafts.append(_Draft(segments=segs, units=run, railroad=lines))
            continue
        unit = run[0]
        parts = _pack(_pieces(unit, cfg, path), cfg)
        made: list[_Draft] = []
        for k, part in enumerate(parts):
            ov = _overlap(parts[k - 1], part, cfg) if k else []
            own = [s for p in part for s in p.segments]
            made.append(
                _Draft(
                    segments=[*ov, *own],
                    units=[unit],
                    part=k + 1,
                    parts=len(parts),
                    kind=part[0].kind,
                    reason=part[0].reason,
                )
            )
        # S4-D9: each removal record on exactly one part (the first whose content reaches it).
        for line in unit.node.railroad_removal_lines:
            target = next(
                (d for d in made if max(s.line_end for s in d.segments if not s.overlap) >= line),
                made[-1],
            )
            target.railroad = (*target.railroad, line)
        drafts.extend(made)
    return drafts


def chunk_page(
    doc: ParsedDocument,
    tree: SectionTree,
    source: PageSource,
    cfg: ChunkingConfig,
    cfg_sha256: str,
) -> ChunkedPage:
    """Chunk one page (design §8). Raises ``ChunkingError`` instead of guessing."""
    units = _units(tree)
    drafts = _drafts(units, cfg, doc.path)
    occurrence: Counter[tuple[object, ...]] = Counter()
    seen_identity: dict[tuple[int, ...], int] = {}
    chunks: list[Chunk] = []
    for d in drafts:
        run = d.units
        merged = len(run) > 1
        node = run[0].node
        section_path = node.path[:-1] if merged else node.path
        merged_paths = tuple(u.node.path for u in run) if merged else ()
        identity = (section_path, merged_paths)
        unit_key = tuple(u.index for u in run)
        if unit_key not in seen_identity:
            seen_identity[unit_key] = occurrence[identity]
            occurrence[identity] += 1
        own = [s for s in d.segments if not s.overlap]
        ov = [s for s in d.segments if s.overlap]
        content = "\n\n".join(s.text for s in d.segments)
        blocks = [s.block for s in d.segments if s.block is not None]
        parent_level = 0 if len(node.path) <= 2 else _parent_level(tree, node)
        api = node.api if not merged else None
        draft = dict(
            chunk_id="",
            library=LIBRARY,
            version=source.version,
            behaviour_package=source.behaviour_package,
            version_status="resolved",
            source_type=source.source_type,
            source_kind=source.source_kind,
            introduced_in=source.introduced_in,
            source_path=source.source_path,
            source_url=source.source_url,
            source_repo=source.source_repo,
            source_commit=source.source_commit,
            source_git_blob=source.source_git_blob,
            source_content_hash=source.source_content_hash,
            release_id=source.release_id,
            source_published_at=source.source_published_at,
            source_retrieved_at=source.source_retrieved_at,
            doc_title=tree.title,
            section_path=section_path,
            section_path_fallback=tree.section_path_fallback,
            heading_level=parent_level if merged else node.level,
            unit_kind="prose_merged" if merged else ("api_entry" if api else "prose"),
            merged_section_paths=merged_paths,
            unit_ordinal=run[0].index + 1,
            occurrence=seen_identity[unit_key],
            part_ordinal=d.part,
            part_count=d.parts,
            line_start=min(s.line_start for s in own),
            line_end=max(s.line_end for s in own),
            api_signature=api.signature if api else None,
            api_id=api.api_id if api else None,
            api_reason=api.reason if api else None,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            token_count_approx=approx_tokens(content),
            char_count=len(content),
            split_inside_block=d.kind == "group"
            or (d.kind == "oversize" and d.reason in ("table_row", "list_item") and d.parts > 1),
            table_header_repeated=any(s.header_repeated for s in own),
            overlap_prev_blocks=len(ov),
            overlap_prev_tokens=sum(s.tokens for s in ov),
            overlap_line_start=min(s.line_start for s in ov) if ov else None,
            overlap_line_end=max(s.line_end for s in ov) if ov else None,
            oversize=d.kind == "oversize",
            oversize_reason=d.reason if d.kind == "oversize" else None,
            has_code=any(b.kind == "code" for b in blocks) or "```" in content,
            has_table=any(b.kind == "table" for b in blocks),
            has_unrendered_site_variable=bool(SITE_MARKER.search(content)),
            has_liquid_branches=any(b.meta.get("liquid_branches") == "true" for b in blocks),
            extraction=doc.extraction,
            transformations=(),
            railroad_removal_lines=tuple(sorted(d.railroad)),
            licence=source.licence,
            licence_status=source.licence_status,
            licence_applicability=source.licence_applicability,
            redistributable=source.redistributable,
            repository_licence=source.repository_licence,
            licence_basis=source.licence_basis,
            copyright=source.copyright,
            attribution=source.attribution,
            copyright_date_discrepancy=source.copyright_date_discrepancy,
            parser_version=doc.parser_version,
            chunker_version=cfg.chunker_version,
            chunking_config_sha256=cfg_sha256,
        )
        chunk = Chunk.model_validate(draft)
        chunks.append(
            chunk.model_copy(
                update={"chunk_id": make_chunk_id(identity_key(chunk), cfg.id_hex_chars)}
            )
        )
    page = ChunkedPage(
        chunks=tuple(chunks),
        segments=tuple(tuple(d.segments) for d in drafts),
        empty_sections=tree.empty_sections(),
    )
    check_unique(page.chunks)
    return page


def _parent_level(tree: SectionTree, node: SectionNode) -> int:
    parent_path = node.path[:-1]
    for n in tree.nodes():
        if n.path == parent_path:
            return n.level
    return 0


def chunk_documents(
    docs: Iterable[ParsedDocument],
    manifest: CorpusManifest,
    cfg: ChunkingConfig,
    cfg_sha256: str,
) -> list[tuple[ParsedDocument, SectionTree, ChunkedPage]]:
    """Chunk every included page and check IDs across the whole corpus."""
    out = []
    for doc in docs:
        if not doc.include_in_corpus:
            continue
        tree = build_tree(doc, cfg)
        out.append(
            (doc, tree, chunk_page(doc, tree, page_source(doc, manifest, cfg), cfg, cfg_sha256))
        )
    check_unique(c for _, _, page in out for c in page.chunks)
    return out


# ---------------------------------------------------------------- checks


def reconstruction_problems(doc: ParsedDocument, tree: SectionTree, page: ChunkedPage) -> list[str]:
    """Design §8 content preservation for one page."""
    problems: list[str] = []
    where = doc.path
    index = {id(b): i for i, b in enumerate(doc.blocks)}
    # 1. Every chunk's content is exactly its segments.
    for c, segs in zip(page.chunks, page.segments, strict=True):
        if c.content != "\n\n".join(s.text for s in segs):
            problems.append(f"{where}: chunk {c.chunk_id} content is not its segments")
        if c.content_hash != hashlib.sha256(c.content.encode("utf-8")).hexdigest():
            problems.append(f"{where}: chunk {c.chunk_id} content_hash is wrong")
        if c.token_count_approx != approx_tokens(c.content):
            problems.append(f"{where}: chunk {c.chunk_id} token count is wrong")
    # 2. Non-overlap segments, in chunk order, reproduce every heading line and block once.
    actual: list[tuple[str, object, str]] = []
    groups: dict[int, list[Segment]] = {}
    for segs in page.segments:
        for s in segs:
            if s.overlap:
                continue
            if s.role == "heading":
                actual.append(("heading", s.unit, s.text))
            elif s.role == "block" and s.block is not None:
                actual.append(("block", index.get(id(s.block), -1), s.text))
            elif s.block is not None:
                i = index.get(id(s.block), -1)
                if i not in groups:
                    groups[i] = []
                    actual.append(("block", i, ""))  # filled below
                groups[i].append(s)
    for k, (kind, ref, _text) in enumerate(actual):
        if kind == "block" and isinstance(ref, int) and ref in groups:
            parts = groups[ref]
            header = parts[0].header
            for p in parts[1:]:
                if p.header != header or (header and not p.header_repeated):
                    problems.append(f"{where}: block {ref} repeated header differs")
            lines = [*header, *(line for p in parts for line in p.body)]
            actual[k] = ("block", ref, "\n".join(lines))
    expected: list[tuple[str, object, str]] = []
    for u in _units(tree):
        for s in u.segments:
            if s.role == "heading":
                expected.append(("heading", u.index, s.text))
            elif s.block is not None:
                expected.append(("block", index.get(id(s.block), -1), s.block.text))
    if actual != expected:
        first = next(
            (i for i, (a, e) in enumerate(zip(actual, expected, strict=False)) if a != e),
            min(len(actual), len(expected)),
        )
        problems.append(
            f"{where}: reconstruction differs at item {first} "
            f"({len(actual)} reconstructed, {len(expected)} expected)"
        )
    # 3. Every heading is in a chunk path, a merged path or an empty-section record.
    paths = [c.section_path for c in page.chunks]
    paths += [p for c in page.chunks for p in c.merged_section_paths]
    paths += [e.section_path for e in page.empty_sections]
    for n in tree.nodes():
        if n.kind != "page_root" and not any(p[: len(n.path)] == n.path for p in paths):
            # A section path holds headings (restricted text for release posts): hash it.
            ref = hashlib.sha256(json.dumps(list(n.path)).encode("utf-8")).hexdigest()[:12]
            problems.append(
                f"{where}: section sha256 {ref} (depth {len(n.path)}) is not represented"
            )
    # 4. S4-D9: every S3 railroad record on exactly one chunk or empty record.
    s3 = sorted(t.line for t in doc.transformations if t.code == RAILROAD_RULE and t.line)
    got = sorted(
        [line for c in page.chunks for line in c.railroad_removal_lines]
        + [line for e in page.empty_sections for line in e.railroad_removal_lines]
    )
    if got != s3:
        problems.append(f"{where}: railroad records {got} differ from S3 {s3}")
    return problems


def rule_problems(page: ChunkedPage, cfg: ChunkingConfig) -> list[str]:
    """Approved size, merge, API and overlap rules for one page's chunks."""
    problems: list[str] = []
    budget = cfg.overlap_ratio * cfg.target_tokens
    for c, segs in zip(page.chunks, page.segments, strict=True):
        tag = f"{c.source_path} {list(c.section_path)} part {c.part_ordinal}"
        if c.token_count_approx > cfg.hard_limit_tokens and not c.oversize:
            problems.append(f"{tag}: {c.token_count_approx} tokens without oversize")
        if c.oversize and sum(1 for s in segs if s.role != "heading" and not s.overlap) != 1:
            problems.append(f"{tag}: oversize chunk holds more than one piece")
        if c.unit_kind == "prose_merged":
            if c.token_count_approx > cfg.target_tokens or len(c.merged_section_paths) < 2:
                problems.append(f"{tag}: merged chunk breaks R1")
        elif c.merged_section_paths:
            problems.append(f"{tag}: unmerged chunk lists merged paths")
        if c.unit_kind == "api_entry" and c.api_signature is None:
            problems.append(f"{tag}: API chunk without a signature")
        if c.overlap_prev_tokens > budget:
            problems.append(f"{tag}: overlap {c.overlap_prev_tokens} tokens over the budget")
        if c.overlap_prev_blocks and (c.oversize or c.split_inside_block or c.part_ordinal == 1):
            problems.append(f"{tag}: overlap where none is allowed")
        if not any(not s.overlap for s in segs):
            problems.append(f"{tag}: chunk consists only of overlap")
        if not c.section_path or c.section_path[0] != c.doc_title:
            problems.append(f"{tag}: section path does not start with the title")
    return problems
