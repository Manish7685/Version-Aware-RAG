# RAG-M5 to M8: provisional retrieval and answer results

> **Superseded (2026-11-24).** These are provisional results on the unreviewed 50-item pilot. The final results on the frozen, owner-reviewed test split are in [`RAG_technical_report.md`](RAG_technical_report.md) §7 and README §11. This page is kept unchanged below for the record.

> **PROVISIONAL.** Every number below comes from the 50 pilot items, which are **V0 drafts that the
> owner has not yet reviewed**. Design §5.1 reserves headline results for V2 (human-verified)
> items, so these are development measurements, not benchmark claims. They regenerate from the raw
> outputs with the commands at the end. Nothing here is estimated.

Run date: 2026-11-23. Corpus: 31,438 permitted chunks (MIT documentation and migration guides of
DuckDB 1.2, 1.3, 1.4, 1.5 and 2.0). The 220 release-post chunks are never indexed. Hardware: local
CPU (no CUDA).

## Retrieval (M5/M6)

38 of the 50 items are retrieval-eligible: T1 13, T2 6, T3 12, T4 7. The 12 T5 items have no
evidence by construction. Every method uses the same items, qrels, harness and bootstrap (10,000
paired cluster resamples, a T2 set is one cluster, seed 20261123), and runs both unfiltered and
version-filtered.

| Method | Filter | Recall@10 | MRR@10 | nDCG@10 | VCR@10 | p50 latency |
|---|---|---|---|---|---|---|
| BM25 (bm25s) | none | 0.649 | 0.406 | 0.258 | 0.711 | 1 ms |
| BM25 | version | 0.770 | 0.742 | 0.528 | 0.000 | 2 ms |
| Dense (bge-small-en-v1.5, FAISS) | none | 0.520 | 0.310 | 0.207 | 0.734 | 40 ms |
| Dense | version | 0.658 | 0.558 | 0.428 | 0.000 | 35 ms |
| Hybrid (RRF k=60) | none | 0.658 | 0.416 | 0.259 | 0.726 | 39 ms |
| Hybrid | version | 0.774 | 0.746 | 0.520 | 0.000 | 37 ms |
| Hybrid + cross-encoder (ms-marco-MiniLM-L-6-v2, top 50) | none | 0.634 | 0.346 | 0.241 | 0.721 | 3,361 ms |
| Hybrid + cross-encoder | version | 0.768 | 0.664 | 0.491 | 0.000 | 3,395 ms |
| Hybrid + late interaction (answerai-colbert-small-v1 MaxSim, top 100) | none | 0.507 | 0.375 | 0.239 | 0.711 | 7,390 ms |
| Hybrid + late interaction | version | 0.763 | 0.632 | 0.490 | 0.000 | 7,937 ms |

95% confidence intervals for every cell, and paired differences against BM25, are in
`results/retrieval/report.md` and `summary.json`.

**What the data supports:**

- **Version filtering is the dominant effect.** For every method it raises MRR@10, for example BM25
  from 0.406 to 0.742. It also removes version confusion: without the filter, about 71% of the
  top-10 chunks belong to an incompatible version.
- **Dense is the largest difference from BM25 when filtered,** but it is not significant after
  correction. Its unadjusted intervals exclude zero (MRR@10 −0.184, 95% CI [−0.318, −0.053]; nDCG@10
  −0.100, 95% CI [−0.174, −0.031]), but with Holm correction over the 24 method × filter × metric
  comparisons (two-sided paired cluster bootstrap p-values, added in the final audit) **no**
  comparison against BM25 is significant. An earlier version of this report called dense
  "significantly worse" on the unadjusted interval alone; that claim is withdrawn.
- **No difference against BM25 is significant after correction.** Hybrid (MRR +0.004 [−0.062, 0.067]), the
  cross-encoder (−0.078 [−0.193, 0.036]) and late interaction (−0.110 [−0.229, 0.009]) all have
  intervals that include 0. With 38 provisional items, **no method can be claimed to beat BM25**.
- **Reranking costs latency for no measured gain.** `vrag serve` warms the models at startup, so
  the first request is not a cold start (the first dense query took 55 s cold, 0.2 s warmed). On CPU, the cross-encoder costs about 3.4 s and
  late interaction about 7.4 s per query at p50.

**Method notes:**

- **Late interaction** runs locally as a reranker over the hybrid top 100. A full token-level index
  of all 31,438 chunks (about 1.8 GB of token vectors) is the Colab variant and **has not been
  run**.
- **The ColBERT encoder is implemented on transformers.** It follows the checkpoint's own
  configuration: the `[unused0]`/`[unused1]` prefixes, 32-token queries with `[MASK]` expansion, the
  punctuation skiplist and the 384→96 projection. PyLate 1.6 was rejected because it pins an older
  torch/transformers stack.
- **The fine-tuned embedder has not been trained.** It must train on dev-split pages only, and the
  split is frozen after the owner review. `vrag index finetune-data` refuses until then, and the
  Colab script is `scripts/colab/finetune_embedder.py`.

## Answers (M7)

The production pipeline: version-filtered hybrid, top 8 permitted chunks, `gemini-3.5-flash-lite`
at temperature 0, the structured answer contract, and citations restricted to retrieved chunk ids.
An answer without a valid citation is turned into an abstention. 50 requests in total, all through
the shared quota ledger and the fail-closed privacy audit.

| Metric | Value |
|---|---|
| Answer accuracy, answerable items (deterministic matcher) | 0.789 (30 of 38) |
| Unresolved, needing owner grading | 0 |
| T2 outcomes (6 items) | 4 correct, 0 wrong version, 2 abstained |
| T2 version accuracy / wrong-version rate | 0.667 / 0.000 |
| Abstention precision / recall / F1 (T5 = should abstain) | 0.692 / 0.750 / 0.720 |
| Over-abstention on answerable items | 0.105 (4 of 38) |
| Citation precision (cited chunk on a relevant page) | 0.797 |
| Evidence recall (grade-2 chunks cited) | 0.684 |

**Privacy audit in practice.** On 3 items, a retrieved MIT documentation chunk shared a 10-word
sequence with a restricted release post. The pipeline:

1. drops each chunk that trips the audit on its own;
2. audits the rebuilt prompt again;
3. sends it only if it passes.

All three passed and are flagged `context_filtered`. Before this rule, those 3 items were blocked
outright. The rule is listed for owner review.

## Error taxonomy (M8)

The production pipeline fails 11 of 50 items, fewer than the design's ≥30-failure taxonomy target.
That target needs the full V2 benchmark, and failures are not padded.

| Failure | Count | Attribution |
|---|---|---|
| Correct fact, different surface form (e.g. "throws an error" for gold "throw an error") | 3 (T3) | **matcher strictness**: the V0 drafts' accepted aliases are incomplete; the owner review adds aliases, and the deterministic matcher is deliberately not made fuzzy |
| Wrong answer with grade-2 evidence in context (a yes/no question answered with a function name) | 1 (T3) | generation |
| Abstained although grade-2 evidence was in context | 2 (one T2 set, versions 1.4 and 1.5) | generation |
| Abstained, evidence not retrieved (same page only) | 1 (T1) | retrieval. This is a `context_filtered` item: the dropped chunk was likely its evidence, a cost of the privacy boundary |
| Abstained, evidence not retrieved at all | 1 (T4) | retrieval |
| Answered a T5 question the documentation cannot answer | 3 | generation (abstention) |

**Reading:**

- **Most failures are not retrieval faults.** 3 of 11 are strict-matcher near-misses, 6 are
  generation faults and 2 are retrieval faults. The answer accuracy of 0.789 is therefore a strict
  lower bound until the owner review completes the aliases.
- **T3 (feature-introduced-in) questions** are the weakest answer type.
- **Wrong-version answers did not occur** with filtering on.

## CI gate (M9)

`vrag eval gate check` implements design §6.2 exactly:

- exact fractions;
- 10,000 PCG64 cluster resamples;
- the 0.625th percentile lower bound;
- the τ/C bands;
- the outcome table, worst-of combination and exit codes.

On the provisional pilot it was exercised in three ways:

- **Identical scores:** PASS, exit 0.
- **Simulated regression** (inflated baseline): BLOCK, exit 1.
- **Without the frozen V2 ci-gate split:** ERROR, exit 2. This is the enforced mode until that split
  exists.

## Reproduce

```
python -m uv run vrag index build --methods bm25,dense
python -m uv run vrag eval retrieval --methods bm25,dense,hybrid,rerank,late
python -m uv run vrag eval answers --method hybrid --offline   # re-scores from the local answer cache
python -m uv run vrag eval gate baseline --provisional && python -m uv run vrag eval gate check --provisional
```

Raw outputs: `results/retrieval/`, `results/answers/` and `results/gate/`, plus the answer cache
`data/build/answer_cache.jsonl`. All of these are git-ignored and stay local.

## Final-audit additions (2026-11-23, same provisional items)

Regenerated with `vrag eval retrieval --methods bm25,dense,hybrid,rerank,late` and `vrag eval
errors`. Retrieval metrics are identical to the tables above (deterministic). Additions:

- **Holm-corrected significance** (24 comparisons against BM25): none significant (see above).
- **Hard negatives:** 32 of 38 items have version-confuser chunks (174 in total). Without the filter,
  23-29% of every method's top 10 are version confusers (confuser@10); with it, 0%.
- **Slices (filtered MRR@10):** BM25 and hybrid score 1.000 on the 6 version-specific items and
  0.67-0.68 on the 7 multi-hop items; dense is lowest on every slice. Intervals are wide.
- **Error analysis:** 184 failures over 31 distinct items across all methods and both filter modes
  (wrong_version 48, all unfiltered; wrong_section 34; lexical_miss 33; chunk_split 29; missing_hop
  29; answer failures 11), with 45 near misses. The categories are an automatic first pass from ids,
  qrels and tags; the owner's confirmation is pending. File: `results/errors/error_analysis.md`.
- **Citation support / faithfulness (Tier M, θ 0.5 uncalibrated):** 0.527 / 0.541 over 39
  explanation sentences of 37 non-abstained answers (`results/answers/support_hybrid.json`).
- **Latency:** the audit run shared the CPU with other jobs, so its latencies (for example rerank p50
  7.8 s) are not used; the p50 values in the table above come from the earlier run on an idle machine.
