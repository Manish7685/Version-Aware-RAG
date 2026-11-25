# RAG-M3 S4: Section Tree, Chunker and Stable IDs

Status: **complete (2026-11-21). Design approved by the owner (decisions S4-D1 to S4-D12); S4a, S4b, S4c and S4d each reviewed and approved; committed as one S4 commit (S4-D7).** S4 runs in four sub-stages (S4a–S4d), each reviewed before the next; one commit after S4d (S4-D7). S5 and S6 are out of scope.

This document records the S4 design. It builds on `RAG-M3_plan.md` §4–§5 (chunking design, refinement R1, Q3) and on the S3/S3b parser (`RAG-M3_S3_parsing.md`). Where it refines the plan, the refinement is named and owner-approved. RAG-M1 documents are not changed.

## 1. Inputs and scope

- **Input:** S3's `ParsedDocument` for every included page (2,022: 2,005 docs pages including the 2 migration guides, and 17 release posts), and, in S4d, the 4 GitHub release-note bodies from the ignored cache. **S4 never re-reads raw Markdown for docs or posts;** it consumes S3 blocks only.
- **Output:** a section tree per page, chunks, and an empty-section record. S4c writes the chunk files, a committed chunk manifest and a committed report (§13a; the owner's S4c authorisation, 2026-11-21, moved chunk-file writing into S4). **S5 keeps** `unresolved.jsonl`, the build report, `vrag corpus validate`, the verify-parse-chunk pipeline that refuses to build on any hash mismatch, and the inclusion of the S4d release-note chunks in the build.
- **Not in S4:** embeddings, indexes, retrieval, reranking, generation, benchmark questions, S5, S6.

## 2. Survey of the real corpus (read-only, 2026-11-21)

| Measure | Value |
|---|---|
| Sections with content | 38,488 (posts 223) |
| API entries | 10,719: 8,519 code-span headings, 47 call-signature headings, 2,121 Sphinx `dt` entries, 32 function-table sections. **S4a correction: 10,699, with 12 function-table sections. After S4-D8: 10,670, with 18 call-signature headings.** The survey script's cell check was looser than the approved rule (it accepted `Description` alone); the 20 extra were ODBC handle tables (`guides/odbc/general.md`: Environment, Connection, Statement, Descriptor, in each of the 5 versions), which are not function documentation |
| Prose sections | 27,769, of which 18,050 are under 64 approximate tokens |
| Structural headings (only subsections) | 1,660 |
| Empty sections (no content, no subsections) | 193: 109 `railroad_diagram_removed`, 84 `no_source_content` (all 193 are nodes in the tree and records; §9.1) |
| Sections over 512 tokens | 698 (623 prose, 75 API) |
| Single blocks over 512 tokens | 340: 164 code, 147 tables, 25 lists, 4 Sphinx API blocks |
| Over-cap tables whose rows map 1:1 to source lines | 147 of 147 |
| Repeated section paths within a page | 0 |
| H1 headings different from the page title | 2 (1.5 `sql/query_syntax/unnest.md`) |
| Estimated chunks (rough simulation of R1, not the implementation) | about 31,500 (1.2 ≈ 4.7k, 1.3 ≈ 5.5k, 1.4 ≈ 5.7k, 1.5 ≈ 6.4k, 2.0 ≈ 9.0k, posts ≈ 0.2k) |

The plan's 9–13k estimate (§6) assumed H2/H3 chunking. R1 (all levels, unmerged API entries) gives about three times as many, smaller chunks. **The number is not reduced artificially** (owner, 2026-11-21); its retrieval effect is measured at RAG-M5. Build outputs (about 60–90 MB) go to the git-ignored `data/build/`.

## 3. Owner decisions (2026-11-21)

| # | Decision |
|---|---|
| S4-D1 | **Chunk IDs: option B.** Canonical JSON key with `occurrence` instead of the page position; `unit_ordinal` kept as metadata only (§6) |
| S4-D2 | **Empty sections: option A.** No chunk; all 193 recorded with version, path, section path, line range and reason. Structural headings stay in descendants' paths. Empty headings are never attached to unrelated sections |
| S4-D3 | **Over-cap lists** split between top-level items; an item is never split; an over-cap item stays whole (`oversize`, `list_item`). **Over-cap Sphinx API blocks** stay whole (`oversize`, `api_entry_block`), never merged. Q3 (tables, code) unchanged |
| S4-D4 | **Content** = the section's own heading as a Markdown heading, then its blocks. Ancestors only in `section_path`. Later parts don't repeat the heading. Table groups repeat the header (Q3). Breadcrumbs deferred to RAG-M5 |
| S4-D5 | **GitHub release notes in S4d**, from the ignored cache, hash-verified against S2 first, `source_kind = github_release`, Q7 as a recorded transformation, licence uncertain / unverified / not redistributable, text never committed. Stop on any missing or changed source |
| S4-D6 | **`configs/chunking.toml`**, separate from `corpus.toml` (the manifest's `config_sha256` is unaffected). Holds sizes, overlap, merge threshold, boundary levels 1–6, oversize rules and the two migration-guide paths |
| S4-D7 | **One commit after S4d**; review and owner approval after each of S4a, S4b, S4c, S4d; no intermediate commits or pushes |
| S4-D8 | **Call-signature rule tightened** (S4a review): a call signature is a dotted identifier written directly before the parentheses (`database.get()`). This keeps the 18 real signatures (six Node.js method headings in 1.2, 1.3, 1.4) and makes the 29 former false positives prose (`httpfs (Wasm-Flavored)`, `HTTP(S)`, `Java (JDBC)`, `Server (Online)`, `Delta (and Unity Catalog)`, `` `IN (val1, val2, ...)` (Tuple) ``, `Upsert (Insert or Update)`). API entries: 10,670 |
| S4-D10 | **Q7 identifiers beyond the approved pattern** (S4d): option B with a refinement. Four rules, each with its own transformation code: the approved PR attribution (` (#n)`), a bare PR URL (`#n`), a compare URL between two version tags kept as its compact range (`v1.2.0...v1.2.1`), and any remaining handle (`@[contributor]`). Each rule matches only its pattern; every application is recorded |
| S4-D11 | **Release-note chunk output**: option A. `data/build/chunks/github_releases.jsonl` (git-ignored), a separate committed manifest with hashes, counts and metadata only, and a committed report that withholds all release-note text. S4c outputs stay byte-identical |
| S4-D12 | **Copyright**: nullable; `null` for GitHub release notes, where none is recorded. Never invented |
| S4-D9 | **Railroad provenance** (S4a review): the 205 S3 removal records stay authoritative. Each section node and empty-section record carries `railroad_removal_lines` (the records inside its span); each chunk carries the records of its own content, every record on exactly one chunk or empty record, never repeated across split parts |

## 4. Section tree (S4a)

```text
SectionNode
  path          tuple[str, ...]   (title, H…, H…); the page root is (title,)
  kind          page_root | heading | api_entry
  heading       str | None        heading text (inline Markdown as S3 produced it) or the Sphinx signature
  level         int               0 page root; 1–6 headings; Sphinx entry = enclosing heading level + dt depth
  occurrence    int               0-based index among nodes of this page with the same path, in document order
  line_start    int               heading line (page root: first content line, or 1)
  line_end      int               last line of its own blocks (heading line if none)
  blocks        tuple[Block, ...] its own blocks: up to the next heading or API entry of any level
  children      tuple[SectionNode, ...]
  api           ApiInfo | None    reason, signature, api_id
  state         content | structural | empty
  empty_reason  railroad_diagram_removed | no_source_content | None
```

Rules:
1. **Boundaries:** every heading block of levels 1–6 starts a node. An H1 whose text equals the page title is not a node (plan §4.1); its line is recorded as the root's title line. Other H1s are level-1 nodes. A heading is attached under the nearest preceding node of a lower level; skipped levels are kept as written, never filled in.
2. **Sphinx API entries:** each `api_entry` block is its own node, nested by its `level` (the `dt` depth): a class is the parent of its methods. They hang under the current heading node (2.0 has `<h2>` sections; 1.2–1.5 have none, so they hang under the root). The node's `heading` is the signature; its path element is the `api_id` (plan §4.5).
3. **API detection for Markdown sections (R1):** a heading node is an API entry when
   - its whole heading text is one code span (`` `map_extract(map, key)` ``), reason `heading_code_span`;
   - or its whole heading text is a call signature: a dotted identifier written directly before the parentheses, `^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\(.*\)$` (S4-D8), reason `heading_signature`. An undotted name (`count(x)`), a space before the parenthesis, or a prose parenthetical (`HTTP(S)`, `Java (JDBC)`) is not a signature;
   - or its first block is a header-less table whose first-column cells include `Description` plus at least one of `Example`, `Result`, `Alias`, `Aliases`, `Parameters`, `Signature`, reason `function_table`.

   A heading that is only partly code ("Syntax for `CREATE SECRET`") is not an API entry. `api_signature` is the heading text (without the surrounding backticks for a code span) or the Sphinx signature.
4. **State:** `content` if the node has blocks; `structural` if it has none but has children; `empty` otherwise (no blocks, no children). This matches S3's empty-section definition. A structural node whose children are all empty is accounted for through its children's empty-section records, whose `section_path` contains its heading (0 such nodes in the corpus). A page root without blocks is neither recorded nor chunked (most pages begin with a heading).
5. **Empty reason:** `railroad_diagram_removed` if an S3 `html_railroad_removed` transformation lies in `[line_start, next heading line)`; otherwise `no_source_content`.
6. **Missing title:** the root path element is the `source_path` and `section_path_fallback = true` (0 pages today).
7. **Tree-level preservation:** every S3 block belongs to exactly one node, in document order; every heading block is a node (or the skipped title H1).

## 5. Chunk schema (S4b)

Plan §5 fields, made concrete. Every field is required unless marked nullable.

| Group | Field | Type / values |
|---|---|---|
| Identity | `chunk_id` | `c-` + 20 hex (§6) |
| | `library` | `"duckdb"` |
| | `version` | `1.2 \| 1.3 \| 1.4 \| 1.5 \| 2.0` |
| | `behaviour_package` | `1.2.2 \| 1.3.2 \| 1.4.5 \| 1.5.6 \| 2.0.1` |
| | `version_status` | `resolved` (unresolved sources never become chunks; S5 writes them to `unresolved.jsonl`) |
| Source | `source_type` | `documentation` (docs pages) \| `changelog` (posts, migration guides, GitHub releases) |
| | `source_kind` | `docs_page \| release_post \| migration_guide \| github_release` |
| | `introduced_in` | nullable; posts: configured release; migration guides: `2.0.0`; GitHub releases: tag version |
| | `source_path` | repository path, or `releases/tag/vX.Y.Z` |
| | `source_url` | canonical page URL, post URL, or release URL |
| | `source_repo` | `duckdb/duckdb-web` or `duckdb/duckdb` |
| | `source_commit` | pinned commit; GitHub releases: the release's `tag_commit` as recorded in S2 |
| | `source_git_blob` | nullable (GitHub releases have none) |
| | `source_content_hash` | sha256 of the raw source bytes, from the manifest |
| | `release_id`, `source_published_at`, `source_retrieved_at` | nullable; GitHub releases: S2's `release_id`, `published_at`, `first_retrieved_at`; posts: publish date only |
| Structure | `doc_title` | str |
| | `section_path` | non-empty list[str]; the unit's own path, or the parent's for merged chunks |
| | `section_path_fallback` | bool |
| | `heading_level` | int; 0 for the page root unit; parent's level for merged chunks |
| | `unit_kind` | `api_entry \| prose \| prose_merged` |
| | `merged_section_paths` | list[list[str]]; `[]` unless merged |
| | `unit_ordinal` | int; position of the (first) unit in the page. **Metadata only, not in the ID** |
| | `occurrence` | int (§6) |
| | `part_ordinal`, `part_count` | int; 1-based; 1/1 unless split |
| | `line_start`, `line_end` | int; source lines covered by the chunk's own (non-overlap) content |
| API | `api_signature`, `api_id`, `api_reason` | nullable; API entries only (`api_id` for Sphinx) |
| Content | `content` | str (§7) |
| | `content_hash` | sha256 of `content` as UTF-8 |
| | `token_count_approx`, `char_count` | int; tokens of the whole `content` (heading, repeated header and overlap included) |
| Split, overlap, oversize | `split_inside_block` | bool; true for table row groups and list item groups |
| | `table_header_repeated` | bool; true for table row groups after the first |
| | `overlap_prev_blocks`, `overlap_prev_tokens` | int; 0 unless the part starts with overlap |
| | `overlap_line_start`, `overlap_line_end` | nullable int |
| | `oversize` | bool |
| | `oversize_reason` | nullable: `code_block \| table_row \| list_item \| api_entry_block` |
| Audit | `has_code`, `has_table`, `has_unrendered_site_variable`, `has_liquid_branches` | bool |
| | `extraction` | `markdown \| sphinx_html` |
| | `transformations` | list[str]; chunk-level recorded transformations (S4d: `q7_pr_attribution_shortened`) |
| | `railroad_removal_lines` | list[int]; S4-D9: lines of the S3 railroad-removal records in this chunk's own content; for a split unit, each record goes to the first part whose content reaches its line |
| Licence | `licence`, `licence_status`, `licence_applicability`, `redistributable`, `repository_licence`, `licence_basis`, `copyright`, `attribution`, `copyright_date_discrepancy` | Docs pages and migration guides: `MIT`, `verified`, `null`, `true` (the S1 classification, an approved project assumption, not a new legal determination). Posts: copied unchanged from the manifest record (`null`, `unverified`, `uncertain`, `false`, `MIT`, …). GitHub releases: `null`, `unverified`, `uncertain`, `false` (Q8, S4-D5), attribution "DuckDB release notes, github.com/duckdb/duckdb, release <tag>" |
| Reproducibility | `parser_version`, `chunker_version`, `chunking_config_sha256` | str |

## 6. Stable chunk IDs (S4-D1)

**Key.** For every chunk, the JSON array

```text
[library, version, source_kind, source_path, section_path, merged_section_paths,
 occurrence, part_ordinal, content_hash]
```

with `section_path` a list of strings, `merged_section_paths` a list of lists of strings (`[]` if not merged), `occurrence` and `part_ordinal` integers, and `content_hash` the 64-character lowercase hex sha256 of `content`.

**Canonicalisation.** `json.dumps(key, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`, encoded as UTF-8. Strings are used exactly as produced (no Unicode normalisation, no trimming, no case folding); there is no string joining anywhere.

**Hash.** `chunk_id = "c-" + sha256(canonical_bytes).hexdigest()[:20]` (80 bits).

**`occurrence`.** For each page, chunks are grouped by their identity `(section_path, merged_section_paths)`; within a group, each distinct unit (or merge run) gets 0, 1, 2… in document order. Parts of one split unit share its occurrence and differ by `part_ordinal`. Today every page has 0 repeated paths, so every occurrence is 0.

**Properties and tests.**

| Property | How it holds |
|---|---|
| No randomness, no processing-order dependence | The key is a pure function of one page's content and the config |
| Versions distinct | `version` is in the key; identical text in two versions gives different IDs (test) |
| Unrelated insertion leaves existing IDs unchanged | The key has no page position. Test: insert a new section elsewhere on a page; every other chunk keeps its ID |
| Content change changes affected IDs | `content_hash` is in the key. Test: edit one section; exactly the chunks containing the edit change |
| Duplicate paths distinguished | `occurrence` (test with two identical headings under one parent) |
| Collisions | Corpus-wide uniqueness check; a collision fails the run with both chunks named (test with a forced collision through a truncated-hash hook) |

**Documented, predictable exceptions to "unrelated insertion".** An insertion is not unrelated, and the listed IDs change, when it:
- joins or breaks an R1 merge run (that merged chunk's content changes);
- lands inside a split section (that part's content changes, and the next part's overlap may change);
- has the same path as a later section (that later section's `occurrence` increases).

## 7. Chunk content (S4-D4)

- **Unit content:** the unit's own heading as a Markdown heading line (`#` × level, then the heading text exactly as S3 produced it), a blank line, then its blocks' text joined by blank lines. The page-root unit has no heading line. A Sphinx API entry's first line is its signature as a level-`min(level, 6)` heading, then its body.
- **Merged chunk:** the units' contents in document order, joined by blank lines (each unit keeps its own heading line).
- **Split parts:** part 1 starts with the heading; later parts start with their overlap (if any) and continue with their own pieces; the heading is not repeated.
- **Table row groups:** each group is the original header row and delimiter row, then its rows, exactly as in S3's table text.
- **List item groups:** the original lines of the selected top-level items, unchanged. Ordered lists keep their original numbers, so a later group may start at `6.`. **No synthetic context text is added** (S4-D3 "only where structurally necessary": a top-level item's nested sub-items always travel with it, which is the only structural context a list item needs).
- Block text is never edited by the chunker. S4d's Q7 transformation is the only content change, applied to release-note text and recorded per chunk.

## 8. Merge and split algorithm (S4b)

Token count = `approx_tokens` (S3's dependency-free `\w+|[^\w\s]` count), applied to the exact text concerned. Parameters come from `configs/chunking.toml` (§10).

1. **Units.** Every `content` node is one unit, in document order. `structural` nodes produce nothing (their headings live in descendants' paths); `empty` nodes go to the empty-section record (§9).
2. **Merge (R1).** A unit is *mergeable* if it is not an API entry and its unit content has fewer than `merge_below` (64) tokens. Walking units in document order, a run collects consecutive mergeable units with an identical parent path and no other unit between them (a unit's subsections break contiguity), while the run's joined content stays at or below `target` (350). A run of one gives a `prose` chunk; a longer run gives one `prose_merged` chunk (`section_path` = parent path, `merged_section_paths` = each unit's path, `heading_level` = parent level). A merged chunk is never split (it is at most 350 tokens by construction).
3. **Small units.** A unit whose content is at most `hard_limit` (512) tokens is one chunk.
4. **Pieces.** A unit over the limit is cut into pieces at block boundaries; the heading line belongs to the first piece:
   - an ordinary block is one piece;
   - a table over the limit becomes row groups: header and delimiter plus as many consecutive rows as fit within `target`, never more than `hard_limit`; a row is never split; a single row whose header-plus-row exceeds the limit becomes its own `oversize` piece (`table_row`);
   - a list over the limit becomes groups of consecutive top-level items packed the same way; an item is never split; a single over-limit item becomes an `oversize` piece (`list_item`);
   - a code block over the limit is one `oversize` piece (`code_block`), never truncated;
   - a Sphinx API block over the limit is one `oversize` piece (`api_entry_block`);
   - an API Markdown unit over the limit is split like prose at its block boundaries; every part keeps the API path, `unit_kind = api_entry` and `api_signature`.
5. **Packing.** Pieces are packed greedily in order. A part closes when it has reached `target` tokens or the next piece would take it over `hard_limit`. Row groups, item groups and oversize pieces always form a part on their own.
6. **Overlap.** A part that follows an ordinary-block part starts with that part's trailing whole blocks, as many as fit within `overlap_ratio × target` (0.15 × 350 = 52 tokens), provided the new part stays within `hard_limit` and still contains at least one new piece. No overlap is taken from, or added to, a row group, an item group or an oversize part, and none crosses units. Overlap is recorded (`overlap_prev_blocks`, `overlap_prev_tokens`, overlap lines) and excluded from `line_start`/`line_end`.
7. **Implementation notes (S4b).**
   - *Merge contiguity* is read strictly: two units merge only if no other section of any state (including an empty or structural heading) lies between them. On the real corpus this affects 0 pairs.
   - *The heading line counts* when deciding whether the first block is over the limit (A-S4-3). In 9 real units the heading tips a table or list over 512; they split into row or item groups. No code block is affected (oversize code stays exactly 164).
   - *Line ranges:* table row groups carry exact row lines (all 147 over-limit tables map rows 1:1 to source lines). List item groups carry their list block's full line range, because S3 records no per-item lines.
   - *Header-less tables* (Kramdown pipe rows) have no header to repeat; their row groups set `table_header_repeated = false`.
8. **Order.** Chunks are ordered by version, source path, unit source position (heading line, or first block line for the page root), part. Nothing is reordered within a page. (Tree pre-order equals source order except when a Sphinx section has loose text after nested entries, which occurs 0 times in the corpus; ordering by source position covers it.)

**Content-preservation check (per page, in tests and in S4c over the whole corpus).**
- Removing each chunk's overlap prefix, repeated table header rows and the separators, the chunks' contents, in order, reproduce exactly: every unit's heading line, every S3 block text, and, for split tables and lists, every row and every top-level item exactly once, in source order.
- Every heading node appears in some chunk's `section_path` or `merged_section_paths`, or in the empty-section record; the skipped title H1 is accounted for as the root's title line.
- Every included page yields at least one chunk or is entirely recorded (headings only).

## 9. Empty sections and special cases

| Case | Policy |
|---|---|
| Structural headings (1,660) | No chunk; heading in descendants' `section_path`; counted |
| Empty sections (193) | No chunk (S4-D2). Record: `version`, `source_kind`, `source_path`, `section_path`, `line_start`, `line_end`, `reason` (`railroad_diagram_removed` 109, `no_source_content` 84). Never attached to another section |
| Diagram-only sections | The railroad case above. Image alt text S3 kept is ordinary text |
| Very short prose | R1 merge; otherwise a chunk on its own |
| API entries | One unit each, never merged; split at block boundaries only if over the limit; Sphinx block over the limit stays whole (`api_entry_block`) |
| Version-dependent content | Every chunk takes its source's version. No cross-version merge or deduplication. Liquid both-branch blocks stay atomic, `has_liquid_branches = true`. `[site:…]` markers set `has_unrendered_site_variable`. Version statements in prose are not interpreted (RAG-M4) |
| Migration guides | `docs/2.0/operations_manual/migration.md`, `docs/2.0/clients/python/migration.md`: chunked once, as `source_kind = migration_guide`, `source_type = changelog`, `version = 2.0`, `introduced_in = 2.0.0`, not also as docs pages. Licence as docs pages |
| Release posts | `release_post`, `changelog`, version = release series, `introduced_in` = release; licence fields from the manifest; text only in memory or `data/build/` |
| Excluded pages (26) | No chunks; S3 reasons carried into the S4 report |

### 9.1 Railroad diagrams in the tree (clarification, S4a review)

S3 removed 205 empty railroad-diagram containers (`<div id="rrdiagram"></div>`), each recorded as an `html_railroad_removed` transformation with its source line. The diagram is drawn by JavaScript from data outside the docs, so the container holds no text. In the S4a tree, every one of the 205 removals falls in exactly one node:

| Where the removal falls | Removals | How it is represented |
|---|---|---|
| A heading section with nothing else | 109 | An `empty` heading node: path, level and heading line kept, `empty_reason = railroad_diagram_removed`, one removal each; an empty-section record with version, path, section path, line range and reason. No chunk (S4-D2) |
| A heading section that also has text, code or tables | 70 | A `content` node with all its blocks; it is chunked normally in S4b |
| The page-root intro, which also has content | 26 | The `content` page-root unit; chunked normally |

Nothing is lost: no text existed in the containers, every heading is kept, every removal stays in S3's transformation record with its line, and the 109 sections whose only content was a diagram carry that reason on their node and record. The 84 `no_source_content` sections are separate: their headings have no content in the source at all. Tests: T5 (fixtures for both cases) and T13 (all 205 removals traced on the real corpus).

## 10. Configuration (`configs/chunking.toml`, S4-D6)

```toml
chunker_version = "s4-1"
target_tokens = 350
hard_limit_tokens = 512
merge_below_tokens = 64
overlap_ratio = 0.15
boundary_levels = [1, 2, 3, 4, 5, 6]
skip_h1_equal_to_title = true
id_hex_chars = 20

[api_detection]
function_table_first_cells = ["Description"]
function_table_other_cells = ["Example", "Result", "Alias", "Aliases", "Parameters", "Signature"]

[oversize]   # owner Q3 and S4-D3; recorded, not tunable away
table = "split_between_rows_repeat_header"
code = "keep_whole_flag"
list = "split_between_top_level_items"
api_entry_block = "keep_whole_flag"

[[migration_guides]]
path = "docs/2.0/operations_manual/migration.md"
introduced_in = "2.0.0"

[[migration_guides]]
path = "docs/2.0/clients/python/migration.md"
introduced_in = "2.0.0"
```

The loader validates the values (positive sizes, `merge_below < target < hard_limit`, boundary levels exactly 1–6, migration-guide paths present in the manifest under `docs/2.0/`). Its sha256 is recorded on every chunk.

## 11. GitHub release notes (S4d, S4-D5)

- Read the 4 bodies only from `data/cache/github_releases/`; before any processing, check each release id and sha256 against the S2 manifest records. A missing, changed or unverifiable body stops the run and names the release; nothing is omitted silently.
- Parse through S3's Markdown path with `source_kind = github_release` (a small, tested parser extension; docs and posts output unchanged).
- Q7 as recorded transformations (S4-D10), applied in this order to text outside code blocks and inline code spans; each occurrence is recorded with its code, its source line and a detail that holds only a PR number or a version range (never a name):
  1. `q7_pr_attribution_shortened`: ` by @user in https://github.com/duckdb/duckdb/pull/<n>` becomes ` (#<n>)` (the approved Q7 pattern);
  2. `q7_pr_url_shortened`: a bare `https://github.com/duckdb/duckdb/pull/<n>` (not a sub-page, not another repository) becomes `#<n>`;
  3. `q7_compare_url_shortened`: `https://github.com/duckdb/duckdb/compare/vA...vB` between two version tags becomes `vA...vB`;
  4. `q7_contributor_handle_masked`: any remaining `@handle` (not an e-mail address, not already masked) becomes `@[contributor]`.

  The raw cache file is never modified; the bodies are re-hashed after processing.
- Version from the tag (`v1.2.1` gives series `1.2`, `introduced_in = 1.2.1`).
- Licence `null`, `unverified`, `uncertain`, `redistributable = false`; provenance from S2 (URL, release id, retrieval time, body hash, attribution).
- No release-note text in any tracked file; committed reports withhold it as for posts; text-bearing outputs only in `data/build/`.

### 11.1 S4d implementation (2026-11-21)

- **Module** `src/version_aware_rag/corpus/release_chunks.py`; **CLI** `vrag corpus releases chunk [--check]`; parser change: `github_release` added to `SourceKind` only (A-S4-5: docs and post output unchanged, so `PARSER_VERSION` stays `s3b-1`; the S3 reports regenerate byte-identical and `vrag corpus chunk --check` passes).
- **Verification before processing:** each cached body's sha256 and size against the S2 config (`verify_releases`); the manifest record's id, hash, size and tag commit against the config; the S2 fetch record's id and hash against the manifest. Any problem stops the run and names the release. A body that triggers a Liquid transformation, or does not parse as a content page, also stops it.
- **Measured on the 4 bodies:** 297 PR attributions, 2 bare PR URLs, 4 compare URLs, 2 remaining handles; no "New Contributors" lines (A-S4-4 settled). CRLF line endings are normalised by the parser; Q7 records carry exact source lines when a block maps line by line to its source (its first lines non-blank, the rest of its range blank), otherwise the block's first line.
- **Provenance:** `source_path = releases/tag/<tag>`; S2's source URL, release id, tag commit, publish and first-retrieval times and body sha256; attribution from S2. The bodies have no title and S2 records no release name, so the approved fallback applies (section paths rooted at the source path, `section_path_fallback = true`). `repository_licence = MIT` refers to the duckdb/duckdb repository (plan §7 L2), not the release text.
- **Outputs (S4-D11):** `data/build/chunks/github_releases.jsonl` (git-ignored); `data/manifest/release_note_chunk_manifest.json` (committed: hashes, counts, per-release verification and Q7 counts); `docs/reports/RAG-M3_S4d_release_notes.md` (committed: verification table, Q7 counts, chunk metadata without content or section paths). S4c's validator lists `github_releases.jsonl` as a known sibling file (validated by S4d), so `vrag corpus chunk --check` still passes; any other unexpected file is still reported.
- **Not S4d:** merging the release-note chunks into the build, `unresolved.jsonl`, the build report and `vrag corpus validate` stay in S5.

## 12. Acceptance criteria

1. All 2,022 included pages (plus the 4 release notes after S4d) are processed with 0 crashes; every chunk validates against the model.
2. Version correctness: docs and migration-guide `source_path` starts with `docs/<version>/`; posts use their configured release's series; release notes their tag's series; `introduced_in` set exactly for change sources.
3. `section_path` is non-empty and starts with the title (or the fallback is flagged); `merged_section_paths` reproduce the original paths.
4. No chunk contains more than one API entry; no API entry is merged.
5. Every chunk is at most 512 tokens unless `oversize`; an oversize chunk contains exactly one oversize piece of an allowed reason; all are listed in the S4 report.
6. Every table row group starts with the header and delimiter; rows are never split and appear exactly once, in order. Every list item group contains whole top-level items, exactly once, in order.
7. Overlap: whole blocks only, at most 15% of target, within one split unit, recorded; no chunk consists only of overlap; none touches row groups, item groups or oversize parts.
8. Content preservation (§8) passes for every page; the 193 empty sections are recorded with the stated reasons and counts.
9. IDs: canonicalisation as §6; unique corpus-wide; identical across two runs; insertion, edit, duplicate-path and version tests pass; a forced collision fails.
10. Determinism: two full runs give byte-identical canonical chunk serialisations (sha256 compared) and byte-identical reports.
11. Licence: docs and migration guides `MIT`/`verified`/`true`; posts and release notes `null`/`unverified`/`uncertain`/`false`. A scan finds no post or release-note text in any tracked file.
12. Unchanged: S3 parser output for docs and posts, S3 reports, the corpus manifest, the pinned commit, S2 evidence, RAG-M1 documents.
13. pytest, ruff, strict mypy and all 11 pre-commit hooks pass; whitespace checks pass on tracked and untracked files.

## 13. Sub-stages

| Stage | Content | Review evidence |
|---|---|---|
| S4a | `chunking.toml` and loader; `sections.py` (tree, API detection, states, empty reasons); unit and real-corpus tree tests | Tree counts per version and kind; the 193 (109/84), 1,660 structural, 10,719 API entries by reason; preservation at tree level |
| S4b | `chunk.py` (units, merge, pieces, packing, overlap, oversize, content, metadata, IDs); unit tests | Fixture results; ID tests; preservation tests |
| S4c | Whole-corpus build (`vrag corpus chunk [--check]`), `vrag corpus chunk-page PATH [--json]`, chunk files, chunk manifest and report (§13a); integration and property tests | Output files with sizes and hashes, counts per version and kind, size histogram, oversize and empty lists, validation, two-build hash comparison |
| S4d | Release-note verification, parsing, Q7, chunks; tests | Hash verification output; Q7 counts; licence fields; no text in tracked files |

## 13a. S4c outputs, CLI and validation

**Module.** `src/version_aware_rag/corpus/chunk_output.py` (kept separate from the `build.py` that plan §3 reserves for S5).

**Outputs.**

| Path | Tracked | Content |
|---|---|---|
| `data/build/chunks/1.2.jsonl` … `2.0.jsonl` | no (git-ignored) | Every chunk of that docs series, including the series' migration-guide and release-post chunks (a post's `version` is its release series). One JSON object per line with sorted keys, UTF-8, LF, final newline; ordered by `source_path`, then position in the page |
| `data/build/chunks/empty_sections.jsonl` | no | The 193 empty-section records, ordered by version, source path, line |
| `data/manifest/chunk_manifest.json` | yes | Schema 1. Generator, pinned commit, parser and chunker versions, chunking-config sha256, corpus-manifest sha256; each output file's path, record count, bytes and sha256; summary counts. **No content** |
| `docs/reports/RAG-M3_S4_chunking.md` | yes | Inputs and output hashes, counts by version and source kind, size distribution, the oversized-chunk list, the empty-section list, licence classes, seven samples. **Release-post text, headings and section paths are withheld** |

**CLI.**
- `vrag corpus chunk`: parses the corpus from the verified cache, chunks it, writes the six files, the chunk manifest and the report, then re-reads the written files and validates them. Exit 0, or 1 if validation finds a problem, or 2 on an input error.
- `vrag corpus chunk --check`: rebuilds in memory, compares every output byte for byte with the files on disk, validates them, and writes nothing.
- `vrag corpus chunk-page PATH [--json]`: one docs page or release post, parsed exactly as the corpus build does (hash-checked); prints its chunks and empty sections. A page outside the manifest exits 2; an excluded page exits 0 with its exclusion reason. Output goes to the local terminal only.

**Validation of the generated files** (`chunk_file_problems`, used by both commands and the tests):
- the six files exist and no other file is in `data/build/chunks/`;
- each file's sha256, size and record count equal the chunk manifest;
- every line validates as a `Chunk` (or `EmptySection`); its version matches its file; lines are ordered by source path;
- `content_hash`, `token_count_approx` and `char_count` match the content; `chunk_id` equals the ID recomputed from the identity key; IDs are unique across all files;
- no chunk over 512 tokens without `oversize`; a non-redistributable chunk has no licence; release-post chunks keep `redistributable = false` and `licence_applicability = uncertain`;
- chunk counts per version equal the manifest.

**Reproducibility.** Two complete builds give byte-identical files (all six outputs, the manifest and the report compared by sha256), and `chunk --check` passes against them.

## 14. Assumptions still open

| # | Assumption | When it is settled |
|---|---|---|
| A-S4-1 | The R1 call-signature rule and the function-table cell list are exact enough | **Settled at the S4a review:** S4-D8 tightened the signature rule (47 to 18); the function-table rule as approved gives 12 (§2) |
| A-S4-2 | Sphinx API `level` = enclosing heading level + `dt` depth, capped at 6 for the content heading line | **Confirmed at the S4a review** (for example `duckdb.Appender.append_row` is level 4 under the H2 `Connection`) |
| A-S4-3 | For the cap, tokens are counted over the chunk's full `content` (heading line, repeated table header and overlap included) | Implemented in S4b (§8 rule 7); for the owner's S4b review |
| A-S4-4 | Identifiers outside the approved Q7 pattern | **Settled (S4-D10):** 0 "New Contributors" lines; 4 compare URLs, 2 bare PR URLs and 2 handles handled by three further recorded rules |
| A-S4-5 | Adding `github_release` to the parser's source kinds must not change any docs or post output | **Confirmed in S4d:** S3 reports byte-identical, S4c `chunk --check` passes; no `PARSER_VERSION` bump |
| A-S4-6 | The 96 railroad removals inside sections with content (§9.1) | **Settled by S4-D9:** `railroad_removal_lines` on nodes, empty records and chunks |

## 15. Test plan (numbered; added 2026-11-21 at the owner's request)

The S4a list below is the one presented for approval, with continuous numbering (T1–T17). S4b–S4d tests are listed now so that the owner's full S4 test list is traceable; they are implemented in their own sub-stages.

### S4a: section tree (`tests/unit/corpus/test_sections.py`, `tests/integration/test_real_chunking.py`)

Unit tests (fixtures):

1. **T1** Nested hierarchy at all six heading levels; each node has its full path.
2. **T2** Skipped levels (H2 then H4) are kept as written, never filled in.
3. **T3** An H1 equal to the title is skipped; a different H1 is a level-1 node.
4. **T4** Content before the first heading is the page-root unit; a page without headings; a missing title gives the source-path root and `section_path_fallback`.
5. **T5** States `content`, `structural`, `empty`; empty reasons `railroad_diagram_removed` (a railroad transformation inside the range) and `no_source_content`; a railroad-only section keeps heading, path, level, line and reason; a section with a diagram and text stays a content section, with the removal still recorded.
6. **T6** API detection: whole-code-span heading, call-signature heading, partly-code heading (not API), header-less function table, ordinary table (not API). S4-D8: the 6 real signature headings are API entries; the 7 former false-positive headings, undotted names, a space before the parenthesis and ordinary prose headings are not.
7. **T7** Sphinx entries: class above methods, under an H2 and under the root; the level rule; signature as heading; `api_id` as the path element.
8. **T8** Repeated paths on one page get occurrence 0, 1.
9. **T9** Tree-level preservation: every S3 block in exactly one node, in document order; every heading accounted for.
10. **T10** Determinism: the same input gives equal trees.
11. **T11** Config: the production file loads; invalid sizes, wrong boundary levels, missing migration guides and unknown keys are rejected.

Real corpus (marked `corpus`; counts pinned to the §2 survey and reported, never adjusted, if they differ):

12. **T12** All 2,022 included pages build trees without error.
13. **T13** Empty sections: 193 = 109 `railroad_diagram_removed` + 84 `no_source_content`, the same pages and lines as the S3 report's list; all 205 railroad removals traced (109 empty heading nodes, 70 heading and 26 page-root content sections), each empty railroad node holding exactly its own removal and a matching record.
14. **T14** 1,660 structural headings; 38,488 content sections (posts 223).
15. **T15** API entries by reason: 8,519 / 18 (S4-D8) / 2,121 / 12 (corrected from the survey's 32, §2), total 10,670; the 18 signatures are exactly the six Node.js headings in 1.2, 1.3, 1.4; the 29 former false positives and the 20 ODBC handle sections are prose; 0 repeated paths within a page; exactly 2 non-title H1s, both in 1.5 `unnest.md`.
16. **T16** Tree-level preservation over the whole corpus.
17. **T17** Spot checks: one page in each of the five versions, two release posts, and both migration guides.

### S4b: chunker (unit tests on fixtures)

18. **T18** R1 merge of adjacent small prose siblings; no merge across parents, across an API entry or over the target; merged paths reconstruct.
19. **T19** API entries never merged; an over-limit Markdown API unit splits with its path and signature on every part.
20. **T20** Long-section splitting at block boundaries; packing closes at the target and never exceeds the limit.
21. **T21** Table row groups repeat the header; rows never split; every row exactly once, in order.
22. **T22** An over-limit single table row stays whole (`oversize`, `table_row`).
23. **T23** An over-limit code block stays whole (`oversize`, `code_block`), never truncated.
24. **T24** List item groups; an over-limit single item stays whole (`list_item`); an over-limit Sphinx block stays whole (`api_entry_block`).
25. **T25** Overlap: whole blocks, at most 52 tokens, recorded, never only overlap, never around row groups, item groups or oversize parts.
26. **T26** Content (S4-D4): own heading line, no repeated heading in later parts.
27. **T27** Complete metadata on every chunk, per source kind, including licence fields.
28. **T28** IDs: canonical encoding (golden key and ID), stability across runs, unrelated insertion, content edit, duplicate paths, version separation, forced collision fails.
29. **T29** Content reconstruction per page (§8), with overlap and repeated headers accounted separately.

### S4b real-corpus validation (implemented with S4b at the owner's request)

- **S4b-R1** Every page reconstructs (§8) and follows the rules (512 limit, R1, API, overlap) on the whole corpus.
- **S4b-R2** IDs unique corpus-wide; two full runs give identical serialised chunks.
- **S4b-R3** Invariants from approved figures: 10,670 API units, oversize code 164, Sphinx blocks 4, table rows 0; 193 empty records (109 + 84); railroad records 96 on chunks + 109 on empty records.
- **S4b-R4** Source kinds, versions and licences for docs pages, migration guides and posts.

### S4c: whole corpus (integration and property tests)

30. **T30** Corpus counts pinned after the owner's S4b review (31,658 chunks; per version, source kind and unit kind; API units, merges, splits, overlap, groups, oversize, empty sections, railroad records, non-redistributable chunks, 22 Liquid-branch chunks). Versions, paths, `introduced_in` and licences per source kind are covered by S4b-R4.
31. **T31** The rendered chunk files validate (§13a) against their manifest; ID uniqueness, the 512 rule and reconstruction are covered corpus-wide by S4b-R1 and S4b-R2.
32. **T32** The committed chunk manifest and report equal a fresh build; two full chunking runs give identical serialisations (S4b-R2).
33. **T33** Real examples: `sql/functions/map.md` in each version (API counts and `map_extract`), `timezones.md` 1.2 and `encodings.md` 2.0 (row groups and repeated headers), the 2.0 Python reference (647 API chunks, 4 oversize blocks), 1.5 `unnest.md` (two level-1 sections), Node.js `reference.md` 1.2–1.4 (oversize list item), both migration guides, all 220 post chunks. The railroad `Syntax` page is covered in S4a (T17). The 1.5 ODBC `linux.md` conditionals are inside link URLs, so no chunk is Liquid-flagged there; the flag is pinned corpus-wide in T30 instead.
34. **T34** No release-post text in the committed report or chunk manifest; `chunk-page` exit codes (0 for a page, 2 for a path outside the manifest, 0 with the reason for an excluded page). Unit tests for §13a validation (missing, unexpected, edited, duplicate, wrong-version, hash and licence cases) and report withholding are in `tests/unit/corpus/test_chunk_output.py`.

### S4d: GitHub release notes

35. **T35** Verification against S2 before processing: clean inputs pass; a missing or edited body, a missing or wrong fetch record and a wrong manifest id each stop the run, naming the release (fixtures). Real bodies: all 4 verify; Q7 counts and chunk counts per release pinned; no empty sections.
36. **T36** Each Q7 rule tested on its own with negative cases (another repository, PR sub-pages, issues, branch compares, e-mail addresses, already-masked handles, inline code and code blocks), the order of application, the recorded details and idempotence. Real bodies: every changed line equals the rule output, every other line is byte-identical, no PR URL, compare URL or contributor handle survives in chunk text, and the raw cache files are byte-identical.
37. **T37** Release-note chunk metadata and licence fields (including `copyright = null`); reconstruction and rule checks; the output file validates against its manifest; the committed manifest and report equal a fresh build; no release-note text or contributor name in any committed output (fixtures and real).

### Traceability to the owner's S4 test list

| Owner requirement | Tests |
|---|---|
| Nested heading hierarchy | T1, T2, T7 |
| All heading levels | T1, T3 |
| Empty sections | T5, T13 |
| API entries remaining separate | T6, T7, T15, T19 |
| Adjacent prose merging | T18 |
| Long-section splitting | T20 |
| Table splitting and repeated headers | T21 |
| Oversized table rows | T22 |
| Oversized code blocks | T23 |
| Oversized lists and API blocks (S4-D3) | T24 |
| Overlap behaviour | T25 |
| Complete metadata preservation | T27, T30 |
| Stable IDs, uniqueness, collision tests | T28, T31 |
| Deterministic output | T10, T32 |
| Version separation | T28, T30 |
| No silent content loss / reconstruction | T9, T16, T29, T31 |
| Real pages, all five versions, posts, migration guides | T17, T33 |
| Whole-corpus property checks | T12–T16, T31 |
| Release notes (S4-D5) | T35–T37 |
