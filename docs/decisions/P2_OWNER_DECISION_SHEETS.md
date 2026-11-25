# P2 owner decision sheets

Prepared 2026-11-23 with AI assistance from the evidence in the repository. Each sheet states the question,
the evidence, the options and a recommendation. **Nothing here is decided until you answer.** Reply
with the sheet number and your choice (for example "D1 done, D2 A, D3 B, D4 approve, D7 commit").

## Status (updated 2026-11-24)

| # | Decision | Status |
|---|---|---|
| D1 | Pilot review corrections | **Policy decided (owner, 2026-11-24):** proposed corrections approved as recommendations. **Item-level review still open:** the owner reviews the 52-row proposed sheet (`docs/benchmark/REVIEW_DECISION_GUIDE.md`) |
| D2 | Held T2 drafts | **Decided:** both T2-H01 drafts released as review candidates (1.5 → 720, 2.0 → 12); `benchmark/review/released_holds.json`; canonical probe `chg-2.0-06` to confirm after the commit |
| D3 | Benchmark scale and protocol | **Decided:** planning target ~200, no padding; 120-item ci-gate kept as a planning requirement (feasibility gap reported in the guide §4); protocol v1 amended with checks T4-S1, T5-S1, T5-S2, T5-S3; splits not frozen |
| D4 | `context_filtered` | **Approved as implemented**; protections and regression tests retained |
| D5 | Gate thresholds | **Direction approved** (scaling rule); final numbers need the frozen, verified ci-gate split and a separate approval. The non-blocking injection was investigated: "RRF k = 1, depth 10" only reorders two interleaved lists and measured no quality loss (Recall@10 +0.033, nDCG@10 +0.013 on the pilot), so it is now a benign probe; a genuine fusion degradation (depth 3: Recall@10 −0.055, nDCG@10 −0.069) BLOCKs. Calibration on the pilot: neutral and 4 benign PASS, 4 of 4 injected BLOCK |
| D6 | Deployment | open (recommendation B: local Docker of record) |
| D7 | Git: commits, push, demo PRs | open |
| D8 | Colab fine-tune and NLI labels | open (needs the review first) |

**Gaps reported, not decided:** genuine T4 (3) and T5 (4) replacements need an inventory extension
(a second discovery pass plus canonical probes) before a T4/T5 redraft round; the 120-item ci-gate
split is not feasible at ~200 items without weakening the test split (guide §4).

---|---|---|---|
| D1 | Pilot review (50 items) | M4 and everything after | Review the proposed sheet (45-60 min) |
| D2 | The two held T2 drafts | T2 count | A: release with a major edit |
| D3 | Benchmark scale and the pilot outcome | M4, M9 | B: about 200 verified items, no padding |
| D4 | `context_filtered` privacy rule | M7 | Approve as implemented |
| D5 | Gate thresholds for the real split size | M9 | Approve the scaling rule |
| D6 | Deployment | M12 (R50, R55) | B: local Docker of record; optional search-only public Space |
| D7 | Git: commits, push, demo PRs | M2/M3 re-check, M9, M10, M12, M13 | Authorise commits now; push separately |
| D8 | Colab fine-tune run and NLI labelling | M6, R24/R25 | After D1 (fine-tune) / after D3 (labels) |

---

## D1. Pilot review

**What:** confirm, change or reject the AI-proposed decision for each of the 50 items.
**Evidence:** `docs/benchmark/REVIEW_DECISION_GUIDE.md` (findings, per-item reasons) and
`benchmark/review/proposed/pilot_review_sheet_PROPOSED.csv` (validated by the importer in memory:
0 problems). Proposed: 30 accept, 12 minor edit, 1 major edit, 7 reject; 7 items marked ambiguous.
**Critical:** four T2 items list another version's answer as an accepted alias (a wrong-version
answer would score as correct); three T5 items are answerable in their target version.
**Action:** fill `reviewer` and `time_spent_seconds`, adjust, save as
`benchmark/review/pilot_review_sheet.csv`, reply "D1 done". The assistant then imports, validates,
generates the pilot report and the split plan, and freezes only when every item has your decision.

## D2. The two held T2 drafts (T2-H01, `SELECT 2 * 3!;`)

**Evidence:** both the 1.5 and the 2.0 pages state 2.0 returns 12 and 1.5 returned 720; the
"conflict" is a defect in the drafts (the @1.5 question names 2.0; its alias `12` is the 2.0
answer). **Options:** A) release with a major edit (neutral question, @1.5 gold 720 without alias 12,
@2.0 gold 12, type value), adding a third T2 set; the assistant adds a reviewed-hold path to the pilot
tooling; B) keep them held and uncounted. **Recommendation: A.** The canonical probe for
`chg-2.0-06` should confirm the runtime values once the tree is committed (D7).

## D3. Benchmark scale and the pilot outcome

> **Owner decision, 2026-11-24 (supersedes the ~200 planning target below):** finish P2 as a
> **small pilot benchmark**: the reviewed pilot (52 records, 40 retained; dev 15, test 25 by the
> split plan). No expansion toward ~200 items and no 120-item ci-gate split. The T4 (4 of 7) and
> T5 (5 of 12) shortfalls and the per-type rates below 80% are documented as limitations, not
> fixed. The owner confirmed all 52 review decisions (AI-assisted; review times are estimates).
> Freeze, commit and push still need separate approval.

**Facts (measured):**

| | Value |
|---|---|
| Verified (V2) items today | **0** |
| Pilot items, proposed accept or edit | 43 of 50 (T1 12, T2 6, T3 11, T4 5, T5 9) |
| Pilot threshold (≥ 80% accept or minor per type) | met for T1, T2, T3; **missed for T4 (71%) and T5 (75%)** |
| Change inventory | 32 changes: 20 pilot candidates (19 drafted), 10 unresolved (need canonical probes), 2 excluded |
| Drafts so far | 90 (40 rejected by automatic checks, including 15 of 23 T2 drafts) |
| T2 evidence found in the docs | 2 accepted sets + 1 fixable held set + 1 new candidate (`epoch_ms` → `make_timestamp_ms`, 1.2/1.3 vs 1.4+) |

**Estimate (labelled as an estimate; no drafting run behind it):** with the current inventory plus
a second discovery pass over the 2.0 migration guide and the canonical probes for the 10 unresolved
changes, genuine documentation evidence supports roughly **150-220 verified items**: T1 plentiful,
T3 20-40, T4 15-30, T5 15-25 (after the alternative-answer check), T2 about 10-15 items (4-6 sets).
Reaching **500** would need about 300 more T1 stable-fact items or weaker items, which adds size
without testing version awareness. Effort for ~150 more items: Gemini drafting within the free quota
(about 1-2 requests per draft, ≤ 400 per day), and about 3-4 hours of your review time.

**Consequences of a smaller benchmark:** wider CIs (for n ≈ 100 test items a proportion near 0.5
has a 95% CI of about ±0.10), so only large differences between methods are detectable; the ci-gate
split cannot be 120 items (≈ 60 instead), so the gate thresholds must scale (D5); slice analyses (T2,
T4) stay small and are reported as such.

**Options:** A) pursue the 500 target (not recommended: padding); B) **about 200 verified items**
(test ≈ 100, ci-gate ≈ 60, dev ≈ 40, grouped by change), with per-type minimums and honest counts;
C) freeze the reviewed pilot only (≈ 43 items; fastest, weakest statistics).
**Recommendation: B**, and for the pilot outcome: approve protocol v1 with two drafting checks
added (T5: no alternative function or documented section answers it in the target version; T4: no
single span answers alone), then redraft T4/T5 under those checks.

## D4. `context_filtered` privacy rule

**Behaviour:** before any request, every prompt is audited for restricted release text (restricted
lines, 10-word restricted sequences, key-like strings). If it fails, each retrieved passage is
audited alone; passages that fail are dropped; the rebuilt prompt is audited again and sent only if
it passes; otherwise the pipeline abstains (`privacy_blocked`) and sends nothing.
**Verified by tests** (`tests/unit/test_gate_answer.py`): the dropped passage never appears in the
prompt sent and the sent prompt passes the full audit; a restricted sequence spanning two passages
blocks the whole prompt (nothing sent); an answer citing a dropped passage becomes an abstention
(`invalid_citation`), so no unsupported answer is returned.
**Impact (provisional run):** 3 of 50 prompts filtered (6%): 2 still answered correctly (T4-14,
T5-11); in 1 (T1-20) the dropped passage held the evidence and the pipeline abstained instead of
answering. Without the rule all 3 would have been blocked. No restricted text reached Gemini.
**Options:** approve as implemented / revert to block-whole-prompt. **Recommendation: approve.**
It is never weaker than blocking: what is sent always passes the same full audit.

## D5. Gate thresholds for the real split size

**Evidence:** `vrag eval gate calibrate --provisional` (`results/gate/calibration.md`). With the
design's fixed τ and C: at 38 items one item moves Recall@10 by 0.026, already more than τ = 0.025;
with 6 T2 items one item moves T2 Recall@10 by 0.167, **more than C = 0.100, so one T2 flip would
BLOCK**, which the design rules out ("a single flip can never block"). Scenarios: the neutral change
PASSes; all three benign perturbations PASS; three of four injected regressions BLOCK (version filter
removed, 10% of chunks deleted, dense-only); "RRF degraded (k = 1, depth 10)" PASSes because it
does not lower quality on this data (not tuned away; reported as observed).
**Proposal (keeps the design's own "approximate items" column):** τ = max(design τ, 3/n) and
C = max(design C, 7/n) for Recall@10 (n = ci-gate items); T2 Recall@10 gated only when the ci-gate
split has at least 20 T2 items (τ = 2/n_T2, C = 4/n_T2), otherwise reported but not gated; nDCG@10
and VCR@10 keep the design values unless calibration on the real split shows natural spread above
τ. The final numbers are computed by `vrag eval gate calibrate` on the frozen ci-gate split and
brought back for approval together with the baseline sha256 (`configs/gate.toml`).
**Recommendation: approve the rule now, approve the numbers when the split exists.**

## D6. Deployment

**Requirement:** R50 and R55 are Advanced tier, "where feasible"; M12 needs "deployed app verified
by a real question" if deployed; §14 accepts a documented fallback.

| Option (checked 2026-11-23) | Cost | Fits our server? | Docker | Persistence | Auth / rate limiting | Notes |
|---|---|---|---|---|---|---|
| Hugging Face Spaces, CPU Basic | free | yes: 2 vCPU, 16 GB RAM, 50 GB (non-persistent) disk | the official docs now say new Docker Spaces need a paid plan; to be verified on your account | none (rebuild on restart) | private Spaces possible; no built-in rate limiting | sleeps after 48 h idle |
| Render free web service | free, no card | **no**: 512 MB RAM, 0.1 CPU | yes | none | none | sleeps after 15 min; bandwidth 5 GB/month |
| Koyeb free instance | free with card | **no**: 512 MB RAM | yes | none | none | card with pre-authorisation required |
| Fly.io | no free tier for new accounts | — | yes | volumes | — | trial only |
| Google Cloud Run | free tier needs a billing account | yes | yes | none | IAM | a billing account is a paid-service step (needs approval) |
| **Local Docker Compose** | free | yes | yes | mounts | 127.0.0.1 only | **verified**: healthy, 9/9 browser checks |

**Privacy constraint:** `/answer` needs the restricted sources for its fail-closed audit; uploading
them to any host is an external transfer that is not authorised. A public deployment can therefore
serve search and compare only, or `/answer` with an audit built from hashes of the restricted text
(not the text); that variant is a design change needing your approval.
**Options:** A) Hugging Face Space with search and compare only, private or with simple rate
limiting added (only if your account can create a free Docker Space); B) **local Docker Compose as
the deployment of record**, with the documented reasons (§14 "fallback documented"); C) Cloud Run
with a billing account (not recommended: paid-service risk).
**Recommendation: B now; A later as an optional public demo.**

## D7. Git

**Needed for:** CI on the new code (M10), re-verifying M2/M3, the canonical probe run, a
fresh-clone reproduction, the demo PRs (M9) and a meaningful commit history (§14).
**Proposal:** the assistant makes about 10 small commits on a feature branch (evidence models, probes,
benchmark tooling, retrieval, answers, gate, API/UI, Docker, docs, tests), each passing the hooks;
nothing is pushed until you say "push". Then the two demo PRs (`docs/gate_demo/README.md`) after
the baseline approval. **Options:** authorise commits / commits and push / not yet.

## D8. Colab run and NLI labels (your hands needed, no decision)

- **Fine-tune (after D1 and the freeze):** the assistant runs `vrag index finetune-data`; you upload three
  files and run `scripts/colab/finetune_embedder.ipynb` on a free T4 (about 5-15 minutes), then
  download one zip. The assistant validates (`vrag index check-model`), indexes and evaluates.
- **NLI labels (best after D3, when answers are regenerated on verified items):** label 200 pairs
  in `results/answers/nli_pairs_to_label.csv` following `docs/benchmark/nli_labelling_guide.md`
  (about 1.5-2 hours).

Sources for D6: [Hugging Face Spaces GPU/hardware docs](https://huggingface.co/docs/hub/en/spaces-gpus),
[freetier.wiki: HF Spaces](https://freetier.wiki/services/hugging-face-spaces/),
[Render free tier 2026](https://agentdeals.dev/vendor/render),
[Koyeb free tier 2026](https://www.srvrlss.io/provider/koyeb/),
[free hosting tiers compared (Sep 2026)](https://flaviocopes.com/hosting-free-tiers/),
[Cloud Run pricing](https://cloud.google.com/run/pricing),
[Google Cloud free features](https://docs.cloud.google.com/free).
