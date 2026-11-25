# RAG-M3 Plan: Corpus Collection and Version Metadata

Status: **revision 10 (2026-11-22).** S1, S2, S3 + S3b and S4 committed locally. **S5 complete:** decisions S5-D1 to S5-D10 approved (`RAG-M3_S5_build.md`); S5a (deterministic build pipeline, `vrag corpus build`) approved; S5b (corpus validation, `vrag corpus validate`) approved; S5c (extended build report) approved; committed locally as the single S5 commit `d471e1ec5a771bb93b07c97860eee7fdf6219503` (S5-D10, not pushed). S3 findings refine this plan (C1–C14, `RAG-M3_S3_parsing.md` §1 and §10); the manifest size budget was resolved by the owner-approved split (`RAG-M3_S3_parsing.md` §10.4). S4 moved chunk-file writing out of S5 (owner, S4c). `check-upstream` and the optional sampled URL check are assigned to S6 (S5-D9). **S6 (`RAG-M3_S6_ci.md`, owner decisions S6-D1 to S6-D10; added in revision 10):**
- S6a (the `corpus-rebuild` CI job) and S6b (`vrag corpus check-upstream`) are implemented and approved after independent review.
- S6c (documentation) is implemented and awaiting owner review.
- The sampled URL check was skipped (S6-D7), and the Q11 backup is deferred (S6-D6).
- IR-3 (RAG-M1 probe suite) is re-assigned to the first stage of RAG-M4, together with IR-2 (S6-D8, owner Option B; `RAG-M3_IR3_report.md`); it is not part of RAG-M3.
- S6 is not yet committed or pushed. **RAG-M3 acceptance still requires a green `corpus-rebuild` run on GitHub.**

Authorities: `RAG-M1_design.md` (approved, D14), `RAG-M1_research_evidence.md`, `RAG-M1_version_manifest.md`, roadmap P2-M3 ("every chunk carries version metadata; the corpus rebuilds from a manifest"). **This plan does not modify the M1 documents.** Where it proposes a deviation from M1 it says so, and that item needs approval (§9).

Labels: **[VERIFIED]** (command run in this session, output recorded here), **[M1]** (approved decision), **[NEW]** (observation made in this session), **[PROPOSAL]**, **[OPEN]** (owner decision needed).

## 1. Verified facts

### 1.1 Documentation source

| Item | Value | Label |
|---|---|---|
| Repository | `https://github.com/duckdb/duckdb-web` (MIT per GitHub licence API) | VERIFIED |
| Pinned commit | `d01484462710d49206816c9eeaeb822361ebe81c` | M1 manifest §1; VERIFIED |
| Fetchable by SHA | `git fetch --depth 1 --filter=blob:none origin d0148446…` succeeded in 3 s; trees only, 624 objects, 239 KiB | VERIFIED, NEW |
| Packed size of the whole repo | ≈ 596 MB (GitHub API `size`) | VERIFIED. A full clone or tarball is wasteful |

### 1.2 Versions and their sources

| Docs version | `docs/X.Y` at pinned commit | .md files | .md bytes | Other files in the tree | Behaviour package (A1) | Status |
|---|---|---|---|---|---|---|
| 1.1 | present | 346 | 2.98 MB | 2.77 MB JS tutorial bundle, fonts, CSS | 1.1.3 | **Not in the corpus** (M1; reconfirmed by owner Q1). 1.1.3 stays a behaviour-test reference package only |
| 1.2 | present | 361 | 3.24 MB | 2.77 MB JS, fonts, CSS (excluded) | 1.2.2 | M1 corpus |
| 1.3 | present | 370 | 3.65 MB | `.rst`, `objects.inv` (excluded) | 1.3.2 | M1 corpus |
| 1.4 LTS | present | 385 | 3.85 MB | same | 1.4.5 | M1 corpus |
| 1.5 | present | 441 | 4.41 MB | same | 1.5.6 | M1 corpus |
| 2.0 | present | 474 | 6.10 MB | same | 2.0.1 | M1 corpus |

- [VERIFIED] The .md counts match M1 manifest §1.1 exactly (361/370/385/441/474; 2,031 total).
- [VERIFIED] Words after stripping front matter total 2,233,600: 1.2 336,099; 1.3 380,272; 1.4 401,117; 1.5 467,098; 2.0 649,014. M1's 2,256,769 counted the whole file including front matter, so both are correct for their definitions.
- [VERIFIED] Public URLs: `https://duckdb.org/docs/1.2/sql/functions/map` returns 200 with canonical `https://duckdb.org/docs/1.2/sql/functions/map.html`. 1.1, 1.4, 2.0 and `stable` also return 200.

### 1.3 Change sources

| Source | Coverage at pinned commit | Label |
|---|---|---|
| duckdb-web release posts (`_posts/`) | 1.1.0; 1.2.0; 1.3.0; 1.4.0–1.4.5; 1.5.0–1.5.6; 2.0.0; 2.0.1. **No posts** for 1.1.1–1.1.3, 1.2.1, 1.2.2, 1.3.1, 1.3.2 | VERIFIED (M1 gap reconfirmed; 1.1.x gap is NEW) |
| Migration guides | `docs/2.0/operations_manual/migration.md`, `docs/2.0/clients/python/migration.md` (inside the 2.0 docs tree) | M1 manifest §1.2 |
| GitHub releases, `duckdb/duckdb` | **All 25 tags v1.1.0–v2.0.1 have a published, non-draft, non-prerelease release with a non-empty body.** Bodies of v1.2.0–v2.0.1 total 525,662 characters | VERIFIED 2026-11-20T17:48:36Z, NEW |
| CHANGELOG / NEWS file in `duckdb/duckdb` root | none | VERIFIED |

**The 1.2.x/1.3.x gap (D13) can be closed** with these GitHub releases (retrieved 2026-11-20T17:48:36Z):

| Tag | Release id | Published (UTC) | Tag → commit | Body sha256 (as returned by the API, UTF-8) |
|---|---|---|---|---|
| v1.2.1 | 203798808 | 2025-03-05T18:03:02Z | `8e52ec43959ab363643d63cb78ee214577111da4` | `3c2d8e0de4a56f3cf795e70e9a20ccb3e3582f77dc1eebd5e490014f3c08b8d4` |
| v1.2.2 | 210741329 | 2025-04-08T08:44:15Z | `7c039464e452ddc3330e2691d3fa6d305521d09b` | `803880ca42eb79676bf8ac76154616441e1a9cf3080f4854612be803eb0cabfa` |
| v1.3.1 | 225478305 | 2025-06-16T13:54:58Z | `2063dda3e6bd955c364ce8e61939c6248a907be6` | `f9febe970d61bcd8af89fe4ced6dfd234105a34ed1ab3263655b0efb378d7408` |
| v1.3.2 | 230414582 | 2025-07-08T10:39:09Z | `0b83e5d2f68bc02dfefde74b846bd039f078affa` | `e86f15c67ee2bf8cfe4c08bd186666e7cbbbf740cb764061903569fe48240074` |

[NEW] What they contain: one sentence (a fixed boilerplate opening, essentially identical across these patch release bodies), then an auto-generated `## What's Changed` list of pull-request titles in the form `* <title> by @<user> in https://github.com/duckdb/duckdb/pull/<n>`. v1.2.1 has 85 such lines and v1.3.2 has 53. These are terse engineering notes, not user documentation. They are useful for "was X fixed in 1.2.2?" questions, but they are low-signal for most retrieval and contain contributor handles.

[VERIFIED] Tags are lightweight (no peeled `^{}` entry), so the tag ref points directly at the commit shown. A release body can still be edited later, so the fetched body is hashed and every build re-verifies it. Under owner Q8 the raw copy is kept in the local cache only, not committed (§4.6).

### 1.4 Document format (survey of all 2,031 pages; one pass takes 7.6 s)

| Construct | Files | Handling needed |
|---|---|---|
| YAML front matter (`title`, `layout`, `redirect_from`, …) | all | Keep `title`; drop the rest |
| `{% link docs/X/... %}` | 1,653 | Resolve to a URL (M1 design §2) |
| `{% include … %}`: `tooltip.html` 78 uses, `install/latest_client.html` 30, `iframe.html` 8, and 2 others | 24 | Can't be rendered without the Jekyll site; see §4.4 |
| `{% if site.* %}`, `{{ site.* }}` | 21 / 93 | **[NEW] Site variables hold the current releases** (`maintained_duckdb_version: "1.5.6"`, `latest_duckdb_version: "2.0.1"` in `_config.yml`). Rendering them would inject 1.5.6/2.0.1 into archived 1.2–1.4 pages; see §4.4 |
| Kramdown attributes `{: …}` | 225 | Drop |
| HTML comments (lint directives, generated-section markers) | 326 | Drop |
| Fenced code blocks | 1,730 | Keep intact |
| Pipe tables | 885 | Keep intact; function docs are tables |
| `<div class="nostroke_table">`, `monospace_table`, `highlight`, `rrdiagram` | 634 | Drop wrapper tags; drop empty railroad-diagram containers |
| Headings, 2.0 only | — | H1 221, H2 1,803, H3 1,454, **H4 2,896, H5 4,443** |
| Mostly-HTML pages (> 50% of lines are HTML) | 18 | `clients/python/reference/index.md` (all 5 versions), `clients/c/appender.md` and `clients/c/replacement_scans.md` (4 versions each), `index.md` landing pages (5) |
| Near-empty pages (< 20 words) | 22 | For example section `overview.md` stubs: no chunk; listed in the build report |

[NEW] **The Python API reference is generated Sphinx HTML inside a `.md` file**, with no Markdown headings. It is the longest "section" in every version (29,445 words in 1.2 to 48,962 in 2.0). It documents APIs that behaviour probe P10 tests (`fetchone`), so dropping it would remove real evidence.

[NEW] **Most headings are H4/H5.** These are function entries such as `#### map_extract(map, key)`, each followed by a Description/Example/Result table. Chunking only at H2/H3 (M1 design §2) would merge dozens of functions into one long section.

## 2. M1 decisions this plan keeps

- Corpus: DuckDB docs 1.2, 1.3, 1.4 LTS, 1.5, 2.0 at the pinned commit; integrity check against M1 manifest §1.1 `content_hash`, refusing to build on a mismatch (IR-6).
- Change sources: 17 posts, 2 migration guides, plus the four 1.2.x/1.3.x GitHub releases (D13, IR-5), hashed. Under the owner's stricter licence treatment (Q8) their raw text is kept locally and **not committed** (§4.6, §7).
- Parsing: strip front matter (keep title), resolve `{% link %}`, drop Kramdown attributes and lint comments, keep tables and code intact.
- Chunk metadata as in M1 design §2, extended (§5). Version compatibility rule: a chunk is compatible with target *v* if `version == v`, or if it is a change source with `introduced_in ≤ v`.
- Raw corpus not committed; the manifest and fetch code are committed. **Refinement (owner Q8):** release-note raw copies are *not* committed, only their metadata and hashes (M1 had proposed committing them).
- Docs claims and runtime observations stay separate record types (`DocClaim`, `RuntimeObservation`; M1 §2.1). **M3 produces chunks only.** Claims and observations are created at RAG-M4.
- Assumptions A1 and A3 stay labelled unverified. Nothing in M3 asserts that a docs page is correct.

## 3. Proposed repository changes

```
configs/corpus.toml                     NEW  explicit config: versions, sources, include/exclude globs, parser and chunker parameters
data/manifest/corpus_manifest.json      NEW  committed, generated: every source file with hash, version, kind, licence status, include/exclude reason;
                                             release-note METADATA and body hashes (no release-note text), plus the 17 investigated-not-included releases
data/cache/  (git-ignored)              NEW  shallow sparse git checkout of duckdb-web at the pinned commit, and
                                             data/cache/github_releases/<tag>.md raw release bodies (local only; Q8)
data/build/  (git-ignored)              NEW  chunks/<version>.jsonl, unresolved.jsonl, build_report.json
src/version_aware_rag/corpus/           NEW
    config.py      CorpusConfig (Pydantic) loaded from TOML (stdlib tomllib)
    models.py      SourceFile, ReleaseNote, Block, Section, Chunk models
    acquire.py     pinned shallow/sparse git fetch; release-note fetch into the local cache with hash verification
    manifest.py    build and verify the manifest; changed/missing detection; M1 content_hash check
    parse.py       Markdown → blocks (markdown-it-py), Liquid/Kramdown cleanup, HTML-page extraction (stdlib html.parser)
    sections.py    heading tree → section paths
    chunk.py       chunker and stable IDs
    build.py       verify → parse → chunk → deterministic JSONL output + build report
    validate.py    corpus validation (also used by tests and CI)
src/version_aware_rag/cli.py            CHANGED  `vrag corpus fetch | snapshot-releases | verify | build | validate | check-upstream`
tests/unit/corpus/                      NEW  unit tests on synthetic fixtures
tests/fixtures/corpus/                  NEW  small synthetic Markdown and HTML pages (our own text, MIT)
tests/integration/test_corpus_build.py  NEW  real-corpus checks, marked `corpus` (needs the fetched cache; runs in CI)
.github/workflows/ci.yml                CHANGED  new `corpus-rebuild` job
THIRD_PARTY_NOTICES.md, README.md, DEVELOPMENT.md, .gitignore   CHANGED
docs/design/RAG-M3_plan.md              NEW  this document
```

**New runtime dependencies (Q5, approved conditionally): used only if genuinely required, pinned exactly (`==`), with tests for the behaviour they provide.**

| Package | Purpose | Requirement test (S3) | Tests if adopted |
|---|---|---|---|
| `markdown-it-py` (MIT) | CommonMark-correct block tokenizer (fences, nested lists, pipe tables via its built-in `table` rule) | Expected to be required: a hand-written regex parser of fences, lists and tables is the most likely source of silent chunking errors. Adopted at S3 with the reason recorded | Fences with `~~~`, backtick fences inside lists, tables with escaped pipes and inline code, headings inside code (must not split), setext headings, HTML blocks |
| `PyYAML` (MIT, `safe_load` only) | Front matter `title` | **Conditional.** S3 first scans all 2,031 front-matter blocks. If every `title` is a plain or simply quoted single-line scalar, a small stdlib extractor with tests is used and PyYAML is **not** added. If not, PyYAML is added | Quoted titles with colons, escaped quotes, missing title (recorded error), malformed front matter (recorded error, never a crash) |

HTML extraction uses the standard library (`html.parser`). No other dependency is proposed.

## 4. Chunking design [PROPOSAL]

Nothing here is claimed optimal. Parameters are explicit in `configs/corpus.toml`, recorded in the manifest (config hash), and tuned on dev at RAG-M5 as M1 specifies.

### 4.1 Units
1. Parse each page into blocks: heading, paragraph, list, code fence, table, HTML block.
2. Build a heading tree. `section_path` = `[page title, H2, H3, …]`; an H1 equal to the title is skipped.
3. **Section unit = the content directly under one heading, up to the next heading of any level** (H2–H6).

### 4.1a M3 implementation refinement R1: section boundaries at all heading levels (owner Q2, approved with conditions)

This refines M1 design §2 ("heading-aware at H2/H3"). **The M1 design document is not changed.** The refinement is recorded here, in the manifest's `config` block (`chunker.boundary_levels = [2,3,4,5,6]`), and in the datasheet at RAG-M4.

- **Why:** H4/H5 carry most of the API and function reference (2.0: H4 2,896, H5 4,443; §1.4). With H2/H3 only, one section holds dozens of functions and must be split at arbitrary points.
- **Hierarchy preserved:** every unit keeps its **full** `section_path` (for example `["Map Functions", "Map Functions", "map_extract(map, key)"]`), and `heading_level` records the level of its own heading. Nothing is re-parented or flattened.
- **API entries are never merged.** A unit is an **API entry** if any of these hold:
  - its heading is a code span or a call signature (for example `` `map_extract(map, key)` ``);
  - its body is a function-documentation table (rows `Description` / `Example` / `Result` and similar);
  - it was extracted from an HTML API `<dt>` signature (§4.5).

  Each API entry is its own chunk however small it is, so unrelated functions are never combined to reach a size target.
- **Merging is allowed only for small non-API prose siblings**:
  - each one is below `merge_below` (64 approximate tokens);
  - they are contiguous and share the same parent;
  - no API entry lies between them;
  - the merged total stays at or below the target.

  It never crosses a parent heading, a page or a version.
- **Effect on metadata:**

  | Field | Unmerged unit | Merged chunk |
  |---|---|---|
  | `section_path` | The unit's own full path | The **parent's** full path (the deepest common ancestor) |
  | `merged_section_paths` | `[]` | Each merged unit's own full path, in document order, so the original hierarchy can be reconstructed |
  | `heading_level` | Level of its heading | Level of the parent |
  | `unit_kind` | `api_entry` or `prose` | `prose_merged` |

- **Effect on counts:** more, smaller chunks than an H2/H3 scheme, especially in function-reference pages. The build report gives counts per `unit_kind` and a size histogram. No retrieval effect is claimed; that is measured at RAG-M5.

### 4.2 Sizes
- **Target about 350 tokens, cap 512** (M1).
- **Tiny prose units** merge only under the rules in §4.1a. **Tiny API entries stay single.**
- **Long units** (> 512) split at block boundaries, with about 15% overlap (whole blocks) only when splitting (M1). Parts keep the unit's `section_path` and get `part_ordinal`/`part_count`.
- **A single code block or table larger than the cap (owner Q3):**
  - **Tables:** split between rows, repeating the header row in every part; a row is never split. A single row over the cap stays intact with `oversize = true`.
  - **Code blocks:** kept whole as one chunk even over the cap, `oversize = true`, listed in the build report, never truncated. Revisited at RAG-M5 against the embedder's real input limit.
  - **No overlapping duplicate chunks** for oversize blocks.
- **Token counts:** a dependency-free, deterministic approximation (`\w+|[^\w\s]` pieces), recorded as `token_count_approx`. The real embedder tokenizer is chosen at RAG-M5, which checks that no chunk exceeds that model's input limit. If any does, the cap is lowered in config and the corpus rebuilt; that is deterministic and recorded.

### 4.3 Code blocks and tables
- Kept verbatim, including the language tag.
- Never paraphrased or reflowed.
- `has_code` and `has_table` flags are recorded.

### 4.4 Navigation and boilerplate
- **Front matter:** keep `title` only.
- **Links:** `{% link docs/X/… %}` resolves to the canonical `https://duckdb.org/docs/X/….html`, and the link text is kept in `content`.
- **Kramdown attributes, HTML comments, wrapper `<div>`s, empty railroad-diagram containers:** dropped.
- **`{% include %}`:** these can't be rendered without the site's include files. Tooltips keep their visible text argument. Other includes are replaced by nothing and listed in the build report. The exact rule per include is decided at S3 from what the 24 files contain.
- **`{{ site.* }}` and `{% if site.* %}`: never substituted with current-release values** (§1.4; owner Q6, approved). The variable becomes a neutral marker `[site:<name>]`, and the chunk gets `has_unrendered_site_variable = true`. The `{% if %}`/`{% endif %}` tags are removed and the enclosed text kept. A test checks that no value from `_config.yml` (for example `1.5.6`, `2.0.1`) is introduced into any chunk by the renderer.
- **Landing pages** (`index.md`) that are only link grids are classified as navigation and excluded, with the reason in the manifest. This is decided per page at S3 and listed.

### 4.5 HTML-dominated pages
**Approved (Q9): extract, don't drop.** How the embedded HTML is parsed (stdlib `html.parser`, an event-driven tag parser; no browser rendering, no scripts):

1. **Split the page.** Front matter is removed as for Markdown pages. A page is routed to the HTML extractor if more than 50% of its non-blank lines are HTML (18 pages, §1.4). The routing decision is recorded per file in the manifest (`extraction = "html"`), and the list is reviewed at S3.
2. **Structure → section path.**
   - `<section id=…>` with its `<h2>`/`<h3>` text gives the heading levels; `headerlink` anchors (`¶`) are discarded.
   - Each `<dt id="duckdb.Class.method">` starts an **API entry** unit. Its section path is `[page title, <h2 text>, <dt id>]`, and the visible signature text becomes `api_signature`.
   - Its `<dd>` content is the unit body. Nested `<dl>` (methods inside a class) become child units under the class's path.
3. **Text and code.**
   - `<p>`, `<li>`, `<td>` text is kept, with whitespace normalised and entities decoded.
   - `<pre>` and `<div class="highlight">` are kept **verbatim** as code (no whitespace changes).
   - Inline `<code>` becomes backtick spans.
   - Tables are rebuilt as pipe tables.
   - `<span class="pre">` fragments are joined so signatures read as written.
4. **Dropped:** wrapper `<div>`s, permalink anchors, empty elements, `<script>`, `<style>`.
5. **Robustness.**
   - Unclosed or unexpected tags are tolerated by `html.parser`. The extractor tracks nesting and records a `parse_warning` for any imbalance; it never crashes and never silently drops a section.
   - Any page whose extracted text is under 50% of its visible text is flagged for review.
6. **Applies to:** the Python API reference (5 versions), C `appender` and `replacement_scans` (4 versions each). `index.md` landing pages are handled by §4.4 (navigation decision), not extracted as API.
7. **Tests:** synthetic Sphinx-shaped fixtures (nested `<dl>`, `<pre>` with significant whitespace, entities, broken nesting), plus a golden-output check on a small excerpt of one real page at S3.

### 4.6 Change sources
- **Release posts:** `version` = the release's minor series, `introduced_in` = the exact release from an **explicit mapping table in config**, for example `_posts/2025-11-12-announcing-duckdb-142.md → 1.4.2`. A test checks that each post's title contains its mapped version.
  - The version is **never parsed from the filename digits**, because `142` is ambiguous.
  - An unmapped post is `version_status = "unresolved"`, goes to `unresolved.jsonl`, and is excluded from every version-specific output.
- **GitHub releases (Q4, approved): only v1.2.1, v1.2.2, v1.3.1, v1.3.2.**
  - Version from the tag (`v1.2.1` → series 1.2, `introduced_in` 1.2.1).
  - The other **17** releases in 1.2.0–2.0.1 (v1.2.0, v1.3.0, v1.4.0–v1.4.5, v1.5.0–v1.5.6, v2.0.0, v2.0.1) are recorded in the manifest as `status = "investigated_not_included"`, with release id, URL, publish date and body hash. They produce no chunks. Adding any of them needs owner approval.
- **Raw copy (Q7, Q8).**
  - The release body is fetched from the GitHub API, written **unchanged** to `data/cache/github_releases/<tag>.md` (git-ignored, local only), and its sha256 is recorded in the committed manifest.
  - **Every build re-verifies the raw copy against that hash; a mismatch stops the build.**
  - A fresh checkout (including CI) refetches the 4 bodies (4 API calls) and verifies the hashes. If GitHub's text has changed, the rebuild fails loudly and reports the release; it never silently accepts new text.
- **Retrieval text (Q7, approved).** In chunk `content` only, each PR line's ` by @user in https://github.com/duckdb/duckdb/pull/<n>` becomes ` (#<n>)`. This removes contributor names and URLs and keeps the PR number. A test checks that the transformation touches only that pattern and that the raw file is untouched.
- **Licence (Q8, stricter treatment, approved).** Release-note chunks carry:
  - `licence = null` and `licence_status = "unverified"`;
  - `source_url`, `release_id`, `source_retrieved_at`, `source_content_hash`;
  - `attribution` = "DuckDB release notes, github.com/duckdb/duckdb, release <tag>".
  - They are **never committed** and are excluded from any publicly distributed corpus or derived dataset until their licence is established. They are marked `redistributable = false`, and any future export command must refuse them (enforced by test when such a command exists).
- **Migration guides:** emitted **once**, as `source_kind = migration_guide`, `version = 2.0`, `introduced_in = 2.0.0`, and removed from the plain docs pass, so the same text isn't indexed twice.
- **Compatibility comparison:** `introduced_in ≤ v` compares against the behaviour package of series *v* (1.4 → 1.4.5, A1). So a 1.4.2 note is compatible with targets 1.4, 1.5 and 2.0.

### 4.7 Stable chunk IDs
- `chunk_id = "c-" + sha256(library | version | source_kind | source_path | section_path | unit_ordinal | part_ordinal | content_hash)[:20]` (80 bits). A test fails on any collision.
- **Deterministic:** same inputs and config give the same IDs, in the same order (sorted by version, then source path, then position).
- **Content-sensitive by design:** if a source changes, the ID changes. Benchmark labels don't depend on chunk IDs, because M1 qrels are anchored to evidence spans.

### 4.8 Trade-offs, stated honestly
- **Small function-level chunks** give precise citations and a sharper version-confusion signal. They also give less context per chunk and more chunks. Merging tiny siblings is a compromise to be measured at RAG-M5.
- **The merge threshold (64) and target (350)** are starting points, not tuned values.
- **Overlap duplicates content.** It is used only inside split long units.
- **Approximate token counts** may be off by roughly ± 20–30% against a subword tokenizer. This is an estimate; it is measured at RAG-M5.

## 5. Chunk metadata schema [PROPOSAL]

Brief requirements (★) plus the M1 fields:

| Field | Type | Notes |
|---|---|---|
| ★ `chunk_id` | str | §4.7 |
| ★ `library` | `"duckdb"` | |
| ★ `version` | `1.2 \| 1.3 \| 1.4 \| 1.5 \| 2.0` | Docs series. A required enum, so no chunk can lack it |
| `version_status` | `resolved \| unresolved` | `unresolved` chunks are only written to `unresolved.jsonl` |
| `behaviour_package` | str | For example `1.4.5` (assumption A1, labelled as such in the datasheet) |
| ★ `source_type` | `documentation \| changelog` | Brief's two-way category |
| `source_kind` | `docs_page \| release_post \| migration_guide \| github_release` | The same enum as M1 `DocClaim.source_kind`. M1 §2's `docs \| release_note \| migration` maps 1:1 |
| `introduced_in` | str \| null | Change sources only |
| ★ `source_path` | str | Repository path, or `releases/tag/vX.Y.Z` |
| ★ `source_url` | str | Canonical page URL, or the release URL |
| `source_repo` | str | `duckdb/duckdb-web` or `duckdb/duckdb` |
| ★ `source_commit` | str | Pinned commit; for GitHub releases, the tag's commit plus `release_id` |
| `release_id`, `source_published_at`, `source_retrieved_at` | — | GitHub releases (and posts' publish dates) |
| ★ `source_content_hash` | sha256 | Of the raw source bytes (file, or the raw release body as returned by the API) |
| `doc_title` | str | |
| ★ `section_path` | list[str] | Never empty: the page title at minimum. `section_path_fallback = true` if the page has no headings. Full hierarchy, or the parent's for merged chunks (§4.1a) |
| `heading_level` | int | Level of the unit's own heading (parent's for merged chunks) |
| `unit_kind` | `api_entry \| prose \| prose_merged` | §4.1a; API entries are never merged |
| `merged_section_paths` | list[list[str]] | Full path of each merged unit, in order; `[]` otherwise |
| `api_signature` | str \| null | API entries only |
| `unit_ordinal`, `part_ordinal`, `part_count` | int | Position in page; split parts |
| ★ `content` | str | Cleaned text, code and tables verbatim |
| ★ `content_hash` | sha256 | Of `content` (UTF-8) |
| `token_count_approx`, `char_count` | int | |
| `has_code`, `has_table`, `split_inside_block`, `has_unrendered_site_variable`, `extraction` (`markdown \| html`) | — | Audit flags |
| ★ `licence` | SPDX string \| null | `MIT` for duckdb-web content (verified, M1). **`null` for GitHub release notes** (Q8) |
| `licence_status` | `verified \| unverified` | `unverified` for release notes until established; never inferred from the repository licence |
| `licence_note` | str \| null | For example "duckdb/duckdb repository is MIT; applicability to release text not established" |
| `redistributable` | bool | `false` whenever `licence_status = unverified` |
| `copyright`, `attribution` | str | For example "Stichting DuckDB Foundation"; release notes: "DuckDB release notes, github.com/duckdb/duckdb, release <tag>" |
| `pipeline_version`, `config_hash` | str | Reproducibility |

## 6. Storage, download and processing estimates

| Item | Estimate | Basis |
|---|---|---|
| Git trees at pinned commit | 0.24 MB | VERIFIED |
| Blobs needed (5 versions' .md + 17 posts) | ≈ 21.3 MB uncompressed. **≈ 6–9 MB on the wire** (estimate, assuming git compression of about 3×) | VERIFIED sizes; compression is an estimate |
| Excluded on purpose | 1.2's 2.8 MB JS tutorial bundle, fonts, CSS, `.rst`, `objects.inv`, and all non-docs site content (≈ 596 MB repo) | VERIFIED |
| Release notes (4, D13/Q4) | ≈ 36 KB, local cache only (not committed, Q8); 4 API calls per fresh build | VERIFIED sizes (body characters: 10,583 + 7,733 + 11,043 + 6,368) |
| Local cache (sparse checkout and .git) | ≈ 30–40 MB | Estimate |
| Build outputs (chunks JSONL, about 9–13k chunks) | ≈ 40–70 MB | Estimate from ≈ 2.23M words at ≈ 1.3–1.5 approximate tokens per word and ≈ 350 tokens per chunk, plus about 0.6 KB of metadata per chunk |
| Processing time | Under 1 minute for parse and chunk on the laptop. The fetch depends on network (3 s for trees) | Survey pass: 7.6 s for all pages |
| Memory | < 500 MB (pages are processed one at a time; outputs streamed) | Estimate |
| **Duplicate downloads** | None. One shallow fetch at one commit covers all versions. The cache is keyed by commit and reused only if `verify` passes | Design |

All of this is well within the free tier, including a CI job that refetches about 10 MB per run.

## 7. Licensing and provenance risks

| # | Risk | Assessment | Proposal |
|---|---|---|---|
| L1 | duckdb-web docs, posts and code examples | MIT, "Copyright 2018-2025 Stichting DuckDB Foundation"; the licence covers "the Software and associated documentation files" (M1, VERIFIED). Examples are part of the same repo files | `licence = "MIT"`, copyright and attribution on every chunk; THIRD_PARTY_NOTICES updated |
| L2 | GitHub release bodies (`duckdb/duckdb`) | The repo is MIT (VERIFIED), but **release text isn't a repository file**, so the licence's coverage of it is **not established** | **Owner Q8 (stricter):** `licence = null`, `licence_status = "unverified"`, plus source URL, release id, retrieval date, content hash and attribution. Raw text is local only, never committed, `redistributable = false`, and excluded from any public corpus until established |
| L3 | Contributor handles in release bodies | Public data, but irrelevant to retrieval | Kept in the raw local copy; removed from chunk content (Q7, approved) |
| P1 | Pinned commit stops being fetchable (upstream force-push or GC) | Low probability; the build would then fail loudly, never silently substitute | Owner Q11: **deferred to S6** (option: a docs snapshot asset on our own GitHub Release, MIT content only, never release notes). **S6: deferred beyond RAG-M3 (S6-D6)**; revisit triggers are in `RAG-M3_S6_ci.md` §5. A loss of the commit is reported loudly by the CI fresh fetch and by `vrag corpus check-upstream` |
| P2 | Release body edited upstream | Possible. Because the raw copy is not committed (Q8), a fresh build depends on GitHub still serving the same text | Hash in the committed manifest; mismatch stops the build and names the release; `vrag corpus check-upstream` reports drift (implemented in S6b: read-only, exit 0/1/2, release text never shown; `RAG-M3_S6_ci.md` §4); never auto-updates. Recovery needs owner decision (§9.3 U2) |
| P3 | Docs pages wrong for their version (A3) | One verified case | Not addressed by M3. Chunks are provenance, not truth; conflicts are handled at RAG-M4 (`EvidenceConflict`) |
| P4 | `{{ site.* }}` injecting current versions into archived pages | VERIFIED risk (§1.4) | Never substituted (§4.4) |

## 8. Implementation stages (small and reviewable)

**Stage protocol (owner Q10).** After each stage:
1. Show the changed files.
2. Run the relevant tests.
3. Present the evidence and results.
4. **Wait for owner approval.**
5. Only then create that stage's commit.

**No push without explicit approval.**

| Stage | Content | Demonstration | Commit |
|---|---|---|---|
| **S1 Sources and manifest** | Config, models, pinned shallow sparse fetch, manifest build/verify, M1 `content_hash` check | Recomputed per-version hashes equal M1 manifest §1.1; a tampered file fails `verify` | After approval |
| **S2 Release notes** | Fetch the 4 releases (Q4) into the local cache; record metadata, hash and `licence_status = unverified` in the manifest; the 17 others as `investigated_not_included`; changelog-gap register (no posts for 1.2.1, 1.2.2, 1.3.1, 1.3.2) | Raw body hashes equal §1.3; no release text in any tracked file (`git grep` check) | After approval |
| **S3 Parser** | Markdown blocks, cleanup rules, HTML extractor, include/landing-page decisions | **Built (2026-11-21):** `docs/design/RAG-M3_S3_parsing.md`, `docs/reports/RAG-M3_S3_source_inspection.md`, `docs/reports/RAG-M3_S3_samples.md`; markdown-it-py 4.3.0 added (exact pin), PyYAML not needed. **S3b:** conditional branches (option B) and the 17 release posts (manifest schema 3) | After approval |
| **S4 Sections and chunker** | Section tree, merge and split, IDs | Fixture tests (empty, malformed, duplicate, very long); sample chunks per version for review. **Done (2026-11-21):** S4a–S4d, `RAG-M3_S4_chunking.md`, `docs/reports/RAG-M3_S4_chunking.md`, `docs/reports/RAG-M3_S4d_release_notes.md` | One commit after S4d (S4-D7) |
| **S5 Build and validate** | Deterministic build, `unresolved.jsonl`, build report, `vrag corpus validate` | Two builds byte-identical; validation passes; counts per version and kind reported. **Complete:** decisions S5-D1 to S5-D10, `RAG-M3_S5_build.md`; S5a, S5b and S5c approved; committed as `d471e1e` (not pushed) | One commit after S5c (S5-D10): done |
| **S6 CI and docs** | `corpus-rebuild` CI job (fresh checkout: fetch, verify, build twice, compare, validate); README, notices, DEVELOPMENT.md, tracker. **API access in CI uses only the built-in Actions `GITHUB_TOKEN` (passed as the `GITHUB_TOKEN` environment variable, which the release fetcher reads); no personal tokens and no committed secrets** (owner, 2026-11-21). **As implemented (`RAG-M3_S6_ci.md`):** two independent checkouts, each with its own fresh fetch and build, compared on every `ALL_OUTPUTS` path and its SHA-256; 45 corpus tests with zero skips read from the JUnit XML; only three text-free evidence artifacts (the validation log sanitized); plus the read-only `vrag corpus check-upstream` (local or manual, never in CI) | Green CI run on GitHub: **pending** (S6d, after the D10 fresh-clone rehearsal of the S6 commit) | One consolidated S6 commit after S6c (S6-D9); push only with explicit owner approval; then RAG-M3 review |

## 9. Owner decisions

### 9.1 Decision table (owner, 2026-11-20)

| # | Question | Owner decision | Where applied |
|---|---|---|---|
| Q1 | Corpus versions | **Approved: five only**: docs 1.2, 1.3, 1.4, 1.5, 2.0 (behaviour packages 1.2.2, 1.3.2, 1.4.5, 1.5.6, 2.0.1). 1.1.3 stays a behaviour-test reference package, not corpus. M1 manifest unchanged | §1.2, §2 |
| Q2 | Heading levels and merging | **Approved with documented refinement**: H2–H5 (and H6) as boundaries; merge small siblings only when appropriate; hierarchy preserved; **unrelated API functions never merged to meet a size target**; M1 design unchanged; recorded as M3 refinement R1 | §4.1a, §4.2, §5 |
| Q3 | Over-cap single code block or table | **Decided.** Tables: **A**, split between rows, header repeated in every part, never split a row; a single row over 512 tokens stays intact, flagged `oversize`. Code: **B**, the whole block stays one chunk even over 512, flagged `oversize`, listed in the build report, never truncated; revisited at RAG-M5 against the embedder's real input limit. **No overlapping duplicate chunks** (D rejected) | §4.2, §9.2 |
| Q4 | Release-note scope | **Approved: v1.2.1, v1.2.2, v1.3.1, v1.3.2 only.** The other 17 recorded as investigated, not included. No silent expansion | §4.6, §8 S2 |
| Q5 | `markdown-it-py`, `PyYAML` | **Approved conditionally**: only if genuinely required; exact pins; purpose explained; tests for the behaviour they support | §3 |
| Q6 | Site variables | **Approved**: neutral markers; current-version values never substituted | §4.4 |
| Q7 | Release-note cleanup | **Approved**: retrieval text drops contributor names and verbose URLs, keeps PR numbers; raw source unchanged and independently verifiable | §4.6 |
| Q8 | Release-note licence | **Stricter treatment**: not classified as MIT; `licence_status = unverified` with source URL, release id, retrieval date, content hash and attribution; raw copies local only; not in any publicly distributed corpus until established | §4.6, §5, §7 |
| Q9 | API HTML pages | **Approved**: extract with structure, headings, code and API information preserved; parsing method documented | §4.5 |
| Q10 | Commits | **Approved**: per stage, after showing files, tests and evidence and receiving approval; no push without explicit approval | §8 |
| Q11 | GitHub Release backup | **Deferred to S6**; S6: **deferred beyond RAG-M3** (S6-D6, `RAG-M3_S6_ci.md` §5) | §7 P1 |

### 9.2 Q3, as asked (decided; see §9.1)

**Question.** What should the chunker do when a **single block** (one fenced code block, or one table) is longer than the 512-token cap on its own, so it can't fit in one chunk even with nothing else in it?

**Context.**
- M1 design §2 says chunking must "never split inside a table row or code block", and sets a 512-token cap.
- Those two rules conflict only for such over-cap blocks.
- How often this happens in the real corpus is **unknown**; S3 will count it before anything is implemented.
- Token counts are approximate until RAG-M5 fixes the embedder (§4.2).

**Alternatives** (each can be chosen separately for code blocks and for tables):

| | Alternative | Effect on code blocks | Effect on tables | Consistent with M1 wording? |
|---|---|---|---|---|
| **A** | **Split between lines or rows**, flagged `split_inside_block = true`. Tables repeat their header row in every part; code parts are marked "continued" | A long listing is cut between lines; a part may lack context such as a `CREATE TABLE` defined earlier | No row is ever cut; every part is a valid table with its header | **Tables: yes** (no row is split). **Code: no** (splits inside a code block) |
| **B** | **Keep the block whole** in one over-cap chunk, flagged `oversize = true`; reported | Listing stays intact | Table stays intact | **Yes** for both, but **breaks the 512 cap**. At RAG-M5 an embedder with a smaller input limit would truncate the tail silently unless handled then |
| **C** | **Exclude the block** from chunks; record it in the build report | Content lost from retrieval | Content lost from retrieval | Yes, but loses information; risky for function tables |
| **D** | **Keep whole (as B) and also emit overlapping line/row windows** as extra chunks pointing to the whole block | Intact copy plus searchable windows | Same | Partly. Duplicates content, so retrieval metrics must deduplicate; more complex |

**My recommendation.** Treat the two block types differently, and decide finally only after the S3 frequency count:
- **Tables: A.** Split between rows and repeat the header row. This keeps M1's "never split inside a table row" exactly, and every part stays a self-contained table.
- **Code blocks: B for now.** Keep each listing whole with `oversize = true`, listed in the build report. At RAG-M5, once the embedder and its true token limit are known, any listing that still exceeds the limit comes back to you as a specific list with options (for example A at blank-line boundaries). Nothing is truncated silently.
- **If S3 finds zero over-cap blocks of a type**, the decision for that type is moot and nothing special is implemented.

I recommend against C (information loss) and D (duplication complicates evaluation) unless S3 shows a large number of cases.

### 9.3 Remaining unresolved issues

| # | Issue | Proposal | Needs |
|---|---|---|---|
| U1 | Q3 | **Decided** (§9.1) | — |
| U2 | Reproducibility of release notes. Because raw text isn't committed (Q8), a fresh build depends on GitHub still serving the same text. If upstream edits a body, its hash no longer matches and the build stops | **Approved conditionally:** raw text in the ignored cache only; fetched by the recorded release id; every fetch verified against the committed hash; on mismatch stop and show the owner the difference; never silently accept; if unavailable, fail clearly with the reason | — |
| U3 | IR-5's "done when" in M1 (D14) says the M1 manifest §1.2 table is filled from actual fetches. The owner now says the M1 manifest must not change | **Approved:** release-note evidence goes in the M3 corpus manifest; no RAG-M1 document is modified | — |
| U4 | PyYAML may not be needed | S3 front-matter scan decides; result reported before adding it | Owner: keep approach; decided after S3 inspection |
| U5 | Exact handling of the 24 `{% include %}` files and the 5 `index.md` landing pages | Decided per file at S3 from the actual content, listed for review | Owner: keep approach; reviewed individually at S3 |
| U6 | Reading GitHub Actions results on the private repo (for S6 evidence) | Owner pastes or screenshots the run, or approves installing `gh` | Owner: screenshots or pasted results |

## 10. Acceptance tests and how each is demonstrated

| Brief requirement | Test | Demonstrated by |
|---|---|---|
| Every chunk has a valid version | Pydantic enum; `validate` counts `version_status`; unresolved never in `chunks/<v>.jsonl` | Unit tests + `validate` on the real build |
| Valid source URL and path | `source_path` exists in the manifest; URL matches the canonical pattern; optional sampled HTTP 200 check (20 URLs, not in CI): **skipped (S6-D7)** | Unit + validate; sampled check output shown once (not run: S6-D7) |
| Section path or documented fallback | Non-empty; `section_path_fallback` counted | Unit (heading-less fixture) + validate |
| Content hashes match content | Recompute sha256 for every chunk | validate |
| Stable chunk IDs across runs | Build twice, byte-identical JSONL and manifest | CI `corpus-rebuild` + local |
| Correct version association | Docs: path prefix `docs/X.Y` equals `version`. Posts and releases: mapping table, title-contains-version test | Unit + validate |
| Changelog-to-version association | Every change-source chunk has `introduced_in` within its series; compatibility-rule unit tests | Unit |
| Licence metadata present | 100% of chunks have `licence_status` and attribution. `licence` is non-null exactly when `licence_status = verified`. Every release-note chunk is `unverified` and `redistributable = false`. No release-note text in tracked files | validate + unit + `git grep` check |
| Hierarchy preserved, API entries unmerged (R1) | Every `api_entry` has exactly one unit; `merged_section_paths` reconstruct the original paths; no merged chunk contains an API entry | Unit fixtures + validate |
| Site variables not substituted | Renderer fixtures; no `_config.yml` value introduced | Unit |
| Manifest completeness | Every included file has hash, version, kind and licence; every excluded file has a reason; per-version `content_hash` equals M1 §1.1 | validate + unit |
| Reproducible reconstruction | Fresh checkout in CI: fetch, verify, build, validate | GitHub Actions run |
| Changed upstream detected | Tampered cache file makes `verify` fail with the path; an edited release body (cache or upstream) is detected by hash and stops the build; `check-upstream` reports drift | Unit tests (fixtures); `check-upstream`: `tests/unit/corpus/test_upstream.py` (injected fake fetchers, no network) |
| Unresolved provenance handled safely | Unmapped post goes to `unresolved.jsonl` and nowhere else | Unit |
| Empty and malformed documents | Empty page gives no chunk (reported); unclosed fence, bad front matter or broken HTML give a recorded parse error, never a crash or a silent drop | Unit fixtures |
| Duplicate content | Identical text in two versions gives distinct IDs and is counted in a duplicate report. No cross-version deduplication, because the duplicates are the version-confusion signal M1 measures | Unit + build report |
| Unusually long sections | > cap splits with overlap; over-cap single block is flagged | Unit fixtures + build report |
