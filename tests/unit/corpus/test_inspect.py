"""S3: inspection helpers and report rendering on fixture pages."""

from __future__ import annotations

from version_aware_rag.corpus.chunk import approx_tokens  # moved from inspect in S4b
from version_aware_rag.corpus.config import ParsingConfig, SamplePage
from version_aware_rag.corpus.inspect import (
    CorpusInspection,
    PageResult,
    output_words,
    ratio_bucket,
    render_inspection_report,
    render_samples,
    sections,
    visible_word_estimate,
)
from version_aware_rag.corpus.parse import parse_page

SITE = "https://duckdb.org"


def _page(path: str, body: str) -> PageResult:
    source = f"---\nlayout: docu\ntitle: T\n---\n{body}"
    doc = parse_page(
        raw=source.encode(),
        version=path.split("/")[1],
        path=path,
        source_sha256="s",
        site_base=SITE,
    )
    est = visible_word_estimate(source)
    return PageResult(doc, source, output_words(doc) / est if est else 1.0)


def test_approx_tokens_and_buckets() -> None:
    assert approx_tokens("SELECT a, b FROM t;") == 7  # SELECT a , b FROM t ;
    assert [ratio_bucket(r) for r in (0.5, 0.9, 1.0, 1.2)] == [
        "<0.80",
        "0.80-0.95",
        "0.95-1.05",
        ">1.05",
    ]


def test_visible_estimate_ignores_markup_and_character_references() -> None:
    src = (
        "---\ntitle: X\n---\n"
        "<p>Hello &#182; <b>world</b></p> [link](https://a.b/c) {% link x %} {{ y }}\n"
    )
    assert visible_word_estimate(src) == 3  # Hello, world, link


def test_empty_sections_are_detected() -> None:
    page = _page(
        "docs/1.2/x.md",
        '## Syntax\n\n<div id="rrdiagram"></div>\n\n## Parent\n\n### Child\n\nText\n',
    )
    flags = [(h.text, empty) for h, _, empty in sections(page.doc.blocks)]
    assert flags == [("Syntax", True), ("Parent", False), ("Child", False)]


def test_reports_are_deterministic_and_contain_sections() -> None:
    pages = [
        _page("docs/1.2/a.md", "## A\n\nText {{ site.maintained_duckdb_version }}.\n"),
        _page("docs/2.0/b.md", "| x |\n|---|\n| 1 |\n\n```sql\nSELECT 1;\n```\n"),
    ]
    insp = CorpusInspection(commit="c" * 40, parsing_config_sha256="0" * 64, pages=pages)
    first = render_inspection_report(insp)
    assert first == render_inspection_report(insp)
    for heading in (
        "## 1. Pages",
        "## 2. Construct counts",
        "## 5. Blocks over 512",
        "## 6. Transformations applied",
        "## 10. Content-coverage check",
    ):
        assert heading in first
    assert "| site.maintained_duckdb_version | 1 |" in first
    parsing = ParsingConfig(
        site_base=SITE,
        excluded_pages=(),
        samples=(SamplePage(path="docs/1.2/a.md", label="Fixture", lines=(1, 7)),),
    )
    samples = render_samples(insp, parsing)
    assert samples == render_samples(insp, parsing)
    assert "## 1. Fixture" in samples and "[site:maintained_duckdb_version]" in samples
    assert "5  ## A" in samples  # source excerpt keeps original line numbers
