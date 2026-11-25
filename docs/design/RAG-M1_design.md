# RAG-M1 Design: Version-Aware Retrieval Benchmark and Regression Gate (revision 3)

Status: **APPROVED: final RAG-M1 design** (owner decision D14, 2026-11-20, with the final clarifications recorded in §6.2, §2.1, §3.3 and §9.3). RAG-M1 is complete. Implementation requirements carried forward are listed in §9.4.

- Evidence: `RAG-M1_research_evidence.md`.
- Exact pins and reproduction commands: `RAG-M1_version_manifest.md`.
- No application code has been written, and no experimental results exist. All numbers below are design targets or proposals unless marked otherwise.
- Requirement IDs `P2-Rxx` refer to the roadmap PDF. This document delivers P2-M1 and fixes the plans for P2-M3, M4, M5/M7/M8 and M9.

**Already approved by the owner (2026-11-20):**
- DuckDB as the corpus.
- Versions 1.2, 1.3, 1.4 LTS, 1.5 and 2.0.
- A 500-question target with five categories.
- A 200/300 dev/test split, provisionally.
- Gemini kept out of CI.
- The Gemini key deferred to RAG-M4.

**Final clarifications recorded with D14:**
- Unrounded comparisons, with rounding only for display; exact `Fraction` arithmetic for rational metrics (§6.2).
- Deterministic ordering of the top-10 worst items (§6.2).
- Baseline protection with a hash check (§6.2).
- Field-level evidence schema (§2.1).
- INV-1 stated as a required, not-yet-implemented invariant (§3.3).

**Revision 3 changes (owner review of revision 2):**
- Documentation claims and runtime behaviour recorded separately (§2.1, §3.2). No claim of full docs/runtime alignment.
- Rule against using unverified items as ground truth, specified as required invariant INV-1, with planned code enforcement (§3.3; not yet implemented).
- Pilot report contents extended: rejected examples, corrections and how protocol v1 is derived (§3.4).
- CI gate made fully deterministic, with every combination and boundary defined (§6.2).
- 1.2.x/1.3.x GitHub release notes added at RAG-M3 (§2).
- Tier M results reported in a separate table from Tier H (§5.1).

**Revision 2 changes:**
- Exact version manifest added.
- A 50-question pilot gates the full benchmark, and review happens in batches.
- Human-verified and model-judged evaluation are strictly separated, with each metric's method defined.
- CI-gate rule rewritten around practical tolerances combined with paired-bootstrap significance.
- Every claim labelled as verified, assumption or proposal.

## 1. Research question and hypotheses

> When documentation changes across versions, do retrievers return outdated or conflicting information? Which methods retrieve the correct version-specific evidence, and can an answer pipeline cite it or abstain correctly?

The hypotheses below are to be tested, not assumed:
- **H1:** Without version filtering, retrievers show substantial version confusion. Motivated by the measured page overlap (evidence §3.4).
- **H2:** Metadata version filtering removes most confusion but not for change-history questions, which need changelog evidence.
- **H3:** Hybrid RRF and cross-encoder reranking improve ranking within the correct version. A fine-tuned embedder trained on version-confuser negatives helps the version-specific slice most.

Non-goals: a generic DuckDB chatbot (P2-R58).

## 2. Corpus (plan for RAG-M3)

| Item | Decision |
|---|---|
| Source | duckdb-web `d01484462710d49206816c9eeaeb822361ebe81c`, `docs/{1.2,1.3,1.4,1.5,2.0}/**/*.md` (2,031 files, 2,256,769 words) [VERIFIED, manifest §1.1] |
| Change sources | The 17 release posts and 2 migration guides listed with hashes in manifest §1.2 [VERIFIED]. **Plus the GitHub release notes for v1.2.1, v1.2.2, v1.3.1 and v1.3.2 (owner decision D13), fetched at RAG-M3.** Each fetched body is stored in the repo and its source URL, retrieval time and sha256 are recorded in the manifest. The stored copy is indexed, never the live page |
| Integrity | RAG-M3 recomputes every `content_hash` in manifest §1.1 and refuses to build on a mismatch [PROPOSAL] |
| Parsing | Strip front matter (keep `title`); resolve `{% link docs/X/... %}` to `https://duckdb.org/docs/X/...`; drop Kramdown attributes and lint comments; keep tables and code blocks intact [PROPOSAL] |
| Chunking | Heading-aware at H2/H3, target about 350 tokens with a 512 cap, about 15% overlap only when splitting a long section; never split inside a table row or code block. Tuned on dev at RAG-M5 [PROPOSAL] |
| Chunk metadata | `chunk_id`, `version`, `doc_path`, `heading_path`, `url`, `source_type` (`docs` \| `release_note` \| `migration`), `content_sha256`, `introduced_in` (change sources only) [PROPOSAL] |
| Version compatibility rule | A chunk is **compatible** with target version *v* if `version == v`, or if it is a change-source chunk with `introduced_in ≤ v`. This rule drives filtering and the Version Confusion Rate [PROPOSAL] |
| Raw data in git | The manifest, the fetch script and the stored release-note copies (small text). Chunks and indexes are built locally or in CI [PROPOSAL] |

### 2.1 Documentation claims and runtime behaviour are separate records

Assumptions A1 (docs X.Y describes the latest patch) and A3 (version pages match their package) stay **unverified** and labelled (evidence §0.2). The project never claims complete docs/runtime alignment unless it has been tested. Results that mention alignment state exactly how many items were checked by execution.

The data keeps three kinds of record apart [PROPOSAL]:

| Record | Content | Produced by |
|---|---|---|
| **Doc claim** | What a specific docs or change-source passage says: chunk, span, version, claim text | Annotation (evidence spans) |
| **Runtime observation** | What a pinned package returned for a probe: `probe_id`, package version, wheel sha256, raw output, platform | Probe runner (manifest §5.1) |
| **Conflict** | A doc claim and a runtime observation for the same version that disagree. Links to both, with a short description | Comparison step at RAG-M4, confirmed by the owner |

#### Field-level schema (owner, D14)

Each record type is its own JSONL file under `data/evidence/`, validated by a Pydantic model at RAG-M3/M4. Every record answers five questions: what the source is, which version it applies to, the exact evidence, what was observed, and its verification status. The fields that answer them are marked ★.

**`DocClaim`**: what a documentation or change-source passage says.

| Field | Type | Meaning |
|---|---|---|
| `claim_id` | str | Stable ID, `dc-<sha256(source_path + span_text)[:12]>` |
| ★ `source_kind` | enum `docs_page \| release_post \| migration_guide \| github_release` | Kind of source |
| ★ `source_repo` | str | For example `duckdb/duckdb-web`, or `duckdb/duckdb` for GitHub releases |
| ★ `source_commit` | str \| null | Pinned commit (null only for `github_release`) |
| ★ `source_path` | str | File path, or release tag for `github_release` |
| ★ `source_sha256` | str | sha256 of the whole source file, or of the stored release body; must match the manifest |
| `source_url` | str | Public URL, for citation |
| `heading_path` | list[str] | Headings down to the span |
| ★ `doc_version` | enum `1.2 \| 1.3 \| 1.4 \| 1.5 \| 2.0` | Version folder the page belongs to (for change sources: the release it announces) |
| `introduced_in` | str \| null | Change sources only: the version the claim says introduced the change |
| ★ `span_text` | str | **Exact** quoted text; must occur verbatim in the source |
| `span_char_start`, `span_char_end` | int | Offsets in the source file |
| ★ `claim_summary` | str | The claim in one normalised sentence, for example "`date_trunc` on DATE returns TIMESTAMP" |
| `claimed_value` | str \| null | Typed value if the claim states one, for example `TIMESTAMP` |
| ★ `verification_level` | enum `V0 \| V1 \| V2` | V1 = span found verbatim and hash matches; V2 = the owner confirmed the claim reading |
| `verified_by`, `verified_at` | str \| null, datetime \| null | Who verified it and when |
| `created_by` | enum `owner \| script \| gemini_draft` | Provenance |

**`RuntimeObservation`**: what a pinned package returned.

| Field | Type | Meaning |
|---|---|---|
| `obs_id` | str | Stable ID, `ro-<probe_id>-<package_version>-<platform_tag>` |
| ★ `probe_id` | str | For example `P02`; the probe text is versioned in the repo |
| ★ `probe_sha256` | str | sha256 of the exact probe code that ran |
| `probe_kind` | enum `sql \| python_api \| control` | Control probes must match across versions (evidence §4.2) |
| ★ `package` | str | `duckdb` |
| ★ `package_version` | str | For example `1.5.6` |
| ★ `wheel_sha256` | str | Must match manifest §2 |
| ★ `applies_to_doc_version` | enum `1.2 … 2.0 \| ref` | The docs version this package stands for (assumption A1) |
| `python_version`, `platform` | str | For example `3.12.16`, `Windows-11-10.0.26200-SP0` |
| ★ `observed_status` | enum `ok \| error` | Whether the query ran |
| ★ `observed_value` | str | `repr` of the result, or the **full** exception type and message (untruncated) |
| `run_at` | datetime | When it ran |
| `runner_commit` | str | Commit of the probe runner |
| ★ `verification_level` | enum `auto_executed \| reproduced` | `reproduced` = matched in a second, independent run, for example the Linux CI run |

**`EvidenceConflict`**: links a disagreeing claim and observation. It never modifies either one.

| Field | Type | Meaning |
|---|---|---|
| `conflict_id` | str | Stable ID |
| ★ `claim_id` | str | → `DocClaim` |
| ★ `obs_ids` | list[str] | → one or more `RuntimeObservation` records for the same `doc_version` |
| ★ `doc_version` | str | Version where they disagree |
| ★ `description` | str | What disagrees, for example "page shows scalar `42`; 1.1.3 returns list" |
| `detected_by` | enum `script \| owner` | How it was found |
| ★ `status` | enum `suspected \| confirmed \| dismissed` | Only the owner can set `confirmed` or `dismissed` |
| `resolution_note` | str \| null | For example "claim refers to a later version"; neither record is edited |
| `reviewed_by`, `reviewed_at` | str \| null, datetime \| null | Who reviewed it and when |

Benchmark items (§3.2) reference these by ID: `evidence_spans` maps to `claim_id`s, `runtime_observations` to `obs_id`s, and `doc_conflict` is true when a linked `EvidenceConflict` is `confirmed`.

Rules:
- A conflict never overwrites either record. Both stay as they were observed.
- Items with a conflict get `doc_conflict = true`. They are reported as their own diagnostic slice and excluded from headline and gated metrics (§3.5).
- The benchmark measures retrieval from **documentation**. For conflict items, the reference data stores the docs-based answer and the runtime answer **separately**, and never merges them into one gold value.
- Runtime results are cited as evidence only for the exact package and platform they ran on (assumption A2 until RAG-M2).

## 3. Benchmark (plan for RAG-M4)

### 3.1 Question taxonomy (approved)

Every question has exactly one **target version**.

| Type | Definition | Expected behaviour | Final target |
|---|---|---|---|
| **T1 Stable** (control) | Answer identical in all versions | Answer with citation | 110 |
| **T2 Version-specific** | Answer differs across versions. Written as **sets** of the same question with different target versions and gold answers | Answer for the target version, not another version's answer | 150 (≥ 60 sets) |
| **T3 Change-introduced** | When or what changed, or whether a feature exists in the target version | Answer from release-note or migration evidence plus docs | 90 |
| **T4 Multi-hop** | Needs at least two evidence chunks | Answer citing all required chunks | 70 |
| **T5 Unanswerable** | Feature absent in the target version, or answer not in the docs | **Abstain** with a reason | 80 |

Rationale for 500: for a proportion near 0.5, ±0.05 at 95% needs about 384 items, so the 300-item test split gives about ±0.057 overall; slice intervals will be wider. **Quality beats count:** after the pilot, the owner may approve lower counts for slices limited by the number of real changes. Weak items are never used as padding.

Each item carries hard-negative tags (P2-R04):
- **Version-confusers:** the same `doc_path` and section in incompatible versions, identified automatically.
- **Lexical confusers:** BM25 top-20 non-relevant chunks of the target version, confirmed by a human.

### 3.2 Reference data (what humans verify)

Every item stores:
- `question`, `type`, `target_version`
- `gold_short_answer`, a typed field: one of `value | type_name | identifier | version | yes_no | sql_expression | none`
- `accepted_aliases`
- `other_version_answers` (T2: the gold answer for each other version where it differs)
- `evidence_spans`, each a `(version, doc_path, heading_path, exact text)` tuple
- `answerable` (bool), `abstain_reason` (T5)
- `runtime_observations`: a list of references to probe records (§2.1), kept separate from `evidence_spans`
- `gold_basis`: `docs` or `docs+runtime` (whether the gold answer was also confirmed by execution)
- `doc_conflict` (bool), plus `runtime_answer` and `conflict_note` when it is true
- `verification_level`, `reviewer`, `review_batch`

Qrels are derived from evidence spans, not chunk IDs, so re-chunking never invalidates labels:
- **2:** the chunk contains a required span for a compatible version.
- **1:** same section, compatible version, supporting but insufficient.
- **0:** everything else, including identical text from an incompatible version.

### 3.3 Verification levels

| Level | Meaning | Who |
|---|---|---|
| **V0 Draft** | Generated, unchecked | Gemini (drafting) |
| **V1 Auto-checked** | Schema valid; every evidence span found verbatim in a compatible version of the pinned corpus; probe-consistent where a probe exists; not a near-duplicate of another item | Deterministic scripts |
| **V2 Human-verified** | The owner accepted it, possibly after editing: question is clear, gold answer correct for the target version, spans sufficient, type correct | Owner |

**Rule (approved):** headline results, resume claims and the CI gate use **only items the owner verified (V2)**. V0 and V1 items may be used for dev tuning, but are never treated as ground truth.

**Required design invariant (INV-1):** no non-V2 item may contribute to headline results, resume claims or the CI gate.

**Implementation status: NOT IMPLEMENTED.** No code exists yet. INV-1 counts as enforced only once code and tests that enforce it exist and pass:
- **Code:** the harness check below, built at RAG-M5, plus the gate at RAG-M9.
- **Tests:** a test that injects a V1 item into a headline or gate run and asserts the run fails, and a test that an edited V2 item is demoted.

Until then, documents describe INV-1 as a requirement, never as a working safeguard. Planned enforcement:
- The harness takes a required `min_level` argument. Headline and gate entry points hard-code `V2`, and fail with an error (not a warning) if any non-V2 item is in the set.
- Every results file records the verification level of each item, plus the counts per level and per type.
- Results produced on V0 or V1 items are written to a separate `results/dev_unverified/` path and carry a **"not human-verified"** header in every table and plot.
- If an item is edited after V2, it drops back to V1 until re-reviewed, and the `item_hash` change shows up in the results file.

### 3.4 Pilot (50 questions) before any scale-up

**Composition:** T1 11, T2 15 (at least 5 sets spanning three or more versions), T3 9, T4 7, T5 8. It is drawn from a pilot change inventory of about 15 changes, covering all four version boundaries.

**Steps:**
1. Write the protocol v0: prompts, the `gold_short_answer` typing guide, span rules and rejection reason codes. Draft codes, finalised in v0:
   - `R1` ambiguous target version
   - `R2` gold answer wrong for the target version
   - `R3` evidence spans insufficient or wrong
   - `R4` wrong type
   - `R5` answer leaks into the question (copied identifiers)
   - `R6` trivial or unnatural question
   - `R7` near-duplicate
   - `R8` docs/runtime conflict mishandled
   - `R9` other, with a comment
2. Build the pilot change inventory (Gemini-assisted extraction). The owner reviews it, and each change with an observable effect gets a probe, run on all corpus versions with the manifest §5.1 method.
3. Draft about 70 candidates, run the V1 checks, and select 50 that meet the composition.
4. The owner reviews the 50 in **two batches of 25**. Each item gets accept, edit or reject, a reason code, and time spent.
5. Write a **pilot report** (`docs/benchmark/pilot_report.md`). Every number comes from the stored review records; nothing is estimated. Contents:
   - **Acceptance by type:** for each of T1–T5, accepted unchanged, accepted after minor edit, accepted after major edit, rejected, plus the acceptance rate against the 80% threshold.
   - **Rejection reasons:** counts by reason code and by type.
   - **Examples of rejected questions:** at least one per reason code that occurred, and at least one per type that had rejections. Each example shows the drafted question, the target version, the drafted gold answer and spans, the owner's reason and comment, and, if the item was salvageable, the corrected version.
   - **Edit analysis:** what the owner changed on accepted-after-edit items (question wording, gold answer, spans, type, target version).
   - **V1 check results:** failure rate by check (span not found, probe disagrees, schema, near-duplicate), and V1 false passes: items that passed V1 but the owner rejected.
   - **Documentation conflicts:** cases found, with the doc claim and the runtime observation shown side by side (§2.1).
   - **Bias indicators:** lexical-overlap ratio per type; share of owner-written items.
   - **Review time:** median and inter-quartile range per item, per type and per batch (batch 1 against batch 2 shows learning effects).
   - **Recommended corrections:** see the next list.
6. **Deriving protocol v1.** Each problem found maps to a concrete, recorded correction:

   | Problem found in the pilot | Kind of correction |
   |---|---|
   | A rejection reason that recurs (for example ≥ 3 items, or ≥ 20% of a type's rejections) | Change the drafting prompt or rules for that type, and add the rejected example as a **negative example** in the prompt |
   | Edits that recur (for example gold answers too long, spans too wide) | Tighten the typing guide or span rules |
   | A V1 false pass | Add or strengthen an automatic check so the same failure is caught before review |
   | A type that misses 80% | Redesign its drafting method (for example, owner-written seeds for T5), or propose a lower target for that type to the owner, with the yield data. Never pad with weak items |
   | Review time much higher than expected | Change the review UI or format, or batch size; never skip review |

   Protocol v1 is a versioned diff of v0 (prompts, guide, checks) with a **change log linking each change to the pilot evidence** that motivated it.
7. **Validation before scale-up.** The v1 prompts redraft the rejected pilot candidates' *topics*, run the V1 checks, and show the owner a small sample (about 10) to confirm the fixes work. These are new items; the original pilot review records stay unchanged.
8. **Owner decision:** approve protocol v1 and the scale-up plan, or run another pilot round. **The remaining 450 questions are not drafted until this approval.**

**Exit criteria (approved):**
- At least 80% of items accepted, or accepted with minor edits, in every type.
- 100% of accepted items pass V1.
- No unresolved systematic problem, such as ambiguous target versions or leaking answers.
- Review time measured, so batch sizes can be planned realistically.

Pilot items join **dev**, never test, because the protocol was tuned on them.

### 3.5 Annotation protocol (scale-up after pilot approval)

1. **Change inventory (full).** Every change in the manifest's change sources, with `change_id, version, feature, before, after, source_path, probe_sql?`. The owner reviews it in batches of 25 rows.
2. **Executable verification.** Probes run on all five corpus versions (manifest §5.1). Results are stored with the package version and wheel hash.
3. **Drafting.** Done in batches of 50. Prompts are versioned in git, and every response is cached (model ID, prompt hash, temperature, timestamp), so the work resumes under about 20 requests/day and never repeats a call.
4. **V1 checks** run automatically on every draft.
5. **Docs vs behaviour.** If a target-version page contradicts that version's probe, the item is tagged `doc_conflict`. It is kept as a diagnostic slice and excluded from headline and gated metrics.
6. **Human review in batches of 25**, at the owner's pace. There is no deadline and no requirement to review many items at once. Review order:
   1. ci-gate items (they must be V2 before the gate is enabled at RAG-M9)
   2. test items
   3. extra dev items
7. **Coverage targets:**
   - ci-gate: 100% V2.
   - Test: 100% V2 as the goal, reached over time. If fewer are verified when results are produced, the report uses only V2 test items and states the actual *n* and wider intervals.
   - Dev: at least 25% V2.
8. **Bias controls.**
   - At least 10% of questions are owner-written.
   - Prompts require paraphrase rather than copied identifiers.
   - The lexical-overlap ratio (share of question tokens appearing in the gold span) is reported per type.
   - Near-duplicates are removed.
9. **Consistency check.** One annotator means no inter-annotator agreement. Instead:
   - **(a)** About 10% of each completed batch is re-labelled blind at least 7 days later, and intra-annotator Cohen's κ is reported.
   - **(b)** Owner labels are compared with probe outcomes where probes exist.

   Both are reported as single-annotator limitations.
10. **Datasheet:** procedure, prompts, counts per type and verification level, rejection statistics, known biases, licence (DuckDB docs MIT, attribution) and splits.

### 3.6 Splits (approved provisionally; revisited after the pilot)

| Split | Size | Use |
|---|---|---|
| dev | 200 (includes the 50 pilot items) | All tuning: chunking, RRF k, rerank depth, abstention and NLI thresholds, prompts |
| ci-gate | 120 V2 items drawn from dev: T1 25, T2 40, T3 20, T4 15, T5 20 | PR regression gate |
| test | 300 | Frozen after RAG-M4. Run only for reported results |
| embedder-train | Extra generated questions over dev page groups only | RAG-M6 |

**Leakage controls:**
- Split by page group: each `doc_path` across all versions stays in one split.
- T2 sets stay together.
- Training negatives are mined only from non-test page groups, and RAG-M6 reports zero overlap.

## 4. Retrieval methods (RAG-M5/M6)

One interface, `retrieve(query, version | None, k) → ranked chunks`, so one harness evaluates all six methods (P2-R08–R13). Each runs **unfiltered** and **version-filtered**.

| Method | Plan | Tier |
|---|---|---|
| BM25 | `bm25s` | Core |
| Dense | Sentence-Transformers + FAISS (CPU); small open embedder chosen on dev, licence verified at RAG-M5 | Core |
| Hybrid | RRF, k = 60 default (Cormack et al. 2009), tuned on dev | Core |
| Fine-tuned dense | Contrastive training with version-confuser and lexical negatives, on Colab | Advanced |
| Cross-encoder rerank | Rerank the hybrid top 50; depth tuned on dev | Advanced |
| Late interaction | ColBERT-style; library chosen after checking maintenance. If infeasible, the problem and alternatives go to the owner; never silently dropped | Stretch |

## 5. Evaluation methodology

### 5.1 Two tiers, never mixed

| | **Tier H: human-referenced** | **Tier M: model-judged** |
|---|---|---|
| Ground truth | V2 reference data (§3.2) and executed probes | A model's judgement (local NLI model, optional Gemini judge, optional RAGAS) |
| Computation | Deterministic code against the reference data | A model scores the system output |
| Role | **Headline results, CI gate, resume claims** | Diagnostics only. Never gated, never called ground truth |
| Reporting | Metric with a paired-bootstrap 95% CI | Metric with a CI **and** the judge's measured agreement with human labels (κ, precision, recall), plus judge model ID and threshold |

Reporting rules (approved):
- Tier H and Tier M results go in **separate tables** under separate headings. Tier M is titled "supplementary, model-judged".
- A Tier M number is never reported without the judge's agreement with human labels. That agreement goes in its own table: judge model and version, threshold, calibration *n*, held-out *n*, κ, precision and recall.
- Tier M results are never used as ground truth, never feed the CI gate, and never appear in resume claims as accuracy.

### 5.2 Answer output contract

The answer pipeline must return structured JSON:
```json
{"abstain": false, "short_answer": "TIMESTAMP", "explanation": "…", "citations": ["<chunk_id>", "…"]}
```
The `short_answer` field makes deterministic grading possible. Free text lives only in `explanation`.

### 5.3 Metric definitions

**Retrieval (Tier H).**
- Recall@{5, 10, 20}, MRR@10 and nDCG@10 over graded qrels.
- **Version Confusion Rate@10 (VCR@10):** the share of top-10 chunks that are *incompatible* with the target version (§2 rule).
- Items without any grade-2 span (some T5) are excluded from retrieval metrics and counted separately.

**Answer correctness (Tier H).** Deterministic matching of `short_answer` against `gold_short_answer` and its aliases, by answer type:
- Normalised string, or identifier match ignoring case and whitespace, for `value`, `identifier` and `type_name`.
- Exact match for `version` and `yes_no`.
- **Execution equivalence** for `sql_expression`: both expressions run on the target version's pinned DuckDB package, and their results are compared.

Outputs the matcher can't resolve go into an **unresolved bucket that the owner grades** in batches of 25. They are never auto-graded by an LLM for Tier H. The unresolved rate is reported.

**Version correctness (Tier H, T2).** For a T2 item with target *v*:
- `correct` if the answer matches the gold for *v*.
- `wrong_version` if it matches the gold of another version that differs from *v*'s.
- `other_error` otherwise.
- Abstaining counts as `other_error`, because T2 items are answerable.

Reported as accuracy, with the **wrong-version rate** reported separately because it is the specific failure this project targets.

**Abstention (Tier H).**
- Positives = T5 items.
- Prediction = the `abstain` flag.
- Report precision (correct abstentions ÷ all abstentions), recall (abstentions on T5 ÷ |T5|), F1, and the over-abstention rate on answerable items.
- Whether the stated reason is correct (for example "introduced in 1.4") is graded by the owner on all T5 abstentions in the test split.

**Citation correctness (Tier H).** Computed from qrels:
- **Citation precision:** the share of cited chunks with grade ≥ 1 for the target version.
- **Evidence recall:** the share of an item's required evidence spans covered by cited chunks. For T4 this means all hops.

**Citation support (Tier M).**
- For each sentence in `explanation`, a local NLI cross-encoder decides whether its cited chunks entail it.
- The entailment threshold θ is set on a human-labelled calibration set from dev, about 100 sentence–citation pairs labelled in batches of 25.
- Agreement is reported on a **separate** held-out human-labelled set of about 100 pairs.

**Faithfulness (Tier M).** Same NLI procedure, with the premise being all retrieved context. It reports the share of `explanation` sentences that are entailed, with judge agreement measured the same way.

Optional Tier M extras: a Gemini judge for free-text explanation quality, and RAGAS. Both are diagnostics only, cached, and reported with agreement on the same human-labelled sets.

**Latency:** per stage, P50 and P95.

### 5.4 Statistics

- **Paired cluster bootstrap over questions:** 10,000 resamples, fixed seed. A T2 set is resampled as one cluster and other items individually. Baseline and candidate share every resample.
- Report percentile 95% CIs for each metric and for the **difference** between methods.
- When several pairwise method comparisons are claimed significant, use Holm correction.
- Before any result is reported, metric code is unit-tested against hand-computed examples.
- **Error analysis:** at least 30 failures, classified as wrong version, wrong section, chunk split, lexical miss, missing changelog hop, matcher gap or `doc_conflict`.

## 6. CI regression gate (RAG-M9)

### 6.1 What runs

On every pull request:
- Tier H retrieval metrics for the **production pipeline** (version-filtered hybrid, plus rerank once it exists), computed on the frozen 120-item V2 **ci-gate** split.
- CPU-only, deterministic, and no API calls.
- The corpus is rebuilt from the manifest, and indexes are cached by manifest hash + embedder version + chunker-config hash.

`baseline.json` stores **per-question scores** plus config hashes. If the item set or corpus hash differs, the gate reports **ERROR** (stale baseline), never PASS.

### 6.2 Decision rule (deterministic)

**Requirement (owner):** the CI outcome must be a pure function of its inputs. The same candidate, baseline and config always give the same outcome, and every combination of practical and statistical significance has exactly one defined outcome. No step involves human judgement or randomness that isn't seeded.

#### Inputs

| Input | Definition |
|---|---|
| Per-question scores | Candidate and baseline scores on the same 120 ci-gate items (from `baseline.json`), matched by `item_id` and sorted by `item_id` |
| Regression R | Signed so that **positive means worse**: R = baseline − candidate for nDCG@10 and Recall@10; R = candidate − baseline for VCR@10 (lower is better) |
| Point estimate | R̄ = mean of the per-item R over the metric's eligible items, **unrounded**: an exact `Fraction` for Recall@10 and VCR@10, float64 for nDCG@10 (see the precision rule) |
| Bootstrap | 10,000 paired cluster resamples (T2 sets as clusters), generator `numpy.random.Generator(PCG64(seed=20261120))`, cluster order fixed by sorted cluster ID. The same resample indices are used for all four metrics |
| Lower bound L | The 0.625th percentile of the bootstrap R̄ distribution (`numpy.quantile`, method `"linear"`), **unrounded**. One-sided α = 0.025, Bonferroni over 4 metrics: 0.025 / 4 = 0.00625 |

**Precision rule (owner, D14):**
- Every threshold and significance comparison uses **unrounded** values: R̄ against 0, τ and C, and L against 0.
- Rounding is applied **only when values are displayed** in reports and PR comments, to 4 decimal places.
- Determinism with unrounded values comes from fixing the computation, not from rounding:
  - float64 throughout;
  - inputs sorted by `item_id`;
  - for metrics whose per-item scores are rational (Recall@10 and VCR@10 are counts out of 10), R̄ is computed **exactly** with `fractions.Fraction`, and τ and C are parsed from their decimal strings as `Fraction`. A drop of exactly τ therefore compares as exactly τ, with no binary-float error;
  - for nDCG@10, which involves logarithms, the mean is computed in float64 with an order-independent, correctly rounded sum (`math.fsum`);
  - fixed seed and generator;
  - pinned numpy version in the lockfile.
- τ and C are stored in config as decimal strings and parsed once.
- **Cross-platform note:** if the RAG-M9 determinism test ever shows a platform difference in the last bits, that is reported to the owner as a defect to fix. It is never handled by silently adding rounding.
| τ, C | Practical tolerance and hard cap from §6.3, with 0 < τ < C |

#### Two questions per metric

1. **Practical size:** which band does R̄ fall in?
   - **none:** R̄ ≤ 0 (no regression, or an improvement)
   - **negligible:** 0 < R̄ < τ
   - **meaningful:** τ ≤ R̄ < C
   - **gross:** R̄ ≥ C

   A value exactly at τ counts as meaningful, and exactly at C counts as gross.
2. **Statistical significance:** **S = (L > 0)**. The whole one-sided 99.375% interval lies on the "worse" side. L exactly 0 counts as not significant.

#### Outcome for every combination

| Practical band → / Significant? ↓ | none (R̄ ≤ 0) | negligible (0 < R̄ < τ) | meaningful (τ ≤ R̄ < C) | gross (R̄ ≥ C) |
|---|---|---|---|---|
| **S false** | PASS | PASS | **WARN** | BLOCK |
| **S true** | PASS (cannot occur in practice*) | **PASS + note** | BLOCK | BLOCK |

\* If R̄ ≤ 0, the outcome is PASS regardless of S, so the cell is defined even for pathological bootstrap distributions.

**What each outcome does**, all fixed in advance:

| Outcome | CI job exit code | Merge blocked by CI? | Recorded |
|---|---|---|---|
| PASS | 0 | no | Gate report artifact |
| PASS + note | 0 | no | Report plus PR comment: "statistically detectable but below the practical tolerance" with R̄ and L |
| **WARN** | **0** | **no** | Report plus PR comment with R̄, L, τ, C, the 10 items that got worse most (see the ordering rule below), and a line in `gate_history.jsonl` |
| BLOCK | 1 | yes (required check) | Report plus PR comment |
| ERROR | 2 | yes | Stale baseline, item or corpus hash mismatch, missing items, or a metric that is NaN. Never treated as PASS |

#### The meaningful-but-not-significant case (WARN)

This is the case the owner asked about. A drop at least as large as τ was observed, but the 120-item set can't rule out that it comes from which questions happen to be in the set.

**WARN is a fixed, non-blocking outcome: exit code 0, merge not blocked by CI.** The owner's merge decision is a normal code-review decision made outside the gate. It doesn't change the CI result, so the CI outcome stays deterministic.

Why WARN doesn't block:
- Blocking would fail builds on changes the data can't distinguish from noise. That is the false-alarm problem the owner asked to avoid.
- Large drops are still caught without significance: anything at or beyond the hard cap C blocks unconditionally.

**"10 items that got worse most": deterministic ordering.** Only items with per-item R > 0 are listed, sorted by:
1. per-item R, descending, compared unrounded;
2. ties broken by `item_id`, ascending (plain string order).

If fewer than 10 items got worse, all of them are listed. The list is informational and never affects the outcome.

#### Baseline protection (owner, D14)

- **Approved baseline.** `baseline.json` changes **only when the owner explicitly approves an update**, through a dedicated baseline-update PR, with the approval logged in `EXECUTION_TRACKER.md`.
  - No automatic ratcheting, and no update on merge.
  - CI jobs have no write path to it.
- **Integrity check.** The approved baseline's sha256 is recorded in the tracker and in `gate_config`. If the file doesn't match the recorded hash, the gate reports **ERROR**. That also catches an accidental edit in an ordinary PR.
- **Repeated WARNs.** Every PR is evaluated against **the same approved baseline**, never against the previous PR or the current `main`. So several merged WARN-level drops **add up** against it.
- Once the total drop reaches C, or becomes significant, every later PR **BLOCKs** until it is fixed or the owner explicitly approves a new baseline.
- Outcomes are logged in `gate_history.jsonl` together with the baseline hash they were measured against.

#### Combining the four metrics

The overall result is the **worst** individual outcome, ordered ERROR > BLOCK > WARN > PASS + note > PASS.

#### Edge cases

- **Neutral change:** identical per-question scores give R = 0 for every item, so R̄ = 0, every bootstrap value is 0, L = 0, and the outcome is PASS.
- **Exact boundary:** values are compared exactly at τ and C, since nothing is rounded. For example, a Recall@10 R̄ of exactly 3/120 equals τ = 0.025 as a `Fraction`, so it is classified meaningful.
- **Determinism test (RAG-M9):** the gate is run twice on the same inputs. Both runs must produce byte-identical reports.

### 6.3 Proposed tolerances (calibrated at RAG-M9 before the gate is enabled)

Granularity: with about 120 retrieval-eligible items, one question flipping in a binary metric moves the mean by about 0.008. In the 40-item T2 slice, one flip moves it by 0.025. τ is set to at least about 2–3 questions' worth, so a single flip can never block.

| Gated metric (production pipeline, ci-gate split) | τ (practical tolerance) | C (hard cap) | Approximate items |
|---|---|---|---|
| nDCG@10, all items | 0.020 | 0.050 | continuous |
| Recall@10, all items | 0.025 | 0.060 | τ ≈ 3, C ≈ 7 |
| Recall@10, T2 slice (40) | 0.050 | 0.100 | τ = 2, C = 4 |
| VCR@10, all items (increase) | +0.020 | +0.050 | continuous |

### 6.4 Calibration and governance

**Calibration at RAG-M9 (results reported to the owner before enabling):**
- (a) **Neutral changes**, such as refactors or reordered index builds, must give Δ = 0 and PASS.
- (b) **Benign perturbations**, such as ±5% chunk overlap or a different random seed where one exists, are used to see natural Δ spread. They should mostly PASS or WARN.
- (c) **Injected regressions** must all BLOCK: the version filter removed, RRF degraded, 10% of chunks deleted, and a weaker embedder swapped in.

**Governance:**
- Any change to τ, C, α or the gate split needs owner approval.
- `baseline.json` changes only through a dedicated PR the owner approves. There is no automatic ratcheting.
- An override label needs a written justification and owner approval, and is logged in the tracker.
- **Scheduled and manual runs** (not per PR) cover all six methods in both modes and the answer metrics, including Tier M with Gemini and NLI.
- **Acceptance demo (P2-R20):** a PR that removes the version filter is blocked, and a neutral PR passes.

## 7. Related reading (RAG-M1)

BEIR (Thakur et al., NeurIPS 2021 Datasets & Benchmarks); ColBERT (Khattab & Zaharia, SIGIR 2020); RRF (Cormack, Clarke & Büttcher, SIGIR 2009); RAG (Lewis et al., NeurIPS 2020); RAGAS (Es et al., arXiv 2309.15217); HyDE (Gao et al., arXiv 2212.10496); bootstrap in IR evaluation (Sakai, SIGIR 2006). [ASSUMPTION] These citation details come from memory and are re-verified against the papers before the technical report.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Archived docs contradict behaviour [VERIFIED case] | Probes plus the `doc_conflict` diagnostic slice |
| Gemini free-tier limits unpublished and unstable [VERIFIED / REPORTED] | Cached, resumable drafting at about 20 requests/day; no Gemini in CI or in Tier H |
| LLM-drafted questions favour BM25 | At least 10% owner-written, paraphrase requirement, overlap statistics |
| Too few real changes for T2/T3 targets | The pilot measures yield; counts revised with owner approval, never padded |
| Owner review load | Pilot first, batches of 25, V2-only reporting with honest *n* |
| CI too slow | Index cache plus a small gate split; measured at RAG-M9 |
| Single annotator | Intra-annotator κ and probe agreement, reported as a limitation |
| Linux results differ from Windows [ASSUMPTION A2, untested] | Probes re-run in CI at RAG-M2 |
| Version pages don't match packages [ASSUMPTION A3, one verified counter-example] | Separate doc-claim, runtime and conflict records (§2.1); per-item probes at RAG-M4; alignment never claimed beyond what was checked |
| A probe harness bug looks like a version change | Control probes; a differing control fails the whole run (evidence §4.2) |

## 9. Decisions

### 9.1 Revision 2 decisions (owner, 2026-11-20) and how revision 3 applies them

| # | Decision | Owner verdict | Applied in |
|---|---|---|---|
| 1 | Version manifest | **Approved with condition:** A1 stays labelled unverified; no claim of full docs/runtime alignment until tested; doc conflicts and runtime behaviour kept separate | §2.1, §3.2; manifest §2; evidence §0, §4.1 |
| 2 | Pilot | **Approved:** 50 items, two batches of 25, the proposed distribution, 80% per type; the remaining 450 on hold until the pilot report is approved; the report includes rejection reasons, acceptance by category, review time and recommended corrections | §3.4 (adds rejected examples and the protocol v1 derivation) |
| 3 | Verification levels | **Approved:** V2-only headline and CI; coverage targets kept; unverified items never silently treated as ground truth | §3.3 (enforcement in code) |
| 4 | Two-tier evaluation | **Approved:** Tier H authoritative; Tier M supplementary, with agreement reported separately; never ground truth or gated | §5.1 |
| 5 | CI gate | **Approved provisionally:** define every combination; deterministic outcome; calibration at RAG-M9; threshold changes need owner approval | §6.2, §6.4 |
| 6 | 1.2.x/1.3.x release notes | **Approved:** add at RAG-M3, sources and hashes in the manifest | §2; manifest §1.2 |

### 9.2 Revision 3 items (approved as D14, 2026-11-20)

1. **Deterministic gate rule** (§6.2):
   - WARN is a fixed, non-blocking outcome (exit 0); BLOCK fails CI (exit 1); ERROR exits 2.
   - Boundaries: R̄ = τ is meaningful, R̄ = C is gross, L = 0 is not significant.
   - Fixed seed; overall result is the worst of the four metrics.
2. **Docs/runtime separation** (§2.1) and the reference fields `runtime_observations`, `gold_basis` and `runtime_answer`.
3. **Pilot report contents and protocol v1 derivation** (§3.4 steps 5–8), including reason codes R1–R9.
4. **Control-probe additions** for RAG-M3 (evidence §4.2): a success-path control, and a whole-run failure if any control differs.

### 9.3 Final clarifications recorded with D14

| # | Clarification | Where |
|---|---|---|
| 1 | Unrounded values for all threshold and significance comparisons; rounding for display only; deterministic top-10 ordering (R descending, then `item_id` ascending); PASS / PASS + note / WARN / BLOCK outcome rules unchanged | §6.2 |
| 2 | Baseline changes only with explicit owner approval; repeated WARNs are always measured against the same approved baseline; hash check | §6.2 |
| 3 | Field-level schema for `DocClaim`, `RuntimeObservation` and `EvidenceConflict`: source, version, exact evidence, observed result and verification status; separate records even when they conflict | §2.1 |
| 4 | Excluding unverified items is a **required invariant (INV-1), not yet implemented** | §3.3 |

### 9.4 Implementation requirements carried forward (none implemented yet)

| ID | Requirement | Milestone | Done when |
|---|---|---|---|
| IR-1 | INV-1: non-V2 items excluded from headline results, resume claims and the gate; edited V2 items demoted | RAG-M5 (harness), RAG-M9 (gate) | Enforcing code merged, **and** tests (injected V1 item makes the run fail; edit demotes the item) pass with output shown |
| IR-2 | Evidence records `DocClaim`, `RuntimeObservation` and `EvidenceConflict` as Pydantic models with validation (verbatim span check, hash match against the manifest) | RAG-M3/M4 | Models plus tests pass |
| IR-3 | Probe suite in the repo, with a success-path control; a differing control fails the run; full, untruncated error capture | RAG-M3 | Tests pass; outputs stored as `RuntimeObservation` |
| IR-4 | Linux re-run of the probes (assumption A2) | RAG-M2 CI | CI results compared with manifest §5.1 |
| IR-5 | Fetch, store and hash the v1.2.1, v1.2.2, v1.3.1 and v1.3.2 GitHub release notes (D13) | RAG-M3 | Manifest §1.2 table filled from actual fetches |
| IR-6 | Corpus build refuses to proceed on any `content_hash` mismatch | RAG-M3 | Test with a tampered file fails the build |
| IR-7 | Gate: unrounded and `Fraction` comparisons, fixed seed, top-10 ordering, worst-of combination, exit codes, ERROR on stale or tampered baseline, byte-identical double run | RAG-M9 | Unit tests on hand-computed boundary cases, plus the determinism test |
| IR-8 | Baseline protection: no CI write path; baseline update only through an owner-approved PR logged in the tracker; baseline hash recorded | RAG-M9 | Test that a modified baseline gives ERROR |
| IR-9 | Gate calibration (neutral, benign, injected regressions) reported to the owner before enabling; any τ, C or α change needs owner approval | RAG-M9 | Owner approval logged |
| IR-10 | Pilot: 50 items, two batches of 25, full pilot report; the remaining 450 on hold until the owner approves protocol v1 | RAG-M4 | Owner approval logged |
| IR-11 | Tier M reported in separate tables with judge–human agreement; never gated | RAG-M5/M8 | Report template reviewed |

None of these is described as working until its "done when" condition is met and shown.
