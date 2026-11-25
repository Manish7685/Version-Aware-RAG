"""S3 checks on the real pinned DuckDB snapshot. Skipped unless the cache has been fetched."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import pytest

from version_aware_rag.corpus.config import load_config, load_parsing_config
from version_aware_rag.corpus.inspect import (
    CorpusInspection,
    inspect_corpus,
    render_inspection_report,
    render_samples,
)
from version_aware_rag.corpus.manifest import read_manifest
from version_aware_rag.corpus.parse import split_front_matter

pytestmark = pytest.mark.corpus

EXPECTED_WARNINGS = {
    "api_dt_without_id",  # real Sphinx properties without an id attribute (1.4, 1.5)
    "html_block_generic_text",  # <dl>/<div>/<details> text kept as html_text
    "liquid_for_loop_removed",  # docs/2.0/guides/overview.md (excluded page)
    "liquid_tag_unsupported",  # its {% assign %} over site.pages
    "markdown_link_unparsed",  # upstream typo: link without ")" in 1.2/1.3 aws.md
}


@pytest.fixture(scope="module")
def inspection() -> CorpusInspection:
    root = Path(__file__).resolve().parents[2]
    cfg, _ = load_config(root / "configs/corpus.toml")
    cache = cfg.cache_path(root)
    if not (cache / ".git").is_dir():
        pytest.skip(f"corpus cache not fetched at {cache}")
    parsing, sha = load_parsing_config(root / "configs/parsing.toml")
    return inspect_corpus(read_manifest(root / cfg.paths.manifest), cache, parsing, sha)


def test_every_page_parses_with_only_expected_warnings(inspection: CorpusInspection) -> None:
    assert len(inspection.pages) == 2031 + 17  # docs pages + release posts
    codes = {w.code for p in inspection.pages for w in p.doc.warnings}
    assert codes <= EXPECTED_WARNINGS, codes - EXPECTED_WARNINGS


def test_no_liquid_left_outside_code(inspection: CorpusInspection) -> None:
    for p in inspection.pages:
        for b in p.doc.blocks:
            assert "{%" not in b.text, (p.doc.path, b.line_start)
            if "{{" in b.text:
                # Only the Go composite literals preserved from {% raw %} blocks.
                assert b.kind == "code" or "```" in b.text, (p.doc.path, b.line_start, b.kind)


def test_site_markers_are_recorded_and_links_stay_links(inspection: CorpusInspection) -> None:
    """Every [site:...] marker comes from a recorded transformation; markers placed inside link
    URLs leave with the URL (link targets are not content) and never break the link, so no raw
    Markdown link syntax may survive in any non-code block."""
    markers = sum(
        len(re.findall(r"\[site:[^\]\s]+\]", b.text))
        for p in inspection.pages
        for b in p.doc.blocks
    )
    recorded = sum(
        t.code == "site_variable_neutralised"
        for p in inspection.pages
        for t in p.doc.transformations
    )
    assert recorded == 204
    assert 0 < markers <= recorded
    fenced = re.compile(r"```.*?```", re.S)  # code nested in lists/API entries may show links
    leaks = {
        (p.doc.path, b.line_start)
        for p in inspection.pages
        for b in p.doc.blocks
        if b.kind != "code" and re.search(r"\]\(https?://", fenced.sub("", b.text))
    }
    warned = {
        (p.doc.path, w.line)
        for p in inspection.pages
        for w in p.doc.warnings
        if w.code == "markdown_link_unparsed"
    }
    # The only remaining raw links are malformed in the source itself, and each is warned.
    assert (
        leaks
        == warned
        == {
            ("docs/1.2/extensions/aws.md", 19),
            ("docs/1.3/core_extensions/aws.md", 19),
        }
    )


def test_no_pipe_table_left_as_a_paragraph(inspection: CorpusInspection) -> None:
    """Kramdown renders |-delimited rows as tables; none may remain flattened into prose."""
    flattened = [
        (p.doc.path, b.line_start)
        for p in inspection.pages
        for b in p.doc.blocks
        if b.kind == "paragraph" and b.text.startswith("|")
    ]
    assert flattened == []
    reconstructed = sum(
        t.code == "kramdown_table_reconstructed"
        for p in inspection.pages
        for t in p.doc.transformations
    )
    assert reconstructed > 2900


def test_api_reference_entries_match_source_signatures(inspection: CorpusInspection) -> None:
    for p in inspection.pages:
        if p.doc.extraction != "sphinx_html":
            continue
        with_id = len(re.findall(r'<dt class="sig sig-object py" id=', p.source))
        without_id = len(re.findall(r'<dt class="sig sig-object py">', p.source))
        entries = [b for b in p.doc.blocks if b.kind == "api_entry"]
        assert len(entries) == with_id + without_id, p.doc.path
    assert sum(p.doc.extraction == "sphinx_html" for p in inspection.pages) == 5


def test_release_posts_are_parsed_as_a_separate_source(inspection: CorpusInspection) -> None:
    posts = [p for p in inspection.pages if p.doc.source_kind == "release_post"]
    docs = [p for p in inspection.pages if p.doc.source_kind == "docs_page"]
    assert len(posts) == 17 and len(docs) == 2031
    assert all(p.doc.path.startswith("_posts/") for p in posts)
    assert not any(p.doc.path.startswith("_posts/") for p in docs)
    for p in posts:
        assert p.doc.page_kind == "content" and p.doc.include_in_corpus
        assert p.doc.url.startswith("https://duckdb.org/20"), p.doc.url
        assert p.doc.title is not None


def test_release_post_titles_name_their_configured_release() -> None:
    root = Path(__file__).resolve().parents[2]
    cfg, _ = load_config(root / "configs/corpus.toml")
    manifest = read_manifest(root / cfg.paths.manifest)
    assert [p.release for p in manifest.release_posts] == [
        p.release for p in cfg.release_posts.posts
    ]
    cache = cfg.cache_path(root)
    if not (cache / "_posts").is_dir():
        pytest.skip("docs cache not fetched")
    for post in manifest.release_posts:
        # Schema 5: titles are restricted text; only their sha256 is recorded. Compare as
        # booleans so that no title can reach test output.
        title = split_front_matter((cache / post.path).read_text(encoding="utf-8")).title
        hash_ok = (
            title is not None and post.title_sha256 == hashlib.sha256(title.encode()).hexdigest()
        )
        names_release = title is not None and post.release in title
        assert hash_ok and names_release, post.path
        assert post.authors == ("The DuckDB team",)


def test_conditionals_keep_both_branches_with_versions(inspection: CorpusInspection) -> None:
    kept = [
        t
        for p in inspection.pages
        for t in p.doc.transformations
        if t.code == "liquid_if_else_both_kept"
    ]
    assert len(kept) == 35
    assert all(not t.detail.endswith("versions in else-branch: none") for t in kept)
    labelled = sum(b.text.count("[liquid-if") for p in inspection.pages for b in p.doc.blocks)
    in_urls = sum(
        t.code == "liquid_if_in_link_url" for p in inspection.pages for t in p.doc.transformations
    )
    assert labelled + in_urls == 35  # visible in content, or (in link URLs) in the record


def test_exclusions_are_exactly_the_reviewed_pages(inspection: CorpusInspection) -> None:
    kinds = Counter(p.doc.page_kind for p in inspection.pages if not p.doc.include_in_corpus)
    assert kinds == {"landing": 6, "empty": 20}


def test_no_content_page_below_coverage_threshold(inspection: CorpusInspection) -> None:
    low = [
        (p.doc.path, round(p.coverage, 3))
        for p in inspection.pages
        if p.doc.page_kind == "content" and p.coverage < 0.8
    ]
    assert low == []


def test_committed_reports_equal_a_fresh_regeneration(inspection: CorpusInspection) -> None:
    root = Path(__file__).resolve().parents[2]
    parsing, _ = load_parsing_config(root / "configs/parsing.toml")
    report = (root / "docs/reports/RAG-M3_S3_source_inspection.md").read_text(encoding="utf-8")
    samples = (root / "docs/reports/RAG-M3_S3_samples.md").read_text(encoding="utf-8")
    assert report == render_inspection_report(inspection)
    assert samples == render_samples(inspection, parsing)
