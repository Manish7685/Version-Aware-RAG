"""Extended build-report statistics (RAG-M3 S5c, design docs/design/RAG-M3_S5_build.md §4).

Pure, deterministic functions over chunks and parsed documents. They never return source
text, warning details or any other corpus content: only identifiers, codes, paths, versions,
counts and line numbers (S5c-D2). Every list is sorted and every histogram has fixed boundaries.

The definitions of oversized chunks, near-empty pages, section-path fallbacks and cross-version
duplicates are exactly those of S5b's ``validate.add_warnings`` (S5c requirement 3). This module
cannot import ``validate`` (which imports ``build``, which imports this module), so the shared
threshold is repeated here and a unit test pins it to the validator's value.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from version_aware_rag.corpus.chunk import Chunk
from version_aware_rag.corpus.inspect import INCLUDE_RULES
from version_aware_rag.corpus.parse import ParsedDocument

NEAR_EMPTY_WORDS = 20  # S5-D7; equal to validate.NEAR_EMPTY_WORDS (tested)
# The S4c chunking report's token-size boundaries (chunk_output.render_chunk_report).
TOKEN_EDGES = (0, 32, 64, 128, 256, 350, 512, 513)
TOKEN_LIMIT = 10**9  # exclusive upper bound of the last bin; larger or negative counts are errors
DROPPED_INCLUDES = ("include_filterbar_removed", "include_latest_client_removed")  # S5c-D4
PARTIAL_INCLUDES = ("include_iframe_marked", "include_tooltip_label_kept")  # S5c-D4
_NO_LINE = -1  # sort key for issues without a line


def oversize_chunks(chunks: Sequence[Chunk]) -> dict[str, object]:
    """Every chunk flagged ``oversize`` (as ``add_warnings``: ``c.oversize``)."""
    flagged = sorted(
        (c for c in chunks if c.oversize), key=lambda c: (c.version, c.source_path, c.chunk_id)
    )
    return {
        "count": len(flagged),
        "by_reason": dict(sorted(Counter(str(c.oversize_reason) for c in flagged).items())),
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "version": c.version,
                "source_path": c.source_path,
                "unit_kind": c.unit_kind,
                "oversize_reason": c.oversize_reason,
                "token_count_approx": c.token_count_approx,
            }
            for c in flagged
        ],
    }


def near_empty_pages(chunks: Sequence[Chunk]) -> dict[str, object]:
    """Pages whose chunks hold fewer than ``NEAR_EMPTY_WORDS`` whitespace-separated words in
    total, per (version, source path), as ``add_warnings``. The chunks themselves are kept."""
    words: Counter[tuple[str, str]] = Counter()
    for c in chunks:
        words[(c.version, c.source_path)] += len(c.content.split())
    pages = sorted((p, v, n) for (v, p), n in words.items() if n < NEAR_EMPTY_WORDS)
    return {
        "threshold_words": NEAR_EMPTY_WORDS,
        "count": len(pages),
        "pages": [{"source_path": p, "version": v, "words": n} for p, v, n in pages],
    }


def section_path_fallbacks(chunks: Sequence[Chunk]) -> dict[str, object]:
    """Chunks with ``section_path_fallback`` set, as ``add_warnings``."""
    ids = sorted(c.chunk_id for c in chunks if c.section_path_fallback)
    return {"count": len(ids), "chunk_ids": ids}


def cross_version_duplicates(chunks: Sequence[Chunk]) -> dict[str, object]:
    """Groups of identical chunk content (``content_hash``) present in two or more versions,
    GitHub release notes excluded, as ``add_warnings``. Counting only: nothing is deduplicated."""
    versions: dict[str, set[str]] = defaultdict(set)
    members: Counter[str] = Counter()
    for c in chunks:
        if c.source_kind != "github_release":
            versions[c.content_hash].add(c.version)
            members[c.content_hash] += 1
    groups = {h: vs for h, vs in versions.items() if len(vs) > 1}
    pairs: Counter[str] = Counter()
    for vs in groups.values():
        pairs.update(f"{a}|{b}" for a, b in itertools.combinations(sorted(vs), 2))
    return {
        "count": len(groups),
        "chunks_in_groups": sum(members[h] for h in groups),
        "by_version_count": dict(sorted(Counter(str(len(vs)) for vs in groups.values()).items())),
        "by_version_pair": dict(sorted(pairs.items())),
    }


class TokenCountError(ValueError):
    """A chunk's token count lies outside the histogram range ``[0, TOKEN_LIMIT)``."""


def token_histogram(chunks: Iterable[Chunk]) -> list[dict[str, object]]:
    """Chunks per token-size bin, bins in fixed order (the S4c report's boundaries).

    Every token count is checked before any bin is assigned: a value below 0 or at least
    ``TOKEN_LIMIT`` raises ``TokenCountError`` naming the chunk and the value; it is never
    skipped or placed in another bin."""
    chunks = list(chunks)
    for c in chunks:
        if not 0 <= c.token_count_approx < TOKEN_LIMIT:
            raise TokenCountError(
                f"chunk {c.chunk_id}: token_count_approx {c.token_count_approx} is outside "
                f"the histogram range [0, {TOKEN_LIMIT})"
            )
    edges = [*TOKEN_EDGES, TOKEN_LIMIT]
    bins = list(itertools.pairwise(edges))
    labels = [f"{a}-{b - 1}" if b - a > 1 else f"{a}" for a, b in bins]
    labels[-1] = f">{TOKEN_EDGES[-2]}"
    counts = Counter(
        next(lab for lab, (a, b) in zip(labels, bins, strict=True) if a <= c.token_count_approx < b)
        for c in chunks
    )
    return [{"tokens": lab, "chunks": counts[lab]} for lab in labels]


def _issue_key(record: dict[str, object]) -> tuple[str, str, int, str]:
    line = record["line"]
    return (
        str(record["version"]),
        str(record["path"]),
        line if isinstance(line, int) else _NO_LINE,
        str(record["code"]),
    )


def parse_warning_records(docs: Iterable[ParsedDocument]) -> dict[str, object]:
    """Every parser warning of every parsed page, restricted pages included; the warning's
    ``detail`` (which can quote source text) is never copied."""
    records: list[dict[str, object]] = [
        {
            "version": d.version,
            "path": d.path,
            "source_kind": d.source_kind,
            "include_in_corpus": d.include_in_corpus,
            "code": w.code,
            "line": w.line,
        }
        for d in docs
        for w in d.warnings
    ]
    records.sort(key=_issue_key)
    return {
        "count": len(records),
        "pages": len({(r["version"], r["path"]) for r in records}),
        "by_code": dict(sorted(Counter(str(r["code"]) for r in records).items())),
        "records": records,
    }


def include_records(docs: Iterable[ParsedDocument]) -> dict[str, object]:
    """Every ``{% include %}`` transformation, split into fully dropped includes and partial
    transformations (S5c-D4), each marked with its page's ``include_in_corpus`` (excluded pages
    are reported too); the transformation's ``detail`` is never copied."""
    sections: dict[str, list[dict[str, object]]] = {"dropped": [], "partial": []}
    for d in docs:
        for t in d.transformations:
            if t.code in DROPPED_INCLUDES or t.code in PARTIAL_INCLUDES:
                kind = "dropped" if t.code in DROPPED_INCLUDES else "partial"
                sections[kind].append(
                    {
                        "version": d.version,
                        "path": d.path,
                        "include_in_corpus": d.include_in_corpus,
                        "code": t.code,
                        "line": t.line,
                    }
                )
    for records in sections.values():
        records.sort(key=_issue_key)
    by_rule = Counter(str(r["code"]) for rs in sections.values() for r in rs)
    return {
        "dropped": {"count": len(sections["dropped"]), "records": sections["dropped"]},
        "partial": {"count": len(sections["partial"]), "records": sections["partial"]},
        "rules": {
            code: {
                "class": "dropped" if code in DROPPED_INCLUDES else "partial",
                "count": by_rule[code],
                "handling": INCLUDE_RULES[code],
            }
            for code in sorted((*DROPPED_INCLUDES, *PARTIAL_INCLUDES))
        },
    }
