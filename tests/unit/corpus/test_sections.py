"""S4a section tree (design docs/design/RAG-M3_S4_chunking.md §4; tests T1-T11)."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.corpus.config import ChunkingConfig, load_chunking_config
from version_aware_rag.corpus.parse import PARSER_VERSION, Block, ParsedDocument, parse_page
from version_aware_rag.corpus.sections import (
    SectionNode,
    SectionTree,
    build_tree,
    detect_heading_api,
    preservation_problems,
)

ROOT = Path(__file__).resolve().parents[3]
CFG, _ = load_chunking_config(ROOT / "configs/chunking.toml")


def md(body: str, title: str | None = "Page", path: str = "docs/2.0/page.md") -> ParsedDocument:
    front = f"---\ntitle: {title}\n---\n" if title is not None else "---\nlayout: docu\n---\n"
    return parse_page(
        raw=(front + body).encode(),
        version="2.0",
        path=path,
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
    )


def tree_of(doc: ParsedDocument) -> SectionTree:
    tree = build_tree(doc, CFG)
    assert preservation_problems(doc, tree) == []  # T9 on every fixture
    return tree


def by_heading(tree: SectionTree) -> dict[str, SectionNode]:
    return {n.heading: n for n in tree.nodes() if n.heading is not None}


# ---------------------------------------------------------------- T1-T4 hierarchy


def test_t1_nested_hierarchy_all_six_levels() -> None:
    body = "".join(f"{'#' * n} L{n}\n\ntext {n}\n\n" for n in range(1, 7))
    nodes = by_heading(tree_of(md(body)))
    assert nodes["L6"].path == ("Page", "L1", "L2", "L3", "L4", "L5", "L6")
    assert [nodes[f"L{n}"].level for n in range(1, 7)] == [1, 2, 3, 4, 5, 6]
    assert all(nodes[f"L{n}"].state == "content" for n in range(1, 7))


def test_t2_skipped_levels_are_kept_as_written() -> None:
    nodes = by_heading(tree_of(md("## A\n\na\n\n#### C\n\nc\n\n### B\n\nb\n")))
    assert (nodes["C"].path, nodes["C"].level) == (("Page", "A", "C"), 4)
    assert (nodes["B"].path, nodes["B"].level) == (("Page", "A", "B"), 3)  # sibling of C


def test_t3_h1_equal_to_title_is_skipped_other_h1_is_a_node() -> None:
    tree = tree_of(md("# Page\n\nintro\n\n# Other\n\no\n\n## Sub\n\ns\n"))
    assert tree.title_h1_lines == (4,)
    assert tree.root.state == "content" and tree.root.blocks[0].text == "intro"
    nodes = by_heading(tree)
    assert "Page" not in nodes
    assert (nodes["Other"].path, nodes["Other"].level) == (("Page", "Other"), 1)
    assert nodes["Sub"].path == ("Page", "Other", "Sub")


def test_t4_page_root_intro_headingless_page_and_missing_title() -> None:
    tree = tree_of(md("intro\n\n## A\n\na\n"))
    assert tree.root.path == ("Page",) and tree.root.state == "content"
    assert tree.root.line_start == 4 and len(tree.root.blocks) == 1

    flat = tree_of(md("only text\n\nmore text\n"))
    assert flat.root.children == () and len(flat.root.blocks) == 2

    untitled = tree_of(md("## A\n\na\n", title=None, path="docs/2.0/untitled.md"))
    assert untitled.section_path_fallback is True
    assert untitled.title == "docs/2.0/untitled.md"
    assert by_heading(untitled)["A"].path == ("docs/2.0/untitled.md", "A")
    assert tree_of(md("x\n")).section_path_fallback is False


# ---------------------------------------------------------------- T5 states and empty reasons


def test_t5_states_and_empty_reasons() -> None:
    body = (
        "## Parent\n\n### Child\n\nc\n\n"
        "## Empty\n\n"
        '## Rail\n\n<div id="rrdiagram"></div>\n\n'
        "## Holder\n\n### Hollow\n\n"
        "## Next\n\nn\n"
    )
    tree = tree_of(md(body))
    nodes = by_heading(tree)
    assert nodes["Parent"].state == "structural"
    assert nodes["Child"].state == "content"
    assert (nodes["Empty"].state, nodes["Empty"].empty_reason) == ("empty", "no_source_content")
    assert (nodes["Rail"].state, nodes["Rail"].empty_reason) == (
        "empty",
        "railroad_diagram_removed",
    )
    assert nodes["Holder"].state == "structural"  # only an empty subsection
    assert nodes["Hollow"].empty_reason == "no_source_content"
    assert tree.root.state == "structural"  # no intro text: not recorded, not chunked

    records = tree.empty_sections()
    assert [(r.section_path, r.reason) for r in records] == [
        (("Page", "Empty"), "no_source_content"),
        (("Page", "Rail"), "railroad_diagram_removed"),
        (("Page", "Holder", "Hollow"), "no_source_content"),  # Holder's heading accounted here
    ]
    rail = records[1]
    assert (rail.version, rail.source_kind, rail.source_path) == (
        "2.0",
        "docs_page",
        "docs/2.0/page.md",
    )
    assert rail.line_start == rail.line_end == nodes["Rail"].line_start


def test_t5_railroad_after_the_section_does_not_count() -> None:
    tree = tree_of(md('## Empty\n\n## Next\n\n<div id="rrdiagram"></div>\n\ntext\n'))
    assert by_heading(tree)["Empty"].empty_reason == "no_source_content"


def test_t5_railroad_only_section_keeps_heading_range_and_reason() -> None:
    doc = md('intro\n\n## Syntax\n\n<div id="rrdiagram"></div>\n\n## Parameters\n\np\n')
    removal = [t.line for t in doc.transformations if t.code == "html_railroad_removed"]
    assert removal == [8]  # S3 keeps the removal record with its source line
    tree = tree_of(doc)
    syntax = by_heading(tree)["Syntax"]
    assert (syntax.kind, syntax.state, syntax.empty_reason) == (
        "heading",
        "empty",
        "railroad_diagram_removed",
    )
    assert (syntax.path, syntax.level, syntax.line_start, syntax.blocks) == (
        ("Page", "Syntax"),
        2,
        6,
        (),
    )
    (record,) = tree.empty_sections()
    assert (record.section_path, record.line_start, record.reason) == (
        ("Page", "Syntax"),
        6,
        "railroad_diagram_removed",
    )
    assert by_heading(tree)["Parameters"].state == "content"  # neighbour unaffected


def test_t5_railroad_inside_a_section_with_content_keeps_the_section() -> None:
    doc = md('## Syntax\n\n<div id="rrdiagram"></div>\n\nThe clause takes a list.\n')
    tree = tree_of(doc)
    syntax = by_heading(tree)["Syntax"]
    assert (syntax.state, syntax.empty_reason) == ("content", None)
    assert [b.text for b in syntax.blocks] == ["The clause takes a list."]
    assert tree.empty_sections() == ()
    assert [t.code for t in doc.transformations] == ["html_railroad_removed"]  # still recorded


# ---------------------------------------------------------------- T6 API detection (R1)


def test_t6_markdown_api_detection() -> None:
    body = (
        "#### `abs(x)`\n\n| Description | Absolute value. |\n| Example | `abs(-1)` |\n\n"
        "#### database.get()\n\nGets a value.\n\n"
        "### Syntax for `CREATE SECRET`\n\nprose\n\n"
        "### `ST_X` Function\n\n| Description | d |\n| Example | e |\n| Result | r |\n\n"
        "### Handles\n\n| Description | d |\n| Type name | t |\n\n"
        "### Normal table\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    nodes = by_heading(tree_of(md(body)))
    api = {h: (n.api.reason, n.api.signature) if n.api else None for h, n in nodes.items()}
    assert api == {
        "`abs(x)`": ("heading_code_span", "abs(x)"),
        "database.get()": ("heading_signature", "database.get()"),
        "Syntax for `CREATE SECRET`": None,  # only partly code
        "`ST_X` Function": ("function_table", "`ST_X` Function"),
        "Handles": None,  # Description without another function-table cell
        "Normal table": None,  # a table with a header row
    }


# S4-D8: the 6 real signature headings (1.2-1.4 Node.js reference) and the 7 distinct
# false-positive headings (29 sections) that the original rule accepted.
REAL_SIGNATURES = [
    "connection.stream(sql, ...params)",
    "database.get()",
    "database.stream(sql, ...params)",
    "queryResult.asyncIterator()",
    "statement.get()",
    "statement.stream(sql, ...params)",
]
FORMER_FALSE_POSITIVES = [
    "httpfs (Wasm-Flavored)",
    "HTTP(S)",
    "Java (JDBC)",
    "Server (Online)",
    "Delta (and Unity Catalog)",
    "`IN (val1, val2, ...)` (Tuple)",
    "Upsert (Insert or Update)",
]


@pytest.mark.parametrize("heading", REAL_SIGNATURES)
def test_t6_s4d8_dotted_call_signatures_are_api_entries(heading: str) -> None:
    info = detect_heading_api(heading, [], CFG)
    assert info is not None and (info.reason, info.signature) == ("heading_signature", heading)


@pytest.mark.parametrize(
    "heading",
    [
        *FORMER_FALSE_POSITIVES,
        "count(x)",  # undotted: not a signature under S4-D8 (a code span would be)
        "database.get ()",  # space before the parenthesis
        "Overview",
        "Syntax",
        "Examples (Advanced)",
        "Version 1.2 (LTS)",
    ],
)
def test_t6_s4d8_prose_headings_are_not_api_entries(heading: str) -> None:
    assert detect_heading_api(heading, [], CFG) is None


def test_t6_s4d8_former_false_positives_are_prose_sections_in_a_tree() -> None:
    body = "".join(f"## {h}\n\nSome prose.\n\n" for h in FORMER_FALSE_POSITIVES)
    nodes = by_heading(tree_of(md(body)))
    assert {h: nodes[h].api for h in FORMER_FALSE_POSITIVES} == dict.fromkeys(
        FORMER_FALSE_POSITIVES
    )
    assert all(nodes[h].state == "content" for h in FORMER_FALSE_POSITIVES)


# ---------------------------------------------------------------- T7 Sphinx entries


def _sphinx(with_h2: bool) -> ParsedDocument:
    def entry(level: int, api_id: str, sig: str, a: int, b: int) -> Block:
        meta = {"signature": sig} | ({"api_id": api_id} if api_id else {})
        return Block(
            kind="api_entry",
            text=f"body of {sig}",
            line_start=a,
            line_end=b,
            level=level,
            meta=meta,
        )

    blocks = [Block(kind="heading", text="Connection", line_start=5, line_end=5, level=2)]
    blocks = (blocks if with_h2 else []) + [
        entry(1, "duckdb.C", "class duckdb.C", 7, 20),
        entry(2, "duckdb.C.m", "m(self) → None", 9, 12),
        entry(2, "duckdb.C.n", "n(self) → int", 14, 18),
        Block(kind="code", text="n()", line_start=15, line_end=16),
        Block(kind="html_text", text="loose text", line_start=21, line_end=21),
        entry(1, "", "class duckdb.NoId", 22, 30),
    ]
    return ParsedDocument(
        parser_version=PARSER_VERSION,
        source_kind="docs_page",
        version="1.2",
        path="docs/1.2/clients/python/reference/index.md",
        url="https://duckdb.org/docs/1.2/clients/python/reference/index.html",
        source_sha256="0" * 64,
        title="Python Client API",
        extraction="sphinx_html",
        page_kind="content",
        include_in_corpus=True,
        exclusion_reason=None,
        blocks=tuple(blocks),
        warnings=(),
        transformations=(),
        counts={},
    )


@pytest.mark.parametrize("with_h2", [True, False])
def test_t7_sphinx_entries_nest_by_depth(with_h2: bool) -> None:
    tree = tree_of(_sphinx(with_h2))
    nodes = {n.heading: n for n in tree.nodes() if n.kind == "api_entry"}
    base = ("Python Client API", "Connection") if with_h2 else ("Python Client API",)
    h = 2 if with_h2 else 0
    cls, m, n = nodes["class duckdb.C"], nodes["m(self) → None"], nodes["n(self) → int"]
    assert (cls.path, cls.level) == ((*base, "duckdb.C"), h + 1)
    assert (m.path, m.level) == ((*base, "duckdb.C", "duckdb.C.m"), h + 2)
    assert n.path == (*base, "duckdb.C", "duckdb.C.n")
    assert [c.heading for c in cls.children] == ["m(self) → None", "n(self) → int"]
    assert cls.api is not None and (cls.api.reason, cls.api.api_id) == ("sphinx_dt", "duckdb.C")
    assert [b.kind for b in n.blocks] == ["api_entry", "code"]  # code inside n's source range
    holder = by_heading(tree)["Connection"] if with_h2 else tree.root
    assert [b.text for b in holder.blocks] == ["loose text"]  # after C's range: not C's
    noid = nodes["class duckdb.NoId"]
    assert noid.path == (*base, "class duckdb.NoId") and noid.api is not None
    assert noid.api.api_id is None


# ---------------------------------------------------------------- T8-T10


def test_t8_repeated_paths_get_occurrence_numbers() -> None:
    tree = tree_of(md("## A\n\n### Syntax\n\nx\n\n## A\n\n### Syntax\n\ny\n"))
    got = [(n.path, n.occurrence) for n in tree.nodes() if n.kind == "heading"]
    assert got == [
        (("Page", "A"), 0),
        (("Page", "A", "Syntax"), 0),
        (("Page", "A"), 1),
        (("Page", "A", "Syntax"), 1),
    ]


def test_t9_preservation_detects_missing_or_reordered_blocks() -> None:
    doc = md("intro\n\n## A\n\na1\n\na2\n\n## B\n\nb\n")
    tree = build_tree(doc, CFG)
    assert preservation_problems(doc, tree) == []
    extra = Block(kind="paragraph", text="lost", line_start=99, line_end=99)
    grown = doc.model_copy(update={"blocks": (*doc.blocks, extra)})
    assert preservation_problems(grown, tree)
    swapped = list(doc.blocks)
    assert [b.text for b in swapped[2:4]] == ["a1", "a2"]
    swapped[2], swapped[3] = swapped[3], swapped[2]
    assert preservation_problems(doc.model_copy(update={"blocks": tuple(swapped)}), tree)
    heading = Block(kind="heading", text="Ghost", line_start=98, line_end=98, level=2)
    with_heading = doc.model_copy(update={"blocks": (*doc.blocks, heading)})
    assert any("heading not represented" in p for p in preservation_problems(with_heading, tree))


def test_t10_deterministic() -> None:
    body = "intro\n\n## A\n\n### `f(x)`\n\n| Description | d |\n| Example | e |\n\n## B\n\n"
    assert build_tree(md(body), CFG) == build_tree(md(body), CFG)
    assert (
        build_tree(md(body), CFG).model_dump_json() == build_tree(md(body), CFG).model_dump_json()
    )


# ---------------------------------------------------------------- T11 configuration


def _raw() -> dict[str, Any]:
    return tomllib.loads((ROOT / "configs/chunking.toml").read_text(encoding="utf-8"))


def test_t11_production_config_loads() -> None:
    assert (CFG.target_tokens, CFG.hard_limit_tokens, CFG.merge_below_tokens) == (350, 512, 64)
    assert CFG.overlap_ratio == 0.15 and CFG.boundary_levels == (1, 2, 3, 4, 5, 6)
    assert CFG.id_hex_chars == 20 and CFG.chunker_version == "s4-1"
    assert sorted(g.path for g in CFG.migration_guides) == [
        "docs/2.0/clients/python/migration.md",
        "docs/2.0/operations_manual/migration.md",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"merge_below_tokens": 350},
        {"target_tokens": 600},
        {"overlap_ratio": 1.5},
        {"boundary_levels": [2, 3, 4, 5, 6]},
        {"id_hex_chars": 16},
        {"chunker_version": "v1"},
        {"unknown_key": 1},
        {
            "oversize": {
                "table": "split_between_rows_repeat_header",
                "code": "truncate",
                "list": "split_between_top_level_items",
                "api_entry_block": "keep_whole_flag",
            }
        },
        {
            "migration_guides": [
                {"path": "docs/2.0/operations_manual/migration.md", "introduced_in": "2.0.0"}
            ]
        },
        {
            "migration_guides": [
                {"path": "docs/2.0/operations_manual/migration.md", "introduced_in": "1.5.0"},
                {"path": "docs/2.0/clients/python/migration.md", "introduced_in": "2.0.0"},
            ]
        },
    ],
)
def test_t11_invalid_config_is_rejected(change: dict[str, Any]) -> None:
    raw = copy.deepcopy(_raw()) | change
    with pytest.raises(ValidationError):
        ChunkingConfig.model_validate(raw)
