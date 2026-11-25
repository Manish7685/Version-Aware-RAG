# Development guide

Commands and architecture notes for working on this repository. The approved design is `docs/design/RAG-M1_design.md`; changes to it need the owner's approval.

## Commands

If `uv` is not on PATH, use `python -m uv` as below; the commands do not need `make`.

| Task | Command |
|---|---|
| Install | `python -m uv sync --locked` |
| Lint and format check | `python -m uv run ruff check .` and `python -m uv run ruff format --check .` |
| Types | `python -m uv run mypy` (strict, `src/` and `tests/`) |
| All tests | `python -m uv run pytest` |
| One test | `python -m uv run pytest tests/unit/test_cli.py::test_info_json` |
| Smoke tests | `python -m uv run pytest -m smoke` |
| Hooks | `python -m uv run pre-commit run --all-files` |
| Probe check (A2) | `UV="python -m uv" bash scripts/probes/run_probes.sh` (Git Bash) |
| CLI | `python -m uv run vrag info --json` |
| Corpus fetch (network) | `python -m uv run vrag corpus fetch` and `python -m uv run vrag corpus releases fetch` (`GITHUB_TOKEN` optional) |
| Corpus input checks | `python -m uv run vrag corpus manifest --check`, `… corpus verify`, `… corpus releases verify` |
| Corpus build | `python -m uv run vrag corpus build` (`--check` rebuilds in memory, writes nothing) |
| Corpus validate | `python -m uv run vrag corpus validate` (read-only; exit 0 pass, 1 fail, 2 missing output or unloadable input) |
| Real-corpus tests | `python -m uv run pytest -m corpus` (50 tests; they skip without the fetched cache) |
| Upstream drift check | `python -m uv run vrag corpus check-upstream` (network; exit 0 no drift, 1 drift, 2 incomplete; never in CI) |
| Probe suite (IR-3) | `python -m uv run vrag probes run` (six venvs; exit 0 recorded, 1 invalid run, 2 incomplete; nothing written unless 0; needs a clean tree for repository evidence, `--allow-dirty` only with an `--evidence-dir` outside the repo) |
| Evidence | `python -m uv run vrag evidence validate`; `… evidence claim add`, `… observations ingest`, `… conflict suggest \| add \| review` (exit 0 done, 1 refused, 2 input missing; nothing written unless 0) |
| Change inventory | `python -m uv run vrag benchmark inventory validate` (exit 0 valid, 1 invalid, 2 incomplete; every corpus boundary needs a pilot candidate) |
| Pilot (protocol v1) | `python -m uv run vrag benchmark pilot check` (V1 checks; probe consistency stays pending until canonical observations exist), `… pilot select` (the 50-item pilot, review batches of 25 and the review-record template in `benchmark/review/`; recorded decisions are never overwritten), `… pilot report` (pilot report from complete owner review records only; exit 2 while incomplete); `… pilot export-review` / `import-review` (CSV review sheet `benchmark/review/pilot_review_sheet.csv`; import validates every row and writes nothing on any problem; instructions in `benchmark/review/README.md`), `… pilot validation-sample`, `… pilot split-plan` (dry run: change-grouped, type-stratified split and leakage checks), `… pilot freeze` (refuses until every item has a decision), `… pilot datasheet` |
| Probe preflight | `python -m uv run vrag probes preflight` (metadata only; runs nothing; exit 0 ready, 1 not ready: Git state, evidence destination, six pinned venvs and offline hash-verified installs) |
| Drafting (S3) | `python -m uv run vrag benchmark draft plan` (offline plan and privacy audit; sends nothing), `… draft run` (network; needs `[model] id` in `configs/drafting.toml` and `VRAG_GEMINI_API_KEY`; exit 2 means resume later), `… draft build` (offline rebuild of the drafts from the cache) |
| Retrieval indexes (M5) | `python -m uv run vrag index build --methods bm25,dense` (writes git-ignored `data/build/index/`; dense downloads `BAAI/bge-small-en-v1.5` once) |
| Retrieval eval (M5/M6) | `python -m uv run vrag eval retrieval --methods bm25,dense,finetuned,hybrid,rerank,late` (writes `results/retrieval/summary.json`, `report.md` and raw `runs.jsonl`; slices, Holm-corrected significance; `--split auto` uses the frozen test split once it exists, otherwise the pilot, labelled PROVISIONAL; `finetuned` needs `data/build/models/embedder-ft/` and `vrag index build --methods finetuned`) |
| Answer eval (M7) | `python -m uv run vrag eval answers --method hybrid` (Gemini through the quota ledger and fail-closed audit; `--offline` uses cached responses only; results in `results/answers/`) |
| CI gate (M9) | `python -m uv run vrag eval gate baseline\|check` (exit 0 PASS/WARN, 1 BLOCK, 2 ERROR; ERROR until `benchmark/splits/ci_gate.jsonl` exists and the baseline sha256 is owner-approved in `configs/gate.toml`; a config change is gated, an item or corpus change is ERROR; `--provisional` is a labelled dry run on the V0 pilot). CI job `eval-gate` stays dormant until split, baseline and approved hash exist |
| Citation support / faithfulness (M7, Tier M) | `python -m uv run vrag eval support score\|export-pairs\|calibrate\|dev-labels --method hybrid` (export: 200 stratified pairs, guide `docs/benchmark/nli_labelling_guide.md`; dev-labels are synthetic and refused by calibrate) (local NLI `cross-encoder/nli-deberta-v3-xsmall`; θ uncalibrated until the owner labels `results/answers/nli_pairs_to_label.csv`) |
| Error analysis (M8) | `python -m uv run vrag eval errors` (from `results/retrieval/runs.jsonl` and the answer summary; automatic categories, owner confirmation pending) |
| Fine-tune (M6) | `python -m uv run vrag index finetune-data` (dev split only, refuses leakage; writes pairs + `train_pairs.manifest.json`; exit 2 until the split is frozen); Colab: `scripts/colab/finetune_embedder.ipynb`; back home: `vrag index check-model` (provenance + load), then `vrag index build --methods finetuned` |
| Gate calibration (M9) | `python -m uv run vrag eval gate calibrate [--provisional]` (design §6.4 neutral/benign/injected scenarios + granularity; `results/gate/calibration.md`); demo PR patches in `docs/gate_demo/` |
| Owner decisions | `docs/decisions/P2_OWNER_DECISION_SHEETS.md`; pilot review proposals `benchmark/review/proposed/` (never import them as decisions: reviewer is empty on purpose) |
| Backend + UI (M11) | `python -m uv run vrag serve` (http://127.0.0.1:8000; serves `frontend/dist` when built) |
| Frontend dev | `cd frontend && npm install && npm run dev` (http://localhost:5173, proxies `/api` to port 8000); `npm run build` type-checks and builds |
| Docker | `docker compose up --build` (`compose.yaml`: mounts `data/`, `benchmark/drafts/`, the HF cache; 127.0.0.1:8000), or `docker build -t version-aware-rag .` and the run command in the `Dockerfile` header |
| Reproduce | `REPRODUCE.md` (every step from a fresh clone); acceptance status: `docs/reports/P2_FINAL_ACCEPTANCE_AUDIT.md` |

## Architecture notes

- **Package:** `src/version_aware_rag/`.
  - `settings.py`: pydantic-settings, `VRAG_` prefix.
  - `logging_config.py`: stdlib JSON or console logging to stderr.
  - `cli.py`: argparse; the console script is `vrag`.
  - `repro/probe_check.py`: compares probe JSONL runs.
  - `corpus/` (RAG-M3; plan `docs/design/RAG-M3_plan.md`, stage designs `docs/design/RAG-M3_S*.md`):
    - `config.py` and `manifest.py`: the config and the corpus manifest;
    - `acquire.py` and `releases.py`: the pinned docs fetch and the release-note snapshots;
    - `parse.py`, `liquid.py`, `html_extract.py`, `sections.py`, `chunk.py`, `chunk_output.py`, `release_chunks.py` and `inspect.py`: parsing, chunking and the S3 inspection reports;
    - `build.py`, `report_stats.py`, `validate.py` and `compat.py`: the deterministic build and its validation;
    - `upstream.py`: the read-only drift check;
    - `commands.py`: every `vrag corpus …` subcommand.
  - `evidence/` (RAG-M4 IR-2): `models.py` (the exact D14 schemas), `store.py` (deterministic JSONL; restricted span text only in the git-ignored `data/evidence/local/` sidecar, D1), `verify.py`, `workflow.py` (claims, ingestion, suspected conflicts, owner-only review; one validated atomic write), `commands.py` and `workflow_commands.py`.
  - `benchmark/` (RAG-M4 S2): `inventory.py` (pilot change inventory model and validation), `commands.py`. Data: `benchmark/protocol/` (protocol v0, `protocol_v0.toml`, sha256-recorded prompt templates: changing a prompt means updating its hash, a deliberate protocol change) and `benchmark/inventory/pilot_change_inventory.jsonl` (canonical JSONL; restricted sources cited by path and line range only).
  - `benchmark/` (RAG-M4 S3): `gemini.py` (stdlib REST client; key only in the request header; 429 with a zero quota is fatal, meaning no free tier), `drafting.py` (plan, permitted passages, fail-closed privacy audit, cache, response validation, V0 drafts) and `draft_commands.py`. Config `configs/drafting.toml`; cache `benchmark/drafts/cache.jsonl` (keyed by request sha256, never re-sent) and drafts `benchmark/drafts/pilot_drafts_v0.jsonl` are **git-ignored and local only** (never committed or uploaded as CI artifacts). Owner exclusions are per change in `configs/drafting.toml` (`exclude_types` with a required `exclude_reason`; currently `chg-2.0-09` from T3); every other pilot candidate must appear in the plan, and a type shortfall is reported, never padded; the exact prompt is re-audited immediately before each request. Prompt v1 (`benchmark/protocol/prompts/v1/draft_T2.md`, hashes in `benchmark/protocol/prompts_v1.toml`) replaces only the T2 template; protocol v0 and its prompts are unchanged and still hash-verified; T2 responses must carry `version_answers` (every version documented with version-specific verbatim spans), `behaviour = changed` and consistent `other_version_answers` (`t2_version_checks`). T2 sets come only from the owner's fixed `t2_changes` list in `[plan]` (no substitution; held sets in `t2_held` are flagged `review_hold` and never counted), with documentation-diff passage selection (`t2_passages`: pages present in every version of the set, behaviour-related changed lines, each version's own chunks; migration guide only for its own version); T3 coverage ignores T2 so the T2 list never reorders T3. `draft plan` shows cached vs to-send requests, token estimates and quota impact. Model `gemini-3.5-flash-lite` (free tier; no automatic fallback). `benchmark/quota.py` enforces the `[limits]` of `configs/drafting.toml` (10 RPM, 200,000 input TPM, 400 requests per rolling 24 h, kept under `[account_quota]` 15/250,000/500) for every attempt including retries (at most 2, exponential backoff with jitter), persisted in the git-ignored `benchmark/drafts/quota_ledger.json` (an unreadable ledger stops the run); one run at a time via `benchmark/drafts/run.lock`. Drafts are always V0, `human_verified = false` and `gold = false`; the audit refuses any payload containing a restricted line, a 10-word restricted sequence or a key-like string, and nothing is sent if a restricted source cannot be loaded for the audit.
  - `benchmark/` (RAG-M4 pilot): `pilot.py` (V1 checks: schema, verbatim spans re-read from the corpus, version compatibility via `corpus.compat`, near-duplicates, inventory consistency, probe consistency pending; protocol v1 selection keeping T2 sets whole; review batches and records; pilot report), `pilot_commands.py`. Protocol v1: `benchmark/protocol/protocol_v1.md` and `protocol_v1.toml` (composition T1 13, T2 6, T3 12, T4 7, T5 12; prompt hashes); protocol v0 unchanged.
  - `probes/` (RAG-M4 IR-3): `pins.py` (wheel pins, D2), `suite.py` (definitions, probe hashes, control rule), `runner.py`, `commands.py`; executor `scripts/probes/run_probe_suite.py` (stdlib + duckdb only).
- **Corpus data.** `data/manifest/` is committed, generated output and must be reproduced byte for byte by `vrag corpus build`. Never hand-edit it. `data/cache/` and `data/build/` are git-ignored. Release-note text, release-post body text and release-post titles must never reach a tracked file, a log or a CI artifact. `validate`'s leak scan (R7d, line-based) and short-quotation scan (R7e: double-quoted phrases of 4+ words matching release-post text, excluding phrases shared with the MIT docs or the pinned RAG-M1 runtime output; fails closed if a post cannot be loaded and verified) check committable files; `LEAK_EXCEPTIONS` is empty. Manifest schema 5 keeps only `title_sha256` for release posts, and their `attribution` omits the title.
- **CI `corpus-rebuild` job** (`.github/workflows/ci.yml`, design `docs/design/RAG-M3_S6_ci.md`; enforced by `tests/unit/test_ci_corpus_job.py`):
  - When a `vrag corpus validate` message is added or changed in `validate.py`, add or update its template in the workflow's validate-log sanitizer (`TEMPLATES`, between the `validate-log-sanitizer` markers). `test_every_validate_message_has_a_sanitizer_template` fails until the two match.
  - When real-corpus tests are added or removed, update `EXPECTED_CORPUS_TESTS` in the job. `test_expected_corpus_test_count_matches_the_collected_tests` checks it.
  - Keep `permissions: contents: read`, only the built-in `GITHUB_TOKEN` on the release-fetch steps, and exactly the three evidence artifacts.
  - `check-upstream` must never appear in the workflow.
- **Evidence rules.** Claims and observations stay separate records; a conflict always starts `suspected` and only a review with the reviewer label `owner` confirms or dismisses it. That label is a workflow convention, not authentication; never add an automated path that sets `confirmed` or `dismissed`. Restricted release-note and release-post text is never sent to Gemini or any other external model or service (owner decision, S2 review); later candidate generation uses only permitted evidence. In real-corpus tests, never `assert` an expression containing restricted source text (pytest prints the operands): assert precomputed booleans, and re-raise a `ValidationError` with field locations and error types only, `from None`.
- **`check-upstream` output** carries only check names, field names, ids, hashes, sizes, metadata values and exception type names: never exception messages, release text or the token.
- **CLI convention:** stdout is for command output and stderr for logs. Exit codes: 0 ok, 1 check failed, 2 usage or input error.
- **Hash-pinned files.** `scripts/probes/rag_m1_probes.py` and `scripts/probes/expected/*.jsonl` are byte-identical copies of the RAG-M1 artefacts, and their sha256 values are in the version manifest and `tests/unit/test_pinned_artifacts.py`. Never edit or reformat them. They are excluded from ruff and from the file-rewriting pre-commit hooks. A new probe suite (IR-3, deferred to the first stage of RAG-M4 together with IR-2) goes in a new file.
- **Docs excluded from ruff.** Markdown is excluded from ruff because the design docs embed verbatim, hash-recorded code.
- **Version pins that must stay in sync.** Tool versions in `.pre-commit-config.yaml` must equal `uv.lock`, the CI uv version must be 0.13.9, and actions are pinned to commit SHAs. `tests/unit/test_tooling_pins.py` enforces all three.
- **No placeholder commands.** Add CLI subcommands only when they're implemented.

## Reference (moved from the README)

Detailed instructions that used to live in the README, kept verbatim. Section references such as "§8" point to the earlier README numbering.

### Repository layout

| Path | Purpose | Status |
|---|---|---|
| `src/version_aware_rag/` | Library and application code: CLI, settings, logging | RAG-M2 |
| `src/version_aware_rag/corpus/` | Corpus fetch, manifest, parser, chunker, build, validation and upstream drift check (`vrag corpus …`) | RAG-M3 |
| `src/version_aware_rag/evidence/`, `probes/`, `benchmark/` | Evidence records, the probe suite, benchmark inventory, drafting, pilot review and splits | RAG-M4 |
| `src/version_aware_rag/retrieval/` | Retrievers, harness, metrics, fine-tune data, CI gate (`vrag index`, `vrag eval retrieval`, `vrag eval gate`) | RAG-M5, M6, M9 |
| `src/version_aware_rag/answer/` | Answer pipeline, Tier H scoring, NLI citation support and faithfulness (`vrag eval answers`, `vrag eval support`) | RAG-M7 |
| `src/version_aware_rag/api/` | FastAPI app and `vrag serve` | RAG-M11 |
| `frontend/` | React + TypeScript + Vite + Tailwind UI | RAG-M11 |
| `Dockerfile`, `compose.yaml`, `.dockerignore` | Container image and one-command local run | RAG-M12 |
| `src/version_aware_rag/repro/` | Reproducibility checks (probe comparison) | RAG-M2 |
| `scripts/probes/` | RAG-M1 behavioural probes, byte-identical to the version manifest; hash-pinned requirements; expected output | RAG-M2 |
| `tests/unit/`, `tests/smoke/`, `tests/integration/` | Unit tests; smoke tests run by CI on a fresh clone; real-corpus tests (marker `corpus`, need the fetched cache) | RAG-M2, RAG-M3 |
| `docs/design/` | RAG-M1 design, research evidence and version manifest; RAG-M3 plan and stage designs | RAG-M1, RAG-M3 |
| `docs/reports/` | Generated RAG-M3 reports (source inspection, parser samples, chunking, release-note chunks) | RAG-M3 |
| `data/manifest/` | Committed, generated manifests: corpus manifest, chunk manifests, build manifest. No raw corpus text | RAG-M3 |
| `data/cache/`, `data/build/` | Fetched sources and generated chunk files; git-ignored, never committed | RAG-M3 |
| `benchmark/` | Protocol, change inventory, pilot selection and review sheets; the frozen dev/test splits (`splits/`). Drafts and the quota ledger in `benchmark/drafts/` are git-ignored | RAG-M4, M9 |
| `configs/` | Corpus, parsing, chunking, probes, drafting and gate configurations (TOML) | RAG-M3+ |
| `results/` | Raw outputs and generated tables; git-ignored (regenerate with the commands in §10) | RAG-M5+ |
| `scripts/colab/`, `scripts/validation/` | The Colab embedder fine-tune; the ColBERT-vs-PyLate equivalence check | RAG-M6 |
| `.github/workflows/` | CI: checks, hooks, Linux probes, secret scan, `corpus-rebuild`, `eval-gate` | RAG-M2, M3, M9 |

### CLI

```bash
uv run vrag info [--json]                       # package, interpreter, non-secret settings
uv run vrag probes compare --expected E.jsonl --actual A.jsonl   # exit 0 match, 1 mismatch, 2 bad input
uv run vrag --log-level DEBUG --log-format console info
uv run vrag corpus --help                       # corpus commands (RAG-M3); see §8
uv run vrag probes run                          # IR-3 probe suite on the six pinned packages (RAG-M4)
uv run vrag evidence validate                   # evidence records: claims, observations, conflicts
uv run vrag benchmark inventory validate        # pilot change inventory (RAG-M4 S2)
```

### Configuration

Settings come from environment variables with the prefix `VRAG_`, or from a local `.env` file (git-ignored). See [`.env.example`](.env.example).

- **Logs:** JSON lines on stderr by default; set `VRAG_LOG_FORMAT=console` for human-readable output.
- **Secrets:** never committed. The only secret is `VRAG_GEMINI_API_KEY` (server side; never in the frontend or the image).

### Dataset instructions

The corpus is the DuckDB documentation for versions 1.2, 1.3, 1.4 LTS, 1.5 and 2.0 at the pinned duckdb-web commit, plus the 17 DuckDB release posts and the GitHub release notes for v1.2.1, v1.2.2, v1.3.1 and v1.3.2 as change sources. The plan is [`docs/design/RAG-M3_plan.md`](docs/design/RAG-M3_plan.md); the sources are pinned in [`configs/corpus.toml`](configs/corpus.toml) and checked against the RAG-M1 manifest ([`docs/design/RAG-M1_version_manifest.md`](docs/design/RAG-M1_version_manifest.md)). Every source keeps its own licence and attribution; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

**What is committed and what is not.**

| Location | Content | In git |
|---|---|---|
| [`data/manifest/`](data/manifest) | Generated manifests and metadata: the corpus manifest (paths, hashes, versions, licence fields; release-note ids, hashes and sizes; release-post records with authors, an attribution string and a title hash, not the title), the chunk manifests and the build manifest (counts and output hashes) | committed |
| [`docs/reports/`](docs/reports) | Generated reports, not only metadata. `RAG-M3_S3_samples.md` and `RAG-M3_S4_chunking.md` quote short excerpts of the MIT-licensed documentation pages, and `RAG-M3_S3_source_inspection.md` quotes documentation headings and short template fragments. Release posts appear only by file name, with their text withheld. `RAG-M3_S4d_release_notes.md` contains no release-note text | committed |
| `data/cache/` | The fetched raw sources: the pinned docs snapshot (including the release posts) and the four release-note bodies | git-ignored |
| `data/build/` | The generated chunk files | git-ignored |

Release-note text and release-post body text are never committed and never uploaded by CI; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

**Rebuild the corpus** (needs network access; run from the repository root):

```bash
uv run vrag corpus fetch               # pinned docs snapshot into data/cache/ (anonymous HTTPS)
uv run vrag corpus releases fetch      # the four approved release notes, by id, hash-checked
uv run vrag corpus manifest --check    # the committed manifest matches the fetched snapshot
uv run vrag corpus verify              # cache, committed manifest and RAG-M1 evidence agree
uv run vrag corpus releases verify     # cached release notes match the committed hashes
uv run vrag corpus build               # verify every input, then build all outputs
uv run vrag corpus build --check       # rebuild in memory and compare; writes nothing
uv run vrag corpus validate            # read-only validation: exit 0 pass, 1 fail, 2 missing output or unloadable input
uv run pytest -m corpus                # the real-corpus tests (they skip without the cache)
```

`releases fetch` uses `GITHUB_TOKEN` as a bearer token when it is set (never logged); without it, the anonymous GitHub API rate limit applies. A rebuild reproduces the committed manifests byte for byte. The build totals (for example 31,680 logical-corpus chunks) are in the `counts` of [`data/manifest/build_manifest.json`](data/manifest/build_manifest.json).

**Upstream drift check** (local or manual use only, never in CI):

```bash
uv run vrag corpus check-upstream      # exit 0 no drift, 1 drift, 2 check incomplete
```

It asks the GitHub REST API whether the pinned docs commit is still reachable and whether the four approved release notes still match the committed manifest (body sha256 and byte count, tag, publish time, draft and prerelease flags). It writes nothing and never prints release text: a changed body is reported only by hash and size. Drift takes precedence: exit 1 even if another check was incomplete. Exit 2 means a check could not be completed (network or HTTP error, rate limit, malformed response, unreadable configuration or manifest); failures are reported by exception type only.

#### CI corpus rebuild

The `corpus-rebuild` job in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main`, every pull request and on manual dispatch. It checks out the repository twice, and each checkout fetches the sources afresh and builds the corpus independently (no cached `data/`). It then requires that:
- the input checks, `chunk --check`, `releases chunk --check` and `build --check` pass;
- the committed manifests and reports are reproduced byte for byte;
- `validate` exits 0;
- the 50 real-corpus tests are collected and executed with 0 skipped, failures and errors, read from the JUnit XML;
- every path in `build.ALL_OUTPUTS` exists in both checkouts with identical SHA-256 values.

The job uses `permissions: contents: read` and only the built-in `GITHUB_TOKEN` on the release-fetch steps. Its only artifacts are the output hash list, a sanitized validation log and the corpus-test log; no corpus text, release notes, release posts or caches are uploaded.

#### Evidence records

Documentation claims and runtime observations are kept as separate records in `data/evidence/` (design `docs/design/RAG-M1_design.md` §2.1); a conflict only links them and never edits either one.

- `vrag probes run` runs the IR-3 probe suite (`configs/probes.toml`) on the six hash-pinned DuckDB packages. Controls P09, C1 and C2 must give the same result in every package (C2 after replacing only the package's own `v<version>`), otherwise the run is invalid and nothing is written (exit 0 recorded, 1 invalid, 2 incomplete). Repository evidence needs a clean committed tree.
- `vrag evidence claim add` cuts a claim's text from the hash-verified cached source at the given offsets; release-note and release-post text stays in the git-ignored `data/evidence/local/`.
- `vrag evidence observations ingest` adds recorded observations; `vrag evidence conflict suggest` or `conflict add` creates a **suspected** conflict; only `conflict review --reviewer owner` can confirm or dismiss it. The reviewer is an identity label checked by the workflow, not authentication: the tool cannot verify who runs it, no automated step can confirm or dismiss, and the Git history of the evidence files is the audit trail.
- `vrag evidence validate` checks everything read-only (exit 0 valid, 1 invalid, 2 incomplete).

The canonical probe run (2026-11-24, commit `ac9f206`) recorded 168 runtime observations in `data/evidence/`; no documentation claims or conflicts are recorded. One observed value is stored as a hash because it matched the restricted-text privacy scan ([`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) §4).

**Benchmark protocol and change inventory (RAG-M4 S2, draft).** [`benchmark/protocol/protocol_v0.md`](benchmark/protocol/protocol_v0.md) defines the question types, the `gold_short_answer` typing guide, the evidence-span rules and the rejection reason codes R1–R9; the drafting prompt templates in [`benchmark/protocol/prompts/`](benchmark/protocol/prompts) are versioned by sha256. [`benchmark/inventory/pilot_change_inventory.jsonl`](benchmark/inventory/pilot_change_inventory.jsonl) lists the pilot's behaviour changes with their documentation sources and probes; runtime results not yet confirmed by a canonical probe run are marked as such, and open items are recorded as `unresolved`.

**Pilot (protocol v1).** 50 Gemini-drafted questions (T1 13, T2 6, T3 12, T4 7, T5 12) plus two T2 drafts released from hold (owner decision D2) passed the V1 automatic checks and were reviewed by the owner with AI assistance in [`benchmark/review/`](benchmark/review) (`vrag benchmark pilot export-review` / `import-review`): 40 accepted (V2), 12 rejected. `pilot freeze` wrote the dev/test split (dev 15, test 25) to [`benchmark/splits/`](benchmark/splits). The datasheet is [`docs/benchmark/datasheet.md`](docs/benchmark/datasheet.md).

### Training instructions

The embedder fine-tune (RAG-M6) trains only on dev-split page groups and runs on Colab (free T4):

1. `vrag index finetune-data` writes the training pairs (positives from dev-split evidence,
   BM25-mined hard negatives). It refuses until the split is frozen, which happens after the owner
   review, and refuses any leakage into test or ci-gate pages.
2. `scripts/colab/finetune_embedder.py` trains the model on a free T4, resuming from Drive
   checkpoints.
3. Copy the trained model to `data/build/models/embedder-ft/`, then run
   `vrag index build --methods finetuned` and `vrag eval retrieval --methods bm25,dense,finetuned,hybrid,rerank,late`.

**Not run (owner decision):** the fine-tuned embedder was not trained; the steps above are implemented
and tested but have no model. See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md).

### API documentation

`vrag serve` starts FastAPI. The interactive OpenAPI docs are at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status, indexed chunk count, built methods |
| `GET /versions` | Documentation versions and their behaviour packages |
| `GET /search?q=&version=&method=&k=&filter=` | Ranked permitted chunks; with `version`, only compatible ones (`filter=false`: all versions, each hit marked `compatible` with `version`); for `rerank` and `late` each hit carries `base_rank` (its first-stage hybrid rank) |
| `POST /answer` `{question, version, method}` | Structured answer with citations, or an abstention (503 without a server-side Gemini key) |
| `GET /compare?q=&versions=1.4,2.0&method=` | The same query across versions |

`method` is one of `bm25`, `dense`, `finetuned`, `hybrid`, `rerank` or `late`; a method that is
not built on the server returns 503. Queries are whitespace-normalised and must have 2 (search,
compare) or 3 (answer) non-blank characters and at most 500; an unknown version, a blank or
over-long query, or an empty version list returns 422. With no compatible evidence, `/answer`
abstains (`no_evidence`) without calling the model. Only permitted chunks (MIT documentation and
migration guides) are ever returned or sent to the model. There is no authentication or rate
limiting: the server is meant for local use and binds to 127.0.0.1 by default.

### Deployment instructions

**Docker Compose (one command).** After building the corpus and indexes on the host (§7
Quickstart, first four commands):

```bash
docker compose up --build        # http://127.0.0.1:8000; Ctrl+C, then docker compose down
```

[`compose.yaml`](compose.yaml) mounts `data/`, `benchmark/drafts/` and the Hugging Face cache, passes
`VRAG_GEMINI_API_KEY` through from the host environment when it is set, binds to 127.0.0.1 and
health-checks `/health`.

**Plain Docker.** The image was built and run on 2026-11-23 (Docker 29.9.2, Linux engine). It is
4.14 GB, mostly the CPU-only PyTorch stack; Linux installs torch from the PyTorch CPU index, not the
CUDA build. Built and restricted data never enter the image. Mount them at run time:

```bash
docker build -t version-aware-rag .
docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" \
    -v "$PWD/benchmark/drafts:/app/benchmark/drafts" \
    -v "$HOME/.cache/huggingface:/home/app/.cache/huggingface" \
    -e VRAG_GEMINI_API_KEY version-aware-rag
```

What each mount is for:

- **`data/`:** chunks, indexes and the local restricted sources that the `/answer` privacy audit
  needs.
- **`benchmark/drafts/`:** the shared Gemini quota ledger.
- **The Hugging Face cache:** avoids re-downloading the models.

`/search`, `/compare` and the UI work without the key.

**Public deployment: not done.** A free-tier host needs the 31,438-chunk indexes and the CPU models
(about 1 GB of RAM in use plus a 4 GB image) and, for `/answer`, the restricted sources for the
privacy audit, which must stay on the local machine. By owner decision, the local Docker run
above is the official deployment. Exposing the API publicly would also
need authentication and rate limiting, which are not implemented.

### Reproducibility check (RAG-M1 assumption A2)

`scripts/probes/run_probes.sh` re-runs the ten RAG-M1 behavioural probes against DuckDB 1.1.3, 1.2.2, 1.3.2, 1.4.5, 1.5.6 and 2.0.1:
- Each version gets its own hash-verified venv (`--require-hashes`).
- The script refuses to run if the probe script's sha256 differs from the manifest.
- It compares the `results` objects with the expected RAG-M1 Windows run.

CI runs this on Linux in the `linux-probe-check` job.

### Corpus limitations

The known assumptions A1–A3 are listed in [`docs/design/RAG-M1_research_evidence.md`](docs/design/RAG-M1_research_evidence.md) §0.2. Corpus limitations (RAG-M3):
- The licence of the GitHub release-note text is not established, and the licence applicability of the release posts is uncertain. Neither text is committed or redistributed; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
- A fresh build depends on GitHub still serving the pinned docs commit and the same release-note text. A changed release body stops the build. `check-upstream` reports both, but a commit visible through the API is very likely, not guaranteed, to be fetchable by git; the fresh fetch is the authoritative test.
- A backup of the pinned docs snapshot as a GitHub Release asset is deferred ([`docs/design/RAG-M3_S6_ci.md`](docs/design/RAG-M3_S6_ci.md) §5).
- `validate` passes with four known warnings: oversize chunks, near-empty pages, section-path fallbacks and cross-version duplicates. The duplicates are intended; they are the version-confusion signal the benchmark measures.
