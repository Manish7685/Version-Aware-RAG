# RAG-M3 S5: Deterministic Build and Validation

Status: **decisions S5-D1 to S5-D10 approved by the owner (2026-11-21). S5a complete and approved. S5b (decisions S5b-D1 to S5b-D8, and the leak-scan policy of §3.6) complete and approved after the full regression. S5c (decisions S5c-D1 to S5c-D6, and the two owner-requested improvements recorded in §4.2 and §4.3) approved after independent review. S5 complete.** One consolidated commit after S5c (S5-D10): `d471e1ec5a771bb93b07c97860eee7fdf6219503`, local, not pushed.

Scope comes from `RAG-M3_plan.md` §3 and §8 (S5 row), §10 (acceptance table) and `RAG-M3_S4_chunking.md` §1 and §11.1. S5 builds and validates the corpus; it adds no retrieval, embeddings, benchmark or generation work. S1-S4 code, configs, manifests and reports are not changed (S5a adds one CLI command to `commands.py`).

## 1. Owner decisions (2026-11-21)

| # | Decision |
|---|---|
| S5-D1 | **Release-note chunks stay in their separate file.** The logical corpus is the five version files (`data/build/chunks/<version>.jsonl`) plus `data/build/chunks/github_releases.jsonl`. All S4 outputs stay byte-identical |
| S5-D2 | **`unresolved.jsonl` holds source-level records** (kind, path, sha256, reason, detecting step). An unresolved source never becomes a chunk |
| S5-D3 | **`data/build/build_report.json` stays git-ignored; `data/manifest/build_manifest.json` is committed** with hashes, counts and metadata only. A committed Markdown S5 report is optional and not authorised |
| S5-D4 | **Validation severity:** rule violations are errors (exit 1); informational findings are warnings (exit 0); missing outputs or bad usage exit 2 (implemented in S5b) |
| S5-D5 | **Temporary-folder writing:** verify, build and validate in memory, write to a staging folder, replace the output set only after success; a failure leaves every existing output byte-identical, and a failed replacement must not leave a partially updated corpus |
| S5-D6 | **Both the S4 chunk commands and `vrag corpus build` are kept;** `build` is authoritative for the final corpus and reuses the S4 functions, writing the same S4c and S4d files byte-identically |
| S5-D7 | **The six near-empty pages keep their chunks** and are reported as warnings; S4 outputs and counts are unchanged |
| S5-D8 | **Version-compatibility rule** as a small, independently tested function: a chunk is compatible with target *v* if its version is *v*, or it is a change source whose `introduced_in` is at most *v*, compared as semantic versions, not strings (S5b) |
| S5-D9 | **Not in S5:** `check-upstream` goes to S6; sampled HTTP checks are optional in S6; IR-3 (probe suite) is neither marked complete nor dropped: its exact requirement and a proposed owner are reported separately after S5 |
| S5-D10 | **One consolidated local commit after S5c**, after review; no intermediate commits |

## 2. S5a: `vrag corpus build [--check]` (`src/version_aware_rag/corpus/build.py`)

### 2.1 Order of work

0. **Normal builds only: recover first.** If a journal from an interrupted transaction exists, the previous output set is restored from it before anything else (§2.4). Only then are the inputs verified, so a build that then fails verification leaves the previous consistent set restored, and the CLI says so. With no journal, stale staging and backup folders are removed. `--check` never recovers and never cleans up.
1. **Verify every input before anything is built or written:**
   - the docs cache exists and is at the pinned commit (`open_cache`); its absence is an environment error (exit 2);
   - the corpus manifest and its per-version parts load without integrity problems (`load_manifest`);
   - S1 `verify_cache`: pinned commit; release cache hashes and sizes; per-version `content_hash` against the RAG-M1 evidence (**IR-6**); the committed manifest equals a rebuild from the cache, which includes the corpus-config sha256; every checked-out docs file and release post matches its sha256;
   - S4d `verify_release_inputs`: release ids, hashes, sizes and tag commits between config, manifest and the S2 fetch records;
   - the integrity of S4c and S4d outputs already on disk, against their committed manifests (absent outputs mean a fresh build; present-but-inconsistent outputs stop the build).

   Every failure is collected and reported together; nothing is built. Mismatched existing outputs are diagnosed (§2.8).
2. **Build in memory** with the approved functions only: S3 `inspect_corpus` (hash-checked parsing), S4 `chunk_documents`, S4c renderers, S4d `chunk_release_notes` and renderers.
3. **Detect unresolved sources** (§2.3).
4. **Validate in memory:** S4c `chunk_file_problems` and S4d `release_file_problems` on the rendered outputs; chunk IDs unique across the whole logical corpus; no unresolved source among the chunked sources. Any problem stops the build before writing.
5. **Write** (§2.4), or with `--check` compare every output with the file on disk and write nothing.

### 2.2 Outputs

| Output | Tracked | Notes |
|---|---|---|
| S4c: 5 version files, `empty_sections.jsonl`, `chunk_manifest.json`, `RAG-M3_S4_chunking.md` | as in S4 | byte-identical to `vrag corpus chunk` |
| S4d: `github_releases.jsonl`, `release_note_chunk_manifest.json`, `RAG-M3_S4d_release_notes.md` | as in S4 | byte-identical to `vrag corpus releases chunk` |
| `data/build/unresolved.jsonl` | no | §2.3; empty for the real corpus |
| `data/build/build_report.json` | no | inputs (hashes, versions), the verification checks passed, the logical-corpus file list, every output's path, records, bytes and sha256, and counts (corpus, release notes, logical-corpus total, unresolved by reason); since S5c also the extended sections of §4 (report schema 2) |
| `data/manifest/build_manifest.json` | yes | the same inputs, the logical-corpus list, every other output's path, records, bytes and sha256 (including the build report), and summary counts. No content, no names |

### 2.3 Unresolved sources (S5-D2)

`{"kind", "path", "sha256", "reason", "detecting_step"}`, one per line, ordered by detecting step and path:

| Detecting step | Finds | Reason |
|---|---|---|
| `build.scan_cache_posts` | a `_posts/*.md` file in the docs cache that is not one of the 17 approved posts | `unmapped_release_post` |
| `build.scan_release_cache` | a release body in the release cache that is not one of the 4 approved tags (path recorded as `releases/tag/<tag>`) | `unapproved_release_body` |

The pipeline only ever chunks manifest- and config-approved sources, and in-memory validation fails if an unresolved path appears among the chunked sources. For the real corpus the file is empty: config validation requires all 17 approved posts, the sparse fetch only downloads approved posts, and the release cache holds only the 4 approved bodies. The mechanism is exercised by fixtures.

### 2.4 Writing and its guarantee (S5-D5)

1. `write_outputs` refuses to start while a journal exists (an unrecovered transaction); it never clears backup data in that case.
2. Outputs whose bytes already match the file on disk are left alone.
3. Every changed output is written, with fsync, to `data/build/.build-staging/<path>`.
4. The journal `data/build/.build-journal.json` (files to replace, and those that are new) is written to `.build-journal.tmp` and renamed into place: the transaction begins. A failure in steps 3-4 removes the staging folder and replaces nothing.
5. For each changed output: the existing file is renamed into `data/build/.build-backup/<path>`, then the staged file onto the target.
6. **Any exception in step 5, including `KeyboardInterrupt`,** rolls back every processed file (restore from backup, or remove a file this build created), then removes the journal, staging and backups. An `OSError` is then reported as `BuildError("write outputs", … "every output rolled back")`; any other exception is re-raised unchanged.
7. **If the rollback itself fails,** nothing more is touched: the journal and the remaining backups are kept, the error reports both failures ("rollback incomplete"), `outputs_unchanged` is false, and the CLI says that outputs may be partly changed and that another normal build will recover.
8. **Commit:** after every replacement succeeded, the journal is removed **first**; this is the commit point. Only then are the backups and the staging folder deleted.
9. **If removing the journal fails,** the transaction is *not* committed: nothing more is touched (the journal, every backup and the staging data are kept), and `BuildError("commit", …)` says that journal removal failed, that the transaction could not be committed, and that the next normal build restores the previous consistent set; the original `OSError` is its cause and `outputs_unchanged` is false.

**Interruption at each point and what the next normal build does:**

| Stops | State | Next normal build |
|---|---|---|
| during staging or before the journal is in place | no journal; targets untouched | removes stale staging data; builds |
| during replacement, or after it but before the journal is removed | journal and every backup present | restores the complete previous set, then builds |
| journal removal fails (reported as a commit failure) | journal, every backup and staging present | restores the complete previous set, then builds |
| immediately after the journal is removed | new set committed; backups and staging remain | removes them as stale data; the new set stays |
| during backup or staging cleanup | new set committed; partial leftovers | removes them as stale data; the new set stays |
| during recovery | journal kept until every file is restored | repeats the recovery (idempotent) |

**What is and is not guaranteed.** Each single replacement is an atomic rename within one filesystem (all outputs, staging and backups are inside the repository). A set of files in several folders cannot be renamed atomically, so the set-level guarantee is: *all-or-rollback within the process; after an interruption, the next normal build restores the previous set if the journal still exists, and otherwise the new set is already committed*. Directory entries are not fsynced, so whether a rename survives a power loss depends on the filesystem.

### 2.5 CLI

`vrag corpus build [--check] [--parsing PATH] [--chunking PATH] [--root PATH]`: prints the verification checks passed, each output with records, bytes, sha256 and its state (`written`/`unchanged`, or `ok`/`DIFFERS`/`MISSING` with `--check`), the summary counts and `RESULT`. Exit codes: 0 success; 1 verification, in-memory validation, replacement or `--check` failure; 2 missing input, unreadable configuration or a Git environment failure. The final line states exactly what happened: "no output was changed", "the previous consistent output set was restored … no new output was written" (recovery ran, then the build failed), or "outputs may be partly changed; the journal and backups are kept" (failed rollback or recovery). Unexpected exceptions (programming errors) are not caught.

### 2.6 S5a tests

| Test | Covers |
|---|---|
| `tests/unit/corpus/test_build.py` (fixtures, 27 tests: these 15 plus the 12 correction tests in §2.10) | fresh build and no-op rebuild; two independent builds byte-identical; S4c outputs equal the S4 functions; a tampered corpus file (existing outputs and a fresh root); an M1 `content_hash` mismatch (IR-6); a changed and a missing release body; a missing cache (exit 2); a corrupt existing S4c output; an in-memory validation failure; a replacement failure injected part-way (everything rolled back); an interrupted build detected by `--check` and restored; `--check` reporting stale and missing outputs without touching them; unresolved sources recorded and never chunked |
| `tests/integration/test_corpus_build.py` (real corpus, 4 tests) | two independent full builds byte-identical; S4c and S4d outputs equal those of the S4 commands; the committed build manifest equals a fresh build (pinned counts); `--check` on the repository changes nothing |

Failure cases only ever modify fixture caches in temporary folders; the real caches are read, never modified.

### 2.7 Corrections after the owner's code audit (2026-11-21)

| # | Correction |
|---|---|
| 1 | Journal removal is the commit point; backups are kept until it is gone; stale transaction data is removed only when no journal exists, and only by normal builds (§2.4) |
| 2 | A failed rollback keeps the journal and the remaining backups and reports both failures; the CLI never claims that nothing changed in that case |
| 3 | Any exception during replacement (including `KeyboardInterrupt`) triggers the rollback; the original exception is re-raised (an `OSError` as `BuildError` with the original as its cause) |
| 4 | `write_outputs` refuses to start while a journal exists |
| 5 | Recovery before verification, documented (§2.1 step 0); the error after a failed build lists what recovery restored |
| 6 | Expected failures become build errors: `GitError` and `OSError` while reading inputs → `BuildEnvironmentError` (exit 2); `ReleaseChunkError` and `ChunkingError` → `BuildError` (exit 1); unreadable committed manifests are reported as existing-output problems. Other exceptions propagate unchanged |
| 7 | "Every checked-out source file matches" is reported only when `verify_cache` actually ran that check (it skips it when the release cache fails) |
| 8 | Mismatched existing outputs still stop the build, now with a diagnostic (§2.8) |
| 9 | `--check` runs with `GIT_NO_LAZY_FETCH=1` (§2.9) |
| 10 | (verbatim review) A failure to remove the journal at the commit point is a controlled `BuildError("commit")`: not committed, journal, backups and staging kept, no further cleanup, recovery explained, original `OSError` kept as the cause (§2.4 step 9) |

### 2.8 Existing-output mismatch diagnostic

The build stops and reports, after the individual problems: the exact mismatched files (for example `data/build/chunks/1.2.jsonl`); that they do not match their committed manifests (`chunk_manifest.json`, `release_note_chunk_manifest.json`) and that nothing was changed; and the safe manual procedure: check `git status` and `git diff -- data/manifest docs/reports`, move the listed generated files out of `data/build/chunks/` yourself, then run `vrag corpus build` again. Nothing is deleted or overwritten automatically, and there is no force option.

### 2.9 Git lazy fetching in check mode

The docs cache is a promisor partial clone (`blob:none`), so reading a missing blob would make git fetch it over the network and write it into the cache. `--check` sets `GIT_NO_LAZY_FETCH=1` (supported since git 2.44; the project uses git 2.47.0) for its whole run and restores the previous environment afterwards. S1's git wrapper passes the process environment to git unchanged, so no S1 code is modified. A missing object then fails as a Git environment error (exit 2) and the cache is untouched. Normal builds keep S1's behaviour.

**Limitation (accepted by the owner for this milestone):** `_no_lazy_fetch` sets the variable in the process-wide environment (`os.environ`) and restores it afterwards. This assumes single-threaded CLI execution; it is not thread-safe if `run_build(check=True)` were called concurrently from several threads in one process (another thread's git calls could see, or lose, the setting).

### 2.10 Correction tests (`tests/unit/corpus/test_build.py`)

Interruption before journal removal (the complete old set is restored); immediately after journal removal and during backup cleanup (the new set stays committed, leftovers are removed); a failed rollback (journal and backups kept, a later build recovers); `KeyboardInterrupt` during replacement (rolled back and re-raised); `write_outputs` refusing while a journal exists; recovery followed by failed verification (the previous set stays restored); `--check` with a journal (no recovery, no cleanup); `--check` with missing Git objects in an isolated fixture partial clone (exit 2, `.git` byte-identical, and a control showing git would otherwise fetch); the mismatch diagnostic; repeated recovery being idempotent, including after a failed recovery; a failed journal removal at the commit point (reported as not committed, journal, backups and staging kept, no cleanup after the failure, and the next normal build restores the previous set). On the real corpus, `--check` leaves the outputs, the `data/build` tree and the cache's whole `.git` folder byte-identical.

## 3. S5b: `vrag corpus validate` (`src/version_aware_rag/corpus/validate.py`)

### 3.1 Owner decisions (2026-11-21)

| # | Decision |
|---|---|
| S5b-D1 | Option B: record-level invariants plus **source coverage through the S3 parser** (never the S4 chunker): every block, table row and list item of a page is represented in its chunks |
| S5b-D2 | Cache-dependent checks are warnings ("skipped") when a cache is unavailable |
| S5b-D3 | Manifest completeness: file records carry hash, version, blob and bytes; kind and licence come from the source record, each release-post record and the S2 release records |
| S5b-D4 | Parse warnings and dropped includes stay in the S5c build report |
| S5b-D5 | Leak scan of committable files (tracked plus untracked-not-ignored): release-note and release-post lines over 25 characters, contributor handles of 4 or more characters |
| S5b-D6 | Compatibility compares `introduced_in` with the target's behaviour package, as integer tuples |
| S5b-D7 | Present but unparseable outputs are errors (exit 1) |
| S5b-D8 | `vrag corpus validate` registered additively in `commands.py` |
| (owner) | Every output listed in `build_manifest.json` is verified: missing is exit 2, a hash, size or record mismatch is exit 1; schema and paths are validated, nothing listed is skipped |

### 3.2 Order of checks (read-only; nothing is written or rebuilt)

1. `build_manifest.json`: schema (`schema_version`, `library`, `generator`, `inputs`, `logical_corpus` equal to S5-D1, `counts`), each output entry's fields, safe relative paths, no duplicates, no unknown outputs, every expected output listed, and the current config and corpus-manifest hashes; every listed file must exist (else exit 2) and match its sha256, bytes and records. Then the fixed S5a output set must exist.
2. Corpus manifest: `load_manifest` integrity, `compare_with_m1` (RAG-M1 evidence), record completeness (R9).
3. Integrity reused from S4c (`chunk_file_problems`) and S4d (`release_file_problems`).
4. Every chunk record: version and file (R1), source path and canonical URL per source kind (R2), section path (R3), version, `behaviour_package`, source hash, commit and repository (R5), `introduced_in` and the compatibility rule (R6), licence and attribution per source kind (R7), unit kind, merged paths and the S4 size, oversize and overlap rules (R8).
5. Corpus level: IDs unique across the 6 chunk files (R12a), unresolved sources never chunked (R1c), units complete and consistent (R8c), each API entry one unit and never inside a merged chunk (R8a), empty-section records on manifest pages.
6. Cache tier: source coverage for docs, posts and release notes (§3.3) and the leak scan (§3.4).
7. Warnings: oversize chunks, near-empty pages (S5-D7), section-path fallbacks, cross-version duplicates, skipped checks.

Exit codes (S5-D4): 0 no errors (warnings allowed), 1 errors, 2 missing outputs, an unloadable corpus manifest or unreadable configuration.

### 3.3 Source coverage (S5b-D1)

Each manifest page is parsed with the S3 parser exactly as the build does (`parse_corpus_page`); release notes are parsed and then given S4d's approved Q7 transformation (`apply_q7`). The S4 chunker is not run. For each page:

- an excluded page must have no chunks; an included page with content must have chunks;
- every heading must appear in a chunk's section path or merged paths, or in an empty-section record (the title H1 counts as the title);
- every other block's text must appear in the page's chunk text (overlap repeats whole blocks, so repetition is allowed); a block that does not appear whole is checked piece by piece: table rows with the header and delimiter (repeated headers are allowed), or top-level list items; only if a piece is missing is it reported, with the block's source lines;
- chunk line ranges must be valid, inside the page, ordered, with overlap lines before the chunk's own lines.

Legitimate transformations (overlap, repeated table headers, split tables and lists, Q7, CRLF normalisation by the parser) are therefore accepted, and genuinely missing content is reported. On the real corpus: 2,048 pages (26 excluded), 79,228 blocks verbatim, 174 split, 36,578 headings; release notes 8 verbatim and 4 split after 305 Q7 transformations; 0 problems.

### 3.4 Leak scan (S5b-D5, policy of §3.6)

Committable files (tracked plus untracked-not-ignored, from `git ls-files`) are scanned for release-note lines and release-post lines (normalised, longer than 25 characters, front matter excluded) and for `@handle`s of 4 or more characters from the release bodies. Findings name the file and the number of matching lines, never the text, and are errors.

- **Shared with the docs corpus** (owner, S5b review, narrower rule). A restricted line is not restricted if either (a) its normalised text equals a normalised line of the approved versioned documentation corpus (the manifest's docs pages, read from the cache and hash-checked), in any context; or (b) it is inside a fenced code block of its source and occurs in a docs line that is itself inside a fenced code block, as a plain substring with identical whitespace that starts and ends on token boundaries of the docs line (a token is a run of word characters or of other non-space characters). Prose and headings are never matched by containment; there is no whitespace-insensitive or token-sequence matching. On the real corpus 121 of 1,262 post lines are shared: 118 identical and 3 code lines (the 1.3 full-text-search query and two MERGE clauses that a post wraps and a docs example continues); 0 of 305 release-note lines.
- **Documented exceptions.** `LEAK_EXCEPTIONS` in `validate.py` lists the owner-approved quotations in immutable committed files, each with a reason. An exception covers one occurrence only: the file path, the line number, the sha256 of the whole line and the sha256 of the quoted restricted line must all match. A moved or edited line, the same text in another file, a second occurrence, or another restricted line on the same line is still an error.
- Without a cache the corresponding part is a warning, as before (S5b-D2); without the docs cache no line is treated as shared, so the release-note scan is stricter, never weaker.

### 3.5 Tests

- `tests/unit/corpus/test_compat.py` (17): integer-tuple ordering, behaviour-package comparison, documentation-only-own-series, malformed versions.
- `tests/unit/corpus/test_validate.py` (61): a clean fixture corpus passes and nothing is written; cache-tier warnings; build-manifest schema, paths, records, bytes, hashes and inputs; missing listed or fixed outputs (exit 2); unparseable outputs (exit 1); every record rule by tampering one field; wrong version file and duplicate IDs; missing or duplicate parts; unresolved collision; M1 evidence and excluded reasons; source coverage accepting split tables, split lists, repeated headers and overlap, and reporting a missing row, a missing item, a missing chunk and heading, and source-order violations; Q7 versus missing text; the leak scan for each kind without printing the text; the §3.6 policy (docs-shared lines excluded in the shapes of the three real false positives, including a clause that only starts a longer docs line; 11 adversarial cases: the three legitimate real containment shapes excluded, the real blockquote shapes and prose-against-code not shared, a post-only sentence or heading inside a longer docs line detected, a docs code fragment excluded, a changed SQL token, a partial-token overlap, a narrower table border, stripped markup and text spanning two docs lines all detected, identical lines shared; the four exceptions matching exactly their lines, with the quoted text recovered from the files by fingerprint so no post text enters the tests; the same quotation in another file, a moved line, an edited line and a second occurrence still detected; a similar heading still detected; the cache-absent warnings unchanged); CLI exit codes 0, 0 with warnings, 1, 2 and the unloadable-manifest and missing-config cases.
- `tests/integration/test_corpus_validate.py` (4): read-only on the real corpus (outputs, `data/build` and the cache's `.git` byte-identical), every rule passing (exit 0), the leak-scan policy counts (4 exception occurrences, 121 shared post lines, 0 shared release lines), pinned warnings and coverage counts.

### 3.6 Leak-scan findings on the real corpus and the owner's policy (S5b review, 2026-11-21)

The first real run of the S5b-D5 heuristic reported 5 files: 6 distinct (file, matched line) pairs, 8 file lines, all release-post lines, no release-note text and no handle. Earlier summaries said "2 genuine headings", counting files; it is 3 heading-pair matches in 2 files (4 file lines).

| File:line | Kind | Classification |
|---|---|---|
| `scripts/probes/rag_m1_probes.py:15` | a SQL clause in a probe query (28 characters) | false positive: the text occurs in 1.4, 1.5 and 2.0 `merge_into.md` (inside a longer line) |
| `docs/design/RAG-M1_version_manifest.md:113` | the same probe query (copy in the manifest) | false positive, as above |
| `docs/reports/RAG-M3_S3_samples.md:220, 368` | a markdownlint comment (35 characters) | false positive: an identical line in 164 docs pages |
| `docs/design/RAG-M1_research_evidence.md:107` | the 1.3 post heading on breaking changes (33 characters) | genuine post-only heading, quoted as RAG-M1 evidence |
| `docs/design/RAG-M1_research_evidence.md:109, 217` | the 1.5 post heading on a datetime function change (37 characters) | genuine post-only heading, quoted twice as RAG-M1 evidence |
| `docs/design/RAG-M3_S3_parsing.md:359` | the same 1.5 heading | genuine post-only heading, quoted in S3 when reporting the RAG-M1 finding |

Owner policy: the false positives are removed by the general docs-shared rule of §3.4, not by file-specific exceptions. A first, plain-containment form of that rule was rejected after adversarial tests: it also matched post-only sentences and headings inside longer docs lines, partial tokens (`> 10` in `> 100`), a narrower table border inside a wider one, and text exposed by stripped markup; the approved narrower rule (code-block lines only, token-aligned, identical whitespace) handles all of these and keeps exactly the three legitimate code cases; the four genuine quotations are covered by the four exact `LEAK_EXCEPTIONS`; the documents stay unchanged; nothing is downgraded to a warning. Result: 0 leak errors; `vrag corpus validate` exits 0 with the 4 warnings of §3.2.

## 4. S5c: extended build report (`src/version_aware_rag/corpus/report_stats.py`)

### 4.1 Owner decisions (2026-11-21)

| # | Decision |
|---|---|
| S5c-D1 | The extended content goes into `data/build/build_report.json` (git-ignored); in `build_manifest.json` only that file's `sha256` and `bytes` change |
| S5c-D2 | Content as listed in §4.2; never copied source text, warning or transformation details, release-post passages or other corpus content: identifiers, codes, paths, versions, counts and line numbers only |
| S5c-D3 | A separate pure, deterministic module `report_stats.py`; `validate.py` and `compat.py` untouched |
| S5c-D4 | `include_latest_client_removed` and `include_filterbar_removed` are reported as dropped; `include_iframe_marked` and `include_tooltip_label_kept` as partial transformations |
| S5c-D5 | No committed Markdown S5 report; no change to CLI output |
| S5c-D6 | Build report `schema_version` 2; build manifest stays at schema 1; both generator strings unchanged |

### 4.2 Report sections

| Section | Content |
|---|---|
| `warnings.oversize` | count, counts by reason, and every oversized chunk (id, version, path, unit kind, reason, approximate tokens) |
| `warnings.near_empty_pages` | threshold (20 words), count, and each page (path, version, words); the pages keep their chunks (S5-D7) |
| `warnings.section_path_fallback` | count and chunk ids |
| `warnings.cross_version_duplicates` | count (detail in `duplicates`) |
| `duplicates` | groups of identical content in two or more versions (GitHub release notes excluded), chunks in those groups, groups by number of versions, and pairs of versions; counting only, nothing is deduplicated |
| `sizes.token_histogram` | chunks per token bin (the S4c report's boundaries 0, 32, 64, 128, 256, 350, 512, 513; the last bin ends before 10^9) for the version files, the release notes and the logical corpus. Every token count is checked first: a value below 0 or at least 10^9 raises `TokenCountError` naming the chunk id and the value, and is never skipped or placed in another bin; the error is raised while the outputs are rendered in memory, so nothing is written (owner, S5c review) |
| `parse_warnings` | count, pages, counts by code, and each warning (version, path, source kind, included or not, code, line) for every parsed page, restricted pages included |
| `includes` | dropped and partial include transformations (version, path, the page's `include_in_corpus`, code, line), excluded pages included, and per rule its class, count and handling (`include_in_corpus` added after the owner's S5c review) |

The definitions of oversized chunks, near-empty pages, section-path fallbacks and cross-version duplicates are those of S5b's `add_warnings` (same predicates on the same chunks); `report_stats` cannot import `validate` (import cycle), so the threshold is repeated and pinned by a test. Every list is sorted, histogram bins are fixed, and the JSON is written with sorted keys, so independent builds stay byte-identical.

### 4.3 Real corpus (measured 2026-11-21)

- `vrag corpus build --check` before regenerating: `DIFFERS` for exactly `data/build/build_report.json` and `data/manifest/build_manifest.json`, `ok` for the other 12 outputs; nothing written (snapshot of 266 entries byte-identical). The normal build then wrote exactly those two files; every other output `unchanged`.
- `build_report.json`: 6,250 to 90,812 bytes (sha256 `69872b23…3d96`); pre-existing keys unchanged apart from `schema_version` 1 to 2. `build_manifest.json`: 4,060 to 4,061 bytes, sha256 `bd9cc39b…6848` to `15b7068f…0c7f`; the only changed lines are the build report's `bytes` and `sha256`.
- After the owner's S5c review (include provenance, histogram validation): `build --check` again showed exactly the build report and the build manifest differing (12 outputs ok, nothing written) and the normal build wrote only those two. `build_report.json` 90,812 to 95,142 bytes (sha256 `32c8bfac…4d8f`); with the new field removed it equals the previous report exactly. `build_manifest.json` stays 4,061 bytes, sha256 `15b7068f…0c7f` to `f8ba1b78…108a`, again only the build report's `bytes` and `sha256`. Of the 117 include records, 116 are on included pages; the one on an excluded page is `include_filterbar_removed` in `docs/2.0/guides/overview.md`.
- Oversized chunks 171 (code block 164, list item 3, API entry block 4); near-empty pages 6 (the 1.2 and 1.3 `extensions/troubleshooting.md` and `sql/statements/create_secret.md`, the 1.5 and 2.0 `quack/setup/overview.md`); section-path fallbacks 22; cross-version duplicate groups 6,283 covering 23,691 chunks (in 2, 3, 4 and 5 versions: 2,199, 1,304, 1,203 and 1,577 groups). All equal S5b's validation counts.
- Token histogram of the logical corpus (bins as above): 7,078, 8,754, 7,843, 4,513, 1,911, 1,404, 6, 171.
- Parse warnings 52 on 24 pages: `html_block_generic_text` 30, `api_dt_without_id` 14, `front_matter_missing` 4 (the four GitHub release bodies, which have no front matter), `markdown_link_unparsed` 2, `liquid_tag_unsupported` 1, `liquid_for_loop_removed` 1; 42 on docs pages, 6 on release posts, 4 on release notes, 2 on excluded pages.
- Includes: dropped 31 (`latest_client` 30, `filterbar` 1); partial 86 (`tooltip` label kept 78, `iframe` marked 8).
- Privacy: the S5b leak scan (`check_leaks`) over the generated report finds no release-note line, release-post line or handle, and no chunk line over 25 characters (MIT docs included) occurs in the report.
- The report is larger than the 40-60 KB estimated in the plan, mostly the 171-entry oversize list (about 46 KB); it is git-ignored.

### 4.4 Tests

- `tests/unit/corpus/test_report_stats.py` (12): threshold and histogram boundaries; definitions equal to S5b's `add_warnings` on the same chunks; near-empty pages listed with their chunks kept; duplicate groups and version pairs without deduplication (release notes excluded); oversize entries and histogram with identifiers and counts only; parse warnings kept on restricted pages without details; dropped versus partial includes without details; every section independent of input order; include records carrying `include_in_corpus` for an excluded page (`filterbar_removed`) and an included page; the histogram rejecting -1 and 10^9 with the chunk id and value in the error; 0 and 999,999,999 placed in the first and last bins.
- `tests/unit/corpus/test_build.py` (+3, 30 in total): report sections, report schema 2, manifest schema 1, generators and manifest summary counts unchanged, histograms summing to the chunk counts; report counts equal S5b's on the built fixture corpus; no chunk text in the report.
- `tests/integration/test_corpus_build.py` (+3, 7 in total): the pinned real values above, including exactly one include record on an excluded page (`filterbar_removed`); agreement with S5b validation; the S5b leak scan and the no-corpus-text check on the real report. The existing tests (two builds byte-identical, S4 outputs equal to the S4 commands, committed manifest equal to a fresh build, check mode read-only) cover the extended report too.

## 5. Completion and follow-up

- **Done:** S5a, S5b and S5c approved after independent review; the single consolidated S5 commit (S5-D10) is `d471e1ec5a771bb93b07c97860eee7fdf6219503` (`feat(rag): add deterministic corpus build and validation pipeline`), local, not pushed.
- **After S5:** the IR-3 report (S5-D9).
