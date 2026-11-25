# RAG-M3 S6: CI and Documentation

Status: **S6a and S6b implemented and approved after independent review (2026-11-22); S6c (documentation) implemented, awaiting owner review.** Owner decisions S6-D1 to S6-D10 approved (2026-11-22); IR-3 placement decided (Option B, RAG-M4). §3 and §4 describe the job and the command as implemented; the review-driven changes to the original plan and their reasons are in §9. S6c ends with the one consolidated local S6 commit (S6-D9); D10 rehearses that exact committed state; S6d is only the push and CI evidence collection, with explicit owner approval. RAG-M3 acceptance still requires a green `corpus-rebuild` run on GitHub (§8, criterion 1).

Scope comes from `RAG-M3_plan.md` §8 (S6 row: `corpus-rebuild` CI job with a fresh checkout that fetches, verifies, builds twice, compares and validates; README, notices, `DEVELOPMENT.md`, tracker; API access in CI only through the built-in `GITHUB_TOKEN`), §2 (the `corpus-rebuild` job lives in `.github/workflows/ci.yml`; `check-upstream`), §7 P1/P2, §9 Q11 and U6, §10 (acceptance), and `RAG-M3_S5_build.md` S5-D9. All S1-S5 decisions, outputs and evidence stay as approved.

## 1. Owner decisions (2026-11-22)

| # | Decision |
|---|---|
| S6-D1 | `corpus-rebuild` is a job in the existing `.github/workflows/ci.yml`, with the existing triggers (push to `main`, pull requests, manual dispatch) |
| S6-D2 | Reproducibility: two independent checkouts, each with its own fresh source fetch and its own corpus build |
| S6-D3 | `pytest -m corpus` runs in CI and the job fails if any expected corpus test is skipped |
| S6-D4 | Artifacts: only the output SHA-256 list, the validation log and the corpus-test log. Never corpus text, release bodies, release-post text, caches or generated corpus files |
| S6-D5 | `vrag corpus check-upstream`: minimal and read-only; checks pinned-source reachability and release-body hash consistency; modifies no manifest, cache or other project file; exit codes 0/1/2 with documented meanings (§4); **not** part of PR CI |
| S6-D6 | The GitHub Release backup of the docs snapshot (Q11) is **deferred** (§5); no write permissions are introduced |
| S6-D7 | The optional sampled HTTP 200 check is skipped |
| S6-D8 | The IR-3 report is prepared separately before S6a, scoped to the S5 follow-up, without changing the approved S5 implementation: `RAG-M3_IR3_report.md`. **Placement decided (owner, 2026-11-22): Option B**: IR-3 is implemented in the first stage of RAG-M4 together with IR-2 and the `RuntimeObservation` model; it stays todo, deferred to RAG-M4, and is not implemented in S6 (no probe file changes) |
| S6-D9 | One consolidated local S6 commit after S6c; no push without explicit owner approval |
| S6-D10 | A fresh-clone local rehearsal before pushing, run **after** the S6 commit so that the temporary clone contains exactly the committed S6 implementation, and before S6d |

### 1.1 Additional owner safeguards

1. Output paths are derived from `version_aware_rag.corpus.build.ALL_OUTPUTS`, never listed by hand.
2. The two builds are compared on both the relative output paths and their SHA-256 values.
3. Any missing expected output fails the job.
4. No corpus text, release-note text, release-post text, cache or generated corpus file is uploaded.
5. The corpus-test step verifies the expected test count and zero skips from structured test results, not a loose substring search (§3, step 12).
6. Workflow permissions stay `contents: read`.
7. Release API calls use only the built-in `GITHUB_TOKEN`; no personal access tokens, no committed secrets.
8. Every action stays pinned to a full commit SHA.
9. All approved S1-S5 decisions and evidence are preserved.

## 2. Inspection summary (2026-11-22)

- The existing CI has the jobs `checks` (Ubuntu and Windows: smoke, Ruff, format, mypy, full pytest; the 45 corpus tests skip there for lack of a cache), `pre-commit`, `linux-probe-check` and `secret-scan`; `permissions: contents: read`; uv 0.13.9; actions pinned to SHAs (enforced for `ci.yml` by `tests/unit/test_tooling_pins.py`).
- Docs source: `vrag corpus fetch` makes an anonymous HTTPS partial clone (`--depth 1 --filter=blob:none`) of the public `duckdb/duckdb-web` at the pinned commit with a sparse checkout (about 29 MB), forcing `core.autocrlf=false` and `core.eol=lf`; no credentials are needed.
- Release notes: `vrag corpus releases fetch` makes four REST calls by release id with hash checks; it sends `GITHUB_TOKEN` as a bearer token when set (never logged), which avoids the shared anonymous rate limit.
- Fetch timestamps cannot reach any output: the manifest's `first_retrieved_at` is the fixed config value `evidence_retrieved_at`; fetch records are display-only.
- In a fresh checkout the committed manifests and reports are present and the git-ignored chunk files are absent. `existing_output_problems` only checks the committed manifests when chunk files exist, so the build proceeds; this exact combination has no test yet (added in S6a).
- The committed `chunk_manifest.json`, `release_note_chunk_manifest.json`, `build_manifest.json` and S4 reports carry the sha256 of every chunk file; a Linux build that reproduces them byte for byte proves the Linux outputs equal the Windows-built ones.
- README (still RAG-M2), `THIRD_PARTY_NOTICES.md` (release notes "planned", release posts absent) and `DEVELOPMENT.md` (no corpus commands) are out of date.

## 3. The `corpus-rebuild` job (S6a)

Settings: `runs-on: ubuntu-latest`; `timeout-minutes: 45`; `defaults.run.shell: bash` (`-eo pipefail`); inherits `permissions: contents: read`; actions only `actions/checkout`, `astral-sh/setup-uv` and `actions/upload-artifact`, pinned to the SHAs already used in `ci.yml`; no `actions/cache` for `data/` (every run fetches afresh; the uv package cache is allowed). `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` is set only on the release-fetch steps.

| # | Step | Command |
|---|---|---|
| 1 | Two checkouts *(as implemented; see §9.5)* | `actions/checkout` twice, `path: a` and `path: b`, `persist-credentials: false`. Checkout A also sets `fetch-depth: 0` (full history), because a corpus test run in A reads the S2 manifest from commit `7e4125f`. Checkout B keeps the default shallow clone |
| 2 | Toolchain | `setup-uv` (0.13.9); `uv python install`; `uv sync --locked` in `a/` and in `b/` |
| 3 | Environment facts | print `git --version`, `nproc`, `free -m`; fail unless git is at least 2.44 (S5a check mode relies on `GIT_NO_LAZY_FETCH`) |
| 4 | Fetch docs (a) | `uv run vrag corpus fetch` |
| 5 | Fetch releases (a), token | `uv run vrag corpus releases fetch` |
| 6 | Verify inputs (a) | `uv run vrag corpus manifest --check`; `uv run vrag corpus verify`; `uv run vrag corpus releases verify` |
| 7 | Build A | `uv run vrag corpus build` |
| 8 | Committed evidence reproduced (a) | `git status --porcelain` must be empty and `git diff --exit-code` must pass: the committed manifests, build manifest and reports are byte-identical on Linux |
| 9 | S4 commands agree (a, S5-D6) | `uv run vrag corpus chunk --check`; `uv run vrag corpus releases chunk --check` |
| 10 | Check mode (a) | `uv run vrag corpus build --check` (exit 0, writes nothing) |
| 11 | Validate (a) *(as implemented; see §9)* | `uv run vrag corpus validate` writes to `../validate-raw.txt`, which is never printed or uploaded. A rule-aware sanitizer embedded in the step (between the `validate-log-sanitizer` markers) writes `evidence/validate.txt`. It keeps each finding's severity and rule code and rebuilds the message only from a known template of that rule (99 templates) with typed fields (locations, corpus paths, chunk ids, versions, enum values, counts). Free-form payloads become `<redacted>`, and a message matching no template is withheld whole. The step exits with validate's own status (exit 0; the four known warnings are allowed) |
| 12 | Corpus tests (a) *(as implemented; see §9)* | `uv run pytest -m corpus --collect-only -q` gives the collected node ids; `uv run pytest -m corpus --tb=no -rN --junitxml=../corpus-tests.xml`, output kept as `evidence/corpus-tests.txt`. `--tb=no -rN` keeps assertion values and skip or failure reasons out of the log; `-rN` overrides the project's `-ra`. A stdlib Python check (between the `corpus-junit-check` markers) reads the JUnit XML and requires collected = suite `tests` = number of test cases = `EXPECTED_CORPUS_TESTS` (45, pinned in the job's `env`), with zero `skipped`, `failures` and `errors`, both in the suite totals and on individual test cases. pytest's own exit status is also enforced |
| 13 | Build B (b) *(as implemented)* | steps 4, 5 (token), 6 and 7 in `b/`, then `git status --porcelain` empty and `git diff --exit-code` in `b/` |
| 14 | Compare *(as implemented)* | the path list is printed from `ALL_OUTPUTS` (in `a/`) and must be non-empty. For `a/` and `b/`, every listed path must exist (else fail, naming it), and `sha256sum <relative path>` is written to `a.sha256` and `b.sha256`. `diff -u a.sha256 b.sha256` must be empty, and each file must have exactly as many lines as the path list. `sha256sum` lines hold both the relative path and the hash, so both are compared. `a.sha256` is copied to `evidence/outputs.sha256`, and `a/` must still have a clean working tree |
| 15 | Evidence (`if: always()`) *(as implemented)* | `upload-artifact` (artifact `corpus-rebuild-evidence`) of exactly `evidence/outputs.sha256`, `evidence/validate.txt` and `evidence/corpus-tests.txt` (explicit file paths, no directories or globs, `if-no-files-found: warn`) |

Only the build outputs are compared. They contain no timestamps (S5 showed independent builds are byte-identical; JSON is written with sorted keys); nondeterministic metadata (git internals, fetch-record timestamps, uv caches) lives in the caches, which are not compared. Any real nondeterminism fails the job, as intended.

A safety test (`tests/unit/test_ci_corpus_job.py`, stdlib text checks, no new dependency) pins the job's properties:
- the job exists in `ci.yml`, and the existing jobs and triggers are preserved;
- workflow permissions are `contents: read` only;
- `GITHUB_TOKEN` appears only as `${{ secrets.GITHUB_TOKEN }}`, and only on the two release-fetch steps;
- actions come only from the allowed set, pinned to full SHAs, and no `actions/cache` step touches `data/`;
- the artifact paths are exactly the three evidence files;
- both checkouts fetch, verify and build, and the comparison uses `ALL_OUTPUTS`;
- `EXPECTED_CORPUS_TESTS` equals the collected corpus tests;
- `check-upstream` does not appear in the workflow.

*As implemented (§9):* the same file also extracts the job's embedded code and tests it directly:
- the JUnit check (passes only when every count agrees; rejects skips, failures, errors and count mismatches);
- the validate-log sanitizer:
  - the raw output is never printed or uploaded, and the exit status is preserved;
  - headings never survive, quoted or not;
  - every template is rebuilt exactly;
  - its enumerations equal the code's;
  - unrecognised lines are withheld;
  - every real manifest path (2,031 docs pages, 17 release posts, 4 release-note paths: 2,052) is kept;
- template coverage, checked in both directions against the message sites in `validate.py` found through the AST, with meta-tests on injected sources;
- the corpus pytest flags print no skip reasons while every skip still fails the JUnit check.

SHA pinning stays covered by `test_tooling_pins.py`. `tests/unit/corpus/test_build.py` adds the fresh-checkout test: committed manifests are present, chunk files are absent, the build succeeds, and the committed files stay byte-identical.

## 4. `vrag corpus check-upstream` (S6b)

Read-only drift check for local or manual use; never part of PR CI.

| Check | How | Drift when |
|---|---|---|
| Pinned docs commit reachable | GitHub REST API, `GET /repos/duckdb/duckdb-web/commits/<pinned sha>` | not found (404 or 422) |
| Each approved release body | `GET /repos/duckdb/duckdb/releases/<release id>` for the four approved ids; the body's sha256 and bytes, tag, publish time, draft and prerelease flags are compared with the committed manifest | any field differs |

Exit codes: **0** no drift (commit reachable and all four releases match); **1** drift detected (each finding names the commit or release and the differing field; release text is never printed or saved); **2** the check could not be completed (network or HTTP error, rate limit, or an unreadable configuration or manifest). Uses `GITHUB_TOKEN` when set. It writes nothing: no manifest, cache or other project file. Tests use an injected fake fetcher (no network) and assert that the file tree is byte-identical before and after.

*As implemented* (`src/version_aware_rag/corpus/upstream.py`, the handler in `commands.py`; owner decisions S6b-D1 to S6b-D3 and the audit fixes in §9):

**Exit-code precedence (S6b-D1).**
- **1** if any drift is found, even when another check is incomplete; both are reported.
- **2** if there is no drift but at least one check is incomplete.
- **0** only when every check completed without drift.

**Drift.**
- The commit returns 404 or 422, or a different `sha`.
- A release returns 404.
- A release's `id`, `tag_name`, `published_at`, `draft` or `prerelease` differs, shown as observed and expected (S6b-D2).
- A release body's sha256 or byte count differs; only the hashes and sizes are shown, never text (S6b-D2).
- The comparison is with the committed manifest only (S6b-D3).

**Incomplete, never drift.**
- No response: network failure, timeout, or an HTTP-library error such as a truncated read.
- HTTP 401, 403 or 429 (rate limit or access denied), 5xx, or any other non-200 status.
- A response that is not JSON (including JSON nested too deeply to parse), or has an unexpected shape.
- A release body that cannot be encoded as UTF-8.
- Any unexpected exception, whether in the fetcher, in one check (the other checks still run), or in the handler.
- An unreadable configuration or manifest, including an unreadable manifest part file or directory.

**Output.**
- One `checked …` line per check (one commit and four releases), then `DRIFT` or `INCOMPLETE` findings, then a `RESULT:` line.
- Findings and logs carry only check names, field names, ids, hashes, sizes, metadata values and exception **type names**: never exception messages, release text or the token.
- The body is hashed and measured in memory only.

**HTTP client.** A minimal stdlib client returns the status and body, without reading error bodies; it reuses only the S2 request headers. The S2 `releases.py` is unchanged.

Limitation: a commit visible through the API is very likely, but not guaranteed, to be fetchable by git; the CI fresh fetch remains the authoritative reachability test.

## 5. Q11 (GitHub Release backup) deferred

The backup would publish the pinned MIT docs snapshot as an asset on a release of this repository (never release notes or posts). Deferred because: it needs `contents: write` and a persistent outward action, while S6 keeps CI read-only; the risk it mitigates (P1, the pinned commit becoming unfetchable) is low, and its failure is loud (the fresh fetch and `check-upstream` both report it) rather than silent. Revisit if the pinned commit ever becomes unfetchable or before the project is made public.

## 6. Files per stage

| Stage | Files |
|---|---|
| Planning (now) | NEW `docs/design/RAG-M3_IR3_report.md`, NEW `docs/design/RAG-M3_S6_ci.md`, external `EXECUTION_TRACKER.md` |
| S6a | `.github/workflows/ci.yml` (new job); `tests/unit/corpus/test_build.py` (+1 fresh-checkout test: committed manifests present, chunk files absent, build succeeds and leaves the committed files byte-identical); NEW `tests/unit/test_ci_corpus_job.py` |
| S6b | NEW `src/version_aware_rag/corpus/upstream.py`; `src/version_aware_rag/corpus/commands.py` (additive `check-upstream` registration and handler); NEW `tests/unit/corpus/test_upstream.py` |
| S6c | `README.md`; `THIRD_PARTY_NOTICES.md`; `DEVELOPMENT.md`; `docs/design/RAG-M3_S6_ci.md` (results); `docs/design/RAG-M3_plan.md` (status, S6 row, Q11 deferral, `check-upstream`, IR-3 re-assigned to RAG-M4); external tracker. S6c ends with the one consolidated local S6 commit: the files of S6a-S6c plus the two planning documents |
| D10 rehearsal | no repository change; evidence recorded in the external tracker only |
| S6d | no repository change: the push (explicit owner approval) and CI evidence collection; the evidence is recorded in the external tracker, which needs no repository commit |

## 7. Implementation sequence

1. **Planning** (this document and the IR-3 report): owner review.
2. **S6a**: the CI job, the fresh-checkout test and the CI safety test; focused tests; owner review.
3. **S6b**: `check-upstream` with fixture tests; focused tests; owner review.
4. **S6c**: README, notices, `DEVELOPMENT.md`, plan and this document; full local regression (sequential); owner review; S6c ends with **the one consolidated local S6 commit** (S6-D9), made after owner approval.
5. **D10 rehearsal**: a fresh `git clone` of the local repository at exactly that S6 commit into a temporary folder; the exact CI commands of §3 run there one step at a time in the foreground (two fetches, two builds, compare, validate, corpus tests); evidence recorded in the external tracker; nothing in the repository changes.
6. **S6d**: only the GitHub push and CI evidence collection, and only after explicit owner approval: push `main` (the existing local commits plus the S6 commit); the owner reports the CI run (screenshots or pasted output, U6); the evidence is recorded in the external tracker without another repository commit; RAG-M3 acceptance review.

## 8. Acceptance criteria

1. `corpus-rebuild` is green on GitHub for the pushed commit, with no restored corpus cache.
2. `manifest --check`, `verify` and `releases verify` pass on the fresh fetch.
3. After build A the working tree is unchanged: the committed manifests, build manifest and S4 reports are reproduced byte for byte on Linux.
4. `chunk --check`, `releases chunk --check` and `build --check` pass.
5. `validate` exits 0 with the four known warnings.
6. The corpus tests: collected count equals executed count equals 45, with 0 skipped, 0 failures and 0 errors, read from the JUnit XML.
7. Every path in `ALL_OUTPUTS` exists in both checkouts, and paths and SHA-256 values are identical between the two independent builds.
8. Permissions are `contents: read`; only `GITHUB_TOKEN` is used; no personal tokens or committed secrets; actions pinned to SHAs; artifacts are exactly the three evidence files.
9. `check-upstream` exists, is read-only (tested), uses exit codes 0/1/2 as documented, and is not run in PR CI.
10. The fresh-checkout build test and the CI safety test pass; the full local regression passes; README, notices, `DEVELOPMENT.md`, plan, this document and the tracker are updated.
11. The D10 rehearsal on the committed S6 clone reproduces steps 4-14 locally before any push.

## 9. Results (S6a, S6b, S6c)

### 9.1 Owner decisions added in S6b (2026-11-22)

| # | Decision |
|---|---|
| S6b-D1 | Exit 1 when definitive drift exists, even if another check is incomplete; report both. Exit 2 when there is no drift but a check is incomplete |
| S6b-D2 | Show expected and observed values for changed non-body metadata; for release bodies show only the SHA-256 and byte-count differences; never release text |
| S6b-D3 | Compare exclusively against the committed manifest |

### 9.2 Review-driven changes to the plan (S6a)

| Planned | Implemented | Reason |
|---|---|---|
| Step 11 kept the raw `validate` output as `validate.txt` and uploaded it | The raw output is never printed or uploaded. The uploaded `evidence/validate.txt` comes from a rule-aware sanitizer: 99 templates, typed fields, free-form payloads redacted, unmatched messages withheld whole. The step keeps validate's exit status | Findings can contain source-derived text (for example a section path, which is heading text). A first version that redacted quoted literals was superseded because it also redacted safe enum values |
| No coverage rule for the sanitizer | `test_every_validate_message_has_a_sanitizer_template` compares, in both directions, every message site in `validate.py` (found through the AST) with the templates. Meta-tests run it on injected temporary sources | A new or changed `validate.py` message must not silently fall through to "withheld", and a stale template must be noticed |
| Path field patterns not tested on real data | `test_every_real_manifest_path_survives_the_actual_sanitizer`: all 2,052 manifest paths (2,031 docs, 17 posts, 4 release notes) pass through the workflow's own sanitizer unchanged | Valid findings about real sources must never be withheld because a pattern is too narrow |
| Step 12 used `pytest -rs` | `--tb=no -rN` | Skip and failure reasons, and assertion values, could quote corpus text in the uploaded log. Every count, including skips, comes from the JUnit XML, so nothing is lost (tested: skips are not printed but still fail the check) |
| Step 12 checked the suite totals | The suite totals and every test case's outcome, the number of test cases, and pytest's own exit status | A test case marked skipped, failed or errored must fail the job even if the suite totals disagree |
| Steps 13 to 15 | Build B also runs `git diff --exit-code`; the evidence files are written under `evidence/`, with `a.sha256` copied to `evidence/outputs.sha256`; `a/` is rechecked as clean after the comparison | One evidence directory with explicitly named files, and the same reproduction check on both sides |

### 9.3 S6b audit fixes

Two independent review rounds found exception paths that escaped the CLI with a traceback and process exit 1, the drift code, instead of 2, and one message interpolation. Fixed and tested, with no change to S6b-D1 to D3 and no change to `releases.py`, `validate.py` or `cli.main`:
- Any unexpected exception becomes an incomplete finding naming only the exception type. This covers the injected fetcher, a single check (the other checks continue), the manifest load (an unreadable part file or directory) and the handler.
- `RecursionError` from deeply nested JSON gives `response is not JSON`.
- A body containing an escaped lone surrogate gives `body is not valid UTF-8`.
- `http.client.HTTPException` (for example `IncompleteRead`, `BadStatusLine`) and `ValueError` from the HTTP client become `FetchFailed`.
- A `FetchFailed` finding no longer interpolates the exception message. It names the type of its cause (or `FetchFailed`), because an injected fetcher may put any text in the message.

### 9.4 Not done in S6

- IR-3 and IR-2: deferred to the first stage of RAG-M4 (S6-D8, `RAG-M3_IR3_report.md`). No probe file was changed.
- The Q11 backup: deferred (§5).
- The sampled HTTP 200 check: skipped (S6-D7).
- A live `check-upstream` request: skipped by the owner in S6c. The D10 rehearsal provides the later fresh-clone evidence.
- No new dependency and no write permission.

### 9.5 S6d CI finding: shallow checkout

The first GitHub run of the S6 commit (`fb2ec67`) failed one corpus test: `test_real_corpus.py::test_versioned_docs_and_release_notes_unchanged_by_s3b`. The test runs `git show 7e4125f:data/manifest/corpus_manifest.json` to prove that S3b changed only `release_posts`. `actions/checkout` clones with depth 1 by default, so commit `7e4125f` was absent, git exited 128, and the JUnit gate failed the job on that one failure, as designed.

The D10 rehearsal had used full local clones, so it did not reproduce this. A depth-1 clone of `fb2ec67` reproduced it locally: 44 passed, 1 failed.

**Fix:** `fetch-depth: 0` on checkout A only, where the corpus tests run. Checkout B and the test itself are unchanged. `test_checkout_a_has_full_history_for_the_history_dependent_corpus_test` pins this. Future rehearsals mirror the CI clone depth: full history for A, depth 1 for B.
