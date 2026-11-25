# Benchmark protocol v1 (RAG-M4)

Status: **adopted by the owner on 2026-11-23** for the 50-item pilot (tracker D71-D72). Protocol v0 (`protocol_v0.md`, `protocol_v0.toml`) stays in force and unchanged. Under design §3.4 step 6, protocol v1 is a versioned diff of v0 in which every change is linked to pilot evidence; this amendment contains only the changes that the drafting evidence already supports. Machine-readable parts: [`protocol_v1.toml`](protocol_v1.toml). Further v1 changes follow from the owner's pilot review (design §3.4 steps 4–6).

## 1. Evidence (tracker rows D64–D71)

| Finding | Evidence |
|---|---|
| Documentation-grounded T2 items are scarce in this corpus | 15 T2 sets drafted with strict v1 validation; 6 counted items accepted (2 three-version sets), 2 more held for a documentation conflict (D66, D68) |
| Most version changes are not documented in each version's own pages | Of 20 pilot candidates, 9 lack earlier-version documentation (features introduced at the boundary), 1 has unchanged documentation; strict validation rejected 5 of 7 documentation-diff sets because earlier pages do not state the behaviour or the answers do not differ (D67, D68) |
| Further offline discovery found no clean T2 candidates | Function-example results (16 found, 8 kept, 4 validated: none clean, D69–D70); configuration defaults (12 non-environment settings: in every case the documented change is a documentation correction or table difference, with the exploratory runtime default unchanged across versions, D71) |
| Documentation corrections are a distinct phenomenon | `union.tag`, `unnest` and 12 settings: earlier documentation disagrees with the exploratory runtime of the same version (exploratory, not canonical; D70, D71) |
| The model mislabels the item type and target version | 8 `type_mismatch` and 5 `target_version_mismatch` rejections under the v0 common prompt (D64–D71) |

## 2. Changes from v0

### 2.1 Pilot composition (protocol v0 §2)

| Type | v0 | Accepted V0 drafts at adoption (counted) | v1 |
|---|---|---|---|
| T1 Stable | 11 | 12 | 13 |
| T2 Version-specific | 15 (≥ 5 sets spanning three versions) | 6 (2 sets of three versions) | **6** (all accepted T2 sets; no minimum on three-version sets beyond what was achieved: 2) |
| T3 Change-introduced | 9 | 11 | 12 |
| T4 Multi-hop | 7 | 7 | 7 |
| T5 Unanswerable | 8 | 10 | 12 |
| **Total** | **50** | **46** | **50** |

Reaching 50 needs about four more accepted drafts in T1, T3 and T5. These types still have distinct, undrafted items in the existing approved inventory, with no new candidates and no duplicates. Alternative: a 46-item pilot with the current accepted drafts only. Either way, the two held T2 drafts are added only if their documentation conflict is resolved in the owner's review.

### 2.2 T2 at scale-up (design §3.5)

The full change inventory classifies every change as documentation-grounded (each version's own pages state a different answer), introduced at a boundary, runtime-only, or documentation unchanged. The T2 quota at scale-up is set from the documentation-grounded count, not as a fixed share. T2 sets require each version's own documentation (strict v1 validation), as now.

### 2.3 Documentation corrections are diagnostic only

When the earlier documentation disagrees with that version's canonical observation (a documentation correction), the item is a `doc_conflict` diagnostic item (v0 §6), never a counted T2 item, and is excluded from headline and gated metrics.

### 2.4 Prompts

Prompt v1 files (hashes in `prompts_v1.toml` and `protocol_v1.toml`): `prompts/v1/draft_T2.md` (explicit cross-version answers), `prompts/v1/draft_common.md` (exact `type` value "T1"–"T5" and exact target version) for every item drafted after adoption, and `prompts/v1/draft_T5.md` (a T5 item carries no gold span, v0 §5.6) for redrafts. Items drafted before adoption keep the v0 prompts and their cached responses.

### 2.5 T5 items carry no evidence span

Evidence: 5 accepted T5 drafts cited a passage from the version where the feature exists; V1 rejected them (span from an incompatible version, v0 §5.6). The S3 validator now rejects a T5 response with any evidence span (`t5_has_gold_span`), and the five topics were redrafted with the v1 prompts (design §3.4 step 7).

## 3. Not changed

Item record, answer typing guide, evidence-span rules, rejection codes, review record and verification levels (v0 §3–§9), the strict validators, and the privacy rules (restricted text never sent to any external model).

## Amendment 2026-11-24: T4 and T5 drafting checks (owner decision D3)

Learned from the pilot review. Every new T4 and T5 draft must pass these checks before it can be
counted; `vrag benchmark pilot screen` applies them as an advisory lexical screen and writes
`benchmark/review/proposed/screen_report.md`. A flag goes to the owner; the screen decides nothing.

| Check | Rule | Pilot items it would have caught |
|---|---|---|
| T4-S1 | No single evidence span answers the question alone: the item needs at least two passages, each necessary | T4-01, T4-14 |
| T5-S1 | The feature named in a T5 item is absent from every chunk of the target version and documented in another version | (screen check; none in the pilot after review) |
| T5-S2 | No other documented function, expression or section answers the question in the target version (for example a renamed function, a composite such as `day(last_day(d))`, or a documented section on the topic) | T5-01, T5-04, T5-10, T5-07, T5-13 |
| T5-S3 | No redundant version pairs: one T5 item per feature; asking the same absent feature for another version adds no information | T5-09, T5-11 |

The screen is lexical: it cannot see composite answers that share few words with the question,
so the owner's review remains the final check.
