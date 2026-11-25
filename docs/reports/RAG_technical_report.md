# Version-Aware Retrieval for Software Documentation: a Benchmark, a Method Comparison and an Evaluation Gate

> **Final report at the small-pilot scope (owner decision).** Every number in §7 comes from the
> frozen test split of 25 owner-reviewed (V2) items, so confidence intervals are wide. Numbers are
> copied from the result files named next to them; nothing is estimated. Scope decisions and
> limitations: [`docs/LIMITATIONS.md`](../LIMITATIONS.md).

## Abstract

Retrieval-augmented generation over software documentation fails in a specific way: documentation
for several versions of a library is nearly identical, so retrievers return a passage that was true
for a different version, and the generator cites it confidently. We build a version-aware benchmark
over five versions of the DuckDB documentation (1.2 to 2.0), with question types that isolate stable,
version-specific, change-introduced, multi-hop and unanswerable questions, qrels anchored to verbatim
evidence spans, and automatically identified version-confuser hard negatives. We compare five
retrieval configurations (a sixth, a fine-tuned embedder, is implemented but was not trained), each
with and without a metadata version filter, on one harness with
paired cluster bootstrap confidence intervals, and add a citation-grounded answer pipeline that
abstains when evidence is missing. Finally we implement a deterministic evaluation gate for pull
requests and demonstrate it locally on the pre-review pilot only: there it passes a neutral change and
blocks injected regressions, but its tolerances are uncalibrated design defaults and it is not
enforced in CI (its GitHub Actions job is dormant). On the frozen,
owner-reviewed test split (25 items, a small pilot), the version filter is the dominant effect (it
removes version confusion entirely and raises MRR@10 by 0.21-0.25 for every method), while no learned
method differs significantly from filtered BM25.

## 1. Problem and research question

> When documentation changes across versions, do retrievers return outdated or conflicting
> information? Which methods retrieve the correct version-specific evidence, and can an answer
> pipeline cite it or abstain correctly?

Hypotheses (design §1): **H1** unfiltered retrievers return incompatible-version chunks at a high
rate; **H2** metadata filtering removes most of that confusion but not for change-history questions;
**H3** learned retrievers and rerankers improve over BM25 on version-specific questions.

## 2. Background and related work

- **Lexical and dense retrieval.** BM25 (Robertson and Zaragoza, 2009) remains a strong baseline on
  heterogeneous benchmarks (BEIR; Thakur et al., 2021). Dense bi-encoders (Sentence-BERT, Reimers and
  Gurevych, 2019; BGE, Xiao et al., 2023) embed queries and passages in one space.
- **Fusion and reranking.** Reciprocal Rank Fusion (Cormack et al., 2009) combines rankings without
  score calibration. Cross-encoders rescore query-passage pairs jointly.
- **Late interaction.** ColBERT (Khattab and Zaharia, 2020) keeps token-level embeddings and scores
  with MaxSim, between a bi-encoder's cost and a cross-encoder's quality.
- **RAG and its evaluation.** RAG (Lewis et al., 2020) conditions generation on retrieved passages;
  faithfulness and citation support are commonly measured with NLI or LLM judges (e.g. RAGAS).
- **Gap.** Public retrieval benchmarks rarely contain several near-identical versions of the same
  document, so version confusion is not measured directly.

## 3. Corpus

DuckDB documentation for 1.2, 1.3, 1.4 LTS, 1.5 and 2.0 from the pinned `duckdb-web` commit (MIT),
plus migration guides; release posts and GitHub release notes are collected as change sources but are
**restricted** (their licence is not established): they are kept local, never indexed for retrieval,
never committed and never sent to any model. The build is deterministic and reproduces the committed
manifests byte for byte; 31,438 permitted chunks carry version, section path, URL and content hash.
Chunks are compatible with a target version by the rule in design §2 (`corpus/compat.py`).

## 4. Benchmark construction

- **Types:** T1 stable, T2 version-specific (sets of the same question across versions with
  different answers), T3 change-introduced, T4 multi-hop (two or more evidence chunks), T5
  unanswerable (must abstain).
- **Drafting:** Gemini (`gemini-3.5-flash-lite`, free tier) drafts candidates from permitted passages
  only; every prompt passes a fail-closed audit that refuses any restricted line, any 10-word
  restricted sequence and any key-like string; prompts are versioned by sha256.
- **Automatic checks (V1):** schema, verbatim span re-reading, version compatibility, near-duplicates,
  inventory consistency.
- **Human verification:** the owner reviewed all 52 records (50 selected + 2 released from hold) with
  AI assistance: accept, edit or reject with reason codes R1-R9 and accepted aliases. 40 were
  retained (V2) and 12 rejected (T4 kept 4 of 7, T5 5 of 12).
- **Hard negatives:** version confusers (same page and section in an incompatible version) are tagged
  automatically for every item; lexical confusers (BM25 top-20 non-relevant chunks) are stored as
  candidates until a human confirms them.
- **Splits:** grouped by change id so that one change never spans dev and test, stratified by type,
  with leakage checks; frozen after the review (`pilot freeze`): dev 15 items (5 changes), test 25
  items (12 changes), zero leakage, test-set sha256 recorded in `benchmark/splits/test_manifest.json`.
- **Pilot composition (protocol v1):** T1 13, T2 6 (two three-version sets), T3 12, T4 7, T5 12, plus
  two T2 drafts released from hold by owner decision D2. Test split by type: T1 7, T2 5, T3 7, T4 3,
  T5 3.

## 5. Methods

All retrievers implement one interface `(query, version_filter, k)`; with a version, scores of
incompatible chunks are masked before ranking.

| Method | Implementation |
|---|---|
| BM25 | bm25s, lower-cased word tokens |
| Dense | `BAAI/bge-small-en-v1.5`, query instruction prefix, FAISS inner product |
| Fine-tuned dense | the same model fine-tuned on dev-split pairs with BM25-mined hard negatives (MNRL, Colab) |
| Hybrid | RRF (k = 60) over BM25 and dense, depth 100 |
| Cross-encoder | `cross-encoder/ms-marco-MiniLM-L-6-v2` rescoring the hybrid top 50 |
| Late interaction | `lightonai/answerai-colbert-small-v1` MaxSim rescoring the hybrid top 100 |

The ColBERT encoder is implemented on transformers following the checkpoint's configuration (query
and document prefix tokens, 32-token [MASK]-expanded queries, punctuation skiplist, 384→96
projection). Against PyLate 1.6.0 on 12 inputs it matched with a maximum absolute embedding
difference of 1.6e-7 and identical rankings (`scripts/validation/colbert_vs_pylate/`).

**Answer pipeline.** Version-filtered hybrid top 8 → audited prompt → Gemini (temperature 0) →
JSON `{abstain, short_answer, explanation, citations}`; citations must be retrieved chunk ids, and an
answer without a valid citation becomes an abstention. A passage that trips the privacy audit is
dropped and the prompt re-audited (`context_filtered`); if nothing passes, the pipeline abstains.

## 6. Experimental setup

- **Metrics (Tier H):** Recall@5/10/20, MRR@10, nDCG@10 over graded qrels (2: a compatible chunk
  containing an evidence span; 1: another compatible chunk of the same page); VCR@10, the share of the
  top 10 from incompatible versions; confuser@10, the share that are version-confuser hard negatives.
  Answers: deterministic matching against gold and aliases; T2 version correctness with the
  wrong-version rate; abstention precision, recall and F1; citation precision and evidence recall.
- **Metrics (Tier M):** citation support and faithfulness with a local NLI cross-encoder
  (`cross-encoder/nli-deberta-v3-xsmall`), the share of explanation sentences entailed by a cited (or
  any retrieved) chunk at P(entailment) ≥ θ. θ = 0.5 is **uncalibrated** (owner decision: no labelled
  calibration set).
- **Statistics:** paired cluster bootstrap (10,000 resamples, seed 20261123; a T2 set is one
  cluster), percentile 95% CIs for every metric and for differences against BM25, two-sided bootstrap
  p-values with Holm correction over all method × filter × metric comparisons.
- **Hardware:** a laptop CPU, no GPU; latency is per query, p50 and p95.

## 7. Results (frozen test split)

Source files: `results/retrieval/summary.json` (and raw `runs.jsonl`), `results/answers/summary_hybrid.json`,
`results/answers/support_hybrid.json`, `results/errors/error_analysis.json`, all produced on
2026-11-24 from the frozen test split (`benchmark/splits/test.jsonl`, test-set sha256 `3a3dcb32…9b7fe3`).
The earlier provisional results on the unreviewed pilot are in
`docs/reports/RAG-M5-M8_provisional_results.md` and are superseded.

**Coverage.** The three evaluations use different subsets of the 25 test items, for different reasons:

| Evaluation | Covered | Not covered, and why |
|---|---|---|
| Retrieval (§7.1) | 22 | T5-02, T5-08, T5-12: unanswerable by design, no evidence to retrieve (scored only for abstention in §7.2) |
| Answers (§7.2) | 22 | T2-H01@1.5, T2-H01@2.0, T3-06: no cached Gemini response, and no new requests were made (a run limitation, not a benchmark property); the three T5 items *are* covered here |
| CI gate (§8) | none of the test items | demonstrated only on the pre-review 50-item pilot |

The two sets of 22 are different: retrieval covers the three items without a cached answer, and the
answer evaluation covers the three unanswerable T5 items.

### 7.1 Retrieval

Items evaluated: 22 of 25. Excluded by design (design §5.3): T5-02, T5-08 and T5-12, the three
unanswerable T5 items, which have no evidence spans and therefore no relevant chunk to retrieve; they
are measured by abstention in §7.2. No answerable item was excluded. By type T1 7, T2 5, T3 7, T4 3;
slices change_introduced 7, hard_negative 20, multi_hop 3, stable 7, version_specific 5. Mean with
paired cluster bootstrap 95% CI (10,000 resamples; a T2 set is one cluster). Latency is per query on
a laptop CPU.

| Method | Filter | Recall@10 | MRR@10 | nDCG@10 | VCR@10 | confuser@10 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|
| BM25 | none | 0.489 [0.293, 0.716] | 0.343 [0.180, 0.541] | 0.209 [0.126, 0.310] | 0.695 [0.600, 0.771] | 0.175 [0.104, 0.265] | 0 | 1 |
| BM25 | version | 0.682 [0.480, 0.921] | 0.575 [0.401, 0.771] | 0.465 [0.321, 0.633] | 0.000 | 0.000 | 1 | 2 |
| Dense (bge-small) | none | 0.466 [0.262, 0.690] | 0.253 [0.130, 0.381] | 0.191 [0.124, 0.265] | 0.736 [0.700, 0.774] | 0.125 [0.053, 0.223] | 23 | 38 |
| Dense (bge-small) | version | 0.591 [0.396, 0.789] | 0.467 [0.283, 0.656] | 0.410 [0.315, 0.515] | 0.000 | 0.000 | 20 | 26 |
| Hybrid RRF | none | 0.466 [0.273, 0.700] | 0.313 [0.173, 0.469] | 0.211 [0.137, 0.298] | 0.745 [0.683, 0.800] | 0.190 [0.109, 0.287] | 26 | 28 |
| Hybrid RRF | version | 0.693 [0.490, 0.900] | 0.556 [0.392, 0.740] | 0.463 [0.352, 0.594] | 0.000 | 0.000 | 26 | 29 |
| Hybrid + cross-encoder | none | 0.511 [0.308, 0.738] | 0.341 [0.200, 0.485] | 0.232 [0.157, 0.313] | 0.709 [0.623, 0.783] | 0.170 [0.105, 0.255] | 4213 | 4702 |
| Hybrid + cross-encoder | version | 0.727 [0.520, 0.932] | 0.592 [0.406, 0.789] | 0.466 [0.346, 0.597] | 0.000 | 0.000 | 4679 | 4929 |
| Hybrid + ColBERT MaxSim | none | 0.523 [0.304, 0.761] | 0.270 [0.149, 0.403] | 0.219 [0.146, 0.310] | 0.700 [0.590, 0.790] | 0.150 [0.085, 0.229] | 10804 | 11778 |
| Hybrid + ColBERT MaxSim | version | 0.818 [0.674, 0.952] | 0.509 [0.367, 0.662] | 0.473 [0.378, 0.594] | 0.000 | 0.000 | 6463 | 7955 |

Differences against BM25 (same filter), mean [95% CI], and Holm-corrected significance:

| Method | Filter | ΔRecall@10 | ΔMRR@10 | ΔnDCG@10 | Holm-significant |
|---|---|---|---|---|---|
| Dense (bge-small) | none | -0.023 [-0.340, 0.283] | -0.091 [-0.321, 0.102] | -0.019 [-0.130, 0.075] | none |
| Dense (bge-small) | version | -0.091 [-0.381, 0.174] | -0.108 [-0.369, 0.164] | -0.054 [-0.215, 0.089] | none |
| Hybrid RRF | none | -0.023 [-0.237, 0.184] | -0.030 [-0.177, 0.081] | 0.001 [-0.069, 0.056] | none |
| Hybrid RRF | version | 0.011 [-0.190, 0.238] | -0.019 [-0.168, 0.122] | -0.002 [-0.083, 0.073] | none |
| Hybrid + cross-encoder | none | 0.023 [-0.237, 0.275] | -0.002 [-0.227, 0.195] | 0.023 [-0.078, 0.115] | none |
| Hybrid + cross-encoder | version | 0.045 [-0.160, 0.262] | 0.018 [-0.204, 0.259] | 0.001 [-0.122, 0.122] | none |
| Hybrid + ColBERT MaxSim | none | 0.034 [-0.239, 0.310] | -0.073 [-0.291, 0.099] | 0.009 [-0.093, 0.094] | none |
| Hybrid + ColBERT MaxSim | version | 0.136 [-0.079, 0.341] | -0.066 [-0.296, 0.138] | 0.008 [-0.128, 0.120] | none |

Significance method: two-sided paired cluster bootstrap p-value, Holm-corrected over all 24 method x
filter x metric comparisons against BM25 (alpha 0.05). The filtered-versus-unfiltered comparison is
reported per mode above and is not part of the significance test. The only difference interval that
excludes zero is unfiltered hybrid's VCR@10, 0.050 [0.010, 0.096] higher than BM25's (more version
confusion); it does not survive the Holm correction.

Slices (version-filtered runs), MRR@10 [95% CI]:

| Method | change_introduced (n=7) | hard_negative (n=20) | multi_hop (n=3) | stable (n=7) | version_specific (n=5) |
|---|---|---|---|---|---|
| BM25 | 0.929 [0.786, 1.000] | 0.557 [0.378, 0.765] | 0.250 [0.000, 0.500] | 0.628 [0.378, 0.878] | 0.200 [0.000, 0.333] |
| Dense (bge-small) | 0.518 [0.179, 0.857] | 0.507 [0.313, 0.711] | 0.167 [0.000, 0.500] | 0.449 [0.163, 0.735] | 0.600 [0.333, 1.000] |
| Hybrid RRF | 0.750 [0.429, 1.000] | 0.561 [0.400, 0.755] | 0.222 [0.000, 0.500] | 0.639 [0.327, 0.905] | 0.367 [0.333, 0.417] |
| Hybrid + cross-encoder | 0.729 [0.429, 1.000] | 0.602 [0.404, 0.809] | 0.367 [0.000, 1.000] | 0.548 [0.262, 0.857] | 0.600 [0.333, 1.000] |
| Hybrid + ColBERT MaxSim | 0.677 [0.369, 0.929] | 0.529 [0.377, 0.692] | 0.150 [0.000, 0.250] | 0.501 [0.251, 0.766] | 0.500 [0.500, 0.500] |

Hard-negative slice, unfiltered runs (version confusion is only possible without the filter),
confuser@10, VCR@10 and MRR@10:

| Method | confuser@10 | VCR@10 | MRR@10 |
|---|---|---|---|
| BM25 | 0.175 [0.105, 0.261] | 0.700 [0.600, 0.774] | 0.328 [0.169, 0.532] |
| Dense (bge-small) | 0.125 [0.052, 0.218] | 0.735 [0.695, 0.776] | 0.278 [0.150, 0.412] |
| Hybrid RRF | 0.190 [0.111, 0.284] | 0.750 [0.683, 0.805] | 0.294 [0.163, 0.444] |
| Hybrid + cross-encoder | 0.170 [0.105, 0.253] | 0.710 [0.624, 0.776] | 0.325 [0.185, 0.470] |
| Hybrid + ColBERT MaxSim | 0.150 [0.085, 0.229] | 0.720 [0.620, 0.796] | 0.272 [0.145, 0.418] |

Hard-negative tags: 20 of 22 items have version confusers (258 chunks in total); lexical-confuser
candidates 333 (unconfirmed by the owner).

**Findings.** (1) H1 holds on the test split: without the filter about 70-75% of every method's top
10 come from incompatible versions, and 13-19% are version-confuser chunks of the very page and
section that holds the answer. (2) The version filter removes version confusion entirely and raises
MRR@10 by 0.21-0.25 for every method. (3) H3 is not supported: after Holm correction no method differs
significantly from filtered BM25. Late interaction has the highest filtered Recall@10 (+0.136, CI
including zero) at about 6.5 s per query; the cross-encoder adds about 4.7 s. (4) On the version-specific
slice (5 items) filtered BM25 ranks poorly (MRR@10 0.200) while dense and the cross-encoder reach 0.600,
but with 5 items in 2 clusters this is an observation, not a finding.

### 7.2 Answers (version-filtered hybrid, Gemini, cached responses only)

The answer run used `--offline`: no request was sent to Gemini. 22 of 25 test items had a cached
response; 3 did not (T2-H01@1.5 and T2-H01@2.0, released from hold after the provisional answer run,
and T3-06, whose question was edited in review). The command reports these as missing
(`no_generator`, exit 2) and counts them as abstentions in `summary_hybrid.json`; the table below is
recomputed on the 22 answered items with the same aggregation code.

| Metric (22 answered items) | Value |
|---|---|
| Answer accuracy, answerable items | 0.789 (15 of 19); 0 unresolved |
| T2 version accuracy / wrong-version rate | 0.333 / 0.000 (1 correct, 2 abstained; 3 T2 items answered) |
| Abstention precision / recall / F1 | 0.429 / 1.000 / 0.600 |
| Over-abstention on answerable items | 0.211 (4 items) |
| Citation precision / evidence recall | 0.900 / 0.618 |
| Citation support / faithfulness (Tier M, θ 0.5 uncalibrated) | 0.633 / 0.633 (16 sentences, 15 answers) |
| Prompts with a passage dropped by the privacy audit (`context_filtered`) | 1 (T1-20, which abstained) |

Including the 3 missing items as abstentions (the command's own aggregate): answer accuracy 0.682,
abstention precision 0.300, over-abstention 0.318.

A BM25 answer run is not available: no cached Gemini responses exist for BM25 retrieval on these
items, and no new requests were made.

### 7.3 Error analysis

The automatic classification (design §5.4 categories) flagged 110 cases over 18 distinct items
across all methods and both filter modes; the owner reviewed every row and the accounting is 103
retrieval failures, 4 correct abstentions (not failures) and 3 excluded cases
([`M8_owner_error_review.md`](M8_owner_error_review.md), row-level record in
[`M8_owner_error_review.csv`](M8_owner_error_review.csv)).

- **Retrieval failures (103), automatic categories accepted:** lexical_miss 42, wrong_section 23,
  wrong_version 20 (all in unfiltered runs; in 12 of them some evidence was still retrieved, and
  chunk splitting may be a secondary factor), chunk_split 12, missing_hop 6. 32 are near misses
  (the missing evidence at ranks 11-20). Unfiltered runs account for 62, version-filtered runs for
  41, none of them wrong_version.
- **Answer outcomes (7), relabelled by the owner from the automatic over_abstention:** 3
  retrieval_miss (T2-07@1.4, T2-07@1.5, T4-13: no evidence retrieved, so abstaining was correct), 1
  privacy_filter_abstention (T1-20: a passage dropped by the privacy audit, safe abstention; retrieval
  also missed), 3 excluded (T2-H01@1.5, T2-H01@2.0, T3-06: no model generation in the offline run).
  None of the 15 non-abstained answers to answerable questions was wrong (15 of 19 correct; the
  other 4 are the abstentions above).

With the filter, the remaining retrieval failures are lexical misses, same-page wrong sections and
evidence spread over several chunks, not version confusion. Three items fail in all ten retrieval
runs: T1-20, T2-07@1.5 and T4-13.

### 7.4 Ablations

The filter on/off comparison for every method is the main ablation (§7.1). The fine-tuned embedder
was not trained and a full late-interaction index was not built (owner decisions; §9).

## 8. Evaluation-as-CI gate

`vrag eval gate check` compares per-question Recall@10, nDCG@10, Recall@10 on T2 and VCR@10 of the
production pipeline against an owner-approved baseline: exact fractions, 10,000 seeded PCG64 cluster
resamples, a one-sided lower bound at the 0.625th percentile, practical tolerances τ and hard caps C,
a fixed outcome table (PASS, PASS + note, WARN, BLOCK, ERROR) and worst-of combination. An item or
corpus change makes the baseline stale (ERROR); a configuration change is what a pull request
changes and is gated. The baseline must match the sha256 the owner records in `configs/gate.toml`.

**Local demonstration only (owner decision), on the pre-review pilot.** There is no separate ci-gate
split, so the enforced gate (`vrag eval gate check`) reports **ERROR, exit 2**, by design, and the
GitHub Actions job `eval-gate` stays dormant. The gate was demonstrated with the `--provisional` dry
run on the 50-item pilot before the owner review (38 retrieval-eligible V0 drafts; never on the frozen
test split as a gate):

- Demo patches (`docs/gate_demo/`, 2026-11-23): removing the version filter gives **BLOCK, exit 1**
  (nDCG@10 R̄ +0.256, Recall@10 R̄ +0.136, VCR@10 R̄ +0.737); a docstring-only change gives
  **PASS, exit 0** (R̄ = 0 on every metric). Swapping the production retriever for dense gives
  **BLOCK, exit 1** (nDCG@10 R̄ 0.092, lower bound 0.031, gross band).
- Calibration scenarios of design §6.4 (`results/gate/calibration.md`): the neutral change and four
  benign changes PASS; four of four injected regressions BLOCK.
- Re-running produces a byte-identical report.

These are demonstrations of the mechanism on unreviewed drafts, not an enforced held-out gate; the
thresholds τ and C are the design defaults and were not calibrated for an enforced split, and no
baseline has been approved or recorded in `configs/gate.toml`.

## 9. Limitations

The complete list is in [`docs/LIMITATIONS.md`](../LIMITATIONS.md). The ones that bound these results:

- The benchmark is a small pilot (owner decision): 40 retained items, 25 in the test split, so
  intervals are wide and only large differences are detectable; T4 and T5 are under-represented.
- Labels come from an AI-assisted owner review of Gemini drafts, not an independent annotation.
- The matcher is deliberately strict; correct answers in another surface form count as wrong.
- Late interaction is evaluated as a reranker, not as a full token-level index.
- The fine-tuned embedder was not trained (owner decision).
- The NLI judge is uncalibrated and small; Tier M numbers are diagnostics.
- The CI gate is demonstrated locally on the pre-review pilot (calibration scenarios and demo
  patches), not enforced on a separate split.
- Coverage differs by evaluation (§7): retrieval scores 22 of 25 test items (3 unanswerable T5),
  answers cover 22 of 25 (3 without a cached Gemini response).
- Restricted change sources are not used for answering (H2's changelog hop is not available).

## 10. Conclusion

On the frozen, owner-reviewed test split the strongest lever is not the retriever but the version
filter: it removes version confusion completely and lifts every method. Learned retrievers and
rerankers did not differ significantly from filtered BM25, and the cross-encoder and late interaction
add seconds of CPU latency. The same direction held on the earlier unreviewed pilot. With 22
retrieval-eligible test items, these conclusions are limited to large effects.

## References

- Cormack, G. V., Clarke, C. L. A., Büttcher, S. (2009). Reciprocal rank fusion outperforms Condorcet
  and individual rank learning methods. SIGIR.
- Khattab, O., Zaharia, M. (2020). ColBERT: Efficient and effective passage search via contextualized
  late interaction over BERT. SIGIR.
- Lewis, P., et al. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. NeurIPS.
- Reimers, N., Gurevych, I. (2019). Sentence-BERT: Sentence embeddings using Siamese BERT-networks.
  EMNLP.
- Robertson, S., Zaragoza, H. (2009). The probabilistic relevance framework: BM25 and beyond.
  Foundations and Trends in IR.
- Thakur, N., et al. (2021). BEIR: A heterogeneous benchmark for zero-shot evaluation of information
  retrieval models. NeurIPS Datasets and Benchmarks.
- Xiao, S., et al. (2023). C-Pack: Packaged resources to advance general Chinese embedding (BGE).
- Es, S., et al. (2024). RAGAS: Automated evaluation of retrieval augmented generation. EACL demo.
