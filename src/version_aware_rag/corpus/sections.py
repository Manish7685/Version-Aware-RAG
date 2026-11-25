"""Section tree for one parsed page (RAG-M3 S4a).

Consumes S3's ``ParsedDocument`` only; raw Markdown is never re-read. Design:
``docs/design/RAG-M3_S4_chunking.md`` §4.

- Every heading of levels 1-6 starts a node, except an H1 whose text equals the page title
  (plan §4.1). A heading hangs under the nearest preceding node of a lower level; skipped levels
  are kept as written.
- Every Sphinx ``api_entry`` block is a node, nested by its ``dt`` depth (a class is the parent of
  its methods), under the current heading node.
- A node's own blocks are the blocks up to the next heading or API entry of any level. For Sphinx
  entries, a following block belongs to the deepest open entry whose source range contains it.
- R1 API detection for Markdown headings: whole-code-span heading, call-signature heading, or a
  header-less function-documentation table as the first block. A call signature is a dotted
  identifier directly followed by parentheses (owner S4-D8).
- State: ``content`` (own blocks), ``structural`` (no own blocks, has children), ``empty``
  (neither). Empty sections get a reason; they become records, never chunks (S4-D2).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from version_aware_rag.corpus.config import ChunkingConfig
from version_aware_rag.corpus.parse import Block, ParsedDocument, SourceKind

NodeKind = Literal["page_root", "heading", "api_entry"]
NodeState = Literal["content", "structural", "empty"]
ApiReason = Literal["heading_code_span", "heading_signature", "function_table", "sphinx_dt"]
EmptyReason = Literal["railroad_diagram_removed", "no_source_content"]

CODE_SPAN = re.compile(r"^`[^`]+`$")
# S4-D8: a dotted identifier written directly before the parentheses (`database.get()`).
# Prose headings with a parenthetical ("HTTP(S)", "Java (JDBC)") are not signatures.
CALL_SIGNATURE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\(.*\)$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
RAILROAD_RULE = "html_railroad_removed"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiInfo(_Model):
    reason: ApiReason
    signature: str
    api_id: str | None = None


class SectionNode(_Model):
    path: tuple[str, ...]
    kind: NodeKind
    heading: str | None
    level: int
    occurrence: int
    line_start: int
    line_end: int
    blocks: tuple[Block, ...]
    children: tuple[SectionNode, ...]
    api: ApiInfo | None
    state: NodeState
    empty_reason: EmptyReason | None
    # S4-D9: lines of the S3 railroad-removal records inside this node's span (its heading line,
    # or line 1 for the page root, up to the next node). S3's records stay authoritative.
    railroad_removal_lines: tuple[int, ...]

    def walk(self) -> Iterator[SectionNode]:
        """This node and its descendants in document order (pre-order)."""
        yield self
        for child in self.children:
            yield from child.walk()


class EmptySection(_Model):
    """S4-D2 record for a section with no content and no subsections."""

    version: str
    source_kind: SourceKind
    source_path: str
    section_path: tuple[str, ...]
    line_start: int
    line_end: int
    reason: EmptyReason
    railroad_removal_lines: tuple[int, ...]  # S4-D9


class SectionTree(_Model):
    version: str
    source_kind: SourceKind
    source_path: str
    title: str  # the page title, or the source path when the page has none
    section_path_fallback: bool
    title_h1_lines: tuple[int, ...]  # H1 headings equal to the title, skipped as nodes
    root: SectionNode

    def nodes(self) -> Iterator[SectionNode]:
        return self.root.walk()

    def empty_sections(self) -> tuple[EmptySection, ...]:
        return tuple(
            EmptySection(
                version=self.version,
                source_kind=self.source_kind,
                source_path=self.source_path,
                section_path=n.path,
                line_start=n.line_start,
                line_end=n.line_end,
                reason=n.empty_reason,
                railroad_removal_lines=n.railroad_removal_lines,
            )
            for n in self.nodes()
            if n.state == "empty" and n.kind != "page_root" and n.empty_reason is not None
        )


@dataclass
class _Draft:
    path: tuple[str, ...]
    kind: NodeKind
    heading: str | None
    level: int
    line_start: int | None
    line_end: int | None
    api: ApiInfo | None = None
    api_depth: int = 0
    blocks: list[Block] = field(default_factory=list)
    children: list[_Draft] = field(default_factory=list)

    def walk(self) -> Iterator[_Draft]:
        yield self
        for child in self.children:
            yield from child.walk()


def _first_cells(table_text: str) -> set[str]:
    cells = set()
    for line in table_text.split("\n"):
        parts = _CELL_SPLIT.split(line.strip().strip("|"))
        if len(parts) >= 2:
            cells.add(parts[0].strip().strip("*").strip())
    return cells


def detect_heading_api(
    node_heading: str, blocks: list[Block], cfg: ChunkingConfig
) -> ApiInfo | None:
    """R1 API-entry rules for a Markdown heading node (design §4 rule 3)."""
    text = node_heading.strip()
    if CODE_SPAN.match(text):
        return ApiInfo(reason="heading_code_span", signature=text[1:-1])
    if CALL_SIGNATURE.match(text):
        return ApiInfo(reason="heading_signature", signature=text)
    if blocks and blocks[0].kind == "table" and blocks[0].meta.get("header") == "false":
        cells = _first_cells(blocks[0].text)
        det = cfg.api_detection
        if set(det.function_table_first_cells) <= cells and cells & set(
            det.function_table_other_cells
        ):
            return ApiInfo(reason="function_table", signature=text)
    return None


def _contains(node: _Draft, block: Block) -> bool:
    start, end = node.line_start or 0, node.line_end or 0
    return start <= block.line_start <= end


def build_tree(doc: ParsedDocument, cfg: ChunkingConfig) -> SectionTree:
    """Build the section tree of one parsed page."""
    title = doc.title if doc.title and doc.title.strip() else None
    root_title = title if title is not None else doc.path
    root = _Draft(
        path=(root_title,), kind="page_root", heading=None, level=0, line_start=None, line_end=None
    )
    headings: list[_Draft] = [root]  # open heading chain
    apis: list[_Draft] = []  # open Sphinx entries under the current heading
    current = root
    title_h1: list[int] = []
    levels = set(cfg.boundary_levels)

    for b in doc.blocks:
        if b.kind == "heading" and b.level in levels:
            level = b.level
            if (
                level == 1
                and cfg.skip_h1_equal_to_title
                and title is not None
                and b.text.strip() == title.strip()
            ):
                title_h1.append(b.line_start)
                continue
            apis.clear()
            while headings[-1].level >= level:
                headings.pop()
            parent = headings[-1]
            node = _Draft(
                path=(*parent.path, b.text),
                kind="heading",
                heading=b.text,
                level=level,
                line_start=b.line_start,
                line_end=b.line_end,
            )
            parent.children.append(node)
            headings.append(node)
            current = node
        elif b.kind == "api_entry":
            depth = b.level or 1
            while apis and apis[-1].api_depth >= depth:
                apis.pop()
            parent = apis[-1] if apis else headings[-1]
            api_id = b.meta.get("api_id") or None
            signature = b.meta.get("signature", "")
            node = _Draft(
                path=(*parent.path, api_id or signature),
                kind="api_entry",
                heading=signature,
                level=headings[-1].level + depth,
                line_start=b.line_start,
                line_end=b.line_end,
                api=ApiInfo(reason="sphinx_dt", signature=signature, api_id=api_id),
                api_depth=depth,
                blocks=[b],
            )
            parent.children.append(node)
            apis.append(node)
            current = node
        else:
            if apis:
                while apis and not _contains(apis[-1], b):
                    apis.pop()
                current = apis[-1] if apis else headings[-1]
            current.blocks.append(b)

    return _freeze(doc, root, root_title, title is None, tuple(title_h1), cfg)


def _freeze(
    doc: ParsedDocument,
    root: _Draft,
    root_title: str,
    fallback: bool,
    title_h1: tuple[int, ...],
    cfg: ChunkingConfig,
) -> SectionTree:
    order = list(root.walk())
    # Root range: its own content, else line 1.
    root.line_start = root.blocks[0].line_start if root.blocks else 1
    for d in order:
        if d.blocks:
            d.line_end = max(d.line_end or 0, *(b.line_end for b in d.blocks))
        elif d.line_end is None:
            d.line_end = d.line_start
        if d.kind == "heading" and d.heading is not None:
            d.api = detect_heading_api(d.heading, d.blocks, cfg)
    # Where each node's span ends: the next node's start (document order), for empty reasons.
    starts = [d.line_start or 1 for d in order]
    railroad = [t.line for t in doc.transformations if t.code == RAILROAD_RULE and t.line]
    occurrence: Counter[tuple[str, ...]] = Counter()
    frozen: dict[int, SectionNode] = {}

    for i in reversed(range(len(order))):
        d = order[i]
        children = tuple(frozen[id(c)] for c in d.children)
        state: NodeState = "content" if d.blocks else ("structural" if children else "empty")
        span_start = 1 if d.kind == "page_root" else (d.line_start or 1)
        span_end = starts[i + 1] if i + 1 < len(order) else 10**9
        removals = tuple(line for line in railroad if span_start <= line < span_end)
        reason: EmptyReason | None = None
        if state == "empty" and d.kind != "page_root":
            reason = "railroad_diagram_removed" if removals else "no_source_content"
        frozen[id(d)] = SectionNode(
            path=d.path,
            kind=d.kind,
            heading=d.heading,
            level=d.level,
            occurrence=0,  # set below, in document order
            line_start=d.line_start or 1,
            line_end=d.line_end or d.line_start or 1,
            blocks=tuple(d.blocks),
            children=children,
            api=d.api,
            state=state,
            empty_reason=reason,
            railroad_removal_lines=removals,
        )
    # Occurrence numbers in document order; rebuild bottom-up with them.
    occ: dict[int, int] = {}
    for d in order:
        occ[id(d)] = occurrence[d.path]
        occurrence[d.path] += 1
    rebuilt: dict[int, SectionNode] = {}
    for d in reversed(order):
        node = frozen[id(d)]
        rebuilt[id(d)] = node.model_copy(
            update={
                "occurrence": occ[id(d)],
                "children": tuple(rebuilt[id(c)] for c in d.children),
            }
        )
    return SectionTree(
        version=doc.version,
        source_kind=doc.source_kind,
        source_path=doc.path,
        title=root_title,
        section_path_fallback=fallback,
        title_h1_lines=title_h1,
        root=rebuilt[id(root)],
    )


def preservation_problems(doc: ParsedDocument, tree: SectionTree) -> list[str]:
    """Tree-level preservation (design §4 rule 7), for a tree built from ``doc``.

    Every S3 block is placed in exactly one node (checked by identity), blocks keep their source
    order within each node, API entries keep their order, and every heading block is a node or a
    skipped title H1. (Tree pre-order can differ from source order only when a Sphinx section has
    loose text after nested entries; units are ordered by source position, design §8 rule 8.)
    """
    problems: list[str] = []
    index = {id(b): i for i, b in enumerate(doc.blocks)}
    placed: list[int] = []
    heading_lines: set[int] = set(tree.title_h1_lines)
    for node in tree.nodes():
        own = node.blocks[1:] if node.kind == "api_entry" else node.blocks
        idx = [index.get(id(b), -1) for b in own]
        if -1 in idx:
            problems.append(f"{doc.path}: node {node.path} holds a block not from this document")
        if idx != sorted(idx):
            problems.append(f"{doc.path}: blocks of node {node.path} are not in source order")
        placed.extend(idx)
        if node.kind == "heading":
            heading_lines.add(node.line_start)
    expected = [i for i, b in enumerate(doc.blocks) if b.kind not in ("heading", "api_entry")]
    if sorted(placed) != expected:
        missing = len(set(expected) - set(placed))
        dup = len(placed) - len(set(placed))
        problems.append(f"{doc.path}: {missing} content blocks missing, {dup} placed twice")
    api_blocks = [n.blocks[0] for n in tree.nodes() if n.kind == "api_entry"]
    parsed_api = [b for b in doc.blocks if b.kind == "api_entry"]
    if [id(b) for b in api_blocks] != [id(b) for b in parsed_api]:
        problems.append(f"{doc.path}: API entries {len(api_blocks)}, parsed {len(parsed_api)}")
    for b in doc.blocks:
        if b.kind == "heading" and b.line_start not in heading_lines:
            problems.append(f"{doc.path}:{b.line_start}: heading not represented in the tree")
    return problems
