# Third-party notices

This project's own code is MIT-licensed (see `LICENSE`). **The material listed below is not relicensed by this project.** It keeps its original licence and copyright, and is used under those terms with attribution.

## DuckDB documentation (duckdb-web)

- **Source:** https://github.com/duckdb/duckdb-web, commit `d01484462710d49206816c9eeaeb822361ebe81c`.
- **Licence:** MIT. Copyright 2018-2025 Stichting DuckDB Foundation. The upstream licence file is pinned by hash in `docs/design/RAG-M1_version_manifest.md` §1 (sha256 `7e17fd31249fa875cb3b1c5e05c6c3e99b75509f6a2804ca176c217834de1dcb`).
- **Present in this repository:**
  - short quoted excerpts in `docs/design/` (research evidence and design), used for citation;
  - benchmark evidence: verbatim documentation spans, gold answers and aliases in `benchmark/splits/` (`dev.jsonl`, `test.jsonl`), `benchmark/review/` (review batches, review sheet, review records, `proposed/recommendations.json`) and `benchmark/inventory/pilot_change_inventory.jsonl`, and the examples in `docs/benchmark/` (`datasheet.md`, `pilot_report.md`, `REVIEW_DECISION_GUIDE.md`, `nli_labelling_guide.md`);
  - documentation text and headings quoted in the error review `docs/reports/M8_owner_error_review.md` and `.csv`, and in the results reports `docs/reports/RAG_technical_report.md` and `docs/reports/RAG-M5-M8_provisional_results.md`;
  - documentation passages shown in the application screenshots `docs/screenshots/*.png`;
  - documentation claims in `data/evidence/doc_claims.jsonl` (permitted documentation only; restricted spans stay in a git-ignored local sidecar);
  - excerpts of documentation pages in the generated reports: `docs/reports/RAG-M3_S3_samples.md` and `docs/reports/RAG-M3_S4_chunking.md` (parser and chunker review samples), and headings and short template fragments in `docs/reports/RAG-M3_S3_source_inspection.md`;
  - in `data/manifest/`, metadata only: page paths, hashes, versions and licence fields (corpus manifest), and counts and output hashes (chunk and build manifests).
- **Not committed:** the documentation corpus itself. It is fetched from the pinned commit into the git-ignored `data/cache/` and processed locally; the generated chunk files stay in the git-ignored `data/build/`. CI uploads no corpus text. The evidence spans and documentation excerpts listed above are redistributed under the documentation's MIT licence with this attribution; the benchmark questions, gold answers and annotations written for this project are project content under this project's MIT licence. The questions were drafted with the Gemini API from permitted documentation only and then reviewed and edited by the owner; under the Gemini API Additional Terms of Service (effective 2026-03-23, the version in force when the items were drafted on 2026-11-23) Google does not claim ownership of generated content.

## DuckDB release posts (duckdb-web `_posts`)

- **Source:** the 17 release posts listed in `configs/corpus.toml` (`[release_posts]`), from the same pinned duckdb-web commit.
- **Licence status: unverified; licence applicability: uncertain.** The duckdb-web repository contains an MIT licence (Copyright 2018-2025 Stichting DuckDB Foundation) whose scope is "this software and associated documentation files". No repository file states that this licence, or any other, covers website or blog text, so its applicability to the posts is not established. The evidence inspected and what would resolve the question are recorded in `configs/corpus.toml`.
- **In this repository:** one record per post in `data/manifest/corpus_manifest.json`: path, hashes, size, release, publish date, canonical URL, licence fields, authors, an attribution string and a sha256 of the title (manifest schema 5; the title itself is not stored). In the generated reports, posts appear only by file name; their body text, headings and section paths are withheld (marked "[withheld: licence applicability uncertain, redistributable=false]"). No post body text is committed or uploaded by CI. The text is used only locally, from the git-ignored cache.

## DuckDB release notes on GitHub

- **Source:** the release notes for v1.2.1, v1.2.2, v1.3.1 and v1.3.2 from https://github.com/duckdb/duckdb/releases, fetched by release id.
- **Licence status: unverified.** duckdb/duckdb is MIT-licensed, but GitHub release text is not a repository file, and its licence is not established. It is not redistributed.
- **Attribution:** "DuckDB release notes, github.com/duckdb/duckdb, release {tag}".
- **In this repository:** metadata only: release id, tag, publish time, source URL, body sha256 and byte count in `data/manifest/corpus_manifest.json`, and counts and hashes in `data/manifest/release_note_chunk_manifest.json`. The release text, its headings and contributor names are never committed (`docs/reports/RAG-M3_S4d_release_notes.md` withholds them) and never uploaded by CI. The text lives only in the git-ignored `data/cache/github_releases/`.

## DuckDB software output

`scripts/probes/expected/`, `data/evidence/runtime_observations.jsonl` and some unit-test fixtures (for example `tests/unit/probes/test_executor.py`) contain values and error messages produced by the DuckDB Python packages (`duckdb` on PyPI, MIT, Stichting DuckDB Foundation). They are recorded as test evidence. DuckDB itself isn't redistributed; the probe environments install it from PyPI with pinned hashes.

## Models (downloaded at run time, not redistributed)

No model weights are committed. Each model is downloaded from Hugging Face on first use and keeps its own licence:

| Model | Use | Licence (model card) |
|---|---|---|
| `BAAI/bge-small-en-v1.5` | dense retrieval | MIT |
| `cross-encoder/ms-marco-MiniLM-L6-v2` (requested as `…-L-6-v2`) | cross-encoder reranking | Apache-2.0 (trained on MS MARCO, whose own terms are research-oriented) |
| `cross-encoder/nli-deberta-v3-xsmall` | citation-support NLI | Apache-2.0 |
| `lightonai/answerai-colbert-small-v1` | late-interaction reranking | **Not declared.** The model card states no licence and the repository has no licence file; its base model `answerdotai/answerai-colbert-small-v1` is Apache-2.0. Check the terms before any use beyond this research demo. |

## Software dependencies

Python packages (`uv.lock`) and npm packages (`frontend/package-lock.json`) are installed from their registries and are not vendored. Their licences are permissive (MIT, BSD, Apache-2.0, ISC, PSF, MPL-2.0 for a few unmodified packages, and BlueOak-1.0.0 and CC-BY-4.0 for two build-time npm packages). The Docker build uses the official `node:24-slim` and `python:3.12-slim` images; no image is published from this repository.
