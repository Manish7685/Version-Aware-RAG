"""S3: strict front matter and Liquid template handling."""

from __future__ import annotations

import re

import pytest

from version_aware_rag.corpus.liquid import link_url, render_liquid
from version_aware_rag.corpus.parse import split_front_matter

SITE = "https://duckdb.org"


def _render(text: str) -> tuple[str, list[str], list[str]]:
    r = render_liquid(text, SITE)
    assert r.text.count("\n") == text.count("\n"), "line count must be preserved"
    return r.text, [n.rule for n in r.notes], [w.rule for w in r.warnings]


# ---------------------------------------------------------------- front matter


def test_plain_title_and_body_offset() -> None:
    fm = split_front_matter("---\nlayout: docu\ntitle: Map Functions\n---\n\nBody\n")
    assert fm.title == "Map Functions"
    assert fm.body == "\nBody\n"
    assert fm.body_line_offset == 4
    assert fm.warnings == ()


def test_single_quoted_title_with_yaml_escape() -> None:
    assert split_front_matter("---\ntitle: 'It''s here'\n---\n").title == "It's here"


@pytest.mark.parametrize(
    "line",
    [
        'title: "Esc\\"aped"',
        'title: "open',
        "title: a: b",
        "title: x #comment",
        "title: |",
        "title: 'unterminated",
    ],
)
def test_unsupported_titles_are_flagged_not_guessed(line: str) -> None:
    fm = split_front_matter(f"---\n{line}\n---\nBody\n")
    assert fm.title is None
    assert [w.code for w in fm.warnings] == ["front_matter_title_unsupported"]


def test_escape_free_double_quoted_title() -> None:
    fm = split_front_matter(
        '---\ntitle: "Announcing DuckDB 2.0.0 LTS \u201cCinnamon Teal\u201d"\n---\n'
    )
    assert fm.title == "Announcing DuckDB 2.0.0 LTS \u201cCinnamon Teal\u201d"
    assert fm.warnings == ()


def test_nested_title_key_is_not_the_title() -> None:
    fm = split_front_matter("---\nmeta:\n  title: nested\ntitle: Real\n---\n")
    assert fm.title == "Real"


def test_missing_and_duplicate_title() -> None:
    assert (
        split_front_matter("---\nlayout: x\n---\n").warnings[0].code == "front_matter_title_count"
    )
    dup = split_front_matter("---\ntitle: a\ntitle: b\n---\n")
    assert dup.title is None and dup.warnings[0].code == "front_matter_title_count"


def test_no_front_matter() -> None:
    fm = split_front_matter("# Heading\n")
    assert fm.title is None and fm.body == "# Heading\n" and fm.body_line_offset == 0
    assert fm.warnings[0].code == "front_matter_missing"


def test_front_matter_closed_at_end_of_file_without_newline() -> None:
    fm = split_front_matter("---\nlayout: docu\ntitle: File Formats\n---")
    assert fm.title == "File Formats" and fm.body == "" and fm.warnings == ()


def test_unterminated_front_matter() -> None:
    fm = split_front_matter("---\ntitle: x\nno end\n")
    assert fm.warnings[0].code == "front_matter_unterminated"


# ---------------------------------------------------------------- site variables


def test_site_variables_become_neutral_markers() -> None:
    text, rules, _ = _render(
        "Version {{ site.maintained_duckdb_version }} and {{site.latest_duckdb_hash}}."
    )
    assert text == "Version [site:maintained_duckdb_version] and [site:latest_duckdb_hash]."
    assert rules == ["site_variable_neutralised", "site_variable_neutralised"]


def test_no_current_version_contamination() -> None:
    """The renderer never reads site data, so no version number can be introduced."""
    source = " ".join(
        f"{{{{ site.{name} }}}}"
        for name in (
            "maintained_duckdb_version",
            "latest_duckdb_version",
            "maintained_duckdb_odbc_version",
        )
    )
    source += ' {% include install/latest_client.html client="go" %}{{ latest_version }}'
    text, _, _ = _render(source)
    assert not re.search(r"\d", text), text


def test_latest_client_include_drives_markers() -> None:
    text, rules, warns = _render(
        '{% include install/latest_client.html client="odbc" %}'
        "v{{ latest_package }} / {{ latest_release }}"
    )
    assert text == "v[site:latest_package:odbc] / [site:latest_release:odbc]"
    assert rules[0] == "include_latest_client_removed" and warns == []


def test_latest_variable_without_client_is_warned() -> None:
    text, _, warns = _render("{{ latest_version }}")
    assert text == "[site:latest_version]" and warns == ["latest_variable_without_client"]


def test_assign_alias_of_latest_variable() -> None:
    text, rules, _ = _render(
        '{% include install/latest_client.html client="java" %}'
        "{% assign jdbc_version = latest_package %}jar {{ jdbc_version }}"
    )
    assert text == "jar [site:latest_package:java]"
    assert "liquid_assign_alias" in rules


def test_other_template_variable_is_marked_and_warned() -> None:
    text, _, warns = _render("{{ topic }} and {{ guide.title | escape }}")
    assert text == "[template:topic] and [template:guide.title]"
    assert warns == [
        "template_variable_unresolved",
        "template_variable_unresolved",
        "liquid_filter_ignored",
    ]


# ---------------------------------------------------------------- conditionals (S3-D1, option B)

REAL_ROW = (
    '| {% if site.maintained_duckdb_version != "" %}[{{ site.maintained_duckdb_version }}]'
    "({% link docs/2.0/installation.html %}?version=current){% else %}[1.4.5]"
    "({% link docs/2.0/installation.html %}?version=stable){% endif %} |"
)


def test_both_branches_survive_and_are_labelled() -> None:
    r = render_liquid(REAL_ROW, SITE)
    assert r.text == (
        '| [liquid-if: site.maintained_duckdb_version != ""] '
        "[[site:maintained_duckdb_version]](https://duckdb.org/docs/2.0/installation.html?version=current)"
        " [liquid-else: fallback written in source, hard-coded version 1.4.5] "
        "[1.4.5](https://duckdb.org/docs/2.0/installation.html?version=stable) [liquid-endif] |"
    )


def test_branch_record_keeps_raw_source_of_both_branches_and_the_version() -> None:
    r = render_liquid(REAL_ROW, SITE)
    (note,) = [n for n in r.notes if n.rule == "liquid_if_else_both_kept"]
    assert note.detail.startswith('if site.maintained_duckdb_version != "" | ')
    assert (
        "if-branch: '[{{ site.maintained_duckdb_version }}]({% link docs/2.0/installation.html %}"
        in note.detail
    )
    assert (
        "else-branch: '[1.4.5]({% link docs/2.0/installation.html %}?version=stable)'"
        in note.detail
    )
    assert note.detail.endswith("hard-coded versions in else-branch: 1.4.5")


def test_branches_never_render_current_site_values() -> None:
    text, _, _ = _render(REAL_ROW)
    if_branch = text.split("[liquid-else:")[0]
    visible = re.sub(r"\]\([^)]*\)", "]", if_branch)  # link targets (docs/2.0/...) are paths
    assert not re.search(r"\d", visible), visible  # the if-branch holds only neutral markers
    assert "1.5.6" not in text and "2.0.1" not in text


def test_fallback_without_version_and_multiple_versions() -> None:
    plain = _render("{% if site.x %}A{% else %}see the release notes{% endif %}")[0]
    assert (
        plain == "[liquid-if: site.x] A [liquid-else: fallback written in source] "
        "see the release notes [liquid-endif]"
    )
    multi = _render("{% if site.x %}A{% else %}1.4.5.0 or 1.4.4{% endif %}")[0]
    assert "[liquid-else: fallback written in source, hard-coded version 1.4.4, 1.4.5.0]" in multi


def test_if_without_else_is_labelled_not_unwrapped() -> None:
    text, rules, _ = _render("{% if site.x %}kept{% endif %}")
    assert text == "[liquid-if: site.x] kept [liquid-endif]" and rules == ["liquid_if_labelled"]


def test_unless_and_pipe_in_condition_are_label_safe() -> None:
    text = _render("{% unless a | b %}X{% endunless %}")[0]
    assert text == "[liquid-unless: a \\| b] X [liquid-endif]"


def test_multiline_branches_keep_line_numbers() -> None:
    text, _, _ = _render("{% if a %}\nA\n{% else %}\nB1\nB2 1.4.5\n{% endif %}\nafter")
    assert text.count("\n") == 6 and "B1\nB2 1.4.5" in text and "hard-coded version 1.4.5" in text


def test_conditional_inside_a_link_url_uses_space_free_labels() -> None:
    r = render_liquid(
        "* [x86_64](https://github.com/duckdb/duckdb-odbc/releases/download/v"
        '{% if site.maintained_duckdb_odbc_version != "" %}'
        "{{ site.maintained_duckdb_odbc_version }}"
        "{% else %}1.4.5.0{% endif %}/duckdb_odbc-linux-amd64.zip)",
        SITE,
    )
    url = r.text[r.text.index("](") + 2 : -1]
    assert " " not in url  # the Markdown link stays valid
    assert url == (
        "https://github.com/duckdb/duckdb-odbc/releases/download/v"
        '[liquid-if:site.maintained_duckdb_odbc_version-!=-""][site:maintained_duckdb_odbc_version]'
        "[liquid-else:fallback-written-in-source,hard-coded-version-1.4.5.0]1.4.5.0[liquid-endif]"
        "/duckdb_odbc-linux-amd64.zip"
    )
    rules = [n.rule for n in r.notes]
    assert "liquid_if_in_link_url" in rules and "liquid_if_else_both_kept" in rules
    (note,) = [n for n in r.notes if n.rule == "liquid_if_else_both_kept"]
    assert "else-branch: '1.4.5.0'" in note.detail


def test_conditional_inside_a_for_loop_stays_removed() -> None:
    text, rules, _ = _render("{% for g in x %}{% if g.a %}A{% else %}B{% endif %}{% endfor %}")
    assert text == "" and "liquid_if_else_both_kept" not in rules


def test_unbalanced_and_unclosed_conditionals_are_warned() -> None:
    assert _render("text {% endif %}")[2] == ["liquid_unbalanced"]
    assert _render("{% if x %}never closed")[2] == ["liquid_unclosed"]


# ---------------------------------------------------------------- raw, links, includes, loops


def test_raw_content_is_verbatim_and_not_interpreted() -> None:
    src = (
        '{% raw %}\nreturn []duckdb.ColumnInfo{{Name: "result", T: t}}\n'
        "{{ site.x }} {% link docs/2.0/x.md %}\n{% endraw %}"
    )
    text, rules, warns = _render(src)
    assert '{{Name: "result", T: t}}' in text
    assert "{{ site.x }} {% link docs/2.0/x.md %}" in text
    assert rules == ["liquid_raw_unwrapped"] and warns == []


def test_inline_raw_inside_code_line() -> None:
    text, _, _ = _render("    return x{% raw %}{{Name: t}}{% endraw %}")
    assert text == "    return x{{Name: t}}"


def test_link_and_post_url_resolution() -> None:
    text, rules, _ = _render(
        "[a]({% link docs/1.2/sql/x.md %}) [b]({% link docs/2.0/installation.html %}?e=c) "
        "[c]({% post_url 2021-12-03-duck-arrow %})"
    )
    assert "(https://duckdb.org/docs/1.2/sql/x.html)" in text
    assert "(https://duckdb.org/docs/2.0/installation.html?e=c)" in text
    assert "(https://duckdb.org/2021/12/03/duck-arrow.html)" in text
    assert rules == ["liquid_link_resolved", "liquid_link_resolved", "liquid_post_url_resolved"]


def test_malformed_post_url_is_warned() -> None:
    assert _render("{% post_url not-a-post %}")[2] == ["liquid_post_url_malformed"]


def test_link_url_mapping() -> None:
    assert link_url("docs/2.0/index.md", SITE) == "https://duckdb.org/docs/2.0/index.html"
    assert link_url("images/icons/cli.svg", SITE) == "https://duckdb.org/images/icons/cli.svg"


def test_includes() -> None:
    text, rules, warns = _render(
        '{% include tooltip.html label="Primary" id="support_tier_primary" %} | '
        '{% include iframe.html src="https://shell.duckdb.org" %} | '
        '{% include docs_filterbar.html types="how_to" %}| '
        "{% include something_new.html %}"
    )
    assert text == "Primary | [embedded content: https://shell.duckdb.org] | | "
    assert rules == [
        "include_tooltip_label_kept",
        "include_iframe_marked",
        "include_filterbar_removed",
    ]
    assert warns == ["include_unknown_removed"]


def test_for_loop_body_is_removed_not_emitted_as_markers() -> None:
    text, rules, warns = _render(
        'Intro\n{% for g in pages %}\n<a href="{{ g.url }}">{{ g.title }}</a>\n{% endfor %}\nEnd'
    )
    assert text == "Intro\n\n\n\nEnd"
    assert "liquid_for_loop_removed" in rules and "liquid_for_loop_removed" in warns


def test_comment_block_removed_and_unknown_tag_warned() -> None:
    text, rules, warns = _render(
        "a{% comment %}hidden {{ site.x }}{% endcomment %}b {% cycle 'x' %}"
    )
    assert text == "ab "
    assert rules == ["liquid_comment_removed"] and warns == ["liquid_tag_unknown"]
