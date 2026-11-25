"""Parse one DuckDB docs page into an ordered list of content blocks (RAG-M3 S3).

Pipeline per page (source bytes are never modified):

1. **Front matter**: a strict extractor reads the single top-level ``title:`` value. The corpus
   inspection (S3) found only plain scalars (2,011 pages) and single-quoted scalars without
   escapes (20 pages), so PyYAML is not needed. Any other form produces a recorded warning and
   ``title = None``; the value is never guessed.
2. **Liquid** (``liquid.render_liquid``): neutral markers for site data, links resolved,
   ``{% raw %}`` kept verbatim. Line numbers are preserved.
3. **Extraction**:
   - generated Sphinx API pages (``<div class="documentwrapper">``) go wholly to
     ``html_extract.extract_sphinx_api`` (CommonMark would end their HTML at the first blank
     line and break them apart);
   - every other page is tokenised with markdown-it-py (CommonMark + tables) and turned into
     blocks; raw HTML blocks go through ``html_extract.classify_html_block``.

The output is a flat, ordered block list with heading blocks in place. Building the section
tree and chunks is S4 and is deliberately not done here.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict, Field

from version_aware_rag.corpus.config import ExcludedPage
from version_aware_rag.corpus.html_extract import (
    ExtractedBlock,
    classify_html_block,
    extract_sphinx_api,
    is_sphinx_api_page,
)
from version_aware_rag.corpus.liquid import render_liquid

PARSER_VERSION = "s3b-1"

SourceKind = Literal["docs_page", "release_post", "github_release"]  # github_release: S4d

BlockKind = Literal[
    "heading",
    "paragraph",
    "list",
    "code",
    "table",
    "blockquote",
    "html_text",
    "navigation",
    "api_entry",
]

# Kramdown inline attribute lists: {:.language-sql .highlight}, {:.codebox-client}, {: #id}.
# A class or id must start with a letter, so Python format specs such as {:.2f} or {:02d}
# (which only appear in code anyway) are never matched.
IAL = re.compile(r"\{:\s*(?:[.#][A-Za-z][\w-]*\s*)+\}")
# CommonMark starts an HTML block at a line beginning with "<!--" and runs it to the end of
# the line containing "-->", so Markdown written after a lint comment on the same line (for
# example '2. <!-- markdownlint-disable MD034 --> Text [link](url)') would otherwise reach the
# HTML extractor as raw text. Such blocks have the comment removed and the rest re-parsed.
COMMENT = re.compile(r"<!--.*?-->", re.S)


def _comment_led_markdown(html: str) -> str | None:
    """Markdown remaining after leading comments, or None if this is not such a block."""
    if not html.lstrip().startswith("<!--"):
        return None
    rest = COMMENT.sub("", html).strip()
    return rest if rest and not rest.startswith("<") else None


MARKDOWN_ATTR = re.compile(r"""<\w+[^>]*\smarkdown=["']1["']""")


def _split_markdown_attr_block(html: str) -> tuple[str, str] | None:
    """Kramdown parses the inside of an element with ``markdown="1"`` as Markdown, but CommonMark
    keeps Markdown that follows the opening tags *without a blank line* inside the HTML block.
    Returns (leading HTML, trailing Markdown) when such trailing text exists, else None. The HTML
    part runs through ``</summary>`` if present, otherwise through the leading tag-only lines."""
    lines = html.split("\n")
    if not lines or not MARKDOWN_ATTR.match(lines[0].lstrip()):
        return None
    cut = next((k + 1 for k, ln in enumerate(lines) if "</summary>" in ln), None)
    if cut is None:
        cut = next((k for k, ln in enumerate(lines) if not ln.lstrip().startswith("<")), len(lines))
    tail = "\n".join(lines[cut:])
    if not tail.strip() or tail.lstrip().startswith("<"):
        return None
    return "\n".join(lines[:cut]), tail


CALLOUTS = frozenset(
    {"Warning", "Tip", "Note", "Deprecated", "New", "Bestpractice", "Installation", "Info"}
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Block(_Model):
    kind: BlockKind
    text: str
    line_start: int  # 1-based line in the original source file
    line_end: int
    level: int | None = None  # heading level, or API nesting depth
    lang: str | None = None  # code language
    meta: dict[str, str] = Field(default_factory=dict)


class Issue(_Model):
    code: str
    line: int | None
    detail: str


class ParsedDocument(_Model):
    parser_version: str
    source_kind: SourceKind
    version: str
    path: str
    url: str
    source_sha256: str
    title: str | None
    extraction: Literal["markdown", "sphinx_html"]
    page_kind: Literal["content", "landing", "empty"]
    include_in_corpus: bool
    exclusion_reason: str | None
    blocks: tuple[Block, ...]
    warnings: tuple[Issue, ...]
    transformations: tuple[Issue, ...]  # every applied cleanup rule, in source order
    counts: dict[str, int]  # construct counts for inspection


# ------------------------------------------------------------------ front matter


class FrontMatter(_Model):
    title: str | None
    body: str
    body_line_offset: int  # number of source lines before the body starts
    warnings: tuple[Issue, ...]


def split_front_matter(text: str) -> FrontMatter:
    """Strict ``title`` extraction; see module docstring for the supported forms."""
    if not text.startswith("---\n"):
        return FrontMatter(
            title=None,
            body=text,
            body_line_offset=0,
            warnings=(Issue(code="front_matter_missing", line=1, detail=""),),
        )
    end = text.find("\n---\n", 3)
    if end == -1 and text.rstrip(" \t\r\n").endswith("\n---"):
        # Closing delimiter at end of file without a trailing newline (four 1.2 stubs).
        end = text.rstrip(" \t\r\n").rfind("\n---")
    if end == -1:
        return FrontMatter(
            title=None,
            body=text,
            body_line_offset=0,
            warnings=(Issue(code="front_matter_unterminated", line=1, detail=""),),
        )
    header = text[4:end]
    body_start = min(end + len("\n---\n"), len(text))
    offset = text[:body_start].count("\n")
    titles = [ln for ln in header.split("\n") if ln.startswith("title:")]
    if len(titles) != 1:
        issue = Issue(code="front_matter_title_count", line=1, detail=str(len(titles)))
        return FrontMatter(
            title=None, body=text[body_start:], body_line_offset=offset, warnings=(issue,)
        )
    raw = titles[0][len("title:") :].strip()
    title: str | None
    problem = ""
    if raw.startswith("'"):
        inner = raw[1:-1] if len(raw) >= 2 and raw.endswith("'") else None
        if inner is None or "'" in inner.replace("''", ""):
            title, problem = None, "single-quoted title is not terminated or has a lone quote"
        else:
            title = inner.replace("''", "'")
    elif raw.startswith('"'):
        # Release posts (S3b) use double-quoted titles. Only escape-free ones are accepted;
        # YAML escapes (\", \\, \u...) are reported instead of being interpreted.
        inner = raw[1:-1] if len(raw) >= 2 and raw.endswith('"') else None
        if inner is None or '"' in inner or "\\" in inner:
            title, problem = None, "double-quoted title is unterminated or contains an escape"
        else:
            title = inner
    elif not raw or raw[0] in "|>&*!%@`[{":
        title, problem = None, f"unsupported YAML form {raw[:1]!r}"
    elif ": " in raw or " #" in raw:
        title, problem = None, "plain title containing ': ' or ' #' is ambiguous in YAML"
    else:
        title = raw
    warnings = (
        (Issue(code="front_matter_title_unsupported", line=1, detail=problem),) if problem else ()
    )
    return FrontMatter(
        title=title, body=text[body_start:], body_line_offset=offset, warnings=warnings
    )


# ------------------------------------------------------------------ markdown


class _Ctx:
    def __init__(self, line_offset: int) -> None:
        self.line_offset = line_offset
        self.source_lines: list[str] = []  # the Liquid-rendered body, for pipe-table rows
        self.blocks: list[Block] = []
        self.warnings: list[Issue] = []
        self.transformations: list[Issue] = []
        self.counts: Counter[str] = Counter()

    def lines(self, tok: Token) -> tuple[int, int]:
        if tok.map is None:
            return (self.line_offset + 1, self.line_offset + 1)
        return (self.line_offset + tok.map[0] + 1, self.line_offset + tok.map[1])

    def transform(self, rule: str, line: int | None, detail: str = "") -> None:
        self.transformations.append(Issue(code=rule, line=line, detail=detail))


def _md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable("table")


def _inline(tok: Token | None, ctx: _Ctx, line: int | None) -> str:
    if tok is None or not tok.children:
        return tok.content if tok is not None else ""
    out: list[str] = []
    for child in tok.children:
        t = child.type
        if t == "text":
            text = child.content
            found = IAL.search(text)
            if found:
                ctx.transform("kramdown_attribute_removed", line, found.group(0))
                text = IAL.sub("", text)
            out.append(text)
        elif t == "code_inline":
            fence = "``" if "`" in child.content else "`"
            out.append(f"{fence}{child.content}{fence}")
        elif t == "softbreak":
            out.append(" ")
        elif t == "hardbreak":
            out.append("\n")
        elif t == "image":
            alt = child.content or "".join(c.content for c in (child.children or []))
            out.append(f"[image: {alt}]")
            ctx.counts["image"] += 1
        elif t == "link_open":
            ctx.counts["link"] += 1
        elif t == "html_inline":
            html = child.content
            if html.startswith("<!--"):
                ctx.transform("html_comment_removed", line, html[:60])
            elif re.match(r"<br\s*/?>", html, re.I):
                out.append("\n")
            else:
                ctx.counts["inline_html_tag_stripped"] += 1
        elif t in (
            "link_close",
            "strong_open",
            "strong_close",
            "em_open",
            "em_close",
            "s_open",
            "s_close",
        ):
            continue
        else:
            ctx.warnings.append(Issue(code="unhandled_inline_token", line=line, detail=t))
            out.append(child.content)
    rendered = "".join(out)
    unparsed = re.search(r"\[[^\]]*\]\(https?://\S*", rendered)
    if unparsed:
        # A link that markdown-it could not parse because the source is malformed (for example
        # a missing ")"). The text is kept exactly as written and the problem is reported.
        ctx.warnings.append(
            Issue(code="markdown_link_unparsed", line=line, detail=unparsed.group(0)[:80])
        )
    return rendered


def _matching_close(tokens: list[Token], i: int) -> int:
    """Index of the token closing ``tokens[i]`` (same nesting, nesting delta back to zero)."""
    depth = 0
    for j in range(i, len(tokens)):
        depth += tokens[j].nesting
        if depth == 0:
            return j
    return len(tokens) - 1


def _render_table(tokens: list[Token], ctx: _Ctx, line: int) -> tuple[str, int, int]:
    rows: list[list[str]] = []
    header_rows = 0
    in_head = False
    current: list[str] | None = None
    for tok in tokens:
        if tok.type == "thead_open":
            in_head = True
        elif tok.type == "thead_close":
            in_head = False
        elif tok.type == "tr_open":
            current = []
        elif tok.type == "tr_close" and current is not None:
            rows.append(current)
            header_rows += 1 if in_head else 0
        elif tok.type == "inline" and current is not None:
            current.append(_inline(tok, ctx, line).replace("|", "\\|").strip())
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(row) + " |")
        if idx == header_rows - 1:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines), len(rows) - header_rows, max((len(r) for r in rows), default=0)


# Kramdown: a table row starts with "|"; the trailing "|" is optional (some rows omit it).
PIPE_ROW = re.compile(r"^\s*\|")
DELIMITER_CELL = re.compile(r"^\s*:?-+:?\s*$")


def _split_pipe_row(line: str) -> list[str]:
    """Split one ``| a | b |`` row on unescaped pipes outside code spans."""
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    code_ticks = 0
    while i < len(s):
        ch = s[i]
        if ch == "`":
            run = len(s[i:]) - len(s[i:].lstrip("`"))
            if code_ticks == 0:
                code_ticks = run
            elif run == code_ticks:
                code_ticks = 0
            current.append(s[i : i + run])
            i += run
            continue
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            current.append("|" if code_ticks else "\\|")
            i += 2
            continue
        if ch == "|" and code_ticks == 0:
            cells.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    cells.append("".join(current))
    return cells


def _kramdown_table(lines: list[str], ctx: _Ctx, line: int) -> tuple[str, dict[str, str]] | None:
    """Kramdown renders consecutive ``|``-delimited lines as a table even without a valid GFM
    header delimiter (for example the per-function Description/Example/Result tables, or a
    delimiter row with a different column count). CommonMark leaves them as a paragraph, so they
    are rebuilt here from the source rows. Returns None if any line isn't a pipe row."""
    if not lines or not all(PIPE_ROW.match(ln) for ln in lines):
        return None
    rows = [_split_pipe_row(ln) for ln in lines]
    delimiter = next(
        (k for k, r in enumerate(rows) if r and all(DELIMITER_CELL.match(c) for c in r)), None
    )
    body_rows = [r for k, r in enumerate(rows) if k != delimiter]
    header_rows = delimiter if delimiter else 0
    rendered = [
        [
            _inline(_md().parseInline(cell.strip())[0], ctx, line).replace("|", "\\|").strip()
            for cell in r
        ]
        for r in body_rows
    ]
    out: list[str] = []
    for k, r in enumerate(rendered):
        out.append("| " + " | ".join(r) + " |")
        if header_rows and k == header_rows - 1:
            out.append("| " + " | ".join("---" for _ in r) + " |")
    meta = {
        "rows": str(len(rendered) - header_rows),
        "columns": str(max(len(r) for r in rendered)),
        "header": "true" if header_rows else "false",
        "source": "kramdown_pipe_rows",
    }
    return "\n".join(out), meta


def _render_container(tokens: list[Token], ctx: _Ctx, line: int) -> str:
    """Lists and blockquotes as indented text; nested code fences kept verbatim."""
    lines: list[str] = []
    stack: list[list[int | str]] = []  # [kind, next_number]
    prefix: str | None = None

    def indent() -> str:
        return "  " * max(0, len(stack) - 1)

    def put(text: str) -> None:
        nonlocal prefix
        for k, ln in enumerate(text.split("\n")):
            if k == 0 and prefix is not None:
                lines.append(indent() + prefix + ln)
                prefix = None
            else:
                lines.append(indent() + ("  " if stack else "") + ln if ln else "")

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "bullet_list_open":
            stack.append(["bullet", 0])
        elif tok.type == "ordered_list_open":
            stack.append(["ordered", int(tok.attrGet("start") or 1)])
        elif tok.type in ("bullet_list_close", "ordered_list_close"):
            stack.pop()
        elif tok.type == "list_item_open" and stack:
            kind, n = stack[-1]
            prefix = "- " if kind == "bullet" else f"{n}. "
            if kind == "ordered":
                stack[-1][1] = int(n) + 1
        elif tok.type == "inline":
            put(_inline(tok, ctx, line))
        elif tok.type in ("fence", "code_block"):
            lang = tok.info.split()[0] if tok.info.strip() else ""
            put(f"```{lang}\n{tok.content.rstrip(chr(10))}\n```")
            ctx.counts["code_in_container"] += 1
        elif tok.type == "html_block" and (rest := _comment_led_markdown(tok.content)):
            ctx.transform("html_comment_removed", line, tok.content[:60])
            put(_inline(_md().parseInline(rest)[0], ctx, line))
        elif tok.type == "html_block":
            res = classify_html_block(tok.content, line, line)
            if res.block is not None:
                put(res.block.text)
        elif tok.type == "table_open":
            close = _matching_close(tokens, i)
            put(_render_table(tokens[i : close + 1], ctx, line)[0])
            i = close
        i += 1
    return "\n".join(lines).strip("\n")


def _parse_markdown(text: str, ctx: _Ctx) -> None:
    ctx.source_lines = text.split("\n")
    tokens = _md().parse(text)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        start, end = ctx.lines(tok)
        t = tok.type
        if t == "heading_open":
            level = int(tok.tag[1])
            ctx.counts[f"heading_h{level}"] += 1
            text_ = _inline(tokens[i + 1], ctx, start).strip()
            ctx.blocks.append(
                Block(kind="heading", text=text_, line_start=start, line_end=end, level=level)
            )
            i += 3
            continue
        if t == "paragraph_open":
            pipe_rows = ctx.source_lines[tok.map[0] : tok.map[1]] if tok.map else []
            kram = _kramdown_table(pipe_rows, ctx, start)
            if kram is not None:
                ctx.counts["table"] += 1
                ctx.counts["table_kramdown_reconstructed"] += 1
                ctx.transform("kramdown_table_reconstructed", start, f"{len(pipe_rows)} rows")
                ctx.blocks.append(
                    Block(kind="table", text=kram[0], line_start=start, line_end=end, meta=kram[1])
                )
                i += 3
                continue
            text_ = _inline(tokens[i + 1], ctx, start).strip()
            if text_:
                ctx.blocks.append(
                    Block(kind="paragraph", text=text_, line_start=start, line_end=end)
                )
                ctx.counts["paragraph"] += 1
            i += 3
            continue
        if t in ("fence", "code_block"):
            lang = tok.info.split()[0] if tok.info.strip() else None
            ctx.counts["code_fence" if t == "fence" else "code_indented"] += 1
            ctx.blocks.append(
                Block(
                    kind="code",
                    text=tok.content.rstrip("\n"),
                    line_start=start,
                    line_end=end,
                    lang=lang,
                )
            )
            i += 1
            continue
        if t in ("bullet_list_open", "ordered_list_open", "blockquote_open", "table_open"):
            close = _matching_close(tokens, i)
            sub = tokens[i : close + 1]
            if t == "table_open":
                table, rows, cols = _render_table(sub, ctx, start)
                ctx.counts["table"] += 1
                meta = {"rows": str(rows), "columns": str(cols)}
                ctx.blocks.append(
                    Block(kind="table", text=table, line_start=start, line_end=end, meta=meta)
                )
            else:
                kind: BlockKind = "blockquote" if t == "blockquote_open" else "list"
                body = _render_container(sub[1:-1] if kind == "blockquote" else sub, ctx, start)
                ctx.counts[kind] += 1
                meta = {}
                first = body.split(" ", 1)[0] if body else ""
                if kind == "blockquote" and first in CALLOUTS:
                    meta["callout"] = first
                if body:
                    ctx.blocks.append(
                        Block(kind=kind, text=body, line_start=start, line_end=end, meta=meta)
                    )
            i = close + 1
            continue
        if t == "html_block" and (rest := _comment_led_markdown(tok.content)):
            ctx.transform("html_comment_removed", start, tok.content[:60])
            nested = _Ctx(start - 1)
            _parse_markdown(rest, nested)
            ctx.blocks.extend(nested.blocks)
            ctx.warnings.extend(nested.warnings)
            ctx.transformations.extend(nested.transformations)
            ctx.counts.update(nested.counts)
            i += 1
            continue
        if t == "html_block" and (split := _split_markdown_attr_block(tok.content)):
            head, tail = split
            head_end = start + head.count("\n")
            res = classify_html_block(head, start, head_end)
            ctx.counts[f"html_block_{res.classification}"] += 1
            if res.block is not None:
                ctx.blocks.append(_from_extracted(res.block))
            ctx.transform("html_markdown_attr_reparsed", start, f"{tail.count(chr(10)) + 1} lines")
            nested = _Ctx(head_end)
            _parse_markdown(tail, nested)
            ctx.blocks.extend(nested.blocks)
            ctx.warnings.extend(nested.warnings)
            ctx.transformations.extend(nested.transformations)
            ctx.counts.update(nested.counts)
            i += 1
            continue
        if t == "html_block":
            res = classify_html_block(tok.content, start, end)
            ctx.counts[f"html_block_{res.classification}"] += 1
            for w in res.warnings:
                ctx.warnings.append(Issue(code=w.code, line=w.line, detail=w.detail))
            if res.block is None:
                ctx.transform(f"html_{res.classification}_removed", start, tok.content[:60])
            else:
                ctx.blocks.append(_from_extracted(res.block))
                if res.classification == "generic":
                    ctx.warnings.append(
                        Issue(
                            code="html_block_generic_text",
                            line=start,
                            detail=res.block.meta.get("first_tag", ""),
                        )
                    )
            i += 1
            continue
        if t == "hr":
            i += 1
            continue
        ctx.warnings.append(Issue(code="unhandled_block_token", line=start, detail=t))
        i += 1


def _from_extracted(b: ExtractedBlock) -> Block:
    return Block(
        kind=b.kind,
        text=b.text,
        line_start=b.line_start,
        line_end=b.line_end,
        level=b.level,
        lang=b.lang,
        meta=dict(b.meta),
    )


# ------------------------------------------------------------------ page


def page_url(path: str, site_base: str) -> str:
    """``docs/2.0/sql/x.md`` -> ``https://duckdb.org/docs/2.0/sql/x.html``."""
    stem = path[: -len(".md")] if path.endswith(".md") else path
    return f"{site_base.rstrip('/')}/{stem}.html"


def parse_page(
    *,
    raw: bytes,
    version: str,
    path: str,
    source_sha256: str,
    site_base: str,
    excluded: Mapping[str, ExcludedPage] | None = None,
    source_kind: SourceKind = "docs_page",
    url: str | None = None,
) -> ParsedDocument:
    """Parse one page. Never raises on content problems: they become warnings."""
    warnings: list[Issue] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        text = raw.decode("utf-8", "replace")
        warnings.append(Issue(code="decode_error", line=None, detail=str(exc)))
    fm = split_front_matter(text)
    warnings.extend(fm.warnings)
    liquid = render_liquid(fm.body, site_base, line_offset=fm.body_line_offset)
    ctx = _Ctx(fm.body_line_offset)
    for note in liquid.notes:
        ctx.transform(note.rule, note.line, note.detail)
    warnings.extend(Issue(code=w.rule, line=w.line, detail=w.detail) for w in liquid.warnings)

    extraction: Literal["markdown", "sphinx_html"]
    if is_sphinx_api_page(liquid.text):
        extraction = "sphinx_html"
        blocks, html_warnings = extract_sphinx_api(liquid.text, fm.body_line_offset)
        ctx.blocks = [_from_extracted(b) for b in blocks]
        ctx.counts["api_entry"] = sum(b.kind == "api_entry" for b in blocks)
        warnings.extend(Issue(code=w.code, line=w.line, detail=w.detail) for w in html_warnings)
    else:
        extraction = "markdown"
        _parse_markdown(liquid.text, ctx)
    warnings.extend(ctx.warnings)

    # Flag blocks holding labelled Liquid conditional branches (decision S3-D1, option B).
    ctx.blocks = [
        b.model_copy(update={"meta": {**b.meta, "liquid_branches": "true"}})
        if ("[liquid-if: " in b.text or "[liquid-unless: " in b.text)
        else b
        for b in ctx.blocks
    ]
    content_blocks = [b for b in ctx.blocks if b.kind != "heading" and b.text.strip()]
    page_kind: Literal["content", "landing", "empty"]
    exclusion = (excluded or {}).get(path)
    if exclusion is not None:
        page_kind, include, reason = exclusion.kind, False, exclusion.reason
    elif not content_blocks:
        page_kind, include, reason = "empty", False, "no content after front matter"
    else:
        page_kind, include, reason = "content", True, None
    return ParsedDocument(
        parser_version=PARSER_VERSION,
        source_kind=source_kind,
        version=version,
        path=path,
        url=url if url is not None else page_url(path, site_base),
        source_sha256=source_sha256,
        title=fm.title,
        extraction=extraction,
        page_kind=page_kind,
        include_in_corpus=include,
        exclusion_reason=reason,
        blocks=tuple(ctx.blocks),
        warnings=tuple(warnings),
        transformations=tuple(ctx.transformations),
        counts=dict(sorted(ctx.counts.items())),
    )
