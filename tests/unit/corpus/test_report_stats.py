"""S5c build-report statistics (design docs/design/RAG-M3_S5_build.md §4).

Chunks come from the real S3 parser and S4 chunker on synthetic pages; the definitions are
checked against S5b's ``validate.add_warnings`` on the very same chunks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from version_aware_rag.corpus import validate
from version_aware_rag.corpus.chunk import Chunk, chunk_page
from version_aware_rag.corpus.config import load_chunking_config
from version_aware_rag.corpus.inspect import INCLUDE_RULES
from version_aware_rag.corpus.parse import Issue, ParsedDocument, parse_page
from version_aware_rag.corpus.report_stats import (
    DROPPED_INCLUDES,
    NEAR_EMPTY_WORDS,
    PARTIAL_INCLUDES,
    TOKEN_EDGES,
    TOKEN_LIMIT,
    TokenCountError,
    cross_version_duplicates,
    include_records,
    near_empty_pages,
    oversize_chunks,
    parse_warning_records,
    section_path_fallbacks,
    token_histogram,
)
from version_aware_rag.corpus.sections import build_tree

from .test_chunk import source as page_source

ROOT = Path(__file__).resolve().parents[3]
CHUNKING, CHUNKING_SHA = load_chunking_config(ROOT / "configs/chunking.toml")
MARKER = "SECRET-DETAIL-MARKER-7f3a"  # must never reach a report


def _doc(body: str, version: str = "2.0", path: str | None = None) -> ParsedDocument:
    path = path or f"docs/{version}/page.md"
    return parse_page(
        raw=("---\ntitle: Page\n---\n" + body).encode(),
        version=version,
        path=path,
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
    )


def _chunks(body: str, version: str = "2.0", path: str | None = None) -> list[Chunk]:
    doc = _doc(body, version, path)
    page = chunk_page(
        doc,
        build_tree(doc, CHUNKING),
        page_source(version=version, path=doc.path),
        CHUNKING,
        CHUNKING_SHA,
    )
    return list(page.chunks)


def _words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def _corpus() -> list[Chunk]:
    shared = f"## Shared\n\n{_words(40, 'same')}\n"
    code = "## Code\n\n```sql\n" + "\n".join(f"SELECT {i}, {_words(8, 'c')};" for i in range(120))
    return [
        *_chunks(shared + code + "\n```\n", "2.0"),
        *_chunks(shared, "1.4"),  # same content in another version: one duplicate group
        *_chunks("## Tiny\n\nfew words here\n", "2.0", "docs/2.0/tiny.md"),  # near-empty
    ]


def _loaded(chunks: list[Chunk]) -> list[validate.LoadedChunk]:
    return [validate.LoadedChunk(c, "f", i) for i, c in enumerate(chunks, 1)]


def test_threshold_and_histogram_edges_match_s5b_and_s4c() -> None:
    assert NEAR_EMPTY_WORDS == validate.NEAR_EMPTY_WORDS
    assert TOKEN_EDGES == (0, 32, 64, 128, 256, 350, 512, 513)  # chunk_output report bins


def test_definitions_equal_s5b_validation_on_the_same_chunks() -> None:
    chunks = _corpus()
    chunks[0] = chunks[0].model_copy(update={"section_path_fallback": True})
    report = validate.ValidationReport()
    validate.add_warnings(_loaded(chunks), report)
    assert oversize_chunks(chunks)["by_reason"] == report.counts["oversize"]
    assert oversize_chunks(chunks)["count"] == sum(c.oversize for c in chunks) >= 1
    assert near_empty_pages(chunks)["count"] == report.counts["near_empty_pages"] == 1
    assert section_path_fallbacks(chunks)["count"] == report.counts["section_path_fallback"] == 1
    duplicates = cross_version_duplicates(chunks)
    assert duplicates["count"] == report.counts["cross_version_duplicates"] == 1


def test_near_empty_pages_keep_their_chunks_and_are_listed() -> None:
    chunks = _corpus()
    near = near_empty_pages(chunks)
    # S5b counts the words of the chunk content, which includes the heading ("## Tiny").
    assert near["pages"] == [{"source_path": "docs/2.0/tiny.md", "version": "2.0", "words": 5}]
    assert any(c.source_path == "docs/2.0/tiny.md" for c in chunks)  # nothing removed


def test_duplicates_count_groups_and_pairs_without_deduplicating() -> None:
    chunks = _corpus()
    before = [c.chunk_id for c in chunks]
    dup = cross_version_duplicates(chunks)
    assert dup == {
        "count": 1,
        "chunks_in_groups": 2,
        "by_version_count": {"2": 1},
        "by_version_pair": {"1.4|2.0": 1},
    }
    assert [c.chunk_id for c in chunks] == before
    releases = [c.model_copy(update={"source_kind": "github_release"}) for c in chunks]
    assert cross_version_duplicates(releases)["count"] == 0  # release notes excluded, as S5b


def test_oversize_list_and_histogram_are_identifiers_and_counts_only() -> None:
    chunks = _corpus()
    over = oversize_chunks(chunks)
    entries = over["chunks"]
    assert isinstance(entries, list) and entries
    assert set(entries[0]) == {
        "chunk_id",
        "version",
        "source_path",
        "unit_kind",
        "oversize_reason",
        "token_count_approx",
    }
    hist = token_histogram(chunks)
    assert [h["tokens"] for h in hist] == [
        "0-31",
        "32-63",
        "64-127",
        "128-255",
        "256-349",
        "350-511",
        "512",
        ">512",
    ]
    assert sum(int(str(h["chunks"])) for h in hist) == len(chunks)
    assert token_histogram([]) == [{"tokens": h["tokens"], "chunks": 0} for h in hist]


def _with_issues(
    doc: ParsedDocument,
    *,
    warnings: tuple[Issue, ...] = (),
    transformations: tuple[Issue, ...] = (),
) -> ParsedDocument:
    return doc.model_copy(update={"warnings": warnings, "transformations": transformations})


def test_parse_warnings_keep_restricted_pages_and_never_copy_details() -> None:
    post = _doc("## A\n\ntext\n", path="_posts/2026-01-01-announcing-duckdb-201.md")
    post = post.model_copy(update={"source_kind": "release_post"})
    docs = [
        _with_issues(
            _doc("x\n"), warnings=(Issue(code="api_dt_without_id", line=9, detail=MARKER),)
        ),
        _with_issues(
            post, warnings=(Issue(code="html_block_generic_text", line=None, detail=MARKER),)
        ),
    ]
    out = parse_warning_records(docs)
    assert out["count"] == 2 and out["pages"] == 2
    assert out["by_code"] == {"api_dt_without_id": 1, "html_block_generic_text": 1}
    assert MARKER not in json.dumps(out)
    records = out["records"]
    assert isinstance(records, list)
    assert {r["source_kind"] for r in records} == {"docs_page", "release_post"}
    assert set(records[0]) == {
        "version",
        "path",
        "source_kind",
        "include_in_corpus",
        "code",
        "line",
    }


def test_includes_separate_dropped_from_partial_and_never_copy_details() -> None:
    assert set(DROPPED_INCLUDES) | set(PARTIAL_INCLUDES) == set(INCLUDE_RULES)
    issues = tuple(
        Issue(code=code, line=line, detail=MARKER)
        for line, code in enumerate(
            [
                "include_latest_client_removed",
                "include_filterbar_removed",
                "include_iframe_marked",
                "include_tooltip_label_kept",
                "include_tooltip_label_kept",
                "liquid_link_resolved",
            ],
            start=3,
        )
    )
    out = include_records([_with_issues(_doc("x\n"), transformations=issues)])
    assert out["dropped"] == {
        "count": 2,
        "records": [
            {
                "version": "2.0",
                "path": "docs/2.0/page.md",
                "include_in_corpus": True,
                "code": "include_latest_client_removed",
                "line": 3,
            },
            {
                "version": "2.0",
                "path": "docs/2.0/page.md",
                "include_in_corpus": True,
                "code": "include_filterbar_removed",
                "line": 4,
            },
        ],
    }
    partial = out["partial"]
    assert isinstance(partial, dict) and partial["count"] == 3
    rules = out["rules"]
    assert isinstance(rules, dict)
    assert {k: (v["class"], v["count"]) for k, v in rules.items()} == {
        "include_filterbar_removed": ("dropped", 1),
        "include_iframe_marked": ("partial", 1),
        "include_latest_client_removed": ("dropped", 1),
        "include_tooltip_label_kept": ("partial", 2),
    }
    assert MARKER not in json.dumps(out)


def test_every_section_is_deterministic_whatever_the_input_order() -> None:
    chunks = _corpus()
    docs = [
        _with_issues(
            _doc("x\n", path=f"docs/2.0/p{i}.md"),
            warnings=(Issue(code="api_dt_without_id", line=i, detail=""),),
            transformations=(Issue(code="include_iframe_marked", line=i, detail=""),),
        )
        for i in range(5)
    ]

    def render(cs: list[Chunk], ds: list[ParsedDocument]) -> str:
        return json.dumps(
            [
                oversize_chunks(cs),
                near_empty_pages(cs),
                section_path_fallbacks(cs),
                cross_version_duplicates(cs),
                token_histogram(cs),
                parse_warning_records(ds),
                include_records(ds),
            ],
            sort_keys=True,
        )

    assert render(chunks, docs) == render(chunks[::-1], docs[::-1])


def test_include_records_carry_include_in_corpus_for_excluded_pages() -> None:
    # The real case: docs_filterbar.html removed on an excluded (landing) page.
    excluded = _doc("x\n", path="docs/2.0/landing.md").model_copy(
        update={"include_in_corpus": False, "exclusion_reason": "landing page"}
    )
    filterbar = (Issue(code="include_filterbar_removed", line=5, detail=MARKER),)
    tooltip = (Issue(code="include_tooltip_label_kept", line=7, detail=MARKER),)
    out = include_records(
        [
            _with_issues(excluded, transformations=filterbar),
            _with_issues(_doc("x\n"), transformations=tooltip),
        ]
    )
    assert out["dropped"] == {
        "count": 1,
        "records": [
            {
                "version": "2.0",
                "path": "docs/2.0/landing.md",
                "include_in_corpus": False,
                "code": "include_filterbar_removed",
                "line": 5,
            }
        ],
    }
    partial = out["partial"]
    assert isinstance(partial, dict)
    assert partial["records"] == [
        {
            "version": "2.0",
            "path": "docs/2.0/page.md",
            "include_in_corpus": True,
            "code": "include_tooltip_label_kept",
            "line": 7,
        }
    ]
    rules = out["rules"]
    assert isinstance(rules, dict)
    assert rules["include_filterbar_removed"]["class"] == "dropped"  # classification unchanged
    assert MARKER not in json.dumps(out)


def _with_tokens(chunk: Chunk, tokens: int) -> Chunk:
    return chunk.model_copy(update={"token_count_approx": tokens})


@pytest.mark.parametrize("tokens", [-1, TOKEN_LIMIT])
def test_histogram_rejects_token_counts_outside_its_range(tokens: int) -> None:
    chunks = _corpus()
    bad = _with_tokens(chunks[1], tokens)
    with pytest.raises(TokenCountError) as err:
        token_histogram([chunks[0], bad, chunks[2]])
    message = str(err.value)
    assert bad.chunk_id in message and f"token_count_approx {tokens}" in message
    assert f"[0, {TOKEN_LIMIT})" in message
    assert TOKEN_LIMIT == 10**9


def test_histogram_boundary_values_zero_and_limit_minus_one() -> None:
    chunk = _corpus()[0]
    hist = token_histogram([_with_tokens(chunk, 0), _with_tokens(chunk, TOKEN_LIMIT - 1)])
    assert {str(h["tokens"]): h["chunks"] for h in hist if h["chunks"]} == {"0-31": 1, ">512": 1}
    assert TOKEN_LIMIT - 1 == 999_999_999
