# Reproduce

Every step from a fresh clone to the numbers in the reports. Commands use `uv`; if `uv` is not on
PATH (common on Windows), write `python -m uv` instead. Expected exit codes are given; anything else is a
failure. Network steps are marked.

## 0. Requirements

git 2.44 or later, [uv](https://docs.astral.sh/uv/) 0.13.9 (it installs CPython 3.12.16 from
`.python-version`), Node 24 and npm 11 for the UI, Docker for the container. No GPU. About 6 GB of
disk for the environment, corpus, indexes and models.

## 1. Environment (network)

```bash
git clone <repo-url> version-aware-rag && cd version-aware-rag
uv sync --locked                 # exact versions from uv.lock; Linux gets CPU-only torch
uv run pytest -m smoke           # exit 0
```

## 2. Corpus (network; RAG-M3)

```bash
uv run vrag corpus fetch                      # pinned duckdb-web commit
uv run vrag corpus releases fetch             # 4 release notes by id; GITHUB_TOKEN optional
uv run vrag corpus manifest --check           # exit 0
uv run vrag corpus verify && uv run vrag corpus releases verify    # exit 0
uv run vrag corpus build                      # writes data/build/ and data/manifest/
git status --porcelain data/manifest docs/reports                  # empty: byte-identical rebuild
uv run vrag corpus validate                   # exit 0 (4 informational warnings)
uv run pytest -m corpus                       # 50 real-corpus tests, 0 skipped
```

## 3. Indexes (network once for models; RAG-M5)

```bash
uv run vrag index build --methods bm25,dense  # 31,438 permitted chunks; downloads bge-small
```

The cross-encoder, the ColBERT checkpoint and the NLI model download on first use.

## 4. Benchmark items

The provisional pilot drafts (`benchmark/drafts/`) are local only and git-ignored by design, so a
fresh clone cannot re-evaluate the provisional numbers. The frozen splits in `benchmark/splits/`
(written by `vrag benchmark pilot freeze` after the owner review) are committed; once they exist,
every evaluation below runs from a fresh clone on the test split (`--split auto`).

## 5. Evaluation

```bash
uv run vrag eval retrieval --methods bm25,dense,hybrid,rerank,late   # results/retrieval/
uv run vrag eval answers --method hybrid --offline   # needs the answer cache; otherwise
                                                     # VRAG_GEMINI_API_KEY and quota
uv run vrag eval support score --method hybrid       # local NLI, no API
```

Seeds are fixed (bootstrap 20261123, gate PCG64 20261120). Retrieval metrics are deterministic;
latency is machine-dependent.

## 6. CI gate (RAG-M9)

The gate is not enforced (owner decision): there is no separate ci-gate split and no approved
baseline (`configs/gate.toml` has an empty `baseline_sha256`), and the `eval-gate` CI job is dormant.

```bash
uv run vrag eval gate check        # exit 2 (ERROR), by design: no ci-gate split, no approved baseline
```

### Historical record: the pilot demonstration (do not re-run)

The gate demonstration in technical report §8, `docs/gate_demo/` and `results/gate/calibration.md`
was run on 2026-11-23 and 2026-11-24 with `--provisional` against the 50-item pilot **before** the
owner review and the freeze. The commands below are kept as a record of what was run. **Do not re-run them on this
tree:** once `benchmark/splits/test_manifest.json` exists, `--provisional` evaluates the **frozen
test split's items**, so the output would no longer be the pilot demonstration, and using it to
tune thresholds, methods or prompts would break the test split's hold-out.

```text
# HISTORICAL, 2026-11-23/24, pre-review pilot (38 retrieval-eligible V0 drafts). Not safe to re-run now.
vrag eval gate baseline --provisional
vrag eval gate check --provisional --method hybrid          # PASS, exit 0
vrag eval gate check --provisional --method dense           # BLOCK, exit 1
vrag eval gate calibrate --provisional                      # design §6.4 scenarios
git apply docs/gate_demo/<patch> && vrag eval gate check --provisional --method hybrid   # then git apply -R
```

There is no supported way to re-run the pilot demonstration from the frozen tree; re-running the
gate properly needs a separate ci-gate split and an approved baseline, which this project does not
have (owner decision).

## 7. Application

```bash
(cd frontend && npm ci && npm run build)
uv run vrag serve                               # http://127.0.0.1:8000 and /docs
docker compose up --build                       # the same in a container
```

## 8. Quality checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
uv run pre-commit run --all-files
```

## What was actually run, and where

The record of which of these steps were executed, on which tree, with which result, is
[`docs/reports/P2_FINAL_ACCEPTANCE_AUDIT.md`](docs/reports/P2_FINAL_ACCEPTANCE_AUDIT.md) §4. From a
fresh clone of `main` (2026-11-24) steps §1, §2, §3, the §5 retrieval evaluation and §7 were run
and passed; the retrieval results were identical to the development repository's (latency
excluded). The §5 answer evaluation and §8 were run in the development repository.
