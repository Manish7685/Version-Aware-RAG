# M8 owner review of the error analysis (frozen test split)

Reviewed by the owner on 2026-11-24. Source: the automatic error analysis of `vrag eval errors` on the
frozen 25-item test split (`results/errors/error_analysis.json`, design §5.4 categories), which is
left unchanged. The row-by-row record, with the automatic category, the evidence, the AI-assisted proposal
and the owner's decision and note, is
[`M8_owner_error_review.csv`](M8_owner_error_review.csv) (110 rows, ids E001-E110).

Evidence for each row was recomputed from the stored raw outputs only (rankings in
`results/retrieval/runs.jsonl`, relevance grades from the frozen items, version-confuser tags,
answer flags): ids, ranks and counts, no document text.

## Decisions

- **103 retrieval failures:** every automatic category accepted. For the 12 `wrong_version` rows
  where some evidence was still retrieved (E001, E004, E006, E011, E023, E030, E046, E049, E052,
  E068, E071, E074), the category is kept and an owner note records that chunk splitting may be a
  secondary contributing factor.
- **7 answer outcomes** (all automatically labelled `over_abstention`; none is a failure after review) were relabelled:
  - E104, E105, E106 (T2-07@1.4, T2-07@1.5, T4-13): `retrieval_miss`. No evidence was retrieved
    (recall@10 0), so the abstention was correct; the failure is in retrieval.
  - E107, E108, E109 (T2-H01@1.5, T2-H01@2.0, T3-06): `exclude`. No model generation occurred (no
    cached response in the offline run); not counted as model errors.
  - E110 (T1-20): `privacy_filter_abstention`. The privacy audit dropped a passage and the pipeline
    abstained safely, which was correct; retrieval also missed the required evidence.

## Final counts

| Group | Category | Count |
|---|---|---|
| Retrieval failures (103) | lexical_miss | 42 |
| | wrong_section | 23 |
| | wrong_version (all in unfiltered runs; 12 with partial evidence) | 20 |
| | chunk_split | 12 |
| | missing_hop | 6 |
| Answer outcomes (7) | retrieval_miss (correct abstention, evidence not retrieved) | 3 |
| | privacy_filter_abstention (correct, safe abstention) | 1 |
| | exclude (no model generation) | 3 |
| | over_abstention (model abstained despite retrieved evidence) | 0 |

- Accounting of the 110 reviewed rows: **103 retrieval failures** (over all 18 items), **4 correct
  abstentions** (3 retrieval-miss abstentions and 1 privacy-filter abstention; not failures) and
  **3 excluded** cases (no model generation).
- Near misses (missing evidence ranked 11-20): 32 retrieval failures.
- By filter mode: unfiltered runs 62 (wrong_version 20, lexical_miss 28, wrong_section 12,
  chunk_split 2); version-filtered runs 41 (lexical_miss 14, wrong_section 11, chunk_split 10,
  missing_hop 6, wrong_version 0).
- None of the 15 non-abstained answers to answerable questions was wrong (15 of 19 correct on the
  22 answered items): every flagged answer outcome is either a correct abstention caused by a
  retrieval miss, a correct privacy-filter abstention, or a case with no generation.
- Items failing in all 10 retrieval runs: T1-20, T2-07@1.5, T4-13.
