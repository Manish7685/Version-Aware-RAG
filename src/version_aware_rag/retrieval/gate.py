"""The CI regression gate (RAG-M9; design §6.1-§6.3), a pure function of its inputs.

Four gated metrics (production pipeline, ci-gate split): nDCG@10 (all items), Recall@10 (all
items), Recall@10 (T2 slice) and VCR@10 (all items, an increase is worse). Per metric:

- R = baseline - candidate (nDCG, Recall) or candidate - baseline (VCR): positive is worse;
- R-bar: exact ``Fraction`` mean for Recall@10 and VCR@10, ``math.fsum`` float64 for nDCG@10;
- bootstrap: 10,000 paired cluster resamples (T2 sets are clusters, sorted cluster ids),
  ``numpy.random.Generator(PCG64(20261120))``, the same indices for all four metrics;
  L = 0.625th percentile (``numpy.quantile``, ``linear``), S = L > 0;
- band: none (R <= 0), negligible (0 < R < tau), meaningful (tau <= R < C), gross (R >= C);
- outcome table of §6.2; overall = worst of ERROR > BLOCK > WARN > PASS + note > PASS.

Unrounded values are compared; rounding happens only for display. Stale baselines (item set or
config hash mismatch), missing items or NaN metrics are ERROR, never PASS.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

SEED = 20261120
RESAMPLES = 10_000
QUANTILE = 0.00625  # one-sided 0.025, Bonferroni over four metrics
TOLERANCES: dict[str, tuple[str, str]] = {  # design §6.3, decimal strings parsed once
    "ndcg@10": ("0.020", "0.050"),
    "recall@10": ("0.025", "0.060"),
    "recall@10_t2": ("0.050", "0.100"),
    "vcr@10": ("0.020", "0.050"),
}
EXIT = {"PASS": 0, "PASS + note": 0, "WARN": 0, "BLOCK": 1, "ERROR": 2}
ORDER = ["PASS", "PASS + note", "WARN", "BLOCK", "ERROR"]


@dataclass(frozen=True)
class MetricOutcome:
    metric: str
    r_bar: float
    lower: float
    band: str
    significant: bool
    outcome: str
    worst_items: tuple[str, ...]


def band(r: float | Fraction, tau: Fraction, cap: Fraction) -> str:
    if r <= 0:
        return "none"
    if r < tau:
        return "negligible"
    return "meaningful" if r < cap else "gross"


def outcome(b: str, significant: bool) -> str:
    if b == "none":
        return "PASS"
    if b == "negligible":
        return "PASS + note" if significant else "PASS"
    if b == "meaningful":
        return "BLOCK" if significant else "WARN"
    return "BLOCK"


def _exact(x: Any) -> Fraction:
    return x if isinstance(x, Fraction) else Fraction(str(x))


def decide(
    baseline: Mapping[str, Mapping[str, Any]],
    candidate: Mapping[str, Mapping[str, Any]],
    clusters: Mapping[str, str],
    types: Mapping[str, str],
    resamples: int = RESAMPLES,
) -> tuple[str, list[MetricOutcome], list[str]]:
    """``baseline`` and ``candidate`` map item_id -> metric -> value. Returns the overall outcome,
    per-metric outcomes and any ERROR reasons."""
    errors = []
    if set(baseline) != set(candidate):
        errors.append("item set differs from the baseline (stale baseline)")
        return "ERROR", [], errors
    items = sorted(baseline)
    cluster_ids = sorted({clusters[i] for i in items})
    members = {c: [i for i in items if clusters[i] == c] for c in cluster_ids}
    rng = np.random.Generator(np.random.PCG64(SEED))
    picks = rng.integers(
        0, len(cluster_ids), size=(resamples, len(cluster_ids))
    )  # same indices for all metrics
    results: list[MetricOutcome] = []
    for metric, (tau_s, cap_s) in TOLERANCES.items():
        source = "recall@10" if metric == "recall@10_t2" else metric
        eligible = [i for i in items if (metric != "recall@10_t2" or types[i] == "T2")]
        vals: dict[str, tuple[Any, Any]] = {
            i: (baseline[i].get(source), candidate[i].get(source)) for i in eligible
        }
        if any(
            b is None
            or c is None
            or (isinstance(b, float) and math.isnan(b))
            or (isinstance(c, float) and math.isnan(c))
            for b, c in vals.values()
        ):
            errors.append(f"{metric}: missing or NaN per-item value")
            continue
        if not eligible:
            errors.append(f"{metric}: no eligible items")
            continue
        sign = -1 if metric == "vcr@10" else 1
        if metric == "ndcg@10":
            r_items = {i: sign * (float(vals[i][0]) - float(vals[i][1])) for i in eligible}
            r_bar: float | Fraction = math.fsum(r_items.values()) / len(eligible)
        else:
            r_exact = {i: sign * (_exact(vals[i][0]) - _exact(vals[i][1])) for i in eligible}
            r_items = {i: float(v) for i, v in r_exact.items()}
            r_bar = sum(r_exact.values(), Fraction(0)) / len(eligible)
        dist = np.empty(resamples, dtype=np.float64)
        for n in range(resamples):
            chosen = [i for c in picks[n] for i in members[cluster_ids[c]] if i in r_items]
            dist[n] = math.fsum(r_items[i] for i in chosen) / len(chosen) if chosen else np.nan
        dist = dist[~np.isnan(dist)]
        lower = float(np.quantile(dist, QUANTILE, method="linear")) if dist.size else math.nan
        if math.isnan(lower):
            errors.append(f"{metric}: bootstrap undefined")
            continue
        tau, cap = Fraction(tau_s), Fraction(cap_s)
        b = band(r_bar, tau, cap)
        sig = lower > 0
        worst = tuple(
            i
            for i, _ in sorted(
                ((i, r) for i, r in r_items.items() if r > 0), key=lambda x: (-x[1], x[0])
            )[:10]
        )
        results.append(MetricOutcome(metric, float(r_bar), lower, b, sig, outcome(b, sig), worst))
    if errors:
        return "ERROR", results, errors
    overall = max((r.outcome for r in results), key=ORDER.index, default="ERROR")
    return overall, results, errors


def per_item_scores(
    items: Sequence[dict[str, Any]],
    recall10: Sequence[float],
    ndcg10: Sequence[float],
    vcr10: Sequence[float],
) -> dict[str, dict[str, Any]]:
    """Per-item scores for ``baseline.json`` (Recall@10 and VCR@10 stored as exact fractions)."""
    out = {}
    for x, r, n, v in zip(items, recall10, ndcg10, vcr10, strict=True):
        out[str(x["item_id"])] = {
            "recall@10": str(Fraction(r).limit_denominator(1000)),
            "ndcg@10": n,
            "vcr@10": str(Fraction(v).limit_denominator(1000)),
        }
    return out
