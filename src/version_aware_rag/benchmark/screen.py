"""Protocol v1 drafting checks for T4 and T5 (owner decision D3, 2026-11-24), as a deterministic,
advisory screen over permitted chunks. A flag sends the item to the owner with its reason; it
never rejects, accepts or edits anything.

- **T5-S1 feature absent:** every feature identifier named in the item (backticked or snake_case
  words in the question and abstain reason) is absent from all chunks of the target version and
  present in at least one other version.
- **T5-S2 no documented alternative:** no single target-version chunk covers most of the
  question's content terms (a sign that another function or section answers it; the screen found
  the lambda-scoping and epoch_ms cases this way).
- **T4-S1 no single span suffices:** no evidence span lies in a chunk that contains the gold answer
  and also covers most of the question's content terms.

Coverage is lexical, so it is a screen: a miss (for example a composite answer such as
``day(last_day(d))``) still needs human judgement.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from version_aware_rag.retrieval.corpus import RetrievalCorpus

COVERAGE_FLAG = 0.8
MIN_TERMS = 3
_WORD = re.compile(r"[a-z0-9_]+")
_IDENT = re.compile(r"`([^`]+)`|\b([a-z]+(?:_[a-z0-9]+)+)\b|\b([A-Z]{2,}(?: [A-Z]{2,})*)\b")
STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "duckdb",
        "for",
        "from",
        "function",
        "functions",
        "how",
        "i",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "use",
        "used",
        "using",
        "version",
        "versions",
        "what",
        "when",
        "which",
        "with",
        "would",
        "you",
        "your",
        "there",
        "their",
        "that",
        "these",
        "those",
        "than",
        "then",
        "also",
        "any",
        "each",
    ]
)


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in STOP}


def feature_identifiers(item: dict[str, Any]) -> set[str]:
    text = f"{item.get('question') or ''} {item.get('abstain_reason') or ''}"
    out = set()
    for a, b, c in _IDENT.findall(text):
        tok = (a or b or c).strip().lower()
        if tok and not re.fullmatch(r"\d+(\.\d+)*|duckdb", tok):
            out.add(tok)
    # "merge" inside "merge into", "uuid" inside "uuid_extract_version": keep the longest names.
    return {t for t in out if not any(t != o and t in o for o in out)}


@dataclass
class ScreenResult:
    item_id: str
    type: str
    flags: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)


def _best_coverage(terms: set[str], chunks: Sequence[Any]) -> tuple[float, str | None]:
    best, where = 0.0, None
    for c in chunks:
        cov = len(terms & _terms(c.content)) / len(terms) if terms else 0.0
        if cov > best:
            best, where = cov, c.chunk_id
    return best, where


def screen_item(item: dict[str, Any], corpus: RetrievalCorpus) -> ScreenResult:
    res = ScreenResult(str(item["item_id"]), str(item["type"]))
    target = item["target_version"]
    mask = corpus.mask(target)
    in_target = [c for i, c in enumerate(corpus.chunks) if mask[i]]
    if item["type"] == "T5":
        idents = feature_identifiers(item)
        present_target = sorted(t for t in idents if any(t in c.content.lower() for c in in_target))
        present_elsewhere = sorted(
            t
            for t in idents
            if any(t in c.content.lower() for i, c in enumerate(corpus.chunks) if not mask[i])
        )
        res.facts.update(
            identifiers=sorted(idents),
            present_in_target=present_target,
            present_elsewhere=present_elsewhere,
        )
        if not idents:
            res.flags.append("T5-S1: no feature identifier named; absence cannot be checked")
        elif present_target:
            res.flags.append(f"T5-S1: {', '.join(present_target)} documented in {target}")
        elif not present_elsewhere:
            res.flags.append("T5-S1: feature not documented in any version")
        terms = _terms(item["question"]) - idents - set().union(*map(_terms, idents or {""}))
        cov, where = _best_coverage(terms, in_target)
        res.facts.update(question_terms=len(terms), best_coverage=round(cov, 2), chunk=where)
        if len(terms) >= MIN_TERMS and cov >= COVERAGE_FLAG:
            res.flags.append(
                f"T5-S2: chunk {where} of {target} covers {cov:.0%} of the question's terms "
                "(possible documented alternative)"
            )
    if item["type"] == "T4":
        gold = " ".join(_WORD.findall((item.get("gold_short_answer") or "").lower()))
        terms = _terms(item["question"])
        for sp in item.get("evidence_spans") or []:
            text = sp.get("text") or ""
            holders = [c for c in in_target if text and text in c.content]
            for c in holders[:1]:
                content = " ".join(_WORD.findall(c.content.lower()))
                cov = len(terms & _terms(c.content)) / len(terms) if terms else 0.0
                if gold and gold in content and cov >= COVERAGE_FLAG:
                    res.flags.append(
                        f"T4-S1: span in chunk {c.chunk_id} contains the gold and covers "
                        f"{cov:.0%} of the question (may answer alone)"
                    )
    return res


def render(results: Sequence[ScreenResult], label: str) -> str:
    lines = [
        f"# T4/T5 drafting-check screen ({label})",
        "",
        "Advisory, lexical (protocol v1 checks T4-S1, T5-S1, T5-S2; owner decision D3). "
        "A flag means "
        "the owner should look; it decides nothing.",
        "",
        "| Item | Type | Flags |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.item_id} | {r.type} | {'; '.join(r.flags) or 'none'} |")
    return "\n".join(lines) + "\n"
