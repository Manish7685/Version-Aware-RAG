"""Corpus-wide source inspection for RAG-M3 S3.

Parses every page listed in the committed corpus manifest (from the verified local cache) and
writes two deterministic Markdown reports (no timestamps, sorted output):

- ``RAG-M3_S3_source_inspection.md``: construct counts per version, sections, oversized blocks,
  transformations, warnings, include usage, exclusions and a content-coverage check;
- ``RAG-M3_S3_samples.md``: the configured before/after sample pages.

Token counts are a dependency-free approximation (``\\w+|[^\\w\\s]``), as in the RAG-M3 plan §4.2.
The coverage check compares the parser's output words with an independent, deliberately crude
estimate of the source's visible words; it is a heuristic used to find pages worth reviewing,
not a proof of completeness.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from version_aware_rag.corpus.chunk import approx_tokens  # moved to the chunker (S4b)
from version_aware_rag.corpus.config import ParsingConfig
from version_aware_rag.corpus.manifest import CorpusManifest
from version_aware_rag.corpus.parse import PARSER_VERSION, Block, ParsedDocument, parse_page

WORD = re.compile(r"\w+")
MARKER = re.compile(r"\[(?:site|template):[^\]]*\]")
TOKEN_CAP = 512
RATIO_BUCKETS = ("<0.80", "0.80-0.95", "0.95-1.05", ">1.05")

INCLUDE_RULES = {
    "include_tooltip_label_kept": (
        "tooltip.html: visible label kept; tooltip body (site data) not rendered"
    ),
    "include_latest_client_removed": (
        "install/latest_client.html: removed; the latest_* variables it sets become neutral markers"
    ),
    "include_iframe_marked": "iframe.html: replaced by [embedded content: <src>]",
    "include_filterbar_removed": "docs_filterbar.html: removed (search/filter UI)",
}


def visible_word_estimate(source: str) -> int:
    """Crude, parser-independent estimate of a page's visible words (see module docstring)."""
    body = source
    if source.startswith("---\n"):
        end = source.find("\n---", 3)
        body = source[end + 4 :] if end != -1 else source
    body = re.sub(r"\{%-?\s*(?:end)?raw\s*-?%\}", "", body)
    body = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", body, flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"&#?\w+;", " ", body)  # character references are not words
    body = re.sub(r"</?[A-Za-z][^<>\n]*>", " ", body)
    body = re.sub(r"\]\([^)]*\)", "] ", body)
    body = re.sub(r"\{:[^}]*\}", " ", body)
    return len(WORD.findall(body))


def output_words(doc: ParsedDocument) -> int:
    n = 0
    for b in doc.blocks:
        n += len(WORD.findall(MARKER.sub(" ", b.text)))
        n += len(WORD.findall(b.meta.get("signature", "")))
    return n


def ratio_bucket(ratio: float) -> str:
    if ratio < 0.8:
        return "<0.80"
    if ratio < 0.95:
        return "0.80-0.95"
    return "0.95-1.05" if ratio <= 1.05 else ">1.05"


@dataclass
class PageResult:
    doc: ParsedDocument
    source: str
    coverage: float
    redistributable: bool = True  # False: no source text may appear in committed reports


WITHHELD = "[withheld: licence applicability uncertain, redistributable=false]"


def visible(page: PageResult, text: str) -> str:
    """Text for a committed report: the text itself, or a placeholder for restricted pages."""
    return text if page.redistributable else WITHHELD


@dataclass
class CorpusInspection:
    commit: str
    parsing_config_sha256: str
    pages: list[PageResult] = field(default_factory=list)


def inspect_corpus(
    manifest: CorpusManifest, cache: Path, parsing: ParsingConfig, parsing_sha256: str
) -> CorpusInspection:
    """Parse every manifest file from the cache, in manifest order (version, then path).

    Refuses to use a cached file whose sha256 differs from the manifest.
    """
    result = CorpusInspection(manifest.source.commit, parsing_sha256)
    exclusions = parsing.exclusions()
    for rec in manifest.files:
        raw = (cache / rec.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != rec.sha256:
            raise ValueError(f"{rec.path}: cached file differs from the manifest; run verify")
        doc = parse_page(
            raw=raw,
            version=rec.version,
            path=rec.path,
            source_sha256=rec.sha256,
            site_base=parsing.site_base,
            excluded=exclusions,
        )
        source = raw.decode("utf-8", "replace")
        est = visible_word_estimate(source)
        coverage = output_words(doc) / est if est else 1.0
        result.pages.append(PageResult(doc, source, coverage))
    for post in manifest.release_posts:
        raw = (cache / post.path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != post.sha256:
            raise ValueError(f"{post.path}: cached post differs from the manifest; run verify")
        doc = parse_page(
            raw=raw,
            version=post.version,
            path=post.path,
            source_sha256=post.sha256,
            site_base=parsing.site_base,
            excluded=exclusions,
            source_kind="release_post",
            url=post.canonical_url,
        )
        source = raw.decode("utf-8", "replace")
        est = visible_word_estimate(source)
        coverage = output_words(doc) / est if est else 1.0
        result.pages.append(PageResult(doc, source, coverage, redistributable=post.redistributable))
    return result


def group(doc: ParsedDocument) -> str:
    """Report column: the docs version, or "posts" for release posts (a separate source)."""
    return "posts" if doc.source_kind == "release_post" else doc.version


# ------------------------------------------------------------------ report helpers


def _table(header: Sequence[str], rows: Iterable[Sequence[object]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        cells = (str(c).replace("|", "\\|").replace("\n", " ") for c in row)
        out.append("| " + " | ".join(cells) + " |")
    return out


def sections(blocks: Sequence[Block]) -> list[tuple[Block, int, bool]]:
    """(heading, words until the next heading, empty?).

    A section is empty when no content follows before the next heading of the same or a higher
    level, or the end of the page. A heading directly followed by deeper headings has
    sub-sections and is not empty.
    """
    out = []
    for i, b in enumerate(blocks):
        if b.kind != "heading":
            continue
        words = 0
        empty = True
        for nxt in blocks[i + 1 :]:
            if nxt.kind == "heading":
                if words == 0 and (nxt.level or 9) > (b.level or 0):
                    empty = False
                break
            words += len(WORD.findall(nxt.text))
            empty = False
        out.append((b, words, empty))
    return out


def _by_count(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


# ------------------------------------------------------------------ inspection report


def _pages_section(insp: CorpusInspection, versions: list[str]) -> list[str]:
    by_version: dict[str, list[PageResult]] = defaultdict(list)
    for p in insp.pages:
        by_version[group(p.doc)].append(p)
    rows: list[list[object]] = []
    totals = [0] * 6
    for v in versions:
        kinds = Counter(p.doc.page_kind for p in by_version[v])
        ext = Counter(p.doc.extraction for p in by_version[v])
        values = [
            len(by_version[v]),
            kinds["content"],
            kinds["empty"],
            kinds["landing"],
            ext["markdown"],
            ext["sphinx_html"],
        ]
        totals = [a + b for a, b in zip(totals, values, strict=True)]
        rows.append([v, *values])
    rows.append(["all", *totals])
    header = [
        "docs version / posts",
        "pages",
        "content",
        "empty",
        "excluded (landing)",
        "markdown",
        "sphinx_html",
    ]
    note = (
        "Columns 1.2-2.0 are versioned documentation pages; `posts` are the release posts, "
        "a separate source type (S3b)."
    )
    return ["## 1. Pages", "", note, "", *_table(header, rows)]


def _constructs_section(insp: CorpusInspection, versions: list[str]) -> list[str]:
    counts: dict[str, Counter[str]] = {v: Counter() for v in versions}
    blocks: dict[str, Counter[str]] = {v: Counter() for v in versions}
    for p in insp.pages:
        counts[group(p.doc)].update(p.doc.counts)
        blocks[group(p.doc)].update(b.kind for b in p.doc.blocks)

    def rows(table: dict[str, Counter[str]]) -> list[list[object]]:
        keys = sorted({k for c in table.values() for k in c})
        return [
            [k, *[table[v][k] for v in versions], sum(table[v][k] for v in versions)] for k in keys
        ]

    lang = Counter(
        (b.lang or "(none)") for p in insp.pages for b in p.doc.blocks if b.kind == "code"
    )
    callouts = Counter(
        b.meta.get("callout", "(none)")
        for p in insp.pages
        for b in p.doc.blocks
        if b.kind == "blockquote"
    )
    return [
        "## 2. Construct counts (parser output)",
        "",
        *_table(["construct", *versions, "all"], rows(counts)),
        "",
        "Blocks produced, by kind:",
        "",
        *_table(["block kind", *versions, "all"], rows(blocks)),
        "",
        "Code blocks by language (top 15):",
        "",
        *_table(["language", "blocks"], lang.most_common(15)),
        "",
        "Blockquote callouts:",
        "",
        *_table(["callout", "blocks"], _by_count(callouts)),
    ]


def _front_matter_section(insp: CorpusInspection) -> list[str]:
    forms: Counter[str] = Counter()
    site_files = any_files = 0
    for p in insp.pages:
        title_line = next((ln for ln in p.source.split("\n")[:40] if ln.startswith("title:")), None)
        if title_line is None:
            forms["no title line"] += 1
        else:
            value = title_line[len("title:") :].strip()
            if value.startswith("'"):
                forms["single-quoted"] += 1
            elif value.startswith('"'):
                forms["double-quoted"] += 1
            else:
                forms["plain"] += 1
        site_files += bool(re.search(r"\{\{-?\s*site\.", p.source))
        any_files += "{{" in p.source
    fm_warn = Counter(
        w.code for p in insp.pages for w in p.doc.warnings if w.code.startswith("front_matter")
    )
    return [
        "## 3. Front matter and template usage (raw source)",
        "",
        *_table(["title form", "pages"], sorted(forms.items())),
        "",
        f"Front-matter warnings: {dict(sorted(fm_warn.items())) or 'none'}.",
        "",
        f"Files containing `{{{{`: {any_files}; containing `{{{{ site.`: {site_files}. The "
        "difference is Liquid local variables set by includes, Liquid loop variables, and Go "
        "composite literals inside `{% raw %}` (kept verbatim as code).",
    ]


def _sections_section(insp: CorpusInspection) -> list[str]:
    empty_rows: list[list[object]] = []
    longest: list[tuple[int, str, int, str]] = []
    for p in insp.pages:
        for h, words, empty in sections(p.doc.blocks):
            if empty:
                empty_rows.append([p.doc.path, h.line_start, f"H{h.level} {visible(p, h.text)}"])
            longest.append((words, p.doc.path, h.line_start, visible(p, h.text)))
    longest.sort(key=lambda r: (-r[0], r[1], r[2]))
    out = [
        "## 4. Sections (heading to next heading)",
        "",
        "Empty sections (no content before the next heading of the same or higher level): "
        f"{len(empty_rows)}.",
        "",
        *_table(["page", "line", "heading"], empty_rows[:40]),
    ]
    if len(empty_rows) > 40:
        out += ["", f"... and {len(empty_rows) - 40} more (first 40 shown)."]
    out += [
        "",
        "Longest Markdown sections (words), top 10:",
        "",
        *_table(["words", "page", "line", "heading"], [list(r) for r in longest[:10]]),
    ]
    return out


def _oversize_section(insp: CorpusInspection) -> list[str]:
    over: list[tuple[int, str, str, int, str]] = []
    big_rows: list[list[object]] = []
    for p in insp.pages:
        for b in p.doc.blocks:
            n = approx_tokens(b.text + " " + b.meta.get("signature", ""))
            if n > TOKEN_CAP:
                over.append((n, b.kind, p.doc.path, b.line_start, b.lang or ""))
            if b.kind == "table":
                for row in b.text.split("\n")[2:]:
                    if approx_tokens(row) > TOKEN_CAP:
                        big_rows.append([p.doc.path, b.line_start, approx_tokens(row)])
    over.sort(key=lambda r: (-r[0], r[2], r[3]))
    kinds = dict(sorted(Counter(r[1] for r in over).items()))
    out = [
        f"## 5. Blocks over {TOKEN_CAP} approximate tokens",
        "",
        f"Total: {len(over)} blocks; by kind: {kinds}. "
        f"Single table rows over {TOKEN_CAP} tokens: {len(big_rows)}.",
        "",
        *_table(["approx tokens", "kind", "page", "line", "lang"], [list(r) for r in over[:25]]),
    ]
    if big_rows:
        out += ["", "Oversized single table rows:", ""]
        out += _table(["page", "table line", "row tokens"], big_rows)
    return out


def _transformations_section(insp: CorpusInspection) -> list[str]:
    trans = Counter(t.code for p in insp.pages for t in p.doc.transformations)
    site_vars = Counter(
        t.detail
        for p in insp.pages
        for t in p.doc.transformations
        if t.code == "site_variable_neutralised"
    )
    branches: list[list[object]] = [
        [p.doc.path, t.line, visible(p, t.detail)]
        for p in insp.pages
        for t in p.doc.transformations
        if t.code == "liquid_if_else_both_kept"
    ]
    return [
        "## 6. Transformations applied",
        "",
        *_table(["rule", "applications"], sorted(trans.items())),
        "",
        "Neutralised template variables (expression -> marker `[site:...]`):",
        "",
        *_table(["expression", "occurrences"], _by_count(site_vars)),
        "",
        f"`{{% if %}}…{{% else %}}` blocks ({len(branches)}); both branches kept and labelled "
        "(owner decision S3-D1, option B). Raw source of each branch and the hard-coded "
        "versions found in the fallback:",
        "",
        *_table(["page", "line", "condition | if-branch | else-branch | versions"], branches),
    ]


def _warnings_section(insp: CorpusInspection) -> list[str]:
    warn = Counter(w.code for p in insp.pages for w in p.doc.warnings)
    rows: list[list[object]] = [
        [w.code, p.doc.path, w.line, visible(p, w.detail[:80])]
        for p in insp.pages
        for w in p.doc.warnings
    ]
    return [
        "## 7. Warnings",
        "",
        *_table(["code", "count"], sorted(warn.items())),
        "",
        *_table(["code", "page", "line", "detail"], rows[:60]),
    ]


def _includes_section(insp: CorpusInspection) -> list[str]:
    rows: list[list[object]] = []
    for p in insp.pages:
        c = Counter(t.code for t in p.doc.transformations if t.code in INCLUDE_RULES)
        if c:
            used = ", ".join(f"{k.split('_')[1]} x{n}" for k, n in sorted(c.items()))
            rows.append([p.doc.path, used, p.doc.page_kind])
    excluded: list[list[object]] = [
        [p.doc.path, p.doc.page_kind, p.doc.exclusion_reason, len(p.doc.blocks)]
        for p in insp.pages
        if not p.doc.include_in_corpus
    ]
    return [
        "## 8. Pages using `{% include %}`",
        "",
        f"{len(rows)} pages.",
        "",
        *_table(["page", "include templates", "page kind"], rows),
        "",
        "Handling per template:",
        "",
        *_table(["rule", "handling"], sorted(INCLUDE_RULES.items())),
        "",
        "## 9. Pages excluded from the retrieval corpus",
        "",
        *_table(["page", "kind", "reason", "blocks parsed"], excluded),
    ]


def _coverage_section(insp: CorpusInspection) -> list[str]:
    est_total = sum(visible_word_estimate(p.source) for p in insp.pages)
    out_total = sum(output_words(p.doc) for p in insp.pages)
    buckets = Counter(ratio_bucket(p.coverage) for p in insp.pages)
    odd = sorted(
        (p for p in insp.pages if not 0.95 <= p.coverage <= 1.05),
        key=lambda p: (p.coverage, p.doc.path),
    )
    summary = ", ".join(f"{k}: {buckets[k]}" for k in RATIO_BUCKETS)
    return [
        "## 10. Content-coverage check (heuristic)",
        "",
        f"Output words {out_total} / estimated visible source words {est_total} = "
        f"{out_total / est_total:.4f}. Pages by ratio: {summary}.",
        "",
        "Pages outside 0.95-1.05:",
        "",
        *_table(
            ["ratio", "page", "page kind"],
            [[f"{p.coverage:.3f}", p.doc.path, p.doc.page_kind] for p in odd],
        ),
    ]


def _posts_section(insp: CorpusInspection) -> list[str]:
    rows: list[list[object]] = []
    for p in insp.pages:
        if p.doc.source_kind != "release_post":
            continue
        kinds = Counter(b.kind for b in p.doc.blocks)
        rows.append(
            [
                p.doc.path.removeprefix("_posts/"),
                p.doc.version,
                _withheld_title(p.doc.title),
                len(p.doc.blocks),
                kinds["heading"],
                kinds["code"],
                kinds["table"],
                len(p.doc.transformations),
                len(p.doc.warnings),
                p.doc.page_kind,
            ]
        )
    header = [
        "post",
        "series",
        "title (withheld; sha256 prefix)",
        "blocks",
        "headings",
        "code",
        "tables",
        "transformations",
        "warnings",
        "page kind",
    ]
    return [
        "## 11. Release posts (separate source type: `release_post`)",
        "",
        f"{len(rows)} posts from duckdb-web `_posts/` at the pinned commit. Their metadata, "
        "hashes and licence are in the corpus manifest (`release_posts`).",
        "",
        *_table(header, rows),
    ]


def _withheld_title(title: str | None) -> str:
    """Release-post titles are restricted text (owner, 2026-11-23): reports show a hash prefix."""
    if title is None:
        return "none"
    return f"withheld (sha256 {hashlib.sha256(title.encode('utf-8')).hexdigest()[:12]})"


def render_inspection_report(insp: CorpusInspection) -> str:
    groups = {group(p.doc) for p in insp.pages}
    versions = sorted(groups - {"posts"}) + (["posts"] if "posts" in groups else [])
    lines = [
        "# RAG-M3 S3 source inspection (generated)",
        "",
        f"Generated by `vrag corpus inspect` with parser `{PARSER_VERSION}` from duckdb-web "
        f"`{insp.commit}`; `configs/parsing.toml` sha256 `{insp.parsing_config_sha256}`. All "
        "numbers are measured on the pinned snapshot. Regenerate instead of editing.",
        "",
    ]
    for part in (
        _pages_section(insp, versions),
        _constructs_section(insp, versions),
        _front_matter_section(insp),
        _sections_section(insp),
        _oversize_section(insp),
        _transformations_section(insp),
        _warnings_section(insp),
        _includes_section(insp),
        _coverage_section(insp),
        _posts_section(insp),
    ):
        lines += [*part, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# ------------------------------------------------------------------ samples


def _block_lines(b: Block, show_text: bool = True) -> list[str]:
    attrs = [f"lines {b.line_start}-{b.line_end}"]
    if b.level is not None:
        attrs.append(f"level {b.level}")
    if b.lang:
        attrs.append(f"lang `{b.lang}`")
    attrs += [f"{k}=`{v}`" for k, v in sorted(b.meta.items())]
    text = b.text if len(b.text) <= 700 else b.text[:700] + " […]"
    if not show_text:
        text = WITHHELD
    return [
        f"- **{b.kind}** ({', '.join(attrs)})",
        "",
        "  ````text",
        *[f"  {ln}".rstrip() for ln in text.split("\n")],
        "  ````",
        "",
    ]


def render_samples(
    insp: CorpusInspection, parsing: ParsingConfig, include_restricted: bool = False
) -> str:
    """Samples report. Pages with redistributable=False show structure only, unless
    ``include_restricted`` (the local, git-ignored copy)."""
    by_path = {p.doc.path: p for p in insp.pages}
    lines = [
        "# RAG-M3 S3 before/after samples (generated)",
        "",
        f"Generated by `vrag corpus inspect` with parser `{PARSER_VERSION}`. Each sample shows an "
        "excerpt of the original source (unchanged, with source line numbers) and every parsed "
        "block that starts inside that excerpt. Block text longer than 700 characters is cut "
        "here for readability only; the parser output is never truncated. Pages that are not "
        "redistributable (the release posts) show structure only; their text is in the local "
        "copy `data/build/RAG-M3_S3_samples_local.md`, which is never committed.",
    ]
    for n, s in enumerate(parsing.samples, start=1):
        page = by_path.get(s.path)
        lines += ["", f"## {n}. {s.label}", "", f"`{s.path}`", ""]
        if page is None:
            lines.append("**Not found in the manifest.**")
            continue
        src_lines = page.source.split("\n")
        start, end = s.lines if s.lines else (1, len(src_lines))
        end = min(end, len(src_lines))
        d = page.doc
        excluded = f" (excluded: {d.exclusion_reason})" if not d.include_in_corpus else ""
        width = len(str(end))
        show = page.redistributable or include_restricted
        excerpt = (
            [f"{i:>{width}}  {src_lines[i - 1]}".rstrip() for i in range(start, end + 1)]
            if show
            else [WITHHELD]
        )
        lines += [
            f"Page: title {f'`{d.title}`' if show else _withheld_title(d.title)}, "
            f"extraction `{d.extraction}`, kind `{d.page_kind}`"
            f"{excluded}, {len(d.blocks)} blocks, {len(d.warnings)} warnings.",
            "",
            f"### Source, lines {start}-{end}",
            "",
            "````text",
            *excerpt,
            "````",
            "",
            "### Parsed blocks starting in this excerpt",
            "",
        ]
        shown = [b for b in d.blocks if start <= b.line_start <= end]
        for b in shown:
            lines += _block_lines(b, show)
        if not shown:
            lines += ["(no blocks start in this excerpt)", ""]
        tr = [t for t in d.transformations if t.line is not None and start <= t.line <= end]
        wn = [w for w in d.warnings if w.line is None or start <= w.line <= end]
        lines += ["### Transformations in this excerpt", ""]
        if tr:
            lines += _table(
                ["line", "rule", "detail"],
                [[t.line, t.code, t.detail[:100] if show else WITHHELD] for t in tr],
            )
        else:
            lines.append("none")
        lines += ["", "### Warnings in this excerpt", ""]
        if wn:
            lines += _table(
                ["line", "code", "detail"],
                [[w.line, w.code, w.detail[:100] if show else WITHHELD] for w in wn],
            )
        else:
            lines.append("none")
    return "\n".join(lines) + "\n"
