"""S4b chunker (design docs/design/RAG-M3_S4_chunking.md §5-§9; tests T18-T29)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from version_aware_rag.corpus.chunk import (
    Chunk,
    ChunkedPage,
    ChunkIdCollision,
    PageSource,
    approx_tokens,
    canonical_json,
    check_unique,
    chunk_page,
    identity_key,
    make_chunk_id,
    reconstruction_problems,
    rule_problems,
)
from version_aware_rag.corpus.config import load_chunking_config
from version_aware_rag.corpus.parse import PARSER_VERSION, Block, ParsedDocument, parse_page
from version_aware_rag.corpus.sections import build_tree

ROOT = Path(__file__).resolve().parents[3]
CFG, CFG_SHA = load_chunking_config(ROOT / "configs/chunking.toml")


def words(n: int, tag: str = "w") -> str:
    """A paragraph of exactly ``n`` approximate tokens."""
    return " ".join(f"{tag}{i}" for i in range(n))


def source(
    version: str = "2.0", kind: str = "docs_page", path: str = "docs/2.0/page.md"
) -> PageSource:
    post = kind == "release_post"
    return PageSource(
        version=version,
        behaviour_package={"1.4": "1.4.5", "2.0": "2.0.1"}[version],
        source_type="changelog" if post else "documentation",
        source_kind=kind,
        introduced_in="2.0.1" if post else None,
        source_path=path,
        source_url=f"https://duckdb.org/{path}",
        source_repo="duckdb/duckdb-web",
        source_commit="d" * 40,
        source_git_blob="b" * 40,
        source_content_hash="0" * 64,
        release_id=None,
        source_published_at="2026-11-20" if post else None,
        source_retrieved_at=None,
        licence=None if post else "MIT",
        licence_status="unverified" if post else "verified",
        licence_applicability="uncertain" if post else None,
        redistributable=not post,
        repository_licence="MIT",
        licence_basis="fixture basis",
        copyright="Copyright 2018-2025 Stichting DuckDB Foundation",
        attribution="fixture attribution",
        copyright_date_discrepancy=True if post else None,
    )


def doc_of(body: str, path: str = "docs/2.0/page.md", version: str = "2.0") -> ParsedDocument:
    return parse_page(
        raw=("---\ntitle: Page\n---\n" + body).encode(),
        version=version,
        path=path,
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
    )


def chunked(
    body: str, src: PageSource | None = None, path: str = "docs/2.0/page.md", version: str = "2.0"
) -> tuple[ParsedDocument, ChunkedPage]:
    doc = doc_of(body, path, version)
    tree = build_tree(doc, CFG)
    page = chunk_page(doc, tree, src or source(version, path=path), CFG, CFG_SHA)
    assert reconstruction_problems(doc, tree, page) == []  # T29 on every fixture
    assert rule_problems(page, CFG) == []
    return doc, page


def ids(page: ChunkedPage) -> dict[tuple[str, ...], str]:
    return {
        c.section_path + tuple(map("|".join, c.merged_section_paths)): c.chunk_id
        for c in page.chunks
    }


# ---------------------------------------------------------------- T18 merging (R1)


def test_t18_small_siblings_merge_under_their_parent() -> None:
    _, page = chunked(
        "## A\n\n"
        + words(10, "a")
        + "\n\n## B\n\n"
        + words(10, "b")
        + "\n\n## C\n\n"
        + words(10, "c")
        + "\n"
    )
    (c,) = page.chunks
    assert c.unit_kind == "prose_merged" and c.section_path == ("Page",)
    assert c.merged_section_paths == (("Page", "A"), ("Page", "B"), ("Page", "C"))
    assert c.heading_level == 0 and c.content.startswith("## A\n\n" + words(10, "a"))
    assert "## B" in c.content and "## C" in c.content


def test_t18_no_merge_across_parents_api_entries_or_empty_sections() -> None:
    _, parents = chunked("## P\n\n### a\n\nx\n\n## Q\n\n### b\n\ny\n")
    assert [c.section_path for c in parents.chunks] == [("Page", "P", "a"), ("Page", "Q", "b")]
    _, api = chunked("## A\n\nsmall\n\n## `f(x)`\n\nfn\n\n## B\n\nsmall\n")
    assert [c.unit_kind for c in api.chunks] == ["prose", "api_entry", "prose"]
    _, gap = chunked("## A\n\nsmall\n\n## E\n\n## B\n\nsmall\n")
    assert [c.section_path for c in gap.chunks] == [("Page", "A"), ("Page", "B")]
    assert [e.section_path for e in gap.empty_sections] == [("Page", "E")]


def test_t18_merge_stops_at_the_target() -> None:
    body = "".join(f"## U{i}\n\n{words(55, f'u{i}x')}\n\n" for i in range(8))
    unit = approx_tokens(f"## U0\n\n{words(55, 'u0x')}")
    assert unit < CFG.merge_below_tokens
    _, page = chunked(body)
    sizes = [len(c.merged_section_paths) for c in page.chunks]
    per_run = CFG.target_tokens // unit
    assert sizes == [per_run, 8 - per_run]
    assert all(c.token_count_approx <= CFG.target_tokens for c in page.chunks)


def test_t18_large_sections_are_never_merged() -> None:
    _, page = chunked("## A\n\n" + words(70, "a") + "\n\n## B\n\n" + words(5, "b") + "\n")
    assert [c.unit_kind for c in page.chunks] == ["prose", "prose"]


# ---------------------------------------------------------------- T19 API entries


def test_t19_api_entries_stay_single_and_split_with_their_signature() -> None:
    _, small = chunked("### `f(x)`\n\nf\n\n### `g(x)`\n\ng\n")
    assert [(c.unit_kind, c.api_signature) for c in small.chunks] == [
        ("api_entry", "f(x)"),
        ("api_entry", "g(x)"),
    ]
    body = "### `big(x)`\n\n" + "\n\n".join(words(200, f"p{i}x") for i in range(4)) + "\n"
    _, big = chunked(body)
    assert len(big.chunks) > 1
    assert {(c.unit_kind, c.api_signature, c.section_path) for c in big.chunks} == {
        ("api_entry", "big(x)", ("Page", "`big(x)`"))
    }


# ---------------------------------------------------------------- T20 long sections, T26 content


def test_t20_t26_long_section_splits_at_block_boundaries() -> None:
    body = "## Long\n\n" + "\n\n".join(words(150, f"p{i}x") for i in range(6)) + "\n"
    _, page = chunked(body)
    parts = page.chunks
    assert [c.part_ordinal for c in parts] == list(range(1, len(parts) + 1))
    assert all(c.part_count == len(parts) and c.token_count_approx <= 512 for c in parts)
    assert parts[0].content.startswith("## Long\n\n")
    assert not any(c.content.startswith("## Long") or "## Long" in c.content for c in parts[1:])
    assert all(c.section_path == ("Page", "Long") for c in parts)
    for c, segs in zip(parts, page.segments, strict=True):  # whole blocks only
        assert all(s.role in ("heading", "block") for s in segs)
        own = sum(s.tokens for s in segs if not s.overlap)
        assert own >= CFG.target_tokens or c is parts[-1]


# ---------------------------------------------------------------- T21-T24 oversized blocks


def _table(rows: int, cell: int = 18) -> str:
    lines = ["| key | value |", "|---|---|"]
    lines += [f"| r{i} | {words(cell, f'r{i}c')} |" for i in range(rows)]
    return "\n".join(lines)


def test_t21_table_row_groups_repeat_the_header() -> None:
    doc, page = chunked("## T\n\n" + _table(60) + "\n")
    groups = [c for c in page.chunks if c.split_inside_block]
    assert len(groups) == len(page.chunks) >= 3
    table = next(b for b in doc.blocks if b.kind == "table")
    header = table.text.split("\n")[:2]
    rows: list[str] = []
    for k, c in enumerate(groups):
        body = c.content.removeprefix("## T\n\n").split("\n")
        assert body[:2] == header and c.table_header_repeated == (k > 0)
        rows += body[2:]
        assert c.token_count_approx <= 512 and not c.oversize and c.overlap_prev_blocks == 0
    assert rows == table.text.split("\n")[2:]  # every row once, in order, none split
    first, second = groups[0], groups[1]
    assert first.line_start == table.line_start - 2  # heading line through its rows
    assert second.line_start == first.line_end + 1  # exact row lines


def test_t22_single_oversized_row_stays_whole() -> None:
    table = (
        "| key | value |\n|---|---|\n| small | x |\n| huge | "
        + words(600, "h")
        + " |\n| tail | y |"
    )
    _, page = chunked("## T\n\n" + "\n\n".join([words(300, "lead"), table]) + "\n")
    big = [c for c in page.chunks if c.oversize]
    assert [c.oversize_reason for c in big] == ["table_row"]
    assert words(600, "h") in big[0].content and big[0].content.startswith("| key | value |")


def test_t23_oversized_code_block_stays_whole() -> None:
    code = (
        "```sql\n" + "\n".join(f"SELECT {i}, {words(20, f'c{i}x')};" for i in range(40)) + "\n```"
    )
    doc, page = chunked(
        "## C\n\n" + words(100, "lead") + "\n\n" + code + "\n\n" + words(50, "t") + "\n"
    )
    big = [c for c in page.chunks if c.oversize]
    assert [c.oversize_reason for c in big] == ["code_block"]
    block = next(b for b in doc.blocks if b.kind == "code")
    assert block.text in big[0].content and big[0].token_count_approx > 512  # never truncated
    assert big[0].overlap_prev_blocks == 0
    assert page.chunks[page.chunks.index(big[0]) + 1].overlap_prev_blocks == 0


def test_t24_list_item_groups_keep_nested_items() -> None:
    items = [f"- item{i} {words(20, f'i{i}x')}\n  - sub{i} {words(5, f's{i}x')}" for i in range(40)]
    doc, page = chunked("## L\n\n" + "\n".join(items) + "\n")
    groups = [c for c in page.chunks if c.split_inside_block]
    assert len(groups) == len(page.chunks) >= 2
    lst = next(b for b in doc.blocks if b.kind == "list")
    rebuilt = "\n".join(c.content.removeprefix("## L\n\n") for c in groups)
    assert rebuilt == lst.text  # items intact, in order, sub-items with their item
    for c in groups:
        lines = c.content.removeprefix("## L\n\n").split("\n")
        assert lines[0].startswith("- item") and lines[-1].startswith("  - sub")


def test_t24_oversized_list_item_and_sphinx_block_stay_whole() -> None:
    items = "- small one\n- " + words(600, "big") + "\n- small two"
    _, page = chunked("## L\n\n" + words(100, "lead") + "\n\n" + items + "\n")
    assert [c.oversize_reason for c in page.chunks if c.oversize] == ["list_item"]

    api = Block(
        kind="api_entry",
        text=words(600, "doc"),
        line_start=5,
        line_end=40,
        level=1,
        meta={"api_id": "duckdb.big", "signature": "big(self) → None"},
    )
    doc = ParsedDocument(
        parser_version=PARSER_VERSION,
        source_kind="docs_page",
        version="2.0",
        path="docs/2.0/clients/python/reference/index.md",
        url="https://duckdb.org/x",
        source_sha256="0" * 64,
        title="Python Client API",
        extraction="sphinx_html",
        page_kind="content",
        include_in_corpus=True,
        exclusion_reason=None,
        blocks=(api,),
        warnings=(),
        transformations=(),
        counts={},
    )
    tree = build_tree(doc, CFG)
    sphinx = chunk_page(doc, tree, source(path=doc.path), CFG, CFG_SHA)
    assert reconstruction_problems(doc, tree, sphinx) == []
    (c,) = sphinx.chunks
    assert (c.oversize, c.oversize_reason, c.unit_kind, c.api_id) == (
        True,
        "api_entry_block",
        "api_entry",
        "duckdb.big",
    )
    assert c.content.startswith("# big(self) → None\n\n")


# ---------------------------------------------------------------- T25 overlap


def test_t25_overlap_is_whole_trailing_blocks_within_the_budget() -> None:
    sizes = [200, 140, 30, 20, 200, 140, 30, 20]
    paras = [words(n, f"p{i}x") for i, n in enumerate(sizes)]
    _, page = chunked("## O\n\n" + "\n\n".join(paras) + "\n")
    first, second = page.chunks[0], page.chunks[1]
    assert first.overlap_prev_blocks == 0
    assert second.overlap_prev_blocks >= 1
    assert 0 < second.overlap_prev_tokens <= CFG.overlap_ratio * CFG.target_tokens
    ov = [s for s in page.segments[1] if s.overlap]
    assert second.content.startswith("\n\n".join(s.text for s in ov))
    assert all(s.text in first.content for s in ov)  # repeated from the previous part
    assert second.overlap_line_start == ov[0].line_start
    assert second.line_start > (second.overlap_line_end or 0)  # own lines exclude overlap


def test_t25_no_overlap_around_groups_or_oversize_parts() -> None:
    body = "## M\n\n" + words(400, "a") + "\n\n" + _table(60) + "\n\n" + words(30, "z") + "\n"
    _, page = chunked(body)
    assert all(c.overlap_prev_blocks == 0 for c in page.chunks)


# ---------------------------------------------------------------- T27 metadata, S4-D9


def test_t27_metadata_for_docs_and_posts() -> None:
    body = (
        "## S\n\nUse [site:latest_version:client] here.\n\n"
        "```sql\nSELECT 1;\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    _, docs = chunked(body)
    (c,) = docs.chunks
    assert (c.library, c.version, c.behaviour_package, c.version_status) == (
        "duckdb",
        "2.0",
        "2.0.1",
        "resolved",
    )
    assert (c.source_type, c.source_kind, c.introduced_in) == ("documentation", "docs_page", None)
    assert (c.licence, c.licence_status, c.licence_applicability, c.redistributable) == (
        "MIT",
        "verified",
        None,
        True,
    )
    assert (
        c.has_code and c.has_table and c.has_unrendered_site_variable and not c.has_liquid_branches
    )
    assert (c.doc_title, c.section_path, c.heading_level, c.unit_ordinal, c.occurrence) == (
        "Page",
        ("Page", "S"),
        2,
        1,
        0,
    )
    assert (c.part_ordinal, c.part_count, c.extraction, c.transformations) == (1, 1, "markdown", ())
    assert (c.parser_version, c.chunker_version, c.chunking_config_sha256) == (
        PARSER_VERSION,
        "s4-1",
        CFG_SHA,
    )
    assert c.content_hash == hashlib.sha256(c.content.encode()).hexdigest()
    assert c.char_count == len(c.content) and c.token_count_approx == approx_tokens(c.content)

    post_path = "_posts/2026-11-20-announcing-duckdb-201.md"
    _, post = chunked("## News\n\nprose\n", source("2.0", "release_post", post_path), post_path)
    p = post.chunks[0]
    assert (p.source_type, p.source_kind, p.introduced_in, p.source_published_at) == (
        "changelog",
        "release_post",
        "2.0.1",
        "2026-11-20",
    )
    assert (p.licence, p.licence_status, p.licence_applicability, p.redistributable) == (
        None,
        "unverified",
        "uncertain",
        False,
    )
    assert p.copyright_date_discrepancy is True


def test_t27_s4d9_railroad_records_follow_their_sections() -> None:
    body = (
        '## Syntax\n\n<div id="rrdiagram"></div>\n\nThe clause.\n\n'
        '## Empty\n\n<div id="rrdiagram"></div>\n\n'
        "## Long\n\n"
        + words(300, "a")
        + '\n\n<div id="rrdiagram"></div>\n\n'
        + words(300, "b")
        + "\n"
    )
    doc, page = chunked(body)
    lines = [
        t.line
        for t in doc.transformations
        if t.code == "html_railroad_removed" and t.line is not None
    ]
    assert len(lines) == 3
    on_chunks = [line for c in page.chunks for line in c.railroad_removal_lines]
    on_empty = [line for e in page.empty_sections for line in e.railroad_removal_lines]
    assert sorted(on_chunks + on_empty) == sorted(lines)  # each record exactly once
    assert on_empty == [lines[1]]
    long_parts = [c for c in page.chunks if c.section_path == ("Page", "Long")]
    assert len(long_parts) == 2 and [len(c.railroad_removal_lines) for c in long_parts] == [0, 1]


# ---------------------------------------------------------------- T28 IDs (S4-D1)


def test_t28_golden_canonical_key_and_id() -> None:
    _, page = chunked("## Überblick\n\nhello\n")
    (c,) = page.chunks
    digest = "270cadcdd0e9267acfd8f9b0673d326e669caaa1a6700951e0da348eaac0ab80"
    assert c.content_hash == digest
    expected = (
        '["duckdb","2.0","docs_page","docs/2.0/page.md",["Page","Überblick"],[],0,1,"'
        + digest
        + '"]'
    ).encode("utf-8")
    assert canonical_json(identity_key(c)) == expected  # no ASCII escaping, no spaces
    assert c.chunk_id == "c-0729d7fa2464bb8c6288" == make_chunk_id(identity_key(c))


def test_t28_ids_are_stable_and_unaffected_by_unrelated_insertions() -> None:
    body = "## A\n\n" + words(100, "a") + "\n\n## B\n\n" + words(100, "b") + "\n"
    _, one = chunked(body)
    _, two = chunked(body)
    assert ids(one) == ids(two)
    _, inserted = chunked("## Z\n\n" + words(100, "z") + "\n\n" + body)
    for path, cid in ids(one).items():
        assert ids(inserted)[path] == cid
    assert {c.unit_ordinal for c in inserted.chunks if c.section_path == ("Page", "A")} == {2}


def test_t28_content_edit_changes_only_the_affected_id() -> None:
    _, before = chunked("## A\n\n" + words(100, "a") + "\n\n## B\n\n" + words(100, "b") + "\n")
    _, after = chunked("## A\n\n" + words(100, "a") + "\n\n## B\n\n" + words(100, "B") + "\n")
    assert ids(before)[("Page", "A")] == ids(after)[("Page", "A")]
    assert ids(before)[("Page", "B")] != ids(after)[("Page", "B")]


def test_t28_duplicate_paths_and_versions_are_distinguished() -> None:
    _, dup = chunked(
        "## A\n\n### S\n\n" + words(80, "x") + "\n\n## A\n\n### S\n\n" + words(80, "x") + "\n"
    )
    same = [c for c in dup.chunks if c.section_path == ("Page", "A", "S")]
    assert [c.occurrence for c in same] == [0, 1]
    assert same[0].content == same[1].content and same[0].chunk_id != same[1].chunk_id
    body = "## A\n\n" + words(80, "x") + "\n"
    _, v14 = chunked(body, path="docs/1.4/page.md", version="1.4")
    _, v20 = chunked(body)
    assert v14.chunks[0].content == v20.chunks[0].content
    assert v14.chunks[0].chunk_id != v20.chunks[0].chunk_id


def test_t28_forced_collision_fails() -> None:
    _, page = chunked("".join(f"## H{i}\n\n{words(80, f'h{i}x')}\n\n" for i in range(20)))
    truncated = [
        c.model_copy(update={"chunk_id": make_chunk_id(identity_key(c), 1)}) for c in page.chunks
    ]
    with pytest.raises(ChunkIdCollision, match="collides"):
        check_unique(truncated)  # 20 chunks, 16 possible one-character IDs
    check_unique(page.chunks)


# ---------------------------------------------------------------- T29 reconstruction


def test_t29_reconstruction_detects_tampering() -> None:
    doc, page = chunked("## A\n\n" + words(100, "a") + "\n\n## B\n\n" + words(100, "b") + "\n")
    tree = build_tree(doc, CFG)
    assert reconstruction_problems(doc, tree, page) == []
    edited = page.chunks[0].model_copy(update={"content": page.chunks[0].content + " extra"})
    tampered = ChunkedPage((edited, *page.chunks[1:]), page.segments, page.empty_sections)
    assert any(
        "content is not its segments" in p for p in reconstruction_problems(doc, tree, tampered)
    )
    dropped = ChunkedPage(page.chunks[:1], page.segments[:1], page.empty_sections)
    problems = reconstruction_problems(doc, tree, dropped)
    assert any("reconstruction differs" in p for p in problems)
    assert any("is not represented" in p for p in problems)


def test_chunk_model_rejects_unknown_fields_and_versions() -> None:
    _, page = chunked("## A\n\nx\n")
    data = page.chunks[0].model_dump()
    with pytest.raises(ValueError):
        Chunk.model_validate(data | {"version": "1.1"})
    with pytest.raises(ValueError):
        Chunk.model_validate(data | {"extra": 1})
