"""S3: HTML block classification and Sphinx API extraction (stdlib html.parser)."""

from __future__ import annotations

from version_aware_rag.corpus.html_extract import (
    classify_html_block,
    extract_sphinx_api,
    is_sphinx_api_page,
)

# ---------------------------------------------------------------- HTML blocks in Markdown pages


def test_comment_wrapper_and_railroad_blocks_are_dropped() -> None:
    assert (
        classify_html_block("<!-- markdownlint-disable MD034 -->", 1, 1).classification == "comment"
    )
    assert (
        classify_html_block('<div class="nostroke_table"></div>', 1, 1).classification == "wrapper"
    )
    assert classify_html_block("</div>", 1, 1).classification == "wrapper"
    res = classify_html_block('<div id="rrdiagram"></div>', 1, 1)
    assert res.classification == "railroad" and res.block is None


def test_pre_rendered_code_is_kept_verbatim_with_language() -> None:
    html = (
        '<div class="language-c highlighter-rouge"><div class="highlight"><pre class="highlight">'
        '<code><span class="kt">duckdb_state</span> <span class="nf">f</span>(\n'
        '    <span class="kt">int</span> x &amp;&amp; y);\n</code></pre></div></div>'
    )
    res = classify_html_block(html, 52, 60)
    assert res.classification == "code" and res.block is not None
    assert res.block.lang == "c"
    assert res.block.text == "duckdb_state f(\n    int x && y);"
    assert (res.block.line_start, res.block.line_end) == (52, 60)


def test_navigation_labels_from_sibling_spans() -> None:
    html = (
        '<div class="box-link-wrapper">\n  <div class="box-link half-width">\n'
        '    <a href="https://duckdb.org/docs/1.2/connect/overview.html"></a>\n'
        '    <span class="symbol"><svg class="icon"><use href="#link-01"></use></svg></span>\n'
        "    <span>DuckDB connection overview</span>\n  </div>\n</div>"
    )
    res = classify_html_block(html, 9, 16)
    assert res.classification == "navigation" and res.block is not None
    assert res.block.text == "DuckDB connection overview"


def test_generic_html_keeps_visible_text() -> None:
    res = classify_html_block("<dl>\n<dt><a href='#x'>duckdb</a></dt>\n<dd></dd>\n</dl>", 8, 11)
    assert res.classification == "generic" and res.block is not None
    assert res.block.text == "duckdb" and res.block.meta["first_tag"] == "dl"


def test_malformed_inline_html_warns_but_keeps_text() -> None:
    res = classify_html_block("<p>bold <b>text</p> tail</span>", 3, 3)
    codes = sorted(w.code for w in res.warnings)
    assert codes == ["html_unclosed_tag", "html_unexpected_end_tag"]
    assert res.block is not None and "bold text" in res.block.text and "tail" in res.block.text


def test_containers_split_across_blocks_are_not_flagged() -> None:
    opening = classify_html_block(
        "<details markdown='1'>\n<summary markdown='span'>\n"
        "Click here to see the full example.\n</summary>",
        49,
        52,
    )
    assert opening.warnings == []
    assert opening.block is not None and opening.block.text == "Click here to see the full example."
    closing = classify_html_block("</details>", 70, 70)
    assert closing.warnings == [] and closing.classification == "wrapper"


# ---------------------------------------------------------------- Sphinx API pages

# One physical line in the real pages; built by concatenation to respect the line-length limit.
EXECUTE_SIG = (
    '<span class="sig-name descname"><span class="pre">execute</span></span>'
    '<span class="sig-paren">(</span><em>query</em><span class="sig-paren">)</span>'
    ' &#8594; Conn<a class="headerlink" href="#x">&#182;</a>'
)

SPHINX = """<div class="documentwrapper">
<div class="body" role="main">
<section id="connection">
<h2>Connection<a class="headerlink" href="#connection" title="Link">&#182;</a></h2>
<dl class="py class">
<dt class="sig sig-object py" id="duckdb.Conn">
<span class="sig-name descname"><span class="pre">Conn</span></span>
</dt>
<dd>
<p>Bases: <code>object</code></p>
<dl class="py method">
<dt class="sig sig-object py" id="duckdb.Conn.execute">
EXECUTE_SIG
</dt>
<dd>
<p>Run a query.</p>
<dl class="simple">
<dt>Parameters:</dt>
<dd><p>query: SQL text.</p></dd>
</dl>
<pre>con.execute("SELECT 42")
    .fetchall()</pre>
</dd>
</dl>
<dl class="py property">
<dt class="sig sig-object py">
<span class="pre">Conn.name</span> <span class="pre">-&gt;</span> <span class="pre">str</span>
</dt>
<dd></dd>
</dl>
</dd>
</dl>
</section>
</div>
</div>
""".replace("EXECUTE_SIG", EXECUTE_SIG)


def test_sphinx_page_detection() -> None:
    assert is_sphinx_api_page("\n" + SPHINX)
    assert not is_sphinx_api_page("# Heading\n<div class='documentwrapper'>")


def test_sphinx_entries_headings_and_nesting() -> None:
    blocks, warnings = extract_sphinx_api(SPHINX, line_offset=10)
    kinds = [(b.kind, b.meta.get("api_id"), b.level) for b in blocks]
    assert kinds == [
        ("heading", None, 2),
        ("api_entry", "duckdb.Conn", 1),
        ("api_entry", "duckdb.Conn.execute", 2),
        ("api_entry", "", 2),
    ]
    heading, cls, method, prop = blocks
    assert heading.text == "Connection"  # permalink pilcrow removed
    assert heading.line_start == 14  # source line 4 + offset 10
    assert cls.text == "Bases: object"  # child entries are not copied into the parent
    assert method.meta["signature"] == "execute(query) → Conn"
    assert "Run a query." in method.text
    assert "Parameters:" in method.text and "query: SQL text." in method.text  # field list as text
    assert '```\ncon.execute("SELECT 42")\n    .fetchall()\n```' in method.text  # <pre> verbatim
    assert prop.meta["signature"] == "Conn.name -> str" and prop.text == ""
    assert [w.code for w in warnings] == ["api_dt_without_id"]


def test_sphinx_broken_html_does_not_crash_or_drop_text() -> None:
    broken = (
        '<div class="documentwrapper">\n<dl class="py function">\n'
        '<dt class="sig sig-object py" id="duckdb.f">f()</dt>\n<dd><p>Unclosed description\n'
        "loose text outside\n"
    )
    blocks, warnings = extract_sphinx_api(broken, line_offset=0)
    codes = {w.code for w in warnings}
    assert "api_entry_unclosed" in codes
    entry = next(b for b in blocks if b.kind == "api_entry")
    assert entry.meta["api_id"] == "duckdb.f"
    assert "Unclosed description" in entry.text and "loose text outside" in entry.text


def test_sphinx_text_outside_entries_is_kept_and_warned() -> None:
    html = '<div class="documentwrapper">\n<p>Module docstring text.</p>\n</div>'
    blocks, warnings = extract_sphinx_api(html, line_offset=0)
    assert [(b.kind, b.text) for b in blocks] == [("html_text", "Module docstring text.")]
    assert [w.code for w in warnings] == ["html_text_outside_api_entry"]
