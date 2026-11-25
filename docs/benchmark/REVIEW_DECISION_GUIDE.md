# Pilot review: owner decision guide (prepared with AI assistance, updated 2026-11-24)

> **Historical: the review is complete (2026-11-24).** This guide prepared the owner's review. The
> owner confirmed all 52 decisions (AI-assisted); the records are in
> `benchmark/review/pilot_review_records.jsonl` and the results in `docs/benchmark/pilot_report.md`.
> The owner then chose to finish P2 as a small pilot (decision D3 revision): §4's replacement and
> ~200-item plans below were not pursued.

**Policy decisions already applied to the proposal (owner, 2026-11-24):** D1 corrections retained
(cross-version aliases, answer types, gold answers, evidence); D2 the two T2-H01 drafts released from
hold as candidates (1.5 → 720, 2.0 → 12; `benchmark/review/released_holds.json`); D3 planning target
about 200, no padding, protocol v1 amended with the T4/T5 drafting checks (`protocol_v1.md`
amendment, `vrag benchmark pilot screen`); T5-07 and T5-13 rejected (documented alternative); T5-09
and T5-11 rejected (redundant version pairs; T5-03 and T5-05 kept); T3-16 keeps its gold with no
model-derived alias.

## 1. What you do (about 45 minutes)

1. Open the proposed sheet. Every row has a proposed `decision`, `reason_codes`, a `comment`, and
   any proposed `edit_*` values (evidence text checked verbatim against the corpus).
2. Decide each row; spend your time on the 9 rows in §2. Change anything you disagree with.
3. Fill `reviewer` and `time_spent_seconds` on every row; rewrite `comment` in your words (remove
   the proposal prefix).
4. Save as `benchmark/review/pilot_review_sheet.csv` (CSV UTF-8, 52 rows) and tell me "review done".

## 2. Items that still need your judgement

| Item | Question (as proposed) | Proposed answer | Evidence rationale | Recommendation |
|---|---|---|---|---|
| T3-02 | Does selecting a value from a MAP using bracket notation in DuckDB 1.2 return a LIST or an individual value? | LIST | 1.2 map page: "Selecting from a MAP returns a LIST rather than an individual value". Verbatim, version-specific. The question offers both options, so a guess has a 50% chance | **accept** (the binary framing is natural for a behaviour change; reject only if you want open questions everywhere) |
| T3-06 | In DuckDB 1.4, which function converts milliseconds since the epoch to a timestamp? *(major edit)* | make_timestamp_ms | 1.4 timestamp table row for `make_timestamp_ms(milliseconds)`; `epoch_ms(ms)` is no longer listed in 1.4, so the answer is unique in 1.4. The original yes/no wording was true for every version | **accept the major edit** (it then also forms a natural T2 pair with `epoch_ms` in 1.2/1.3 for the scale-up) |
| T3-16 | In DuckDB 2.0, what type of regular expression matching do the `~` and `!~` operators perform? | partial regular expression matching (aliases: partial regex matching; partial) | 2.0 migration guide, verbatim. No alias added from model output (your policy) | **accept** unchanged |
| T4-13 | In DuckDB 2.0, what data type does the configuration option responsible for controlling identifier casing use? | VARCHAR | Hop 1: the keywords page names `preserve_identifier_case` as the casing option; hop 2: the migration guide gives its 2.0 type. Passes T4-S1. Same answer as T2-07@2.0 | **accept** (different retrieval challenge: the option is not named) |
| T4-14 | Which table function provides metadata about triggers … introduced in DuckDB 2.0? | duckdb_triggers() | The span "The `duckdb_triggers()` function was introduced in DuckDB 2.0." answers alone, failing the approved check T4-S1 | **reject (R4)** |
| T4-01 | Which function can be used as an alternative to bracket notation to retrieve a map value as a list? | element_at | One span ("The `element_at` function can be used to retrieve a map value as a list") answers alone (T4-S1) | **reject (R4)** |
| T4-05 | Which extension contains the associated functions for working with geometries, such as ST_RotateX? | spatial | Hop 1: ST_RotateX is a geometry function (spatial function list); hop 2: geometry functions remain in `spatial` (1.5 geometry page). Passes T4-S1 | **accept**; the weakest retained T4, since the function list sits under the spatial extension's own pages |
| T2-H01@1.5 | What does the expression `SELECT 2 * 3!;` return? *(released from hold, major edit)* | 720 (alias 12 removed) | 1.5 numeric page: "in v1.5 it returned 720". A 1.5 page describing 2.0 behaviour is unusual but consistent with the 2.0 page | **accept the major edit**; runtime confirmation by the canonical probe `chg-2.0-06` after the commit |
| T2-H01@2.0 | What does the expression `SELECT 2 * 3!;` return? *(released, major edit)* | 12 | 2.0 numeric page: "`SELECT 2 * 3!;` now returns `12`" | **accept the major edit** (same probe) |

Rows not listed follow the corrections you approved in D1 and need only confirmation.

## 3. Proposed counts (52 rows: 50 pilot + 2 released T2)

| Type | Rows | accept | minor edit | major edit | reject | Accept or minor (pilot criterion) | ≥ 80%? |
|---|---|---|---|---|---|---|---|
| T1 | 13 | 10 | 2 | 0 | 1 | 12/13 = 92% | yes |
| T2 | 8 | 1 | 5 | 2 | 0 | 6/8 = 75% (6/6 = 100% for the counted pilot T2; the 2 released drafts need major edits) | **no** |
| T3 | 12 | 7 | 3 | 1 | 1 | 10/12 = 83% | yes |
| T4 | 7 | 4 | 0 | 0 | 3 | 4/7 = 57% | **no** |
| T5 | 12 | 5 | 0 | 0 | 7 | 5/12 = 42% | **no** |
| **All** | 52 | 27 | 10 | 3 | 12 | retained 40 | |

Nothing above is verified: V2 starts only with your review records.

## 4. T4/T5 replacements and feasibility

**Replacements needed to restore the pilot composition with genuine items:** T4 needs **3**
(7 planned, 4 retained); T5 needs **7** (12 planned, 5 retained).

**Genuine candidates available now** (screened with T4-S1/T5-S1/T5-S2; evidence checked; candidate
specs for the next drafting run, not drafts):

| Candidate | Type | Target | Question idea | Evidence | Screen |
|---|---|---|---|---|---|
| C-T5-a | T5 | 1.5 | How do I switch `regex_match_operator_semantics` to full matching? | setting absent ≤ 1.5, in 2.0 configuration and migration guide | pass |
| C-T5-b | T5 | 1.5 | What does `SET error_on_division_by_zero = false` change? | setting absent ≤ 1.5, in 2.0 pragmas | pass |
| C-T5-d | T5 | 1.4 | How do I list triggers with `duckdb_triggers()`? | absent ≤ 1.5, in 2.0; related to T5-08 (triggers) but a different function | pass (note the relation) |
| C-T5-c | T5 | 1.5 | Configure metrics with `tracked_metrics`? | absent ≤ 1.5, but 1.5 documents `custom_profiling_settings` for the same purpose | **not recommended** (documented alternative) |
| C-T5-e | T5 | 1.4 | Use the `VARIANT` type? | inventory change `chg-1.5-05` unresolved (needs a canonical probe); lexical screen inconclusive | **not usable yet** |
| C-T4-a | T4 | 2.0 | Which setting restores full `~` matching? | the migration guide names the setting in the same sentence | **fails T4-S1** |
| C-T4-b | T4 | 2.0 | What replaced the JSON custom-metrics option? | no second 2.0 hop (the old option is not in 2.0's configuration table) | **fails** |

**Gap:** T4 **3** and T5 **4** genuine replacements cannot come from the current 32-change inventory
(T4 capacity was already exhausted at 13 distinct items). They need an inventory extension (a second
discovery pass over the 2.0 migration guide and the version diffs, plus canonical probes for the 10
unresolved changes), then a T4/T5 redraft round under the new checks (design §3.4 step 8).

**About 200 items (estimate, not a drafting result):** version-sensitive capacity looks like T2
about 10-14 (sets: T2-05, T2-07, T2-H01, the `epoch_ms` → `make_timestamp_ms` candidate, perhaps
1-2 from discovery), T3 25-30, T4 10-14, T5 12-15, about **60-75 non-T1 items**. ~200 is reachable
only with about 125-140 genuine T1 stable-fact items (≈ 65% of the benchmark). That is genuine
data, but the version-focused slices stay small. **Achievable with that skew; I recommend you
confirm the composition when the inventory extension is done.**

**120 CI-gate items: not feasible without weakening quality.** With ~200 items, a disjoint 120-item
ci-gate split leaves ~80 for test and dev (test CIs about ±0.11); and the gate's T2 metric needs at
least 20 T2 items in the gate split (D5 rule) while only ~10-14 T2 items exist in total. The 120
target stays recorded as a planning requirement; the gap goes to you with the frozen split, as the
policy requires (no silent change).

## 5. Every row

| Item | Type | Version | Proposal | Codes | Reason |
|---|---|---|---|---|---|
| T1-01 | T1 | 1.3 | accept |  | Gold matches the 1.3 docs; question is clear and not version-dependent (stable). |
| T1-03 | T1 | 1.5 | accept |  | Verbatim support; clear. |
| T1-04 | T1 | 2.0 | accept |  | Verbatim support in the 2.0 options table. |
| T1-05 | T1 | 1.2 | accept |  | Supported. The question paraphrases the description closely but does not leak the name. |
| T1-09 | T1 | 2.0 | accept |  | Verbatim support. |
| T1-11 | T1 | 1.3 | accept_minor_edit | R3 | Evidence was only a usage example; replaced with the function-table description, which supports the gold alone. |
| T1-12 | T1 | 1.4 | accept |  | Verbatim support. |
| T1-14 | T1 | 2.0 | accept_minor_edit | R3 | Evidence was only a usage example; replaced with the function-table description (WKB). |
| T1-15 | T1 | 1.2 | accept |  | Verbatim support. |
| T1-16 | T1 | 1.3 | accept |  | Supported by the Node Neo type table. |
| T1-18 | T1 | 1.5 | reject | R9 | Multi-answer gold (a list of three operators); the deterministic matcher compares one normalised string, so order or wording variants fail. Not fixable without changing the information need. |
| T1-19 | T1 | 2.0 | accept |  | Supported by the printf example. |
| T1-20 | T1 | 1.2 | accept |  | Supported (disjoint = no shared points). In the provisional run the privacy filter dropped its evidence (context_filtered); that is a pipeline issue, not an item issue. |
| T2-05@1.4 | T2 | 1.4 | accept_minor_edit | R4 R3 | CRITICAL alias fix: the alias 'regexp_matches' is the 2.0 answer and would score a wrong-version answer as correct. Type sql_expression -> identifier (a function name, not an expression). Evidence now the full table row for `~`. |
| T2-05@1.5 | T2 | 1.5 | accept_minor_edit | R4 R3 | Same as @1.4: remove the cross-version alias 'regexp_matches'; type -> identifier; full table row as evidence. |
| T2-05@2.0 | T2 | 2.0 | accept_minor_edit | R4 | Type sql_expression -> identifier. Gold and evidence correct for 2.0. |
| T2-07@1.4 | T2 | 1.4 | accept_minor_edit | R3 | CRITICAL alias fix: the alias 'varchar' is the 2.0 answer. The old span only implied a boolean; the configuration table states `BOOLEAN` explicitly. Alias BOOL (DuckDB's documented alias of BOOLEAN). |
| T2-07@1.5 | T2 | 1.5 | accept_minor_edit | R3 | Same as @1.4: remove the cross-version alias 'varchar'; explicit `BOOLEAN` evidence from the configuration table. |
| T2-07@2.0 | T2 | 2.0 | accept |  | Gold VARCHAR is stated twice in 2.0 docs; alias 'varchar' is only a case variant. |
| T3-01 | T3 | 2.0 | reject | R7 | Same information need, version and answer as T2-07@2.0 (data type of preserve_identifier_case in 2.0). Keep the T2 set, which is scarcer. |
| T3-02 | T3 | 1.2 | accept |  | Verbatim support. The question offers two options (LIST or value); acceptable for a behaviour-change check. |
| T3-06 | T3 | 1.4 | accept_major_edit | R4 R6 | As drafted ('Does 1.4 provide a function…? yes') it is not change-specific: 1.2 and 1.3 also document epoch_ms(ms) for this. Rewritten to ask for the 1.4 function, which the 1.4 docs name uniquely (epoch_ms(ms) is no longer listed in 1.4). |
| T3-07 | T3 | 1.4 | accept |  | Supported (Alias row). The question names struct_has but asks whether it exists; acceptable for a yes/no change item. |
| T3-09 | T3 | 1.5 | accept |  | Verbatim 'introduced in DuckDB 1.5'. |
| T3-10 | T3 | 1.5 | accept |  | Verbatim support. |
| T3-11 | T3 | 1.5 | accept |  | Supported; the function is absent from the 1.4 docs and present in 1.5 (corpus check), consistent with 'introduced in 1.5'. |
| T3-12 | T3 | 2.0 | accept_minor_edit | R4 | Type identifier -> value. Aliases add natural inflections of the same answer stated in the evidence ('errors', 'throws an error'). |
| T3-14 | T3 | 2.0 | accept_minor_edit | R9 | Gold had literal quotes ('"NULL"'), which the matcher would compare literally; gold -> NULL. |
| T3-15 | T3 | 2.0 | accept_minor_edit | R3 R4 | Gold 'gone' -> 'removed' (same meaning, the usual term); type identifier -> value; the evidence now includes the sentence naming rel.fetchone(). |
| T3-16 | T3 | 2.0 | accept |  | Owner policy (2026-11-24): keep the existing gold and aliases; no alias added from provisional model output. |
| T3-17 | T3 | 2.0 | accept |  | Verbatim support in two pages. |
| T4-01 | T4 | 1.2 | reject | R4 | Not multi-hop: the first span alone answers (element_at retrieves a map value as a list); the second span adds nothing needed. Type cannot be changed in the review, so reject or accept as weak T4. |
| T4-04 | T4 | 1.5 | accept |  | Genuine two-page item (timestamptz and date function tables). |
| T4-05 | T4 | 1.5 | accept |  | Two hops: ST_RotateX is a geometry function; geometry functions are part of the spatial extension. |
| T4-06 | T4 | 2.0 | accept |  | Two pages: the setting from the migration guide, its deprecation from the pragmas page. |
| T4-08 | T4 | 2.0 | reject | R4 R6 R7 | The second span (an UNPACK example) is not needed; duplicates T3-14 (untyped NULL type in 2.0); wording is unnatural ('in this version'). |
| T4-13 | T4 | 2.0 | accept |  | Passes check T4-S1: no single span both names the option and gives its 2.0 type for an option described only by its role. Same final answer as T2-07@2.0 (different retrieval challenge). |
| T4-14 | T4 | 2.0 | reject | R4 | Fails the approved T4 check T4-S1: the span 'The duckdb_triggers() function was introduced in DuckDB 2.0.' answers the question alone; replacing the irrelevant first span does not make it multi-hop. |
| T5-01 | T5 | 1.2 | reject | R2 R4 | Answerable in 1.2: docs/1.2 sql/functions/lambda.md has a 'Scoping' section listing exactly these rules (the provisional model answer reproduced them). Not an abstention item. |
| T5-02 | T5 | 1.2 | accept |  | uuid_extract_version absent from all 1.2 docs (corpus check), present from 1.3. |
| T5-03 | T5 | 1.3 | accept |  | MERGE INTO absent from 1.3 docs, present from 1.4. |
| T5-04 | T5 | 1.3 | reject | R2 R4 | Answerable in 1.3: epoch_ms(ms) 'Converts milliseconds since the epoch to a timestamp' (1.3 timestamp functions). Candidate for a new T2 set instead (epoch_ms in 1.2/1.3 vs make_timestamp_ms in 1.4+). |
| T5-05 | T5 | 1.3 | accept |  | struct_contains and struct_has absent from 1.3 docs, present from 1.4. |
| T5-07 | T5 | 1.4 | reject | R2 | Owner policy (2026-11-24): abstention status questionable. The 1.4 docs document last_day(date), so day(last_day(d)) gives the number of days in the month; flagged by drafting check T5-S2. |
| T5-08 | T5 | 1.5 | accept |  | CREATE TRIGGER absent from 1.5 docs, present in 2.0. |
| T5-09 | T5 | 1.2 | reject | R7 | Owner policy (2026-11-24): redundant version pair of T5-03 (MERGE INTO); T5-03 is kept. |
| T5-10 | T5 | 1.2 | reject | R2 R4 | Answerable in 1.2: epoch_ms(ms) 'Converts integer milliseconds since the epoch to a timestamp' (1.2 timestamp functions). |
| T5-11 | T5 | 1.2 | reject | R7 | Owner policy (2026-11-24): redundant version pair of T5-05 (struct_contains); T5-05 is kept. |
| T5-12 | T5 | 1.3 | accept |  | SHOW SCHEMAS absent from 1.3 docs, introduced in 1.5. |
| T5-13 | T5 | 1.3 | reject | R2 | Owner policy (2026-11-24): as T5-07, last_day(date) is documented in 1.3 (check T5-S2). |
| T2-H01@1.5 | T2 | 1.5 | accept_major_edit | R1 R9 | Released from hold (owner D2, 2026-11-24); NEEDS OWNER REVIEW. The question named DuckDB 2.0 while the target is 1.5 (R1); the alias 12 is the 2.0 answer (cross-version alias). Both 1.5 and 2.0 pages state 1.5 returned 720. Runtime confirmation: canonical probe chg-2.0-06 once the tree is committed. |
| T2-H01@2.0 | T2 | 2.0 | accept_major_edit | R1 | Released from hold (owner D2, 2026-11-24); NEEDS OWNER REVIEW. Question made version-neutral so both versions of the set ask the same question; gold 12 stated by the 2.0 page. Runtime confirmation: canonical probe chg-2.0-06. |

Checks per item: grade-2 evidence in a compatible permitted chunk; gold in the evidence; gold leaking into the question; near-duplicates (Jaccard ≥ 0.6); for T5 the feature's absence from the target version, its presence in another version and the documented-alternative screen; for T4 the single-span screen; the provisional model answers as a second signal. Screen report: `benchmark/review/proposed/screen_report.md`.
