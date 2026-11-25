"""S3: Markdown structure preservation and page-level parsing."""

from __future__ import annotations

import re

from version_aware_rag.corpus.config import ExcludedPage
from version_aware_rag.corpus.parse import Block, ParsedDocument, parse_page

SITE = "https://duckdb.org"


def _parse(
    body: str, title: str = "Fixture Page", path: str = "docs/1.2/x.md", **kw: object
) -> ParsedDocument:
    raw = f"---\nlayout: docu\ntitle: {title}\n---\n{body}".encode()
    return parse_page(raw=raw, version="1.2", path=path, source_sha256="s", site_base=SITE, **kw)  # type: ignore[arg-type]


def _kinds(doc: ParsedDocument) -> list[str]:
    return [b.kind for b in doc.blocks]


def _first(doc: ParsedDocument, kind: str) -> Block:
    return next(b for b in doc.blocks if b.kind == kind)


def test_heading_levels_and_source_line_numbers() -> None:
    doc = _parse("\n## Functions\n\n#### `map_extract(map, key)`\n\nReturns a value.\n")
    headings = [(b.text, b.level, b.line_start) for b in doc.blocks if b.kind == "heading"]
    # front matter is lines 1-4, body line 1 is source line 5
    assert headings == [("Functions", 2, 6), ("`map_extract(map, key)`", 4, 8)]
    assert _first(doc, "paragraph").line_start == 10
    assert doc.counts["heading_h2"] == 1 and doc.counts["heading_h4"] == 1


def test_heading_like_lines_inside_code_stay_code() -> None:
    doc = _parse("## Example\n\n```python\n# not a heading\nprint(1)\n```\n")
    assert _kinds(doc) == ["heading", "code"]
    code = _first(doc, "code")
    assert code.text == "# not a heading\nprint(1)" and code.lang == "python"


def test_tilde_fence_and_indented_code() -> None:
    doc = _parse("~~~sql\nSELECT 1;\n~~~\n\nText\n\n    indented code\n")
    codes = [b for b in doc.blocks if b.kind == "code"]
    assert [(c.text, c.lang) for c in codes] == [("SELECT 1;", "sql"), ("indented code", None)]


def test_code_fence_inside_list_is_kept_in_list_text() -> None:
    doc = _parse("1. Install:\n\n   ```bash\n   pip install duckdb\n   ```\n\n2. Run it.\n")
    lst = _first(doc, "list")
    assert "1. Install:" in lst.text and "2. Run it." in lst.text
    assert "```bash" in lst.text and "pip install duckdb" in lst.text
    assert doc.counts["code_in_container"] == 1


def test_nested_lists_keep_structure() -> None:
    doc = _parse("- a\n  - b\n    - c\n- d\n")
    assert _first(doc, "list").text == "- a\n  - b\n    - c\n- d"


def test_tables_are_normalised_with_header_and_escaped_pipes() -> None:
    doc = _parse(
        "| Name | Description |\n|:-----|-------------|\n"
        "| `a \\| b` | first |\n| [link](https://x.y) | **bold** text |\n"
    )
    table = _first(doc, "table")
    assert table.text.split("\n") == [
        "| Name | Description |",
        "| --- | --- |",
        "| `a \\| b` | first |",
        "| link | bold text |",
    ]
    assert table.meta == {"rows": "2", "columns": "2"}


def test_conditional_branches_survive_parsing_in_a_table_cell() -> None:
    doc = _parse(
        "| Client | Latest version |\n|---|---|\n"
        '| C | {% if site.maintained_duckdb_version != "" %}[{{ site.maintained_duckdb_version }}]'
        "({% link docs/2.0/installation.html %}?version=current){% else %}[1.4.5]"
        "({% link docs/2.0/installation.html %}?version=stable){% endif %} |\n"
    )
    table = _first(doc, "table")
    assert table.text.split("\n")[2] == (
        '| C | [liquid-if: site.maintained_duckdb_version != ""] [site:maintained_duckdb_version] '
        "[liquid-else: fallback written in source, hard-coded version 1.4.5] 1.4.5 [liquid-endif] |"
    )
    assert table.meta["liquid_branches"] == "true"
    assert table.meta["columns"] == "2"  # labels never split the cell
    (note,) = [t for t in doc.transformations if t.code == "liquid_if_else_both_kept"]
    assert note.line == 7  # source line of the row (front matter is lines 1-4)
    assert "hard-coded versions in else-branch: 1.4.5" in note.detail


def test_headerless_kramdown_table_is_reconstructed() -> None:
    doc = _parse(
        '#### `cardinality(map)`\n\n<div class="nostroke_table"></div>\n\n'
        "| **Description** | Return the size of the map. |\n"
        "| **Example** | `cardinality(map([4, 2], ['a', 'b']))` |\n"
        "| **Result** | `2` |\n"
    )
    table = _first(doc, "table")
    assert table.text.split("\n") == [
        "| Description | Return the size of the map. |",
        "| Example | `cardinality(map([4, 2], ['a', 'b']))` |",
        "| Result | `2` |",
    ]
    assert table.meta == {
        "rows": "3",
        "columns": "2",
        "header": "false",
        "source": "kramdown_pipe_rows",
    }
    assert "kramdown_table_reconstructed" in [t.code for t in doc.transformations]
    assert "paragraph" not in _kinds(doc)


def test_kramdown_table_with_mismatched_delimiter_keeps_its_header() -> None:
    doc = _parse("| Function | Alias | Description |\n|:---|:-|\n| `f(x)` | | Does f. |\n")
    table = _first(doc, "table")
    assert table.text.split("\n") == [
        "| Function | Alias | Description |",
        "| --- | --- | --- |",
        "| `f(x)` |  | Does f. |",
    ]
    assert table.meta["header"] == "true" and table.meta["rows"] == "1"


def test_pipes_inside_code_spans_and_escaped_pipes_stay_in_one_cell() -> None:
    doc = _parse("| **Example** | `'a' \\|\\| 'b'` and `x || y` |\n| **Note** | a \\| b |\n")
    rows = _first(doc, "table").text.split("\n")
    assert rows == ["| Example | `'a' \\|\\| 'b'` and `x \\|\\| y` |", "| Note | a \\| b |"]


def test_row_without_trailing_pipe_is_still_a_row() -> None:
    doc = _parse("| **Description** | The correlation coefficient.\n| **Formula** | `f(x)` |\n")
    assert _first(doc, "table").text.split("\n") == [
        "| Description | The correlation coefficient. |",
        "| Formula | `f(x)` |",
    ]


def test_paragraph_with_some_non_pipe_lines_stays_a_paragraph() -> None:
    doc = _parse("| looks like a row |\nbut this line is prose.\n")
    assert _kinds(doc) == ["paragraph"]


def test_kramdown_attributes_removed_only_outside_code() -> None:
    doc = _parse(
        "* `SET x = 1`{:.language-sql .highlight} sets x.\n\n{:.codebox-client}\n"
        "```python\nprint(f'{v:02d} {w:.2f}')\n```\n\nRatio {:.2f} stays in prose.\n"
    )
    assert _first(doc, "list").text == "- `SET x = 1` sets x."
    assert _first(doc, "code").text == "print(f'{v:02d} {w:.2f}')"
    assert "Ratio {:.2f} stays in prose." in [b.text for b in doc.blocks]
    rules = [t.code for t in doc.transformations]
    assert rules.count("kramdown_attribute_removed") == 2
    assert "{:.codebox-client}" not in " ".join(b.text for b in doc.blocks)


def test_blockquote_callouts() -> None:
    doc = _parse("> Warning Do not do this.\n\n> Plain quote.\n")
    quotes = [b for b in doc.blocks if b.kind == "blockquote"]
    assert [(q.text, q.meta.get("callout")) for q in quotes] == [
        ("Warning Do not do this.", "Warning"),
        ("Plain quote.", None),
    ]


def test_marker_inside_link_url_keeps_the_link_a_link() -> None:
    doc = _parse(
        '{% include install/latest_client.html client="odbc" %}'
        "* [x86_64](https://github.com/duckdb/duckdb-odbc/releases/download/"
        "v{{ latest_package }}/duckdb_odbc-linux-amd64.zip)\n"
    )
    assert _first(doc, "list").text == "- x86_64"
    assert doc.counts["link"] == 1


def test_comment_led_list_item_keeps_markdown() -> None:
    doc = _parse(
        "1. First.\n2. <!-- markdownlint-disable MD034 --> Get the [driver](https://x.y/v1/d.zip).\n"
    )
    assert _first(doc, "list").text == "1. First.\n2. Get the driver."
    assert "html_comment_removed" in [t.code for t in doc.transformations]


def test_malformed_source_link_is_kept_and_warned() -> None:
    doc = _parse("> Works with the [`httpfs` extension]({% link docs/1.2/x.md %}.\n")
    assert (
        "[`httpfs` extension](https://duckdb.org/docs/1.2/x.html." in _first(doc, "blockquote").text
    )
    assert [w.code for w in doc.warnings] == ["markdown_link_unparsed"]


def test_links_images_inline_html_and_comments() -> None:
    doc = _parse(
        "See [the docs]({% link docs/1.2/sql/x.md %}) ![diagram](d.png)<br>next "
        "<kbd>Ctrl</kbd> <!-- lint --> end.\n"
    )
    assert _first(doc, "paragraph").text == "See the docs [image: diagram]\nnext Ctrl  end."
    assert doc.counts["link"] == 1 and doc.counts["image"] == 1
    assert "html_comment_removed" in [t.code for t in doc.transformations]


def test_html_blocks_inside_markdown_pages() -> None:
    doc = _parse(
        '<!-- markdownlint-disable -->\n\n<div id="rrdiagram"></div>\n\n'
        '<div class="highlight"><pre class="highlight"><code>SELECT 42;\n</code></pre></div>\n'
    )
    assert _kinds(doc) == ["code"]
    assert _first(doc, "code").text == "SELECT 42;"
    rules = sorted(t.code for t in doc.transformations)
    assert rules == ["html_comment_removed", "html_railroad_removed"]


def test_markdown_inside_details_block_is_parsed() -> None:
    doc = _parse(
        "<details markdown='1'>\n<summary markdown='span'>\nSee the example.\n</summary>\n\n"
        "```sql\nSELECT 1;\n```\n\n</details>\n"
    )
    assert [(b.kind, b.text) for b in doc.blocks] == [
        ("html_text", "See the example."),
        ("code", "SELECT 1;"),
    ]
    assert not [w for w in doc.warnings if w.code.startswith("html_un")]


def test_markdown_attr_block_without_blank_line_is_parsed_as_markdown() -> None:
    doc = _parse(
        "<details markdown='1'>\n<summary markdown='span'>\nMore details.\n</summary>\n"
        "Uses [the `curl` backend](https://curl.se) by default.\n\nNext paragraph.\n</details>\n"
    )
    assert [(b.kind, b.text, b.line_start) for b in doc.blocks] == [
        ("html_text", "More details.", 5),
        ("paragraph", "Uses the `curl` backend by default.", 9),
        ("paragraph", "Next paragraph.", 11),
    ]
    assert "html_markdown_attr_reparsed" in [t.code for t in doc.transformations]


def test_sphinx_pages_are_routed_to_the_html_extractor() -> None:
    body = (
        '<div class="documentwrapper">\n\n<dl class="py function">\n'
        '<dt class="sig sig-object py" id="duckdb.sql">sql(query)</dt>\n\n<dd><p>Run SQL.</p>\n\n'
        "<p>Second paragraph after a blank line.</p></dd>\n</dl>\n</div>\n"
    )
    doc = _parse(body, path="docs/1.2/clients/python/reference/index.md")
    assert doc.extraction == "sphinx_html"
    entry = _first(doc, "api_entry")
    assert entry.meta["api_id"] == "duckdb.sql"
    assert entry.text == "Run SQL.\n\nSecond paragraph after a blank line."


def test_page_kinds_and_configured_exclusion() -> None:
    assert _parse("").page_kind == "empty"
    assert _parse("<!-- only a comment -->\n").page_kind == "empty"
    content = _parse("Some text.\n")
    assert content.page_kind == "content" and content.include_in_corpus
    excl = ExcludedPage(
        path="docs/1.2/index.md", kind="landing", reason="navigation link grid only"
    )
    landing = _parse("## Start\n\nText\n", path="docs/1.2/index.md", excluded={excl.path: excl})
    assert landing.page_kind == "landing" and not landing.include_in_corpus
    assert landing.exclusion_reason == "navigation link grid only"
    assert len(landing.blocks) == 2  # still parsed; only marked excluded


def test_page_metadata() -> None:
    doc = _parse("Text\n", path="docs/1.2/sql/functions/map.md")
    assert doc.url == "https://duckdb.org/docs/1.2/sql/functions/map.html"
    assert doc.title == "Fixture Page" and doc.version == "1.2" and doc.source_sha256 == "s"


def test_invalid_utf8_becomes_a_warning() -> None:
    doc = parse_page(
        raw=b"---\ntitle: X\n---\nbad \xff byte\n",
        version="1.2",
        path="docs/1.2/x.md",
        source_sha256="s",
        site_base=SITE,
    )
    assert "decode_error" in [w.code for w in doc.warnings]
    assert "bad" in _first(doc, "paragraph").text


def test_parser_output_is_deterministic() -> None:
    body = (
        "## A\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n```sql\nSELECT 1;\n```\n\n- item {{ site.v }}\n"
    )
    assert _parse(body).model_dump_json() == _parse(body).model_dump_json()


def test_no_silent_content_loss_on_a_mixed_page() -> None:
    body = (
        "Intro paragraph alpha.\n\n## Section beta\n\n- gamma item\n  - delta nested\n\n"
        "| col epsilon | col zeta |\n|---|---|\n| eta | theta |\n\n"
        "> Tip iota callout.\n\n```sql\nSELECT kappa;\n```\n\n"
        '<div class="nostroke_table"></div>\n\n'
        "Final lambda [mu link]({% link docs/1.2/x.md %}) and `nu code`.\n"
    )
    expected = {
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
    }
    words = set(re.findall(r"\w+", " ".join(b.text for b in _parse(body).blocks)))
    assert expected <= words
