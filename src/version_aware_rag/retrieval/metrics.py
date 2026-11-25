"""Retrieval metrics (design §5.3) and the paired cluster bootstrap (design §5.4).

Own implementations, unit-tested against hand-computed values. Qrels are graded: 2 = a chunk that
contains a required evidence span in a compatible version, 1 = the same page in a compatible
version without a sufficient span, 0 otherwise.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence


def recall_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int) -> float:
    relevant = {c for c, g in qrels.items() if g >= 2}
    if not relevant:
        return math.nan
    return len(relevant & set(ranked[:k])) / len(relevant)


def mrr_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int = 10) -> float:
    for i, c in enumerate(ranked[:k], 1):
        if qrels.get(c, 0) >= 2:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[str], qrels: Mapping[str, int], k: int = 10) -> float:
    dcg = sum((2 ** qrels.get(c, 0) - 1) / math.log2(i + 1) for i, c in enumerate(ranked[:k], 1))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    return dcg / idcg if idcg > 0 else math.nan


def vcr_at_k(ranked: Sequence[str], compatible: Callable[[str], bool], k: int = 10) -> float:
    """Version Confusion Rate@k: share of the top-k chunks incompatible with the target version."""
    top = list(ranked[:k])
    return sum(1 for c in top if not compatible(c)) / len(top) if top else math.nan


def paired_cluster_bootstrap(
    values: Mapping[str, Sequence[float]],
    clusters: Sequence[str],
    n_resamples: int = 10_000,
    seed: int = 20261123,
) -> dict[str, dict[str, float]]:
    """Mean and percentile 95% CI per method, resampling clusters (a T2 set is one cluster); every
    method shares every resample (paired). NaN items are dropped per method."""
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        groups.setdefault(c, []).append(i)
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not security
    samples: dict[str, list[float]] = {m: [] for m in values}
    for _ in range(n_resamples):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        idx = [i for k in pick for i in groups[k]]
        for m, vals in values.items():
            xs = [vals[i] for i in idx if not math.isnan(vals[i])]
            samples[m].append(sum(xs) / len(xs) if xs else math.nan)
    out = {}
    for m, vals in values.items():
        xs = [v for v in vals if not math.isnan(v)]
        s = sorted(v for v in samples[m] if not math.isnan(v))
        lo = s[int(0.025 * (len(s) - 1))] if s else math.nan
        hi = s[int(0.975 * (len(s) - 1))] if s else math.nan
        out[m] = {
            "mean": sum(xs) / len(xs) if xs else math.nan,
            "ci_low": lo,
            "ci_high": hi,
            "n": len(xs),
        }
    return out


def paired_difference_ci(
    a: Sequence[float],
    b: Sequence[float],
    clusters: Sequence[str],
    n_resamples: int = 10_000,
    seed: int = 20261123,
) -> dict[str, float]:
    """Mean of (b - a) with a percentile 95% CI from the same paired cluster resamples."""
    diff = [
        y - x if not (math.isnan(x) or math.isnan(y)) else math.nan
        for x, y in zip(a, b, strict=True)
    ]
    return paired_cluster_bootstrap({"diff": diff}, clusters, n_resamples, seed)["diff"]


def bootstrap_p_value(
    diff: Sequence[float],
    clusters: Sequence[str],
    n_resamples: int = 10_000,
    seed: int = 20261123,
) -> float:
    """Two-sided paired cluster bootstrap p-value for a mean difference of zero: twice the smaller
    share of resampled means on either side of zero (capped at 1). Same resamples as the CIs."""
    groups: dict[str, list[int]] = {}
    for i, c in enumerate(clusters):
        groups.setdefault(c, []).append(i)
    keys = sorted(groups)
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not security
    le = ge = total = 0
    for _ in range(n_resamples):
        pick = [keys[rng.randrange(len(keys))] for _ in keys]
        xs = [diff[i] for k in pick for i in groups[k] if not math.isnan(diff[i])]
        if not xs:
            continue
        mean = sum(xs) / len(xs)
        total += 1
        le += mean <= 0
        ge += mean >= 0
    if total == 0:
        return math.nan
    return min(1.0, 2 * min(le, ge) / total)


def holm(p_values: Mapping[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm-Bonferroni step-down (design §5.4): which hypotheses are rejected at family-wise
    level ``alpha``. NaN p-values are never rejected."""
    ordered = sorted((p, k) for k, p in p_values.items() if not math.isnan(p))
    rejected = {k: False for k in p_values}
    m = len(ordered)
    for rank, (p, k) in enumerate(ordered):
        if p > alpha / (m - rank):
            break
        rejected[k] = True
    return rejected
