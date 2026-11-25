"""RAG-M3 S4 on the real corpus: S4a section trees (T12-T17) and S4b chunker validation.

Counts are pinned to the S4 survey (design §2) and its documented S4a correction. A different
count is a finding to report, never a number to adjust. S4b checks assert rules and invariants
derived from approved figures; the new chunk totals are pinned in S4c after owner review.
Release-post text is never written into this file: post checks are structural only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus.chunk import (
    ChunkedPage,
    chunk_documents,
    reconstruction_problems,
    rule_problems,
)
from version_aware_rag.corpus.chunk_output import (
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
    ChunkingConfig,
    load_chunking_config,
    load_config,
    load_parsing_config,
)
from version_aware_rag.corpus.inspect import inspect_corpus, sections
from version_aware_rag.corpus.manifest import CorpusManifest, read_manifest
from version_aware_rag.corpus.parse import PARSER_VERSION, ParsedDocument, parse_page
from version_aware_rag.corpus.release_chunks import (
    Q7_RULES,
    RELEASE_MANIFEST,
    RELEASE_REPORT,
    ReleaseNotePage,
    apply_q7_line,
    chunk_release_notes,
    release_file_problems,
    render_release_file,
    render_release_manifest,
    render_release_report,
    verify_release_inputs,
)
from version_aware_rag.corpus.releases import body_path
from version_aware_rag.corpus.sections import (
    SectionNode,
    SectionTree,
    build_tree,
    preservation_problems,
)

pytestmark = pytest.mark.corpus

Trees = dict[str, tuple[ParsedDocument, SectionTree]]
ALL = ("1.2", "1.3", "1.4", "1.5", "2.0")


@dataclass(frozen=True)
class Corpus:
    docs: list[ParsedDocument]
    manifest: CorpusManifest
    chunking: ChunkingConfig
    chunking_sha: str


Pages = list[tuple[ParsedDocument, SectionTree, ChunkedPage]]


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    root = Path(__file__).resolve().parents[2]
    cfg, _ = load_config(root / "configs/corpus.toml")
    cache = cfg.cache_path(root)
    if not (cache / ".git").is_dir():
        pytest.skip(f"corpus cache not fetched at {cache}")
    parsing, sha = load_parsing_config(root / "configs/parsing.toml")
    chunking, chunking_sha = load_chunking_config(root / "configs/chunking.toml")
    manifest = read_manifest(root / cfg.paths.manifest)
    insp = inspect_corpus(manifest, cache, parsing, sha)
    return Corpus([p.doc for p in insp.pages], manifest, chunking, chunking_sha)


@pytest.fixture(scope="module")
def trees(corpus: Corpus) -> Trees:
    return {d.path: (d, build_tree(d, corpus.chunking)) for d in corpus.docs if d.include_in_corpus}


@pytest.fixture(scope="module")
def pages(corpus: Corpus) -> Pages:
    return chunk_documents(corpus.docs, corpus.manifest, corpus.chunking, corpus.chunking_sha)


def _group(doc: ParsedDocument) -> str:
    return "posts" if doc.source_kind == "release_post" else doc.version


def _node(tree: SectionTree, last: str) -> SectionNode:
    found = [n for n in tree.nodes() if n.path[-1] == last]
    assert len(found) == 1, (last, len(found))
    return found[0]


def test_t12_every_included_page_builds_a_tree(trees: Trees) -> None:
    assert len(trees) == 2022
    assert Counter(_group(d) for d, _ in trees.values())["posts"] == 17
    assert not any(t.section_path_fallback for _, t in trees.values())


def test_t13_empty_sections_match_s3(trees: Trees) -> None:
    records = [e for _, t in trees.values() for e in t.empty_sections()]
    assert len(records) == 193
    assert Counter(e.reason for e in records) == {
        "railroad_diagram_removed": 109,
        "no_source_content": 84,
    }
    s3 = {
        (d.path, h.line_start)
        for d, _ in trees.values()
        for h, _words, empty in sections(d.blocks)
        if empty
    }
    assert {(e.source_path, e.line_start) for e in records} == s3


def test_t13_railroad_sections_are_kept_and_accounted(trees: Trees) -> None:
    """All 205 S3 railroad removals land in exactly one tree node: 109 are the whole content of
    their section (empty heading nodes, reason recorded), 96 sit in sections with other content
    (70 headings, 26 page-root intros), which stay content sections."""
    where: Counter[str] = Counter()
    railroad_nodes = 0
    for doc, tree in trees.values():
        order = list(tree.nodes())
        removals = [
            t.line
            for t in doc.transformations
            if t.code == "html_railroad_removed" and t.line is not None
        ]
        spans = [
            (n, n.line_start, order[i + 1].line_start if i + 1 < len(order) else 10**9)
            for i, n in enumerate(order)
        ]
        for line in removals:
            holders = [n for n, a, b in spans if a <= line < b and n.kind != "page_root"]
            holder = holders[-1] if holders else tree.root
            where[f"{holder.kind}:{holder.state}"] += 1
        for n, a, b in spans:
            if n.empty_reason == "railroad_diagram_removed":
                railroad_nodes += 1
                assert n.kind == "heading" and n.state == "empty" and n.blocks == ()
                assert sum(a <= line < b for line in removals) == 1  # its own removal
                record = [e for e in tree.empty_sections() if e.section_path == n.path]
                assert [(r.line_start, r.reason) for r in record] == [
                    (n.line_start, "railroad_diagram_removed")
                ]
    assert railroad_nodes == 109
    assert where == {"heading:empty": 109, "heading:content": 70, "page_root:content": 26}
    assert sum(where.values()) == 205  # S3 report: html_railroad_removed 205


def test_t14_structural_and_content_counts(trees: Trees) -> None:
    states: Counter[str] = Counter()
    content: Counter[str] = Counter()
    for doc, tree in trees.values():
        for n in tree.nodes():
            if n.kind != "page_root":
                states[n.state] += 1
            if n.state == "content":
                content[_group(doc)] += 1
    assert states == {"content": 36846, "structural": 1660, "empty": 193}
    assert content == {
        "1.2": 5776,
        "1.3": 6680,
        "1.4": 6999,
        "1.5": 7576,
        "2.0": 11234,
        "posts": 223,
    }
    assert sum(content.values()) == 38488  # 36,846 sections + 1,642 page-root intros


def test_t15_api_entries_paths_and_h1(trees: Trees) -> None:
    reasons = Counter(
        n.api.reason
        for _, t in trees.values()
        for n in t.nodes()
        if n.api is not None and n.state == "content"
    )
    assert reasons == {
        "heading_code_span": 8519,
        "heading_signature": 18,  # S4-D8 (the original rule gave 47)
        "sphinx_dt": 2121,
        "function_table": 12,  # survey said 32; corrected in design §2 (S4a)
    }
    assert sum(reasons.values()) == 10670
    signatures = sorted(
        (t.source_path, n.heading or "")
        for _, t in trees.values()
        for n in t.nodes()
        if n.api is not None and n.api.reason == "heading_signature"
    )
    nodejs = [
        "connection.stream(sql, ...params)",
        "database.get()",
        "database.stream(sql, ...params)",
        "queryResult.asyncIterator()",
        "statement.get()",
        "statement.stream(sql, ...params)",
    ]
    assert signatures == sorted(
        (f"docs/{v}/clients/nodejs/reference.md", h) for v in ("1.2", "1.3", "1.4") for h in nodejs
    )
    former = {
        "clients/wasm/data_ingestion.md": ("httpfs (Wasm-Flavored)", ALL),
        "core_extensions/httpfs/overview.md": ("HTTP(S)", ("1.3", "1.4", "1.5", "2.0")),
        "extensions/httpfs/overview.md": ("HTTP(S)", ("1.2",)),
        "data/appender.md": ("Java (JDBC)", ("1.5", "2.0")),
        "guides/data_viewers/tableau.md": ("Server (Online)", ALL),
        "operations_manual/user_agents.md": ("Delta (and Unity Catalog)", ("1.5", "2.0")),
        "sql/expressions/in.md": ("`IN (val1, val2, ...)` (Tuple)", ALL),
        "sql/statements/update.md": ("Upsert (Insert or Update)", ALL),
    }
    prose = [
        n
        for page, (heading, versions) in former.items()
        for v in versions
        for n in trees[f"docs/{v}/{page}"][1].nodes()
        if n.heading == heading
    ]
    assert len(prose) == 29
    assert all(n.api is None and n.state == "content" for n in prose)
    odbc = [
        n
        for v in ("1.2", "1.3", "1.4", "1.5", "2.0")
        for n in trees[f"docs/{v}/guides/odbc/general.md"][1].nodes()
        if n.heading in ("Environment", "Connection", "Statement", "Descriptor")
    ]
    assert len(odbc) == 20 and all(n.api is None for n in odbc)
    for _, tree in trees.values():
        paths = Counter(n.path for n in tree.nodes())
        assert max(paths.values()) == 1
    h1 = [
        (t.source_path, n.heading)
        for _, t in trees.values()
        for n in t.nodes()
        if n.kind == "heading" and n.level == 1
    ]
    assert h1 == [
        ("docs/1.5/sql/query_syntax/unnest.md", "Calling `unnest()`"),
        ("docs/1.5/sql/query_syntax/unnest.md", "Recursive Unnesting"),
    ]


def test_t16_tree_preservation_over_the_whole_corpus(trees: Trees) -> None:
    problems = [p for doc, tree in trees.values() for p in preservation_problems(doc, tree)]
    assert problems == []


def test_t17_spot_checks_versions_posts_and_migration_guides(trees: Trees) -> None:
    # 1.2: a railroad Syntax section is an empty record, not a node with content.
    select = trees["docs/1.2/sql/query_syntax/select.md"][1]
    rail = [e for e in select.empty_sections() if e.line_start == 76]
    assert [(e.section_path[-1], e.reason) for e in rail] == [
        ("Syntax", "railroad_diagram_removed")
    ]
    # 1.3: the longest section is one content node holding its over-cap table.
    enc = _node(trees["docs/1.3/core_extensions/encodings.md"][1], "All Supported Encodings")
    assert enc.state == "content"
    assert [b.line_start for b in enc.blocks if b.kind == "table"] == [49]  # S3 report §5
    # 1.4: a function entry is an API node under the page title.
    fn = _node(trees["docs/1.4/sql/functions/map.md"][1], "`map_extract(map, key)`")
    assert fn.path[0] == "Map Functions" and fn.api is not None
    assert (fn.api.reason, fn.api.signature) == ("heading_code_span", "map_extract(map, key)")
    # 1.5: the two real H1 sections are level-1 nodes with content.
    unnest = trees["docs/1.5/sql/query_syntax/unnest.md"][1]
    assert _node(unnest, "Recursive Unnesting").level == 1
    # 2.0: Sphinx entries nest class > method under their <h2> section.
    py = trees["docs/2.0/clients/python/reference/index.md"][1]
    method = _node(py, "duckdb.Appender.append_row")
    assert method.path == (
        "Python Client API",
        "Connection",
        "duckdb.Appender",
        "duckdb.Appender.append_row",
    )
    assert method.level == 4 and method.api is not None and method.api.reason == "sphinx_dt"
    # Release posts: structure only (their text is not redistributable).
    for path in (
        "_posts/2026-06-17-announcing-duckdb-145.md",
        "_posts/2026-10-21-announcing-duckdb-200.md",
    ):
        doc, tree = trees[path]
        assert tree.source_kind == "release_post" and tree.title == doc.title
        assert sum(n.kind == "heading" for n in tree.nodes()) >= 5
        assert not any(n.api for n in tree.nodes())
    # Migration guides: chunked once later (S4b); here, complete trees.
    ops_doc, ops = trees["docs/2.0/operations_manual/migration.md"]
    assert preservation_problems(ops_doc, ops) == []
    assert sum(n.kind == "heading" for n in ops.nodes()) == 20
    assert _node(ops, "Lambda Syntax").path[:3] == (
        "Migrating to DuckDB 2.0",
        "Breaking Changes",
        "Lambda Syntax",
    )
    py_doc, py_guide = trees["docs/2.0/clients/python/migration.md"]
    assert preservation_problems(py_doc, py_guide) == []
    # No headings in the source: one page-root unit holding its paragraph and list.
    assert py_guide.root.children == () and py_guide.root.state == "content"
    assert [b.kind for b in py_guide.root.blocks] == ["paragraph", "list"]


# ---------------------------------------------------------------- S4b real-corpus validation


def _serialised(pages: Pages) -> str:
    blob = "\n".join(c.model_dump_json() for _, _, page in pages for c in page.chunks)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_s4b_every_page_reconstructs_and_follows_the_rules(corpus: Corpus, pages: Pages) -> None:
    assert len(pages) == 2022
    problems = [p for doc, tree, page in pages for p in reconstruction_problems(doc, tree, page)]
    problems += [p for _, _, page in pages for p in rule_problems(page, corpus.chunking)]
    assert problems == []


def test_s4b_ids_unique_and_output_deterministic(corpus: Corpus, pages: Pages) -> None:
    chunks = [c for _, _, page in pages for c in page.chunks]
    assert len({c.chunk_id for c in chunks}) == len(chunks)  # chunk_documents also checks
    again = chunk_documents(corpus.docs, corpus.manifest, corpus.chunking, corpus.chunking_sha)
    assert _serialised(again) == _serialised(pages)


def test_s4b_invariants_from_approved_figures(pages: Pages) -> None:
    chunks = [c for _, _, page in pages for c in page.chunks]
    api_units = {(c.source_path, c.unit_ordinal) for c in chunks if c.unit_kind == "api_entry"}
    assert len(api_units) == 10670  # S4-D8; every API entry is a unit of its own
    assert not any(c.unit_kind == "api_entry" and c.merged_section_paths for c in chunks)
    oversize = Counter(c.oversize_reason for c in chunks if c.oversize)
    assert oversize["code_block"] == 164  # S3 report §5: code blocks over 512
    assert oversize["api_entry_block"] == 4  # S3 report §5: Sphinx blocks over 512
    assert oversize["table_row"] == 0  # S3: no single row over 512
    assert set(oversize) <= {"code_block", "api_entry_block", "table_row", "list_item"}
    empties = [e for _, _, page in pages for e in page.empty_sections]
    assert Counter(e.reason for e in empties) == {
        "railroad_diagram_removed": 109,
        "no_source_content": 84,
    }
    on_chunks = sum(len(c.railroad_removal_lines) for c in chunks)
    on_empty = sum(len(e.railroad_removal_lines) for e in empties)
    assert (on_chunks, on_empty) == (96, 109)  # S4-D9: 205 records, each exactly once


def test_s4b_source_kinds_versions_and_licences(corpus: Corpus, pages: Pages) -> None:
    posts = {p.path: p for p in corpus.manifest.release_posts}
    seen_kinds: set[str] = set()
    for doc, _, page in pages:
        for c in page.chunks:
            seen_kinds.add(c.source_kind)
            if c.source_kind == "release_post":
                rec = posts[c.source_path]
                assert (c.version, c.introduced_in, c.source_url) == (
                    rec.version,
                    rec.release,
                    rec.canonical_url,
                )
                assert (c.licence, c.licence_status, c.licence_applicability) == (
                    None,
                    "unverified",
                    "uncertain",
                )
                assert c.redistributable is False and c.source_type == "changelog"
            else:
                assert c.source_path.startswith(f"docs/{c.version}/")
                assert (c.licence, c.licence_status, c.redistributable) == ("MIT", "verified", True)
                guide = c.source_kind == "migration_guide"
                assert c.introduced_in == ("2.0.0" if guide else None)
                assert c.source_type == ("changelog" if guide else "documentation")
            assert c.source_content_hash == doc.source_sha256
    assert seen_kinds == {"docs_page", "migration_guide", "release_post"}
    guides = {
        c.source_path
        for _, _, page in pages
        for c in page.chunks
        if c.source_kind == "migration_guide"
    }
    assert guides == {
        "docs/2.0/operations_manual/migration.md",
        "docs/2.0/clients/python/migration.md",
    }


# ---------------------------------------------------------------- S4c outputs (T30-T34)


def _context(corpus: Corpus) -> BuildContext:
    root = Path(__file__).resolve().parents[2]
    return BuildContext(
        source_commit=corpus.manifest.source.commit,
        parser_version=PARSER_VERSION,
        chunker_version=corpus.chunking.chunker_version,
        chunking_config_sha256=corpus.chunking_sha,
        corpus_manifest_sha256=hashlib.sha256(
            (root / "data/manifest/corpus_manifest.json").read_bytes()
        ).hexdigest(),
    )


def test_t30_pinned_corpus_counts(pages: Pages) -> None:
    counts = corpus_counts(pages)
    assert {k: counts[k] for k in counts if k != "token_percentiles"} == {
        "pages": 2022,
        "chunks": 31658,
        "by_version": {"1.2": 4757, "1.3": 5500, "1.4": 5794, "1.5": 6456, "2.0": 9151},
        "by_source_kind": {"docs_page": 31412, "migration_guide": 26, "release_post": 220},
        "by_unit_kind": {"api_entry": 10817, "prose": 15605, "prose_merged": 5236},
        "api_units": 10670,
        "merged_chunks": 5236,
        "merged_sections": 13678,
        "split_units": 661,
        "split_parts": 2273,
        "overlap_chunks": 216,
        "table_row_group_chunks": 799,
        "list_item_group_chunks": 70,
        "oversize_by_reason": {"api_entry_block": 4, "code_block": 164, "list_item": 3},
        "empty_sections_by_reason": {"no_source_content": 84, "railroad_diagram_removed": 109},
        "railroad_records": {"on_chunks": 96, "on_empty_sections": 109},
        "not_redistributable_chunks": 220,
    }
    chunks = [c for _, _, page in pages for c in page.chunks]
    assert sum(c.has_liquid_branches for c in chunks) == 22


def test_t31_generated_files_validate(corpus: Corpus, pages: Pages) -> None:
    files = render_chunk_files(pages)
    manifest = json.loads(render_chunk_manifest(pages, files, _context(corpus)))
    assert chunk_file_problems(files, manifest) == []
    assert sum(files[n].count(b"\n") for n in CHUNK_FILES if n != "empty_sections.jsonl") == 31658


def test_t32_committed_manifest_and_report_equal_a_fresh_build(
    corpus: Corpus, pages: Pages
) -> None:
    root = Path(__file__).resolve().parents[2]
    ctx = _context(corpus)
    files = render_chunk_files(pages)
    assert (root / CHUNK_MANIFEST).read_bytes() == render_chunk_manifest(pages, files, ctx)
    assert (root / CHUNK_REPORT).read_text(encoding="utf-8") == render_chunk_report(
        pages, files, ctx
    )


def test_t33_real_page_spot_checks(pages: Pages) -> None:
    by = {doc.path: page.chunks for doc, _, page in pages}
    api = {
        v: sum(c.unit_kind == "api_entry" for c in by[f"docs/{v}/sql/functions/map.md"])
        for v in ALL
    }
    assert api == {"1.2": 12, "1.3": 14, "1.4": 14, "1.5": 14, "2.0": 15}
    (extract,) = [
        c for c in by["docs/2.0/sql/functions/map.md"] if c.api_signature == "map_extract(map, key)"
    ]
    assert extract.section_path == ("Map Functions", "`map_extract(map, key)`")
    for path, groups, repeated in [
        ("docs/1.2/sql/data_types/timezones.md", 17, 16),
        ("docs/2.0/core_extensions/encodings.md", 35, 34),
    ]:
        assert sum(c.split_inside_block for c in by[path]) == groups
        assert sum(c.table_header_repeated for c in by[path]) == repeated
    python = by["docs/2.0/clients/python/reference/index.md"]
    assert len(python) == 647 and all(c.unit_kind == "api_entry" for c in python)
    assert sum(c.oversize_reason == "api_entry_block" for c in python) == 4
    unnest = by["docs/1.5/sql/query_syntax/unnest.md"]
    assert sum(c.heading_level == 1 for c in unnest) == 2
    for v in ("1.2", "1.3", "1.4"):
        node = by[f"docs/{v}/clients/nodejs/reference.md"]
        assert [c.oversize_reason for c in node if c.oversize] == ["list_item"]
    assert {c.source_kind for c in by["docs/2.0/operations_manual/migration.md"]} == {
        "migration_guide"
    }
    guide = by["docs/2.0/clients/python/migration.md"]
    assert len(guide) == 6 and sum(c.split_inside_block for c in guide) == 5
    posts = [c for path, cs in by.items() if path.startswith("_posts/") for c in cs]
    assert len(posts) == 220 and {c.unit_kind for c in posts} <= {"prose", "prose_merged"}


def test_t34_no_post_text_in_committed_outputs_and_cli(
    corpus: Corpus, capsys: pytest.CaptureFixture[str]
) -> None:
    root = Path(__file__).resolve().parents[2]
    committed = [(root / p).read_text(encoding="utf-8") for p in (CHUNK_REPORT, CHUNK_MANIFEST)]
    cache = root / "data/cache/duckdb-web-d01484462710/_posts"
    leaks = []
    for post in sorted(cache.glob("*.md")):
        for line in post.read_text(encoding="utf-8").splitlines():
            text = line.strip().lstrip("#").strip()
            skip = ("title:", "layout:", "author:", "excerpt:", "---", "{%", "```")
            if len(text) > 12 and not text.startswith(skip):
                leaks += [text for c in committed if text in c]
    assert leaks == []
    args = ["--root", str(root)]
    assert main(["corpus", "chunk-page", "docs/2.0/sql/functions/map.md", *args]) == 0
    assert "| 17 chunks |" in capsys.readouterr().out
    assert main(["corpus", "chunk-page", "docs/9.9/missing.md", *args]) == 2
    assert main(["corpus", "chunk-page", "docs/2.0/index.md", *args]) == 0
    assert "excluded from the corpus" in capsys.readouterr().out


# ---------------------------------------------------------------- S4d release notes (T35-T37)

RELEASES = ("v1.2.1", "v1.2.2", "v1.3.1", "v1.3.2")
EXPECTED_Q7 = {
    "v1.2.1": {"q7_compare_url_shortened": 1, "q7_pr_attribution_shortened": 85},
    "v1.2.2": {
        "q7_compare_url_shortened": 1,
        "q7_contributor_handle_masked": 1,
        "q7_pr_attribution_shortened": 66,
    },
    "v1.3.1": {
        "q7_compare_url_shortened": 1,
        "q7_pr_attribution_shortened": 93,
        "q7_pr_url_shortened": 1,
    },
    "v1.3.2": {
        "q7_compare_url_shortened": 1,
        "q7_contributor_handle_masked": 1,
        "q7_pr_attribution_shortened": 53,
        "q7_pr_url_shortened": 1,
    },
}


@dataclass(frozen=True)
class Releases:
    pages: list[ReleaseNotePage]
    cache: Path
    ctx: BuildContext


@pytest.fixture(scope="module")
def releases(corpus: Corpus) -> Releases:
    root = Path(__file__).resolve().parents[2]
    cfg, _ = load_config(root / "configs/corpus.toml")
    cache = root / cfg.release_notes.cache_dir
    if not all(body_path(cache, tag).is_file() for tag in RELEASES):
        pytest.skip(f"release-note cache not fetched at {cache}")
    parsing, _ = load_parsing_config(root / "configs/parsing.toml")
    behaviour: dict[str, str] = {v.version: v.behaviour_package for v in corpus.manifest.versions}
    pages = chunk_release_notes(
        cfg.release_notes,
        corpus.manifest.release_notes,
        behaviour,
        cache,
        parsing,
        corpus.chunking,
        corpus.chunking_sha,
    )
    return Releases(pages, cache, _context(corpus))


def test_t35_release_inputs_verify_and_are_processed(corpus: Corpus, releases: Releases) -> None:
    root = Path(__file__).resolve().parents[2]
    cfg, _ = load_config(root / "configs/corpus.toml")
    assert (
        verify_release_inputs(cfg.release_notes, corpus.manifest.release_notes, releases.cache)
        == []
    )
    assert [r.record.tag for r in releases.pages] == list(RELEASES)
    assert {r.record.tag: dict(r.q7_counts) for r in releases.pages} == EXPECTED_Q7
    assert {r.record.tag: len(r.page.chunks) for r in releases.pages} == {
        "v1.2.1": 6,
        "v1.2.2": 5,
        "v1.3.1": 6,
        "v1.3.2": 5,
    }
    assert all(not r.page.empty_sections for r in releases.pages)


def test_t36_q7_touches_only_its_patterns_and_raw_bodies_are_unchanged(
    releases: Releases,
) -> None:
    handle = re.compile(r"(?<![\w@/.\[])@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?![\w-])")
    for r in releases.pages:
        raw = body_path(releases.cache, r.record.tag).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == r.record.body_sha256
        handles = {m.group(0) for m in handle.finditer(raw.decode("utf-8"))}
        assert handles  # the raw bodies do name contributors
        original = parse_page(
            raw=raw,
            version=r.record.version,
            path=r.doc.path,
            source_sha256=r.record.body_sha256,
            site_base="https://duckdb.org",
            source_kind="github_release",
            url=r.record.source_url,
        )
        for before, after in zip(original.blocks, r.doc.blocks, strict=True):
            for old, new in zip(before.text.split("\n"), after.text.split("\n"), strict=True):
                expected, applied = apply_q7_line(old) if before.kind != "code" else (old, [])
                assert new == expected
                if not applied:
                    assert new == old  # unrelated text is byte-identical
        text = "\n".join(c.content for c in r.page.chunks)
        assert not any(rule.pattern.search(text) for rule in Q7_RULES[:3])
        assert not any(h in text for h in handles if h != "@[contributor]")


def test_t37_release_chunk_metadata_outputs_and_no_leaks(releases: Releases) -> None:
    root = Path(__file__).resolve().parents[2]
    for r in releases.pages:
        assert reconstruction_problems(r.doc, r.tree, r.page) == []
        rec = r.record
        for c in r.page.chunks:
            assert (c.source_kind, c.source_type, c.version, c.introduced_in) == (
                "github_release",
                "changelog",
                rec.tag[1:4],
                rec.tag[1:],
            )
            assert (c.source_path, c.release_id, c.source_commit) == (
                f"releases/tag/{rec.tag}",
                rec.release_id,
                rec.tag_commit,
            )
            assert (c.licence, c.licence_status, c.licence_applicability, c.redistributable) == (
                None,
                "unverified",
                "uncertain",
                False,
            )
            assert c.copyright is None and c.repository_licence == "MIT"
    data = render_release_file(releases.pages)
    manifest = render_release_manifest(releases.pages, data, releases.ctx)
    assert release_file_problems(data, json.loads(manifest)) == []
    assert (root / RELEASE_MANIFEST).read_bytes() == manifest
    report = render_release_report(releases.pages, data, releases.ctx)
    assert (root / RELEASE_REPORT).read_text(encoding="utf-8") == report
    lines = set()
    for tag in RELEASES:
        body = body_path(releases.cache, tag).read_text(encoding="utf-8")
        lines |= {
            ln.strip().lstrip("*#").strip() for ln in body.splitlines() if len(ln.strip()) > 25
        }
    committed = [
        (root / p).read_text(encoding="utf-8")
        for p in (RELEASE_MANIFEST, RELEASE_REPORT, CHUNK_MANIFEST, CHUNK_REPORT)
    ]
    assert [ln for ln in lines for c in committed if ln in c] == []
