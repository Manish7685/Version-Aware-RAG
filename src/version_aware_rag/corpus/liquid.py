"""Liquid/Jekyll template handling for DuckDB docs pages (RAG-M3 S3).

Jekyll evaluates Liquid *before* Markdown, including inside code fences, so this pass runs on
the whole page body first, exactly once. It never evaluates site data: values that depend on the
site's *current* configuration are replaced by neutral markers so that archived pages are not
contaminated with current release numbers (owner decision Q6).

Every newline of the input is kept (removed text is replaced by its newlines), so line
numbers in the output equal line numbers in the source.

Rules (each application is recorded by rule id):

- ``{% raw %}…{% endraw %}``: tags removed, content kept verbatim and not interpreted.
- ``{% link docs/X/p.md %}``: replaced by the canonical URL ``https://duckdb.org/docs/X/p.html``.
- ``{% post_url 2021-12-03-duck-arrow %}``: replaced by the Jekyll post URL
  ``https://duckdb.org/2021/12/03/duck-arrow.html``.
- ``{{ site.<name> }}``: ``[site:<name>]``.
- ``{% include install/latest_client.html client="c" %}``: removed; it only assigns
  ``latest_version`` / ``latest_package`` / ``latest_release`` from current site data, which are
  then rendered as ``[site:latest_version:c]`` etc. Markers never contain spaces, so a
  marker inside a link URL keeps the Markdown link valid.
- ``{% include tooltip.html label="L" id="I" %}``: the visible label ``L`` (the tooltip body is
  site data and is not rendered).
- ``{% include iframe.html src="S" %}``: ``[embedded content: S]``.
- ``{% include docs_filterbar.html … %}``: removed (search/filter UI, no content).
- ``{% if C %}A{% else %}B{% endif %}`` (owner decision S3-D1, option B): **both branches are
  kept**, each labelled with the original condition and its branch type:
  ``[liquid-if: C] A [liquid-else: fallback written in source, hard-coded version 1.4.5] B
  [liquid-endif]``. The fallback label names every hard-coded ``x.y.z`` version it contains.
  Neither branch is treated as authoritative; site values inside ``A`` stay neutral markers.
  The raw source of both branches is recorded in the transformation log. Without
  ``{% else %}``: ``[liquid-if: C] A [liquid-endif]``. Labels contain no newline, and a ``|``
  in a condition is escaped, so tables and line numbers are unaffected. Inside a Markdown link
  URL the labels are written without spaces (``[liquid-if:C]A[liquid-else:fallback,…]B
  [liquid-endif]``) so the link stays valid; like any link target, that URL is not block
  content, and both branches remain in the transformation record.
- ``{% comment %}…{% endcomment %}``: removed.
- ``{% for … %}…{% endfor %}``: the loop iterates over site data and cannot be
  evaluated, so its body is removed (recorded) rather than emitted as unresolved markers.
- ``{% assign x = latest_version %}`` (or ``latest_package`` / ``latest_release``): ``x``
  becomes an alias, so ``{{ x }}`` renders the same neutral marker.
- Any other tag or output expression: removed or marked, and recorded as a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TAG = re.compile(r"\{%-?\s*(.*?)\s*-?%\}|\{\{-?\s*(.*?)\s*-?\}\}", re.S)
RAW = re.compile(r"\{%-?\s*raw\s*-?%\}(.*?)\{%-?\s*endraw\s*-?%\}", re.S)
PARAM = re.compile(r'(\w+)="([^"]*)"')
POST = re.compile(r"(\d{4})-(\d{2})-(\d{2})-([\w.-]+)")
VERSION = re.compile(r"(?<![\w.])\d+\.\d+\.\d+(?:\.\d+)?(?![\w.])")
ALIAS = re.compile(r"(\w+)\s*=\s*(latest_version|latest_package|latest_release)$")
LATEST_VARS = ("latest_version", "latest_package", "latest_release")


@dataclass(frozen=True)
class LiquidNote:
    rule: str
    line: int
    detail: str


@dataclass
class LiquidResult:
    text: str
    notes: list[LiquidNote] = field(default_factory=list)  # transformations applied
    warnings: list[LiquidNote] = field(default_factory=list)  # unsupported or suspicious


@dataclass
class _Frame:
    kind: str  # "if" | "comment" | "for"
    condition: str = ""
    in_else: bool = False
    else_label_at: int = -1  # index in the output reserved for the else label
    compact: bool = False  # inside a Markdown link URL: labels without spaces
    raw_if: list[str] = field(default_factory=list)  # raw source of the if-branch
    raw_else: list[str] = field(default_factory=list)  # raw source of the else-branch


def link_url(target: str, site_base: str) -> str:
    """``docs/2.0/sql/x.md`` -> ``https://duckdb.org/docs/2.0/sql/x.html``."""
    path = target.strip().lstrip("/")
    if path.endswith(".md"):
        path = path[: -len(".md")] + ".html"
    return f"{site_base.rstrip('/')}/{path}"


def _newlines(s: str) -> str:
    return "\n" * s.count("\n")


def _label_safe(text: str) -> str:
    """One line, and no unescaped pipe that could split a table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def _compact(text: str) -> str:
    """Label text for use inside a link URL: no whitespace, no parentheses."""
    text = text.replace(", ", ",").replace(" ", "-")
    return text.replace("(", "").replace(")", "").replace("|", "")


def _is_branch_tag(tag_body: str | None) -> bool:
    if tag_body is None:
        return False
    name = tag_body.split(" ", 1)[0]
    return name in ("if", "unless", "else", "endif", "endunless")


class _Renderer:
    def __init__(self, site_base: str, line_offset: int) -> None:
        self.site_base = site_base
        self.line_offset = line_offset
        self.out: list[str] = []
        self.result = LiquidResult(text="")
        self.stack: list[_Frame] = []
        self.client: str | None = None
        self.aliases: dict[str, str] = {}

    def suppressed(self) -> bool:
        return any(f.kind in ("comment", "for") for f in self.stack)

    def record_raw(self, raw: str) -> None:
        """Keep the raw source of every open conditional branch for the transformation log."""
        for frame in self.stack:
            if frame.kind == "if":
                (frame.raw_else if frame.in_else else frame.raw_if).append(raw)

    def emit(self, text: str) -> None:
        self.record_raw(text)
        self.out.append(_newlines(text) if self.suppressed() else text)

    def note(self, rule: str, line: int, detail: str) -> None:
        self.result.notes.append(LiquidNote(rule, line + self.line_offset, detail))

    def warn(self, rule: str, line: int, detail: str) -> None:
        self.result.warnings.append(LiquidNote(rule, line + self.line_offset, detail))

    def _top(self, kind: str, line: int) -> _Frame | None:
        if self.stack and self.stack[-1].kind == kind:
            return self.stack[-1]
        self.warn("liquid_unbalanced", line, f"no open {kind}")
        return None

    def _pop(self, kind: str, line: int) -> _Frame | None:
        frame = self._top(kind, line)
        if frame is not None:
            self.stack.pop()
        return frame

    def tag(self, body: str, raw: str, line: int) -> None:
        name, _, rest = body.partition(" ")
        rest = rest.strip()
        keep = _newlines(raw)
        if name == "comment":
            self.stack.append(_Frame("comment"))
            self.note("liquid_comment_removed", line, "")
        elif name == "endcomment":
            self._pop("comment", line)
        elif name == "for":
            self.stack.append(_Frame("for", f"for {rest}"))
            self.warn("liquid_for_loop_removed", line, raw)
        elif name == "endfor":
            frame = self._pop("for", line)
            if frame is not None:
                self.note("liquid_for_loop_removed", line, frame.condition)
        elif name in ("if", "unless"):
            if self.suppressed():
                self.emit(raw)
                return
            condition = f"{name} {rest}"
            self.stack.append(_Frame("if", condition))
            frame = self.stack[-1]
            frame.compact = self._in_link_destination()
            if frame.compact:
                self.out.append(f"[liquid-{name}:{_compact(rest)}]")
                self.note("liquid_if_in_link_url", line, condition)
            else:
                self.out.append(f"[liquid-{name}: {_label_safe(rest)}] ")
        elif name == "else":
            if self.suppressed():
                self.emit(raw)
                return
            frame = self._top("if", line)
            if frame is not None:
                frame.in_else = True
                frame.else_label_at = len(self.out)
                self.out.append("")  # filled at endif, once the fallback text is known
        elif name in ("endif", "endunless"):
            if self.suppressed():
                self.emit(raw)
                return
            frame = self._pop("if", line)
            if frame is not None:
                self._close_if(frame, line)
        elif self.suppressed():
            self.emit(raw)
            return
        elif name == "link":
            self.out.append(link_url(rest, self.site_base))
            self.note("liquid_link_resolved", line, "")
        elif name == "post_url":
            m = POST.fullmatch(rest)
            if m:
                base = self.site_base.rstrip("/")
                self.out.append(f"{base}/{m[1]}/{m[2]}/{m[3]}/{m[4]}.html")
                self.note("liquid_post_url_resolved", line, rest)
            else:
                self.warn("liquid_post_url_malformed", line, raw)
        elif name == "include":
            self._include(rest, line)
        elif name == "assign" and (alias := ALIAS.match(rest)) is not None:
            self.aliases[alias[1]] = alias[2]
            self.note("liquid_assign_alias", line, rest)
        elif name in ("assign", "capture", "endcapture", "case", "when", "break"):
            self.warn("liquid_tag_unsupported", line, raw)
        else:
            self.warn("liquid_tag_unknown", line, raw)
        self.out.append(keep)

    def _close_if(self, frame: _Frame, line: int) -> None:
        raw_if = "".join(frame.raw_if).strip()
        if frame.in_else:
            raw_else = "".join(frame.raw_else).strip()
            versions = sorted(set(VERSION.findall(raw_else)))
            label = "fallback written in source"
            if versions:
                label += f", hard-coded version {', '.join(versions)}"
            if frame.compact:
                self.out[frame.else_label_at] = f"[liquid-else:{_compact(label)}]"
            else:
                self.out[frame.else_label_at] = f" [liquid-else: {label}] "
            self.note(
                "liquid_if_else_both_kept",
                line,
                f"{frame.condition} | if-branch: {raw_if!r} | else-branch: {raw_else!r} | "
                f"hard-coded versions in else-branch: {', '.join(versions) or 'none'}",
            )
        else:
            self.note("liquid_if_labelled", line, f"{frame.condition} | if-branch: {raw_if!r}")
        self.out.append("[liquid-endif]" if frame.compact else " [liquid-endif]")

    def _in_link_destination(self) -> bool:
        """True if the current output line has an open Markdown link destination ``](…``."""
        line = "".join(self.out).rsplit("\n", 1)[-1]
        start = line.rfind("](")
        if start == -1:
            return False
        rest = line[start + 2 :]
        return ")" not in rest and not any(ch.isspace() for ch in rest)

    def _include(self, rest: str, line: int) -> None:
        template, _, params_text = rest.partition(" ")
        params = dict(PARAM.findall(params_text))
        if template == "install/latest_client.html":
            self.client = params.get("client")
            self.note("include_latest_client_removed", line, f"client={self.client}")
        elif template == "tooltip.html":
            self.out.append(params.get("label", ""))
            self.note("include_tooltip_label_kept", line, f"id={params.get('id', '')}")
            if "label" not in params:
                self.warn("include_tooltip_without_label", line, rest)
        elif template == "iframe.html":
            self.out.append(f"[embedded content: {params.get('src', '?')}]")
            self.note("include_iframe_marked", line, params.get("src", ""))
        elif template == "docs_filterbar.html":
            self.note("include_filterbar_removed", line, params_text.strip())
        else:
            self.warn("include_unknown_removed", line, rest)

    def output(self, expr: str, raw: str, line: int) -> None:
        if self.suppressed():
            self.emit(raw)
            return
        name = expr.split("|", 1)[0].strip()
        name = self.aliases.get(name, name)
        if name.startswith("site."):
            self.out.append(f"[site:{name[len('site.') :]}]")
            self.note("site_variable_neutralised", line, name)
        elif name in LATEST_VARS:
            suffix = f":{self.client}" if self.client else ""
            self.out.append(f"[site:{name}{suffix}]")
            self.note("site_variable_neutralised", line, f"{name}{suffix}")
            if self.client is None:
                self.warn("latest_variable_without_client", line, raw)
        else:
            self.out.append(f"[template:{name}]")
            self.warn("template_variable_unresolved", line, raw)
        if "|" in expr:
            self.warn("liquid_filter_ignored", line, raw)
        self.out.append(_newlines(raw))

    def plain(self, segment: str, line: int) -> None:
        cursor = 0
        for m in TAG.finditer(segment):
            before = segment[cursor : m.start()]
            self.emit(before)
            line += before.count("\n")
            if not _is_branch_tag(m.group(1)):
                self.record_raw(m.group(0))
            if m.group(1) is not None:
                self.tag(m.group(1), m.group(0), line)
            else:
                self.output(m.group(2), m.group(0), line)
            line += m.group(0).count("\n")
            cursor = m.end()
        self.emit(segment[cursor:])


def render_liquid(body: str, site_base: str, line_offset: int = 0) -> LiquidResult:
    """Apply the rules above. ``line_offset`` maps body line 1 to the source file's line."""
    r = _Renderer(site_base, line_offset)
    pos = 0
    line = 1
    for m in RAW.finditer(body):
        r.plain(body[pos : m.start()], line)
        line += body[pos : m.start()].count("\n")
        r.note("liquid_raw_unwrapped", line, "")
        # Tags removed (their newlines kept); content verbatim and not interpreted.
        r.out.append(_newlines(body[m.start() : m.start(1)]))
        r.emit(m.group(1))
        r.out.append(_newlines(body[m.end(1) : m.end()]))
        line += m.group(0).count("\n")
        pos = m.end()
    r.plain(body[pos:], line)
    for frame in r.stack:
        r.warn("liquid_unclosed", line, frame.kind)
    r.result.text = "".join(r.out)
    return r.result
