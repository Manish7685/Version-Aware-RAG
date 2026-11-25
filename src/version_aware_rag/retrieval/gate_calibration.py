"""Gate calibration (RAG-M9, design §6.3-§6.4): run the decision rule on neutral changes, benign
perturbations and injected regressions against the production baseline, and report the
granularity of every gated metric for the split size. The report is evidence for the owner's
threshold decision (IR-9); it never changes tau, C or alpha itself.

Scenarios (each a candidate retriever scored with the same per-item function as the gate):

- neutral: the unchanged production retriever (must give Δ = 0 and PASS);
- benign: RRF k 50, 70 and k 1 with depth 10, fusion depth 80 (natural spread; should mostly
  PASS or WARN);
- injected regressions (must all BLOCK): the version filter removed, RRF degraded (fusion
  depth 3: each list cut to its top 3), 10% of chunks deleted (deterministic by chunk-id hash),
  a weaker retriever swapped in (dense only, no BM25).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from version_aware_rag.retrieval import gate
from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.retrieval.methods import HybridRetriever, Retriever


@dataclass
class _Unfiltered:
    base: Retriever
    name: str = "unfiltered"

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        return self.base.retrieve(query, None, k)


@dataclass
class _Deleted:
    base: Retriever
    deleted: frozenset[str]
    name: str = "deleted10"

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        hits = self.base.retrieve(query, version, k * 3)
        return [h for h in hits if h[0] not in self.deleted][:k]


def deleted_ids(corpus: RetrievalCorpus, share: float = 0.10) -> frozenset[str]:
    """A deterministic ``share`` of chunk ids (by sha256), the "10% of chunks deleted" injection."""
    cut = int(share * 2**32)
    return frozenset(
        c.chunk_id
        for c in corpus.chunks
        if int(hashlib.sha256(c.chunk_id.encode()).hexdigest()[:8], 16) < cut
    )


def scenarios(hybrid: HybridRetriever, corpus: RetrievalCorpus) -> list[tuple[str, str, Retriever]]:
    """(name, expected outcome class, retriever). Expected: PASS, PASS_OR_WARN or BLOCK."""
    bm25, dense = hybrid.bm25, hybrid.dense
    return [
        ("neutral: unchanged production retriever", "PASS", hybrid),
        ("benign: RRF k = 50", "PASS_OR_WARN", HybridRetriever(bm25, dense, rrf_k=50)),
        ("benign: RRF k = 70", "PASS_OR_WARN", HybridRetriever(bm25, dense, rrf_k=70)),
        ("benign: fusion depth 80", "PASS_OR_WARN", HybridRetriever(bm25, dense, depth=80)),
        ("injected: version filter removed", "BLOCK", _Unfiltered(hybrid)),
        # k = 1 with depth 10 only reorders two interleaved lists; measured on the pilot it does
        # not lower quality (2026-11-24 investigation), so it is a benign probe, not an injection.
        ("benign: RRF k = 1, depth 10", "PASS_OR_WARN", HybridRetriever(bm25, dense, 1, 10)),
        ("injected: RRF degraded (fusion depth 3)", "BLOCK", HybridRetriever(bm25, dense, depth=3)),
        ("injected: 10% of chunks deleted", "BLOCK", _Deleted(hybrid, deleted_ids(corpus))),
        ("injected: weaker retriever (dense only)", "BLOCK", dense),
    ]


def granularity(scores: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """How much one item moves each gated mean, against τ and C (design §6.3)."""
    n = len(scores)
    n_t2 = sum(1 for s in scores.values() if s["type"] == "T2")
    out: dict[str, Any] = {"items": n, "t2_items": n_t2}
    for metric, (tau_s, cap_s) in gate.TOLERANCES.items():
        size = n_t2 if metric == "recall@10_t2" else n
        tau, cap = Fraction(tau_s), Fraction(cap_s)
        one = Fraction(1, size) if size else None
        out[metric] = {
            "tau": float(tau),
            "cap": float(cap),
            "one_item_moves_mean_by": float(one) if one else None,
            "items_to_reach_tau": float(tau / one) if one else None,
            "items_to_reach_cap": float(cap / one) if one else None,
            "single_item_can_block": bool(one and one >= cap),
            "single_item_reaches_tau": bool(one and one >= tau),
        }
    return out


def run(
    baseline: dict[str, dict[str, Any]],
    candidates: list[tuple[str, str, Retriever]],
    score: Callable[[Retriever], dict[str, dict[str, Any]]],
    resamples: int,
) -> list[dict[str, Any]]:
    clusters = {i: s["cluster"] for i, s in baseline.items()}
    types = {i: s["type"] for i, s in baseline.items()}
    rows = []
    for name, expected, retriever in candidates:
        cand = score(retriever)
        overall, results, errors = gate.decide(baseline, cand, clusters, types, resamples)
        ok = overall == expected or (
            expected == "PASS_OR_WARN" and overall in ("PASS", "PASS + note", "WARN")
        )
        rows.append(
            {
                "scenario": name,
                "expected": expected,
                "outcome": overall,
                "as_expected": ok,
                "metrics": {
                    r.metric: {
                        "r_bar": r.r_bar,
                        "lower": r.lower,
                        "band": r.band,
                        "outcome": r.outcome,
                    }
                    for r in results
                },
                "errors": errors,
            }
        )
    return rows


def render(label: str, gran: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        f"# Gate calibration ({label})",
        "",
        f"Items: {gran['items']} retrieval-eligible, of which T2: {gran['t2_items']}. "
        "Thresholds are the design §6.3 values; this report does not change them "
        "(IR-9: owner approval).",
        "",
        "## Granularity",
        "",
        "| Metric | τ | C | one item moves the mean by | items to reach τ | items to reach C "
        "| one item can block |",
        "|---|---|---|---|---|---|---|",
    ]
    for metric in gate.TOLERANCES:
        g = gran[metric]
        one = g["one_item_moves_mean_by"]
        lines.append(
            f"| {metric} | {g['tau']:.3f} | {g['cap']:.3f} | "
            f"{'n/a' if one is None else f'{one:.4f}'} | "
            f"{'n/a' if g['items_to_reach_tau'] is None else f'{g["items_to_reach_tau"]:.1f}'} | "
            f"{'n/a' if g['items_to_reach_cap'] is None else f'{g["items_to_reach_cap"]:.1f}'} | "
            f"{'YES' if g['single_item_can_block'] else 'no'} |"
        )
    lines += [
        "",
        "(For continuous metrics such as nDCG@10 and VCR@10, one item can move the mean by at most "
        "this amount.)",
        "",
        "## Scenarios (design §6.4)",
        "",
        "| Scenario | Expected | Outcome | As expected | nDCG@10 R̄ | Recall@10 R̄ "
        "| T2 Recall@10 R̄ | VCR@10 R̄ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        m = r["metrics"]

        def rb(k: str, mm: dict[str, Any] = m) -> str:
            return f"{mm[k]['r_bar']:+.4f} ({mm[k]['outcome']})" if k in mm else "n/a"

        lines.append(
            f"| {r['scenario']} | {r['expected']} | **{r['outcome']}** | "
            f"{'yes' if r['as_expected'] else '**NO**'} | {rb('ndcg@10')} | {rb('recall@10')} | "
            f"{rb('recall@10_t2')} | {rb('vcr@10')} |"
        )
    lines += ["", "R̄ is the mean per-item drop (positive = worse; for VCR, an increase)."]
    return "\n".join(lines) + "\n"
