# Version-Aware RAG: acceptance audit

Audit date: 2026-11-25. Repository state: `origin/main` = `9cfe189` (merge of PR #2), audited on
branch `docs/p2-acceptance`, whose only changes are documentation: this audit, the error-review files,
README, REPRODUCE.md and the technical report.
Authorities: the private project plan (§3.2 requirements P2-R01
to P2-R58, §4.3 stages and acceptance criteria, §11.2 README standard, §14 completion
checklist), then the private portfolio plan §3.1, the approved design `docs/design/RAG-M1_design.md` and the
owner's private project log (decision ids `Dnn` below; not published). This version replaces the pre-review
audit of 2026-11-23 (kept in the development repository's history). Every statement below cites evidence that was checked;
nothing is estimated.

## 1. Executive summary

**Status: complete at the small-pilot scope.**

- **Met:** research and design, repository scaffold, corpus collection, benchmark construction,
  baseline retrieval and evaluation harness, experiments and error analysis, backend and frontend
  integration, containerization.
- **Met with a documented deviation or limitation:** advanced retrieval (fine-tuned embedder
  implemented but not trained; five of six methods have results); version layer, abstention and
  citations (NLI support threshold uncalibrated).
- **Not met as written (scope decision):** the evaluation-as-CI regression gate (not enforced in CI;
  no regressing or neutral pull request was recorded).
- **Not confirmed in this audit:** CI on the final merge. The last verified CI run (CI #6 at
  `7e87a90`) and the PR #1 checks were green, and the full local suite passed (1,046 tests), but the
  checks of the final pull request (PR #2) and the CI run on `main` after its merge could not be
  verified here.
- **Not produced:** a demo video (screenshots are provided instead).
- **Scope (owner decisions D82-D83):** a small pilot benchmark (52 reviewed records, 40 retained,
  frozen into dev 15 and test 25), no expansion towards the 500-item design target, no 120-item
  ci-gate split, no fine-tuning, no NLI labelling, local Docker as the deployment. All limitations
  are listed in [`docs/LIMITATIONS.md`](../LIMITATIONS.md).

## 2. Stages and acceptance criteria (plan §4.3)

| Stage | Acceptance criterion (plan) | Evidence | Verdict |
|---|---|---|---|
| Research and design | Owner approves library choice and benchmark protocol before data collection | Design rev 3 approved (D14); protocol v1 adopted (D72) | **ACCEPTED** |
| Repository scaffold | Fresh clone passes smoke test in CI | CI #3 at `4df3805` (D48); CI #6 at `7e87a90` green; fresh-clone `pytest -m smoke` 4 passed | **ACCEPTED** |
| Corpus collection | Every chunk carries version metadata; corpus reproducible from manifest | Fresh clone (D114): `corpus manifest --check` ALL MATCH, verify and releases verify VERIFIED, `corpus build` byte-identical (0 manifest changes), `validate` PASSED, 50 real-corpus tests passed | **ACCEPTED** |
| Benchmark construction | Counts per slice reported; test split frozen; verification sample documented | 52 records owner-reviewed (AI-assisted, D81-D82): 40 accepted, 12 rejected; splits frozen in `e22349b` (dev 15, test 25, 0 leakage, test-set sha256 `3a3dcb32…9b7fe3` in `benchmark/splits/test_manifest.json`); counts per type and slice in `docs/benchmark/datasheet.md` and report §7.1; review records `benchmark/review/pilot_review_records.jsonl` | **ACCEPTED** |
| Baseline retrieval and eval harness | Eval CLI reproduces baseline numbers with CIs from one command | `vrag eval retrieval` on the frozen test split: one command, paired cluster bootstrap CIs; three runs byte-identical apart from latency (D93-D95); reproduced exactly from the fresh clone (D131) | **ACCEPTED** |
| Advanced retrieval | Each method evaluated on identical splits with CIs; leakage checks documented | BM25, dense, hybrid RRF, cross-encoder rerank and late interaction evaluated on the same 22 test items with CIs and Holm-corrected differences (report §7.1); split leakage 0; fine-tuning leakage refusal tested (`test_finetune_workflow.py`) | **ACCEPTED WITH DOCUMENTED DEVIATION**: the fine-tuned embedder was implemented but not trained; five of six methods have results |
| Version layer, abstention, citations | Answers cite retrievable passages; abstention precision/recall measured | Citations restricted to retrieved chunks (tests); abstention P/R 0.429/1.000 on 22 answered test items (report §7.2); uncached `/answer` in the fresh-clone container cited the item's grade-2 chunk (D124) | **ACCEPTED WITH DOCUMENTED LIMITATION**: the NLI support threshold remains uncalibrated |
| Experiments, ablations, error analysis | Every claim in the report maps to a stored result file | Report §7 cites `results/retrieval/summary.json`, `runs.jsonl`, `results/answers/*`, `results/errors/*`; owner review of all 110 error rows (D127-D129, `docs/reports/M8_owner_error_review.md`) | **ACCEPTED** |
| Evaluation-as-CI regression gate | A regressing PR fails the check and a neutral PR passes | Gate logic and unit tests exist (`retrieval/gate.py`); a local dry run on the pre-review pilot only: removing the version filter BLOCK (exit 1), docstring change PASS (exit 0); calibration scenarios neutral + 4 benign PASS, 4/4 injected BLOCK. Not enforced in CI: no ci-gate split, no approved baseline, `eval-gate` job dormant, no regressing or neutral PR recorded | **NOT MET AS WRITTEN** (scope decision, D83) |
| Testing and debugging gate | All tests green in CI | CI #6 at `7e87a90` green (run in the private development repository); PR #1 checks 7/7 passed; full suite at `25c15ec` 1044 passed, on `fix/answer-run-lock` 1046 passed, 0 failed, 0 skipped | **NOT CONFIRMED IN THIS AUDIT**: the PR #2 checks and the CI run on `main` at `9cfe189` could not be verified (private repository, no GitHub CLI); no claim is made that they passed |
| Backend and frontend integration | Every UI element is backed by live API data | API tests; headless-browser checks against the live server (pre-review audit); the fresh-clone container serves the built UI and assets (D124) | **ACCEPTED** |
| Containerization and deployment | docker compose works from clean clone; deployed app verified by real question | Fresh clone of `9cfe189` (D114-D118, D124): corpus, indexes and image built from scratch (empty BuildKit cache), container healthy, `/health`, `/versions`, UI, `/search` 200, one uncached `/answer` correct with the expected citation, exactly one Gemini request, quota lock released. Local Docker is the deployment (owner decision D83) | **ACCEPTED** |

## 3. Requirement traceability (P2-R01 to P2-R58)

Verdicts: **MET**; **MET WITH LIMITATION** (met, with a limitation documented in
`docs/LIMITATIONS.md`, or where the row says so, in the technical report); **NOT MET (OWNER DECISION)** (deliberately out of the small-pilot scope);
**OPEN**.

### Pipeline (R01-R20)

| ID | Requirement | Evidence | Verdict |
|---|---|---|---|
| P2-R01 | Collect multiple versions of documentation from a fast-moving software library | DuckDB docs 1.2, 1.3, 1.4 LTS, 1.5, 2.0 at a pinned commit; fresh-clone rebuild byte-identical (D114) | MET |
| P2-R02 | Collect relevant changelogs | 4 GitHub release notes and 17 release posts, hash-verified, local only; migration guides retrievable | MET WITH LIMITATION (restricted text never sent to a model; the changelog hop is not used for answering) |
| P2-R03 | Build a custom benchmark | 52 reviewed records, 40 retained, frozen dev 15 / test 25 (`e22349b`) | MET WITH LIMITATION (small pilot, AI-assisted owner review) |
| P2-R04 | Benchmark contains hard negatives | Version confusers tagged automatically: 20 of 22 test items, 258 chunks; confuser@10 reported | MET WITH LIMITATION (333 lexical-confuser candidates not owner-confirmed; documented in the technical report §7.1) |
| P2-R05 | Benchmark contains multi-hop questions | T4: 4 retained (test 3, dev 1) | MET WITH LIMITATION (below the protocol target of 7) |
| P2-R06 | Benchmark contains version-specific questions | T2: 8 retained (test 5, dev 3) | MET |
| P2-R07 | Benchmark contains questions involving changes introduced in specific versions | T3: 11 retained (test 7, dev 4) | MET |
| P2-R08 | Implement BM25 retrieval | `BM25Retriever`; `test_index_build_query.py`; fresh-clone index build (31,438 chunks) | MET |
| P2-R09 | Implement dense retrieval | `DenseRetriever` (bge-small, FAISS); tests; fresh-clone index build | MET |
| P2-R10 | Implement hybrid retrieval using RRF | `HybridRetriever`, `rrf`; tests; evaluated | MET |
| P2-R11 | Fine-tune an embedding model using mined hard negatives | Pipeline implemented and tested (`finetune.py`, Colab notebook, `index check-model`, `finetuned` method); no model trained | NOT MET (OWNER DECISION D83) |
| P2-R12 | Investigate late-interaction retrieval | ColBERT MaxSim reranking of the hybrid top 100; PyLate equivalence check; evaluated on the test split | MET WITH LIMITATION (reranker, not a full token-level index) |
| P2-R13 | Implement cross-encoder reranking | `RerankRetriever`; evaluated; `/search` exposes first-stage ranks | MET |
| P2-R14 | Add metadata-based version filtering | VCR@10 = 0 for every filtered run (report §7.1) | MET |
| P2-R15 | Implement abstention when evidence is insufficient | Abstention recall 1.000; the 3 correct retrieval-miss abstentions in the error analysis | MET |
| P2-R16 | Implement citation grounding | Citations restricted to retrieved chunks; citation precision 0.900 on 22 answered items; fresh-clone `/answer` cited the grade-2 chunk | MET |
| P2-R17 | Evaluate retrieval and answer quality | Report §7.1-§7.2 on the frozen test split | MET WITH LIMITATION (retrieval on 22 of 25 items, the 3 unanswerable excluded by design; answer evaluation on the 22 of 25 test items that had cached Gemini responses, 3 had no cached response) |
| P2-R18 | Report confidence intervals | 95% paired cluster bootstrap CIs on every metric and difference; Holm correction | MET |
| P2-R19 | Build an evaluation-as-CI pipeline using GitHub Actions | CI workflow green (CI #6, PR #1 checks); `eval-gate` job present but dormant | NOT MET (OWNER DECISION D83): gate not enforced in CI |
| P2-R20 | Quality gate that fails a PR when retrieval quality regresses beyond thresholds | Gate logic tested; local demonstration on the pre-review pilot (BLOCK / PASS) | NOT MET (OWNER DECISION D83): no recorded failing PR, thresholds uncalibrated |

### Evaluation (R21-R31)

| ID | Requirement | Evidence | Verdict |
|---|---|---|---|
| P2-R21 | Measure Recall@k | Recall@5/10/20 (report §7.1) | MET |
| P2-R22 | Measure MRR | MRR@10 | MET |
| P2-R23 | Measure nDCG | nDCG@10 | MET |
| P2-R24 | Measure faithfulness | Local NLI faithfulness 0.633 (16 sentences) | MET WITH LIMITATION (θ = 0.5 uncalibrated, owner decision D83) |
| P2-R25 | Measure citation support | Local NLI citation support 0.633 | MET WITH LIMITATION (as R24) |
| P2-R26 | Measure version correctness | T2 version accuracy 0.333, wrong-version rate 0.000 (3 answered T2 items) | MET WITH LIMITATION (very small n) |
| P2-R27 | Hard-negative evaluation | `hard_negative` slice (n = 20) and confuser@10 | MET |
| P2-R28 | Multi-hop evaluation | `multi_hop` slice (n = 3) | MET WITH LIMITATION (n = 3) |
| P2-R29 | Version-specific evaluation | `version_specific` slice (n = 5) | MET WITH LIMITATION (n = 5) |
| P2-R30 | Baseline comparisons | Every method vs filtered BM25 with CIs; none significant after Holm | MET WITH LIMITATION (fine-tuned method absent) |
| P2-R31 | Error analysis | 110 rows owner-reviewed: 103 retrieval failures, 4 correct abstentions, 3 excluded | MET |

### Artifacts (R32-R39)

| ID | Requirement | Evidence | Verdict |
|---|---|---|---|
| P2-R32 | Custom version-aware benchmark | Frozen splits, review records, datasheet committed | MET WITH LIMITATION (small pilot) |
| P2-R33 | Complete RAG system | API, UI, retrieval, answers end to end; fresh-clone container with an uncached `/answer` | MET |
| P2-R34 | Reproducible evaluation pipeline | One command per evaluation; fixed seeds; raw `runs.jsonl`. `vrag eval retrieval` re-run inside the fresh clone of `9cfe189` reproduced every metric, CI, slice, significance result, hard-negative tag and all 220 raw rankings exactly (latency excluded; D131). The answer evaluation reproduces from cached Gemini responses or new requests (REPRODUCE §5) | MET |
| P2-R35 | Retrieval comparison reports | Technical report §7.1 (committed); `results/retrieval/report.md` (generated, git-ignored) | MET |
| P2-R36 | GitHub Actions regression gate | As R19-R20 | NOT MET (OWNER DECISION D83) |
| P2-R37 | Architecture diagrams | `docs/architecture.md` (3 Mermaid diagrams) and README §5 (1); all 4 rendered without errors with Mermaid CLI 11.4.2 (D99) | MET (GitHub rendering not checked in this audit) |
| P2-R38 | Technical report | `docs/reports/RAG_technical_report.md`, results on the frozen test split, error-review decisions in §7.3 | MET |
| P2-R39 | Complete GitHub repository | All work merged to `main` of the development repository (PR #1, PR #2); this public repository is a single-snapshot release of it, published after an ownership and licence review | MET |

### Frontend (R40-R49)

| ID | Requirement | Evidence | Verdict |
|---|---|---|---|
| P2-R40 | Interactive question-answering interface | Ask tab; uncached `/answer` through the fresh-clone container | MET |
| P2-R41 | Documentation version selector | Version select fed by `/versions` | MET |
| P2-R42 | Retrieved document inspection | Search tab; retrieved passages in Ask | MET |
| P2-R43 | Citation display | Cited-evidence cards | MET |
| P2-R44 | Source passages | Passage cards with path, section and chunk id | MET |
| P2-R45 | Retrieval comparison | Compare-methods tab | MET |
| P2-R46 | Reranking inspection | `/search` first-stage ranks and rank-movement badge (`test_api.py`) | MET |
| P2-R47 | Version-aware filtering | Version filter toggle; incompatible passages marked | MET |
| P2-R48 | Abstention when evidence is insufficient | Abstained badge; API tests | MET |
| P2-R49 | Loading and error states | Busy, error and empty states; 422 messages | MET |

UI evidence for R40-R49: the headless-browser checks of the pre-review audit and `docs/screenshots/`
(9 images); the fresh-clone container served the same built UI. No demo video.

### Deployment (R50-R57) and design constraint (R58)

| ID | Requirement | Evidence | Verdict |
|---|---|---|---|
| P2-R50 | Deploy the retrieval backend | Local Docker deployment verified from a fresh clone | MET WITH LIMITATION (local only, owner decision D83; no public host, no auth or rate limiting) |
| P2-R51 | Expose retrieval and answering through FastAPI | `api/app.py`; `test_api.py` (17 tests); fresh-clone container checks | MET |
| P2-R52 | Build the frontend | `npm ci` and `npm run build` in the fresh clone (D116) | MET |
| P2-R53 | Connect frontend and backend | Same-origin serving in the container; UI assets 200 (D124) | MET |
| P2-R54 | Containerize the application | `Dockerfile`, `compose.yaml`; image built from scratch (D118) | MET |
| P2-R55 | Deploy where feasible | Local Docker accepted as the deployment (owner decision D83); free-tier analysis in the decision sheets | MET WITH LIMITATION |
| P2-R56 | Provide local Docker execution | `docker compose up --build` from a fresh clone | MET |
| P2-R57 | Ensure evaluation remains reproducible | As R34 | MET |
| P2-R58 | Not an ordinary PDF chatbot or basic RAG application | Version-aware benchmark, five-method comparison with statistics, version filter, gate mechanism (design D14) | MET |

**Totals (58 requirements):** MET 39, MET WITH LIMITATION 15, NOT MET (OWNER DECISION) 4 (R11,
R19, R20, R36), OPEN 0.

## 4. Evidence

### Git and CI

| Item | Evidence |
|---|---|
| PR #1 (`p2-completion` → `main`) | Merged by the owner as `690f780`; checks 7/7 passed (owner report, D113) |
| PR #2 (`fix/answer-run-lock` → `main`) | Merged by the owner as `9cfe189`, contains fix `302d1c5` (`origin/main` and `refs/pull/2/head` verified, D123, D131). Check results and the CI run on `main` after the merge: not verified in this audit (private repository, unauthenticated API 404, no GitHub CLI) |
| CI #6 | Workflow run on `p2-completion` at `7e87a90`: success (D108-D109) |
| CI jobs | `checks` (Ubuntu and Windows), `pre-commit`, `linux-probe-check`, `secret-scan` (gitleaks; PR scans need `pull-requests: read`, D110), `corpus-rebuild`, `eval-gate` (dormant) |
| Full test suite | 1044 passed at `25c15ec` (D102); 1046 passed, 0 failed, 0 skipped on `fix/answer-run-lock` (D120) |

### Benchmark and results (frozen test split)

- Splits: `benchmark/splits/` (dev 15, test 25), test-set sha256 `3a3dcb32…9b7fe3`.
- Retrieval (22 of 25 items; T5-02, T5-08, T5-12 unanswerable by design): the version filter brings
  VCR@10 from 0.70-0.75 to 0 and raises MRR@10 by 0.21-0.25 for every method; no method differs
  significantly from filtered BM25 after Holm correction; late interaction has the highest filtered
  Recall@10 (0.818 vs 0.682; difference 0.136 [-0.079, 0.341]).
- Answers (filtered hybrid, cached Gemini responses only; 22 of 25 items; T2-H01@1.5, T2-H01@2.0 and
  T3-06 had no cached response): accuracy 0.789 (15 correct of 19 answerable; the 4 abstentions on
  answerable questions count as not correct, and no answered question was wrong), citation precision 0.900,
  abstention precision/recall 0.429/1.000; citation support and faithfulness 0.633 (uncalibrated).
- Error analysis: 103 retrieval failures across all five methods in both filter modes
  (lexical_miss 42, wrong_section 23, wrong_version 20, chunk_split 12, missing_hop 6); all 20
  wrong_version failures are from unfiltered runs and the version-filtered runs have none; plus 4
  correct abstentions and 3 excluded cases.
- Probes: 168 canonical observations at `ac9f206` (`data/evidence/`); per-item probe consistency not
  implemented (LIMITATIONS §4).

### Fresh-clone verification

Local evidence: a folder outside the repository (`m12-fresh-clone`, not published)
(`m12_log.txt`, `m12_log_before_fix_rerun.txt`, `out_*.txt`, `answer_check.py`, `compare_eval.py`).
REPRODUCE.md steps run in the fresh clone: §1 environment and smoke tests, §2 corpus, §3 indexes, §5
retrieval evaluation (`vrag eval retrieval`, exit 0 in 1386 s, results identical to the development
repository's, `out_10b`/`out_10c`), §7 application (frontend build and Docker). The answer evaluation
(§5) and the quality checks (§8) were run in the development repository. A first fresh-clone
evaluation attempt failed with `WinError 1920` because the Docker container had written Linux
symbolic links into the Hugging Face cache shared through the compose bind mount; the re-run used a
separate host-only cache (environment only; no code or input change).

### Where the evidence lives

| Evidence | Location |
|---|---|
| Decisions and run log | The owner's private project log (D1-D134; not published) |
| Results tables | `docs/reports/RAG_technical_report.md` §7 (committed); `results/` (generated, git-ignored) |
| Benchmark | `benchmark/splits/`, `benchmark/review/`, `docs/benchmark/datasheet.md`, `docs/benchmark/pilot_report.md` |
| Error review | `docs/reports/M8_owner_error_review.md` and `.csv` |
| Limitations | `docs/LIMITATIONS.md` |
| Probe evidence | `data/evidence/` |

## 5. Plan and design conflicts (status)

| # | Conflict | Status |
|---|---|---|
| C1 | Planned layout `eval/`, `api/`, `web/`, `docker/` vs the implemented layout | Resolved: the empty scaffold placeholders were removed in the public release; the code lives in `Dockerfile`, `configs/gate.toml` and `frontend/` |
| C2 | Planned "changelog linking" / design H2 vs the privacy decision that restricted release text is never sent to a model | Resolved as a documented limitation |
| C3 | Design 500 items vs the pilot | Resolved: small-pilot scope (D82) |
| C4 | React/Next.js vs React + Vite | No action (allowed) |
| C5 | Design §6.2 WARN PR comment vs read-only permissions | Moot: the gate is not enforced (D83) |
| C6 | Design §6.1 CI index cache | Moot: the gate job is dormant |

## 6. Completion checklist (plan §14)

| Item | Status |
|---|---|
| README to the §11.2 standard with final numbers | Done (README §11 on the frozen test split) |
| Technical report | Done (`docs/reports/RAG_technical_report.md`) |
| Architecture diagrams | Done (rendered locally with Mermaid CLI; GitHub rendering not checked in this audit) |
| Reproduction guide | Done (`REPRODUCE.md`; steps §1-§3, the §5 retrieval evaluation and §7 executed from a fresh clone) |
| CI green | CI #6 and PR #1 checks green; the PR #2 checks and the post-merge run on `main` were not verified in this audit |
| Commit history | Developed in a private repository (implementation, docs, evidence, freeze, results, CI and fix commits, two PRs); this public repository is a single-snapshot release |
| Limitations documented | Done (`docs/LIMITATIONS.md`) |
| Demo video or screenshots | Screenshots (9, `docs/screenshots/`); no demo video |

## 7. Known open items

1. CI on the final merge (`9cfe189`): the PR #2 checks and the CI run on `main` were not verified in
   this audit. The last verified CI run and the full local test suite were green.
2. GitHub rendering of the four Mermaid diagrams was not checked in this audit (they render
   without errors in Mermaid CLI 11.4.2).
