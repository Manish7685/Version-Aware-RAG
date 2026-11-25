"""HTML inside DuckDB docs pages, parsed with the standard library ``html.parser`` (RAG-M3 S3).

Two uses:

1. ``classify_html_block``: one raw HTML block found inside a Markdown page (a markdown-it
   ``html_block`` token). It is classified and turned into zero or one content block:

   - only comments / empty wrappers / empty railroad-diagram containers -> dropped (recorded);
   - pre-rendered highlighted code (``<pre>``) -> a code block, text verbatim;
   - landing-page link boxes (``class="box-link…"``) -> a navigation block of link labels;
   - anything else -> an ``html_text`` block of its visible text (recorded as generic).

2. ``extract_sphinx_api``: a whole generated Sphinx API reference page (the Python client
   reference). Each ``<dt id=…>`` signature becomes an API entry whose description is the text of
   the matching ``<dd>``; nested ``<dl>`` (methods inside classes) become child entries and are
   *not* copied into the parent's description. ``<h2>`` inside ``<section>`` becomes a heading.

Nothing is invented: no structure is synthesised that isn't in the HTML. Unbalanced or unexpected
tags never raise; they are recorded as warnings with their source line, and any visible text that
isn't inside a recognised structure is kept as an ``html_text`` block rather than dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = frozenset({"br", "img", "hr", "meta", "input", "link", "wbr", "source", "col", "area"})
BLOCK = frozenset(
    {
        "p",
        "div",
        "section",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "table",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "pre",
        "blockquote",
        "details",
        "summary",
        "figure",
        "figcaption",
    }
)
SKIP = frozenset({"script", "style", "svg"})
# Containers that Markdown pages legitimately open in one HTML block and close in a later one
# (Kramdown ``markdown='1'`` wrappers, table wrappers). Their balance is not checked per block.
CONTAINERS = frozenset({"div", "details", "section"})


@dataclass(frozen=True)
class HtmlWarning:
    code: str
    line: int
    detail: str


@dataclass
class ExtractedBlock:
    kind: str  # heading | api_entry | code | navigation | html_text
    text: str
    line_start: int
    line_end: int
    level: int | None = None
    lang: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


def _norm_ws(text: str) -> str:
    """Collapse whitespace in prose; lines inside ``` fences (from <pre>) are kept verbatim."""
    out: list[str] = []
    in_code = False
    for raw in text.split("\n"):
        if raw.strip() == "```":
            in_code = not in_code
            out.append("```")
            continue
        if in_code:
            out.append(raw.rstrip("\r"))
            continue
        ln = re.sub(r"[ \t\r\f\v]+", " ", raw).strip()
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


class _TextCollector(HTMLParser):
    """Visible text with block boundaries as newlines; ``<pre>`` kept verbatim."""

    def __init__(self, line_offset: int) -> None:
        super().__init__(convert_charrefs=True)
        self.line_offset = line_offset
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.pre_text: list[str] = []
        self.pre_lang: str | None = None
        self.tags_seen: list[str] = []
        self.classes: list[str] = []
        self.ids: list[str] = []
        self.warnings: list[HtmlWarning] = []

    def _line(self) -> int:
        return self.getpos()[0] + self.line_offset

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        self.tags_seen.append(tag)
        self.classes.extend(a.get("class", "").split())
        if a.get("id"):
            self.ids.append(a["id"])
        for cls in a.get("class", "").split():
            if cls.startswith("language-") and self.pre_lang is None:
                self.pre_lang = cls[len("language-") :]
        if tag in SKIP:
            self.skip_depth += 1
        if tag == "pre":
            self.pre_depth += 1
        if tag == "br" or tag in BLOCK:
            self.parts.append("\n")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if tag not in self.stack:
            if tag not in CONTAINERS:
                self.warnings.append(HtmlWarning("html_unexpected_end_tag", self._line(), tag))
            if tag in BLOCK:
                self.parts.append("\n")
            return
        while self.stack and self.stack[-1] != tag:
            open_tag = self.stack.pop()
            if open_tag not in CONTAINERS:
                self.warnings.append(HtmlWarning("html_unclosed_tag", self._line(), open_tag))
        self.stack.pop()
        if tag in SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        if tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
        if tag in BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.pre_depth:
            self.pre_text.append(data)
        self.parts.append(data)

    def finish(self) -> None:
        self.close()
        for tag in reversed(self.stack):
            if tag not in CONTAINERS:
                self.warnings.append(HtmlWarning("html_unclosed_tag", self._line(), tag))
        self.stack.clear()

    @property
    def text(self) -> str:
        return _norm_ws("".join(self.parts))


@dataclass
class HtmlBlockResult:
    classification: str  # comment | wrapper | railroad | code | navigation | generic
    block: ExtractedBlock | None
    warnings: list[HtmlWarning]


def classify_html_block(html: str, line_start: int, line_end: int) -> HtmlBlockResult:
    """Turn one raw HTML block from a Markdown page into at most one content block."""
    without_comments = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    c = _TextCollector(line_offset=line_start - 1)
    c.feed(html)
    c.finish()
    text = c.text
    if not without_comments.strip():
        return HtmlBlockResult("comment", None, c.warnings)
    if "rrdiagram" in c.ids and not text:
        return HtmlBlockResult("railroad", None, c.warnings)
    if not text:
        return HtmlBlockResult("wrapper", None, c.warnings)
    if "pre" in c.tags_seen:
        code = "".join(c.pre_text).strip("\n")
        block = ExtractedBlock("code", code, line_start, line_end, lang=c.pre_lang)
        block.meta["source"] = "html_pre"
        return HtmlBlockResult("code", block, c.warnings)
    if any(cls.startswith("box-link") for cls in c.classes):
        # Labels sit inside the <a> (2.0) or in a sibling <span> next to an empty <a> (1.2-1.5),
        # so use the box's visible text, one label per line.
        block = ExtractedBlock("navigation", text, line_start, line_end)
        return HtmlBlockResult("navigation", block, c.warnings)
    block = ExtractedBlock("html_text", text, line_start, line_end)
    block.meta["first_tag"] = c.tags_seen[0] if c.tags_seen else ""
    return HtmlBlockResult("generic", block, c.warnings)


# --------------------------------------------------------------------- Sphinx API pages


@dataclass
class _Entry:
    api_id: str
    depth: int
    line_start: int
    signature: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    line_end: int = 0
    in_dt: bool = True


class _SphinxExtractor(HTMLParser):
    def __init__(self, line_offset: int) -> None:
        super().__init__(convert_charrefs=True)
        self.line_offset = line_offset
        self.blocks: list[ExtractedBlock] = []
        self.warnings: list[HtmlWarning] = []
        self.stack: list[str] = []
        self.entries: list[_Entry] = []  # open entries, innermost last
        self.dl_api: list[bool] = []  # open <dl>s: True for Sphinx API lists (class "py ...")
        self.dtdd_roles: list[bool] = []  # open <dt>/<dd>: True if it bounds an API entry
        self.heading: list[str] | None = None
        self.heading_line = 0
        self.loose: list[str] = []  # visible text outside any entry or heading
        self.loose_line = 0
        self.skip_depth = 0  # headerlink anchors and script/style
        self.pre_depth = 0

    def _line(self) -> int:
        return self.getpos()[0] + self.line_offset

    def _sink(self) -> list[str] | None:
        if self.heading is not None:
            return self.heading
        if self.entries:
            e = self.entries[-1]
            return e.signature if e.in_dt else e.body
        return None

    def _flush_loose(self) -> None:
        text = _norm_ws("".join(self.loose))
        if text:
            self.blocks.append(ExtractedBlock("html_text", text, self.loose_line, self._line()))
            self.warnings.append(
                HtmlWarning("html_text_outside_api_entry", self.loose_line, text[:60])
            )
        self.loose = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag not in VOID:
            self.stack.append(tag)
        if tag in SKIP or (tag == "a" and "headerlink" in a.get("class", "")):
            self.skip_depth += 1
            return
        in_api_dl = bool(self.dl_api) and self.dl_api[-1]
        if tag == "dl":
            self.dl_api.append("py" in a.get("class", "").split())
            sink = self._sink()
            (sink if sink is not None else self.loose).append("\n")
        elif tag == "dt" and in_api_dl:
            self.dtdd_roles.append(True)
            self._flush_loose()
            api_id = a.get("id", "")
            if not api_id:
                self.warnings.append(HtmlWarning("api_dt_without_id", self._line(), ""))
            depth = sum(self.dl_api)
            self.entries.append(_Entry(api_id, depth, self._line()))
        elif tag == "dd" and in_api_dl:
            self.dtdd_roles.append(True)
            if not self.entries:
                self.warnings.append(HtmlWarning("api_dd_without_dt", self._line(), ""))
        elif tag in ("dt", "dd"):
            # A definition list inside a docstring ("Parameters:", "Returns:"): plain text.
            self.dtdd_roles.append(False)
            sink = self._sink()
            (sink if sink is not None else self.loose).append("\n")
        elif tag in ("h2", "h3") and not self.entries:
            self._flush_loose()
            self.heading, self.heading_line = [], self._line()
        elif tag == "pre":
            self.pre_depth += 1
            sink = self._sink()
            if sink is not None:
                sink.append("\n```\n")
        elif tag == "br" or tag in BLOCK:
            sink = self._sink()
            target = sink if sink is not None else self.loose
            target.append("\n")
            if tag == "li":
                target.append("- ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        if tag not in self.stack:
            self.warnings.append(HtmlWarning("html_unexpected_end_tag", self._line(), tag))
            return
        while self.stack and self.stack[-1] != tag:
            self.warnings.append(HtmlWarning("html_unclosed_tag", self._line(), self.stack.pop()))
        self.stack.pop()
        if self.skip_depth and (tag in SKIP or tag == "a"):
            self.skip_depth -= 1
            return
        role = self.dtdd_roles.pop() if tag in ("dt", "dd") and self.dtdd_roles else False
        if tag == "dt" and role and self.entries:
            self.entries[-1].in_dt = False
        elif tag == "dt" and not role:
            sink = self._sink()
            (sink if sink is not None else self.loose).append(" ")
        elif tag == "dd" and role and self.entries:
            e = self.entries.pop()
            e.line_end = self._line()
            sig = _norm_ws("".join(e.signature)).replace("\n", " ")
            body = _norm_ws("".join(e.body))
            block = ExtractedBlock("api_entry", body, e.line_start, e.line_end, level=e.depth)
            block.meta = {"api_id": e.api_id, "signature": sig}
            self.blocks.append(block)
        elif tag == "dl":
            if self.dl_api:
                self.dl_api.pop()
        elif tag in ("h2", "h3") and self.heading is not None:
            text = _norm_ws("".join(self.heading))
            self.blocks.append(
                ExtractedBlock("heading", text, self.heading_line, self._line(), level=int(tag[1]))
            )
            self.heading = None
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            sink = self._sink()
            if sink is not None:
                sink.append("\n```\n")
        elif tag in BLOCK:
            sink = self._sink()
            (sink if sink is not None else self.loose).append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        sink = self._sink()
        if sink is None:
            if data.strip() and not self.loose:
                self.loose_line = self._line()
            self.loose.append(data)
        else:
            sink.append(data)

    def finish(self) -> None:
        self.close()
        self._flush_loose()
        for e in reversed(self.entries):
            self.warnings.append(HtmlWarning("api_entry_unclosed", e.line_start, e.api_id))
            body = _norm_ws("".join(e.body))
            block = ExtractedBlock("api_entry", body, e.line_start, self._line(), level=e.depth)
            block.meta = {"api_id": e.api_id, "signature": _norm_ws("".join(e.signature))}
            self.blocks.append(block)
        for tag in reversed(self.stack):
            self.warnings.append(HtmlWarning("html_unclosed_tag", self._line(), tag))


def is_sphinx_api_page(body: str) -> bool:
    """Generated Sphinx reference: starts with the Sphinx document wrapper."""
    return body.lstrip().startswith('<div class="documentwrapper">')


def extract_sphinx_api(
    html: str, line_offset: int
) -> tuple[list[ExtractedBlock], list[HtmlWarning]]:
    """Extract headings and API entries in document order (entries ordered by their start)."""
    x = _SphinxExtractor(line_offset)
    x.feed(html)
    x.finish()
    blocks = sorted(x.blocks, key=lambda b: (b.line_start, b.kind != "heading"))
    return blocks, x.warnings
