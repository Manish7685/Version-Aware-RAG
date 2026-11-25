"""Evaluation harness (design §5): graded qrels from evidence spans, one run per method and mode,
per-item metrics, paired cluster bootstrap CIs and differences against the BM25 baseline, with
Holm-corrected significance (design §5.4), hard-negative tags (design §3.1, P2-R04) and slice
metrics for the version-specific, change-introduced, multi-hop and hard-negative slices
(P2-R27-R29).

Tier H only. Items without any grade-2 chunk (T5, or spans not found in a compatible chunk) are
excluded from retrieval metrics and counted separately (design §5.3).
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from version_aware_rag.retrieval import metrics as m
from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.retrieval.methods import Retriever

METRICS = ("recall@5", "recall@10", "recall@20", "mrr@10", "ndcg@10", "vcr@10", "confuser@10")
SLICE_METRICS = ("recall@10", "mrr@10", "ndcg@10", "vcr@10", "confuser@10")
HOLM_METRICS = ("recall@10", "mrr@10", "ndcg@10")
LEXICAL_DEPTH = 20


def _slice_rules() -> dict[str, Any]:
    """Slices reported per run (design §5.3; P2-R27-R29). hard_negative = items with at least one
    version-confuser chunk for a relevant chunk."""
    return {
        "version_specific": lambda x, t: x["type"] == "T2",
        "change_introduced": lambda x, t: x["type"] == "T3",
        "multi_hop": lambda x, t: x["type"] == "T4",
        "stable": lambda x, t: x["type"] == "T1",
        "hard_negative": lambda x, t: bool(t["version_confusers"]),
    }


_WORD = re.compile(r"[a-z0-9_]+")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _tokens(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _passage_lines(span: dict[str, Any]) -> tuple[str, int, int] | None:
    """``src:<path>:<a>-<b>`` passage ids cite raw source lines; None for other ids."""
    pid = str(span.get("passage_id") or "")
    m = re.fullmatch(r"src:(.+):(\d+)-(\d+)", pid)
    return (m.group(1), int(m.group(2)), int(m.group(3))) if m else None


class UnresolvedEvidenceError(ValueError):
    """An evidence span whose source file cannot be established from the corpus metadata."""


def span_source_path(span: dict[str, Any], corpus: RetrievalCorpus, item_id: str = "?") -> str:
    """The span's source file.

    A recorded ``source_path`` is used as is. Owner-edited evidence (review import) records only
    the passage id ``chunk:<chunk_id>``: the path is then read from that chunk's metadata in the
    built corpus (an exact id lookup, no text matching); the span must record a version, and it
    must equal the chunk's version. Anything else raises ``UnresolvedEvidenceError`` (fail closed: a
    silently missing page would drop grade-1 qrels and distort the scores)."""
    if span.get("source_path"):
        return str(span["source_path"])
    pid = str(span.get("passage_id") or "")
    if not pid.startswith("chunk:"):
        raise UnresolvedEvidenceError(f"{item_id}: span without source_path or chunk passage id")
    i = corpus.by_id.get(pid[len("chunk:") :])
    if i is None:
        raise UnresolvedEvidenceError(f"{item_id}: passage {pid} is not in the corpus")
    chunk = corpus.chunks[i]
    if not span.get("version"):
        raise UnresolvedEvidenceError(f"{item_id}: passage {pid} has no recorded span version")
    if span["version"] != chunk.version:
        raise UnresolvedEvidenceError(
            f"{item_id}: passage {pid} is version {chunk.version}, span says {span['version']}"
        )
    return chunk.source_path


def qrels_for(item: dict[str, Any], corpus: RetrievalCorpus) -> dict[str, int]:
    """Graded qrels from the item's evidence spans and target version (design §3.2).

    Grade 2: a compatible chunk containing a span. Spans are verbatim *source* text while chunk
    content is processed (rendered, extracted), so the match is tried exactly, then on word tokens;
    a span matched by neither falls back to the compatible chunk(s) whose source lines overlap the
    cited passage. Grade 1: another compatible chunk of the same page (version prefix stripped).
    """
    target = item["target_version"]
    mask = corpus.mask(target)
    spans = [sp for sp in item.get("evidence_spans", []) if sp.get("text")]
    item_id = str(item.get("item_id") or item.get("draft_id") or "?")
    pages = {
        "/".join(span_source_path(sp, corpus, item_id).split("/")[2:])
        for sp in item.get("evidence_spans", [])
    }
    compat = [i for i in range(len(corpus.chunks)) if mask[i]]
    qrels: dict[str, int] = {}
    for sp in spans:
        exact, toks = _norm(sp["text"]), _tokens(sp["text"])
        found = [i for i in compat if exact in _norm(corpus.chunks[i].content)]
        if not found and toks:
            found = [i for i in compat if toks in _tokens(corpus.chunks[i].content)]
        if not found and (loc := _passage_lines(sp)) is not None:
            path, a, b = loc
            found = [
                i
                for i in compat
                if corpus.chunks[i].source_path == path
                and corpus.chunks[i].line_start <= b
                and a <= corpus.chunks[i].line_end
            ]
        for i in found:
            qrels[corpus.chunks[i].chunk_id] = 2
    for i in compat:
        c = corpus.chunks[i]
        if c.chunk_id not in qrels and "/".join(c.source_path.split("/")[2:]) in pages:
            qrels[c.chunk_id] = 1
    return qrels


def _page(source_path: str) -> str:
    return "/".join(source_path.split("/")[2:])


def version_confusers(
    item: dict[str, Any], corpus: RetrievalCorpus, qrels: dict[str, int]
) -> list[str]:
    """Design §3.1 version-confusers, identified automatically: chunks of the same page (version
    prefix stripped) and the same section path as a grade-2 chunk, in an incompatible version."""
    mask = corpus.mask(item["target_version"])
    keys = {
        (_page(c.source_path), c.section_path)
        for c in (corpus.chunks[corpus.by_id[cid]] for cid, g in qrels.items() if g >= 2)
    }
    return sorted(
        c.chunk_id
        for i, c in enumerate(corpus.chunks)
        if not mask[i] and (_page(c.source_path), c.section_path) in keys
    )


def lexical_confuser_candidates(
    item: dict[str, Any], qrels: dict[str, int], bm25: Retriever | None
) -> list[str]:
    """Design §3.1 lexical confusers: BM25 top-20 non-relevant chunks of the target version. They
    stay candidates until a human confirms them and never feed a metric."""
    if bm25 is None:
        return []
    ranked = bm25.retrieve(item["question"], item["target_version"], LEXICAL_DEPTH)
    return [c for c, _ in ranked if qrels.get(c, 0) == 0]


def hard_negative_tags(
    items: Sequence[dict[str, Any]],
    corpus: RetrievalCorpus,
    qrels: dict[str, dict[str, int]],
    bm25: Retriever | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        x["draft_id"]: {
            "version_confusers": version_confusers(x, corpus, qrels[x["draft_id"]]),
            "lexical_confuser_candidates": lexical_confuser_candidates(
                x, qrels[x["draft_id"]], bm25
            ),
            "lexical_confirmed": False,  # human confirmation pending (design §3.1)
        }
        for x in items
    }


def cluster_of(item: dict[str, Any]) -> str:
    return str(item["item_id"]).split("@")[0]


@dataclass
class RunResult:
    method: str
    filtered: bool
    per_item: dict[str, list[float]]  # metric -> value per evaluated item
    latency_ms: list[float]
    rankings: list[list[str]] = field(default_factory=list)  # top-k chunk ids per evaluated item


def evaluate(
    items: Sequence[dict[str, Any]],
    corpus: RetrievalCorpus,
    retrievers: Sequence[Retriever],
    k: int = 20,
) -> tuple[list[dict[str, Any]], list[RunResult], dict[str, Any]]:
    qrels = {x["draft_id"]: qrels_for(x, corpus) for x in items}
    evaluated = [x for x in items if any(g >= 2 for g in qrels[x["draft_id"]].values())]
    excluded = [x["draft_id"] for x in items if x not in evaluated]
    bm25 = next((r for r in retrievers if r.name == "bm25"), None)
    tags = hard_negative_tags(evaluated, corpus, qrels, bm25)
    runs: list[RunResult] = []
    for r in retrievers:
        for filtered in (False, True):
            per = {name: [] for name in METRICS}  # type: dict[str, list[float]]
            lat: list[float] = []
            rankings: list[list[str]] = []
            for x in evaluated:
                t0 = time.perf_counter()
                ranked = [
                    c
                    for c, _ in r.retrieve(
                        x["question"], x["target_version"] if filtered else None, k
                    )
                ]
                lat.append((time.perf_counter() - t0) * 1000)
                rankings.append(ranked)
                q = qrels[x["draft_id"]]
                compat = corpus.mask(x["target_version"])

                def is_compat(c: str, cm: list[bool] = compat) -> bool:
                    return cm[corpus.by_id[c]]

                per["recall@5"].append(m.recall_at_k(ranked, q, 5))
                per["recall@10"].append(m.recall_at_k(ranked, q, 10))
                per["recall@20"].append(m.recall_at_k(ranked, q, 20))
                per["mrr@10"].append(m.mrr_at_k(ranked, q, 10))
                per["ndcg@10"].append(m.ndcg_at_k(ranked, q, 10))
                per["vcr@10"].append(m.vcr_at_k(ranked, is_compat, 10))
                confusers = set(tags[x["draft_id"]]["version_confusers"])
                top = ranked[:10]
                per["confuser@10"].append(
                    sum(1 for c in top if c in confusers) / len(top)
                    if confusers and top
                    else math.nan
                )
            runs.append(RunResult(r.name, filtered, per, lat, rankings))
    info = {
        "items": len(items),
        "evaluated": len(evaluated),
        "excluded_no_grade2": len(excluded),
        "excluded_ids": excluded,
        # Why each excluded item has no grade-2 chunk: unanswerable by design (no evidence spans),
        # or evidence that was not found in a compatible chunk (a data or matching problem).
        "excluded_reasons": {
            str(x.get("item_id") or x["draft_id"]): (
                "unanswerable (no evidence spans)"
                if not x.get("evidence_spans")
                else "evidence not found in a compatible chunk"
            )
            for x in items
            if x["draft_id"] in excluded
        },
        "by_type": {
            t: sum(1 for x in evaluated if x["type"] == t)
            for t in sorted({x["type"] for x in evaluated})
        },
        "slices": {
            name: [x["draft_id"] for x in evaluated if rule(x, tags[x["draft_id"]])]
            for name, rule in _slice_rules().items()
        },
        "hard_negatives": tags,
        "qrels": {x["draft_id"]: qrels[x["draft_id"]] for x in evaluated},
    }
    return evaluated, runs, info


def raw_runs(
    evaluated: Sequence[dict[str, Any]], runs: Sequence[RunResult]
) -> list[dict[str, Any]]:
    """One record per (method, filter, item): the ranked ids and every per-item metric. Ids and
    numbers only, so the raw outputs can be stored and every table regenerated from them."""
    out = []
    for r in runs:
        for n, x in enumerate(evaluated):
            out.append(
                {
                    "method": r.method,
                    "filtered": r.filtered,
                    "draft_id": x["draft_id"],
                    "item_id": x["item_id"],
                    "type": x["type"],
                    "target_version": x["target_version"],
                    "ranked": r.rankings[n] if r.rankings else [],
                    "latency_ms": r.latency_ms[n],
                    "metrics": {k: r.per_item[k][n] for k in METRICS},
                }
            )
    return out


def summarise(
    evaluated: Sequence[dict[str, Any]],
    runs: Sequence[RunResult],
    baseline: str = "bm25",
    n_resamples: int = 10_000,
    slices: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    clusters = [cluster_of(x) for x in evaluated]
    ids = [x["draft_id"] for x in evaluated]
    out: dict[str, Any] = {"runs": []}
    base = {r.filtered: r for r in runs if r.method == baseline}
    p_values: dict[str, float] = {}
    for r in runs:
        ci = m.paired_cluster_bootstrap(r.per_item, clusters, n_resamples)
        diffs = {}
        if r.method != baseline and r.filtered in base:
            for name in METRICS:
                diffs[name] = m.paired_difference_ci(
                    base[r.filtered].per_item[name], r.per_item[name], clusters, n_resamples
                )
            for name in HOLM_METRICS:
                d = [
                    b - a
                    for a, b in zip(base[r.filtered].per_item[name], r.per_item[name], strict=True)
                ]
                key = f"{r.method}|{'version' if r.filtered else 'none'}|{name}"
                p_values[key] = m.bootstrap_p_value(d, clusters, n_resamples)
        slice_ci: dict[str, Any] = {}
        for sname, members in (slices or {}).items():
            wanted = set(members)
            idx = [i for i, d in enumerate(ids) if d in wanted]
            if idx:
                slice_ci[sname] = m.paired_cluster_bootstrap(
                    {n: [r.per_item[n][i] for i in idx] for n in SLICE_METRICS},
                    [clusters[i] for i in idx],
                    n_resamples,
                )
        lat = sorted(r.latency_ms)
        out["runs"].append(
            {
                "method": r.method,
                "filtered": r.filtered,
                "metrics": ci,
                "slices": slice_ci,
                "difference_vs_" + baseline: diffs,
                "latency_ms": {
                    "p50": lat[len(lat) // 2] if lat else math.nan,
                    "p95": lat[int(0.95 * (len(lat) - 1))] if lat else math.nan,
                },
            }
        )
    rejected = m.holm(p_values)
    out["significance_vs_" + baseline] = {
        "method": "two-sided paired cluster bootstrap p-value, Holm-corrected over all "
        f"{len(p_values)} method x filter x metric comparisons against {baseline} (alpha 0.05)",
        "tests": {k: {"p": p_values[k], "significant_holm": rejected[k]} for k in sorted(p_values)},
    }
    return out


def render_markdown(summary: dict[str, Any], info: dict[str, Any], label: str) -> str:
    def fmt(c: dict[str, float]) -> str:
        if math.isnan(c["mean"]):
            return "n/a"
        return f"{c['mean']:.3f} [{c['ci_low']:.3f}, {c['ci_high']:.3f}]"

    lines = [
        f"# Retrieval evaluation ({label})",
        "",
        f"Evaluated items: {info['evaluated']} of {info['items']} "
        f"(excluded without a grade-2 chunk: {info['excluded_no_grade2']}); "
        f"by type: {json.dumps(info['by_type'])}. Mean with paired cluster bootstrap "
        "95% CI (10,000 resamples; a T2 set is one cluster).",
        "",
        *(
            [
                "Excluded: "
                + "; ".join(f"{i} ({why})" for i, why in sorted(info["excluded_reasons"].items()))
                + ".",
                "",
            ]
            if info.get("excluded_reasons")
            else []
        ),
        "| Method | Filter | " + " | ".join(METRICS) + " | p50 ms |",
        "|---|---|" + "---|" * (len(METRICS) + 1),
    ]
    for r in summary["runs"]:
        lines.append(
            f"| {r['method']} | {'version' if r['filtered'] else 'none'} | "
            + " | ".join(fmt(r["metrics"][n]) for n in METRICS)
            + f" | {r['latency_ms']['p50']:.0f} |"
        )
    lines += ["", "Differences against BM25 (same filter mode), mean [95% CI]:", ""]
    for r in summary["runs"]:
        d = r.get("difference_vs_bm25") or {}
        if d:
            lines.append(
                f"- {r['method']} ({'version' if r['filtered'] else 'none'}): "
                + "; ".join(
                    f"{n} {fmt(d[n])}" for n in ("recall@10", "mrr@10", "ndcg@10", "vcr@10")
                )
            )
    sig = summary.get("significance_vs_bm25")
    if sig:
        hits = [k for k, v in sig["tests"].items() if v["significant_holm"]]
        lines += [
            "",
            f"Significance: {sig['method']}.",
            "",
            "- Significant after Holm: " + (", ".join(hits) if hits else "none") + ".",
        ]
    if any(r.get("slices") for r in summary["runs"]):
        sizes = ", ".join(f"{k} {len(v)}" for k, v in info.get("slices", {}).items())
        lines += [
            "",
            "## Slices",
            "",
            f"Slice sizes: {sizes}. confuser@10 is the share of the top 10 that are "
            "version-confuser chunks (same page and section, incompatible version); n/a when no "
            "item in the slice has one.",
            "",
            "| Method | Filter | Slice | " + " | ".join(SLICE_METRICS) + " |",
            "|---|---|---|" + "---|" * len(SLICE_METRICS),
        ]
        for r in summary["runs"]:
            for sname, sm in r.get("slices", {}).items():
                lines.append(
                    f"| {r['method']} | {'version' if r['filtered'] else 'none'} | {sname} | "
                    + " | ".join(fmt(sm[n]) for n in SLICE_METRICS)
                    + " |"
                )
    return "\n".join(lines) + "\n"
