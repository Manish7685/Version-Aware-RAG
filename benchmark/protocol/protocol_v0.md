# Benchmark protocol v0 (RAG-M4 S2)

Status: **v0 draft for the 50-item pilot, awaiting owner review.** Source of the requirements: `docs/design/RAG-M1_design.md` §3 (approved, D14), especially §3.4 steps 1–2. Machine-readable parts are in [`protocol_v0.toml`](protocol_v0.toml); the drafting prompt templates are in [`prompts/`](prompts) and are recorded there by sha256. Nothing in S2 drafts questions or calls any model: the prompts are versioned now so that they can be reviewed before S3 uses them. Protocol v1 will be a versioned diff of this file, with each change linked to pilot evidence (design §3.4 step 6).

## 1. Inputs

- **Change inventory:** [`../inventory/pilot_change_inventory.jsonl`](../inventory/pilot_change_inventory.jsonl), validated by `vrag benchmark inventory validate`. Only records with `status = "pilot_candidate"` feed drafting; `unresolved` and `excluded` records do not. Every record keeps the documentation sources and the runtime evidence apart.
- **Corpus:** the chunk files built by `vrag corpus build` (git-ignored), with the version-compatibility rule of `src/version_aware_rag/corpus/compat.py`.
- **Evidence records:** `DocClaim`, `RuntimeObservation` and `EvidenceConflict` in `data/evidence/` (IR-2). Items reference them by id; they are never merged into one answer.

## 2. Question types and pilot composition

Every item has exactly one **target version** (1.2, 1.3, 1.4, 1.5 or 2.0).

| Type | Definition | Expected behaviour | Pilot count |
|---|---|---|---|
| T1 Stable (control) | The answer is identical in every corpus version | Answer with a citation | 11 |
| T2 Version-specific | The answer differs across versions; written as **sets** of the same question with different target versions and gold answers | Answer for the target version only | 15 (at least 5 sets spanning three or more versions) |
| T3 Change-introduced | When or what changed, or whether a feature exists in the target version | Answer from a change source plus the docs | 9 |
| T4 Multi-hop | Needs at least two evidence passages | Answer citing every required passage | 7 |
| T5 Unanswerable | The feature is absent in the target version, or the answer is not in the docs | **Abstain** with a reason | 8 |

The pilot draws on the inventory's change boundaries: every corpus boundary (1.2→1.3, 1.3→1.4, 1.4→1.5, 1.5→2.0) must be represented among the accepted items.

## 3. Item record (design §3.2)

`question`, `type`, `target_version`, `gold_short_answer` (typed, §4), `accepted_aliases`, `other_version_answers` (T2), `evidence_spans` (`(version, doc_path, heading_path, exact text)`, §5), `answerable`, `abstain_reason` (T5), `runtime_observations` (references to `obs_id`s, kept apart from `evidence_spans`), `gold_basis` (`docs` or `docs+runtime`), `doc_conflict` with `runtime_answer` and `conflict_note` when true, `verification_level`, `reviewer`, `review_batch`.

## 4. `gold_short_answer` typing guide

Each gold answer has exactly one type. The answer is the shortest complete answer, written as the docs write it.

| Type | Use when the answer is | Rules | Example (from the inventory) |
|---|---|---|---|
| `value` | A literal result value | Exactly as returned or documented, no surrounding prose; numbers without units unless the unit is part of the value | `inf` (`1 / 0`, documented in the 2.0 migration guide) |
| `type_name` | A SQL data type | Upper case as in the docs (`TIMESTAMP`, `BLOB`, `"NULL"`) | `TIMESTAMP` (`date_trunc` on a DATE in 1.5) |
| `identifier` | A function, statement, setting, operator or other name | Exact spelling and case; no call syntax unless the question asks for it | `struct_contains` |
| `version` | A DuckDB release or docs series | `X.Y` for a docs series, `X.Y.Z` for a release; never `vX.Y` | `1.4` (`MERGE INTO` introduced) |
| `yes_no` | A yes/no fact | `yes` or `no` | `no` (is `MERGE INTO` available in 1.3?) |
| `sql_expression` | A SQL expression or statement | Minimal valid SQL that the docs show; keywords upper case | `TRY(1 // 0)` |
| `none` | Nothing: T5 items only | Empty answer; `abstain_reason` is required | (T5) |

`accepted_aliases` lists only true equivalents (documented aliases such as `struct_has` for `struct_contains`, or `true` for `yes`), never near misses.

## 5. Evidence-span rules

1. **Verbatim.** A span is exact text from one corpus file of a version compatible with the target version (design §2; `compat.py`). No generated, paraphrased or normalised text.
2. **Minimal and sufficient.** The smallest passage that supports the gold answer on its own: usually one sentence, list item, table row or code block; never a whole page.
3. **Located.** Each span records version, doc path, heading path and the exact text, so that qrels are derived from spans, not chunk ids (design §3.2): grade 2 for a chunk with a required span in a compatible version, 1 for the same section without a sufficient span, 0 otherwise (including identical text from an incompatible version).
4. **T3** needs at least one change-source span (release post, migration guide or GitHub release note) and, where the docs state the behaviour, a docs span.
5. **T4** needs at least two spans from different sections or pages.
6. **T5** has no gold span; it records why the answer is absent (feature absent in the target version, or not documented).
7. **Restricted sources (owner decisions Q8, D22; RAG-M4 D1).** Release-note and release-post text is never committed. Spans from those sources are stored as `DocClaim` records whose text lives only in the git-ignored sidecar; benchmark files reference them by `claim_id`. Questions, gold answers, aliases and notes never quote them.

## 6. Documentation and runtime evidence

- The benchmark measures retrieval from **documentation**. Runtime observations confirm or contradict a docs answer; they never replace it.
- `gold_basis = docs+runtime` only when a canonical `RuntimeObservation` for the target version's package agrees with the docs answer. Scratch runs are not evidence.
- If a target-version page contradicts that version's observation, the item gets `doc_conflict = true`, the docs answer and the runtime answer are stored **separately**, and the item becomes a diagnostic item, excluded from headline and gated metrics (design §2.1, §3.5 step 5). A conflict is first recorded as a **suspected** `EvidenceConflict`; only an owner review confirms or dismisses it.

## 7. Rejection reason codes (final for v0)

| Code | Name | Apply when |
|---|---|---|
| R1 | Ambiguous target version | The question does not determine exactly one target version, or reads naturally for another version |
| R2 | Gold answer wrong for the target version | The gold answer is incorrect for the target version, including an answer that is right only for another version |
| R3 | Evidence spans insufficient or wrong | A span is missing, not verbatim, from an incompatible version, or does not support the gold answer alone (T4: fewer than two sufficient spans) |
| R4 | Wrong type | The question type (T1–T5) or the `gold_short_answer` type does not match the item |
| R5 | Answer leaks into the question | The question copies identifiers or values that give the answer away |
| R6 | Trivial or unnatural question | No realistic user would ask it, or it is answerable without the docs |
| R7 | Near-duplicate | It duplicates another item's information need, even if worded differently |
| R8 | Docs/runtime conflict mishandled | `doc_conflict`, `runtime_answer` or the separation of docs and runtime answers is wrong, or an unresolved disagreement is treated as fact |
| R9 | Other | Any other problem; a comment is required |

An item may get several codes; the first is the primary reason.

## 8. Review record (owner, pilot batches of 25)

For every item: `decision` (`accept`, `accept_minor_edit`, `accept_major_edit`, `reject`), `reason_codes` (zero or more of R1–R9; R9 needs a comment), `comment`, `edits` (what changed: question wording, gold answer, spans, type, target version), `time_spent_seconds`, `reviewer`, `review_batch`. Every pilot-report number (design §3.4 step 5) comes from these records.

## 9. Verification levels

V0 drafts are never ground truth; V1 means every automatic check passed (S4); V2 means the owner accepted the item. Headline results, resume claims and the CI gate use only V2 items (INV-1, IR-1); an edit after V2 demotes the item to V1.

## 10. Drafting (S3, not started)

Drafting uses the templates in `prompts/` with a model approved by the owner. Every request and response is cached with the model id, prompt sha256, temperature and timestamp, so that work resumes under the free-tier limits and no call is repeated (design §3.5 step 3). The API key comes from an environment variable and is never written to a file or log.

**Owner decision (2026-11-23, S2 review): restricted text never goes to an external model.** Release-note and release-post text (restricted sources: licence not established, Q8 and D22) is never sent to Gemini or any other external model or service, in any prompt, input or request. Restricted evidence stays local (the git-ignored D1 sidecar). Later candidate generation uses only permitted evidence: the MIT-licensed documentation pages and migration guides, and this project's own inventory descriptions; restricted claims are referenced by `claim_id` only.

The change inventory was built deterministically from the cited sources, with no external model. Gemini is used only in S3 for drafting candidate questions, from permitted evidence.

## 11. Open items for the owner (not decided in v0)

| # | Item |
|---|---|
| O1 | Decided (see §10): restricted text is never sent to an external model. |
| O2 | VARIANT (`chg-1.5-05`): the runtime boundary (1.3→1.4) differs from the first documented version (1.5); its attribution needs a decision before it can become a pilot item. |
| O3 | `MERGE INTO` with several matching source rows (`chg-2.0-11`): the scratch run disagrees with the 2.0 docs; a canonical observation comes first, then a suspected `EvidenceConflict` for owner review. |
| O4 | The inventory has 20 pilot candidates (design: about 15), 10 of them at the 1.5→2.0 boundary; the owner may select or rebalance. |
| O5 | The V1 "probe-consistent" check (design §3.3) needs canonical observations: the canonical committed `vrag probes run` is still pending (clean tree, owner approval). |
