# RAG-M1 Research Evidence (revision 3)

Collected 2026-11-20. Pins and reproduction commands are in `RAG-M1_version_manifest.md`.

## 0. How to read this document

### 0.1 Three kinds of evidence, never merged

A verified fact here is always a fact about **one specific thing**. These are three different things and are kept apart:

| Kind | Label | What it establishes | What it does **not** establish |
|---|---|---|---|
| **Package behaviour** | **[VERIFIED-RUNTIME]** | What a specific pinned wheel (version + sha256) returned when the shown query was executed | That the docs describe this behaviour correctly |
| **Documentation text** | **[VERIFIED-TEXT]** | That a specific file at the pinned commit (path + blob/hash) contains the quoted words | That the words are true of any package |
| **Repository or registry facts** | **[VERIFIED]** | Counts, hashes, tags, dates, PyPI listings, produced by the shown command | Anything about behaviour |

A **docs/runtime agreement** is claimed only for a specific claim whose text was read **and** whose behaviour was executed. Both are cited (§4.1). Agreement is never inferred for pages that weren't checked.

Other labels:
- **[REPORTED]**: a third-party statement I could not verify (for example, forum posts).
- **[ASSUMPTION Ax]**: believed but not tested. Listed in the register in §0.2.
- **[PROPOSAL]**: a design choice, not a fact.

### 0.2 Assumption register (unverified until tested)

| ID | Assumption | Why it matters | How and when it gets tested |
|---|---|---|---|
| **A1** | Docs version X.Y describes the latest X.Y.z patch (1.2.2, 1.3.2, 1.4.5, 1.5.6, 2.0.1) | Gold answers are checked against these packages | Partly, at RAG-M3/M4: every benchmark item with an executable claim gets a probe on that package; disagreements become `doc_conflict`. Never fully proven, so it stays labelled |
| **A2** | Probe results on Linux (manylinux wheels) equal the Windows results | CI runs on Linux | Re-run the probes in CI at RAG-M2 and compare `results` objects |
| **A3** | The `docs/X.Y` page text matches the behaviour of the X.Y package | It decides whether a docs passage is valid evidence | **Not tested in general.** One counter-example is verified (§3.3). Checked per item at RAG-M4, where probes exist; unprobeable items rely on owner review and stay unconfirmed at runtime |

**Revision 3 changes:**
- Evidence kinds separated (§0.1) and assumption register added (§0.2).
- §4 split into package behaviour (§4) and claim-versus-behaviour comparison (§4.1).
- Detailed control probe and its explanation (§4.2).
- Conclusions relabelled.

**Revision 2 changes:**
- Probes re-run in hash-verified environments on the **final patch versions**. Revision 1 tested 1.4.4; the latest 1.4 patch is **1.4.5**.
- Five probes added.
- Exact commands and outputs added for every finding.

**Method.** Each candidate repository was cloned with `git clone --filter=blob:none --no-checkout <url>`. That downloads history but no file contents until they're read, and nothing was checked out or executed. Files were read with `git show <commit>:<path>` and `git ls-tree`. The only code executed was the official DuckDB wheels from PyPI, inside isolated virtual environments.

## 1. Selection criteria (roadmap PDF §3.2)

Each candidate needed:
1. An open licence that allows redistributing derived benchmark data with attribution.
2. Versioned docs available as source for at least 3–4 versions.
3. Changelogs.
4. **Real** breaking changes, verified.
5. A corpus that rebuilds reproducibly on a 16 GB laptop and fits the portfolio, since COP reuses this retriever.

## 2. Candidate comparison

### 2.1 Release series [VERIFIED]

Command, per repository:
```bash
git -C <repo> for-each-ref --sort=-creatordate --format='%(creatordate:short) %(refname:short)' refs/tags \
  | grep -viE 'rc|dev|a[0-9]|b[0-9]|beta|alpha' | head -14
```

Output (abridged to the series used):
```
pandas        2026-11-04 v3.1.1; 2026-10-22 v3.1.0; 2026-01-21 v3.0.0; 2025-06-04 v2.3.0; 2024-09-20 v2.2.3
scikit-learn  2026-09-10 1.9.1; 2026-06-02 1.9.0; 2025-12-10 1.8.0; 2025-06-05 1.7.0; 2024-12-09 1.6.0; 2024-05-21 1.5.0
pydantic      2026-11-19 v2.14.4; 2026-10-08 v2.14.0; 2026-10-29 v1.10.27
numpy         2026-11-15 v2.5.5; 2026-06-21 v2.5.0; 2025-12-20 v2.4.0
sqlalchemy    2026-11-16 rel_2_1_9; 2026-11-16 rel_2_0_58; 2026-09-24 rel_2_1_0
```

DuckDB version dates come from its release posts (§3.2) and from PyPI (manifest §2).

### 2.2 Comparison table

| Library | Licence [VERIFIED: file read at pinned commit] | Changelog structure [VERIFIED] | Docs source [VERIFIED] | Corpus rebuildable from source alone? |
|---|---|---|---|---|
| **DuckDB** (duckdb-web `d0148446`) | MIT; covers "Software and associated documentation files" | Release posts with "Breaking Changes" headings; 2.0 migration guide; Python-client migration guide | Plain Markdown, **every version side by side in one commit** (`docs/1.2 … docs/2.0`) | **Yes** [VERIFIED §3.1] |
| pandas (`1e36a1be`) | BSD-3-Clause | `whatsnew/vX.rst` with "Backwards incompatible API changes" (v2.0.0, v2.1.0, v3.0.0, v3.1.0), "Deprecations" and "Removal of prior version deprecations" sections | reST. `reference/frame.rst` consists of `.. autosummary::` stubs, so API text is in source docstrings. Examples use `.. ipython::`, so outputs exist only after a build | No. It needs a per-version Sphinx build or HTML scraping |
| scikit-learn (`0ab59f9b`) | BSD-3-Clause (`COPYING`) | `whats_new/vX.rst`; `\|API\|` entries per release: 1.4: 17, 1.5: 17, 1.6: 14, 1.7: 7, 1.8: 9, 1.9: 10 | reST + autosummary | No (same reason) |
| Pydantic (`ba7615c4`) | MIT | `HISTORY.md` (4,208 lines) | MkDocs Markdown: 25 pages at v1.10.27, 91 at v2.14.4 | Partly. The break is concentrated in v1→v2 |
| NumPy (`26dfc408`) | BSD-3-Clause | `release/X-notes.rst` with "Expired deprecations" and "Compatibility notes"; 2.4.0 lists many removals | reST + autosummary | No |
| SQLAlchemy (`4917592d`) | MIT | `changelog_14/20/21.rst`, `migration_14/20.rst` | reST: 170 / 187 / 188 files at 1.4 / 2.0 / 2.1 | Partly (API pages use autodoc) |

LangChain and Transformers were **not evaluated**. [ASSUMPTION] Their docs moved between repositories across major versions. I didn't verify this, so they are excluded as unassessed, not as disproven.

## 3. DuckDB documentation

### 3.1 Snapshot inventory [VERIFIED]

Directories at the pinned commit: `0.10, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 2.0, current, lts`.
- `docs/current` and `docs/lts` are the same tree and each holds one file (`objects.inv`). They are not doc copies.
- `_config.yml`: `maintained_duckdb_version: "1.5.6"`, `latest_duckdb_version: "2.0.1"`.
- Per-version file counts, words and content hashes are in manifest §1.1. Total: 2,031 Markdown files, 2,256,769 words.

### 3.2 Breaking changes the project itself documents [VERIFIED-TEXT]

Command: `git show <commit>:_posts/<post>.md | grep -nE "^#+ "`, then the relevant sections read in full. Short quotes below.

These rows are facts about **what the release posts say**. Whether the packages behave that way is a separate question, answered in §4 and compared in §4.1.

| Version | Heading found | Quoted change |
|---|---|---|
| 1.1 | `## Breaking SQL Changes` | "Previously, DuckDB would return `NULL` when dividing by zero… Starting with this release, DuckDB will return `inf` instead." |
| 1.2 | `### Breaking Changes` | A breaking change is documented in the 1.2.0 release post (`_posts/2025-02-05-announcing-duckdb-120.md`, line 29); the original wording is intentionally not reproduced here. Also: `random` state, `list_reduce` indexing, `current_date`/`current_time` in local timezone |
| 1.3 | release-post section A (`_posts/2025-05-21-announcing-duckdb-130.md`, line 18) | Single-arrow lambda deprecated in favour of `lambda x: ...`; the release also documents a lambda-related breaking change; `AT` needs quotes |
| 1.4 | none (LTS release) | New features, including "`MERGE` Statement" and "`FILL` Window Function" |
| 1.5 | release-post section B (`_posts/2026-03-09-announcing-duckdb-150.md`, line 282) | A further breaking change is recorded in the local 1.5.0 release evidence; the original source wording is intentionally not reproduced here. Plus spatial axis-order flip |
| 2.0 | release-post section C (`_posts/2026-10-21-announcing-duckdb-200.md`, line 364) → `docs/2.0/operations_manual/migration.md` `## Breaking Changes` | Storage format; lambda syntax; "Integer division (`//`) and modulo (`%`) by zero… now throw an error instead of returning `NULL`"; "An untyped `NULL` now keeps the `NULL` type instead of defaulting to `INTEGER`"; Python client "requires Python 3.11 or newer and changes several APIs" |

### 3.3 Archived docs are edited after release

```bash
$ git show d0148446:docs/1.1/sql/functions/map.md | sed -n '/#### `map_extract(map, key)`/,/^####/p'
| **Description** | Return the value for a given key, or `NULL` if the key is not contained in the map. ... |
| **Example** | `map_extract(map([100, 5], [42, 43]), 100)` |
| **Result** | `42` |
$ git log --format='%h %ad %s' --date=short -- docs/1.1/sql/functions/map.md
2a4bb09ac 2025-02-27 Restructure directories
$ git log --format='%h %ad %s' --date=short --diff-filter=A -- docs/archive/1.1/sql/functions/map.md docs/1.1/sql/functions/map.md | tail -1
a409603f0 2025-02-05 Add v1.1 archive pages
$ git log -1 --format=%cd --date=short -- docs/1.2
2026-11-16
$ git log --format='%ad %s' --date=short -- docs/1.2 | wc -l
21
```

Three separate facts, each with its own label:
- [VERIFIED-TEXT] The archived 1.1 page shows a scalar result (`42`).
- [VERIFIED-RUNTIME] Probe P01 (§4) shows that the **1.1.3 package returns a list** (`['v']`).
- [VERIFIED-TEXT] The 1.2 release post says the change happened in 1.2.

The 1.1 page and the 1.1.3 package therefore disagree. That is a verified **documentation conflict**, not a runtime change.

[VERIFIED] The archive was created on 2025-02-05, the same day 1.2 was announced. [ASSUMPTION] The newer text was copied in at that time; the git history is consistent with this but doesn't prove it. [VERIFIED] The `docs/1.2` path has 21 commits, the latest dated 2026-11-16.

**Finding:** archived docs aren't a reliable record of released behaviour, so assumption A3 can't be taken for granted. [PROPOSAL] Behaviour questions are checked by execution, and conflicts are recorded separately from runtime behaviour (design §2.1, §3.4).

### 3.4 Version confusion: page overlap between consecutive versions

```bash
git archive d0148446 docs/1.2 docs/1.3 docs/1.4 docs/1.5 docs/2.0 | tar -x -C duckdb-docs-extract
python -I - duckdb-docs-extract/docs <<'EOF'
import sys,re,pathlib,difflib
root=pathlib.Path(sys.argv[1])
def load(v):
    out={}
    for p in (root/v).rglob('*.md'):
        t=p.read_text(encoding='utf-8',errors='replace')
        t=re.sub(r'docs/\d+\.\d+/','docs/V/',t)          # neutralise version-specific links
        out[str(p.relative_to(root/v))]=t
    return out
vs=['1.2','1.3','1.4','1.5','2.0']; data={v:load(v) for v in vs}
for a,b in zip(vs,vs[1:]):
    A,B=data[a],data[b]; common=A.keys()&B.keys()
    same=sum(A[k]==B[k] for k in common)
    minor=sum(1 for k in common if A[k]!=B[k] and difflib.SequenceMatcher(None,A[k],B[k],autojunk=False).quick_ratio()>0.98)
    print(f"{a}->{b}: shared={len(common)} identical_after_link_norm={same} ({same/len(common):.0%}) near-identical(>0.98)={minor} added={len(B.keys()-A.keys())} removed={len(A.keys()-B.keys())}")
EOF
```

Output:
```
1.2->1.3: shared=327 identical_after_link_norm=2 (1%) near-identical(>0.98)=256 added=43 removed=34
1.3->1.4: shared=370 identical_after_link_norm=113 (31%) near-identical(>0.98)=167 added=15 removed=0
1.4->1.5: shared=370 identical_after_link_norm=107 (29%) near-identical(>0.98)=139 added=71 removed=15
1.5->2.0: shared=438 identical_after_link_norm=0 (0%) near-identical(>0.98)=76 added=36 removed=3
```

Caveat: `quick_ratio()` is an upper bound on character-level similarity, so "near-identical" may overcount. Identical-or-near-identical pages make up 79%, 76%, 66% and 17% of shared pages respectively.

**Finding:** an unfiltered retriever will see many wrong-version near-duplicates. This holds especially for 1.2–1.5. 2.0 differs more, which also makes it the most distinctive version.

### 3.5 Parsing requirements

Pages contain Jekyll/Liquid syntax (`{% link docs/2.0/... %}`, `{:.language-sql .highlight}`) and markdownlint comments. Some function tables are generated (`<!-- Start of section generated by scripts/generate_sql_function_docs.py -->`). The parser must resolve or strip these. [PROPOSAL, design §2]

## 4. Package behaviour [VERIFIED-RUNTIME]

This section reports only what each pinned wheel did. It makes no statement about the docs. The comparison with documentation claims is in §4.1.

**Environment:**
- Python 3.12.16 (uv-managed), uv 0.13.9, Windows 11 (`Windows-11-10.0.26200-SP0`).
- Wheels installed with `--require-hashes` against the PyPI sha256 values in manifest §2.
- Script and exact commands are in manifest §4–§5.1.
- Raw output: 6 JSON lines, sha256 `65cb54b8aa7f0ae7389d809ce3fcecca550ab74fc1505cf4f2faa4848e50621f`.

Actual results. Error messages are truncated to the first 90 characters by the script; columns marked ✱ are corpus versions.

| Probe (query) | 1.1.3 (ref) | 1.2.2 ✱ | 1.3.2 ✱ | 1.4.5 ✱ | 1.5.6 ✱ | 2.0.1 ✱ |
|---|---|---|---|---|---|---|
| P01 `map(['k'],['v'])['k']` | `['v']` | `'v'` | `'v'` | `'v'` | `'v'` | `'v'` |
| P02 `typeof(date_trunc('month', DATE …))` | `'DATE'` | `'DATE'` | `'DATE'` | `'DATE'` | `'TIMESTAMP'` | `'TIMESTAMP'` |
| P03 `1 / 0` | `inf` | `inf` | `inf` | `inf` | `inf` | `inf` |
| P04 `1 // 0` | `None` | `None` | `None` | `None` | `None` | `ERROR InvalidInputException: … Division by zero in expression (1 // 0). Use TRY(...) …` |
| P05 `list_transform([1,2], x -> x + 1)` | `[2, 3]` | `[2, 3]` | `[2, 3]` | `[2, 3]` | `[2, 3]` | `ERROR BinderException: … Deprecated lambda arrow (->) detected …` |
| P06 `list_transform([1,2], lambda x: x + 1)` | `ERROR ParserException …` | `ERROR ParserException …` | `[2, 3]` | `[2, 3]` | `[2, 3]` | `[2, 3]` |
| P07 `MERGE INTO t USING s … UPDATE` | `ERROR ParserException … near "MERGE"` | same | same | `99` | `99` | `99` |
| P08 column type of `CREATE TABLE n AS SELECT NULL AS x` | `'INTEGER'` | `'INTEGER'` | `'INTEGER'` | `'INTEGER'` | `'INTEGER'` | `'"NULL"'` |
| P09 control: `SELECT (SELECT * FROM range(2))` | `ERROR InvalidInputException: … More than one row returned by a subquery …` | same | same | same | same | same |
| P10 Python `con.sql("SELECT 42").fetchone()` | `(42,)` | `(42,)` | `(42,)` | `(42,)` | `(42,)` | `ERROR AttributeError: Relations no longer have fetchone() …` |

**Runtime findings:**
- [VERIFIED-RUNTIME] Every corpus boundary has at least one behaviour change observed in the packages: 1.2→1.3 (P06), 1.3→1.4 (P07), 1.4→1.5 (P02), 1.5→2.0 (P04, P05, P08, P10). The 1.1→1.2 reference boundary has P01.
- [VERIFIED-RUNTIME] P03 and P09 return the same output in all six packages.
- [ASSUMPTION A2] The same results hold on Linux with the manylinux wheels. Checked in CI at RAG-M2.

### 4.1 Documentation claims compared with package behaviour

Each row pairs a **specific quoted claim** [VERIFIED-TEXT] with the **probe result** [VERIFIED-RUNTIME]. "Agrees" means only that this one claim matches the packages at the stated boundary.

| Probe | Claim source (pinned commit) | Claimed boundary | Runtime boundary observed | Agrees? |
|---|---|---|---|---|
| P01 | 1.2 release post, "Breaking Changes" | 1.2 | between 1.1.3 and 1.2.2 | yes |
| P01 | `docs/1.1/sql/functions/map.md` (archived 1.1 page) shows scalar `42` | n/a (describes 1.1) | 1.1.3 returns a list | **no: documentation conflict** (§3.3) |
| P02 | 1.5 release post, release-post section B (`_posts/2026-03-09-announcing-duckdb-150.md`, line 282) | 1.5 | between 1.4.5 and 1.5.6 | yes |
| P03 | 1.1 release post (returns `inf`); 2.0 migration guide ("still returns `inf`") | 1.1, unchanged in 2.0 | `inf` in all six | yes |
| P04 | 2.0 migration guide, "Breaking Changes" | 2.0 | between 1.5.6 and 2.0.1 | yes |
| P05 | 1.3 post (deprecated); 2.0 migration guide (disabled) | deprecated 1.3, error in 2.0 | works through 1.5.6, error in 2.0.1 | yes |
| P06 | 1.3 release post | introduced 1.3 | between 1.2.2 and 1.3.2 | yes |
| P07 | 1.4 release post, "`MERGE` Statement" | introduced 1.4 | between 1.3.2 and 1.4.5 | yes |
| P08 | 2.0 migration guide, "Breaking Changes" | 2.0 | between 1.5.6 and 2.0.1 | yes |
| P09 | **none.** This is a control, not a documented change (§4.2) | n/a | identical in all six | n/a |
| P10 | 2.0 version pages: `docs/2.0/clients/python/migration.md` line 14 ("1.x: `rel.fetchone()` … 2.0: these methods are gone") and `conversion.md` line 33 ("A relation has no `fetchone()`") | 2.0 Python client | between 1.5.6 and 2.0.1 | yes |

**What this does and does not show:**
- [VERIFIED] All 8 tested documented **changes** (P01, P02, P04–P08, P10) agree with package behaviour at the claimed boundary, on the final patch of each version. P03's "unchanged" claim also agrees.
- [VERIFIED] **Version-page** claims tested so far: 2. The 2.0 Python pages (P10) **agree** with 2.0.1. The archived 1.1 `map_extract` page **disagrees** with 1.1.3.
- **Not shown:** that the `docs/X.Y` pages for 1.2–2.0 in general match their packages (assumption A3). Two pages are a sample of 2,031 files, not evidence of alignment. Version-page checks happen per benchmark item at RAG-M4 (design §2.1).
- Boundaries are only resolved to "between the two tested patches". For example, P07 shows MERGE is absent in 1.3.2 and present in 1.4.5, not that it appeared in 1.4.0. Assumption A1 covers the gap.

### 4.2 The stable control (P09): purpose, why it errors, expected result

**Purpose.** P09 is a **negative control**: a query whose behaviour is expected **not** to change across the tested versions. The eight change probes can only show that the harness *finds* differences. P09 checks the opposite: that the harness doesn't *invent* differences. Two harness faults would show up here:
- **Environment mix-ups.** If a venv had loaded the wrong DuckDB build, or one result was silently dropped, P09 could differ while being expected to match. (The recorded `duckdb.__version__` in every output line is a second, direct check.)
- **Result capture and normalisation bugs.** For example, errors being formatted differently per version, so that identical behaviour looks different. P09 exercises the error path of `run_sql`, the same path used by P04–P07 and P10.

It also models a **T1 (stable) item** in the benchmark: a fact whose answer must be identical in every corpus version.

**Why it errors in every version.** `SELECT (SELECT * FROM range(2))` uses a subquery as a scalar value, but the subquery returns two rows. The detailed run (manifest §5.4) shows, for all six packages [VERIFIED-RUNTIME]:
- The full error: `More than one row returned by a subquery used as an expression - scalar subqueries can only return a single row. Use "SET scalar_subquery_error_on_multiple_rows=false" to revert to previous behavior of returning a random row.`
- `duckdb_settings()` reports `scalar_subquery_error_on_multiple_rows = true` by default in all six.
- With the setting turned off, the query returns `[(0,)]`, a single row picked arbitrarily.
- The one-row variant `SELECT (SELECT * FROM range(1))` returns `[(0,)]` in all six.

So the error isn't a harness failure. It is DuckDB's deliberate default, consistent across 1.1.3–2.0.1.

[REPORTED, by the error text itself; not tested] Older releases returned an arbitrary row. That change happened **before** 1.1.3, the oldest version tested, so it is outside the corpus range.

**Expected result.** The expectation is **identical output across all versions**, specifically `InvalidInputException` with that message. An error was chosen deliberately: the alternative (setting off) returns an *arbitrary* row, which is not a stable value to compare. The observed result matched the expectation in all six packages.

**Limitation and improvement for RAG-M3 [PROPOSAL].** One control on the error path is thin. When the probe suite moves into the repo, add:
- a success-path control (the one-row variant above, expected `[(0,)]`), already verified here;
- a control that compares the whole result *with* the version string stripped, so normalisation bugs can't hide.

The probe suite then **fails loudly** if any control differs between versions. A differing control invalidates that run rather than being reported as a version change.

### 4.1 PyPI availability [VERIFIED]

`curl -s https://pypi.org/pypi/duckdb/json` lists, for every minor version:
```
1.2: patches=['1.2.0','1.2.1','1.2.2'] latest=1.2.2 uploaded=2025-04-08 yanked=False
1.3: patches=['1.3.0','1.3.1','1.3.2'] latest=1.3.2 uploaded=2025-07-08 yanked=False
1.4: patches=['1.4.0',…,'1.4.5']       latest=1.4.5 uploaded=2026-06-17 yanked=False
1.5: patches=['1.5.0',…,'1.5.6']       latest=1.5.6 uploaded=2026-09-28 yanked=False
2.0: patches=['2.0.0','2.0.1']         latest=2.0.1 uploaded=2026-11-20 yanked=False
```

Each latest patch has cp312 wheels for win_amd64 and manylinux x86_64. Hashes are in the manifest.

## 5. Gemini free tier

- [VERIFIED, fetched page] The official rate-limits page (`ai.google.dev/gemini-api/docs/rate-limits.md.txt`, which redirects to `aistudio.google.com/docs/rate-limits.md.txt`) publishes **no per-model free-tier numbers** for text or embedding models. It says limits "can be viewed in Google AI Studio".
- [VERIFIED, same page] "Specified rate limits are not guaranteed and actual capacity may vary". Limits apply per project, and daily quotas reset at midnight Pacific time.
- [REPORTED] Forum users describe sudden cuts to free-tier Flash daily limits (for example 250 → 20 requests/day), and a Google staff reply calls the free tier "best effort".
- [REPORTED] A forum notice says unrestricted API keys stop working from 19 June 2026.
- [PROPOSAL] Gemini is kept off the CI path. Drafting is cached and resumable and works at about 20 requests/day. Model and limits are read from AI Studio at RAG-M4.

Sources: [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [rate-limits.md.txt](https://ai.google.dev/gemini-api/docs/rate-limits.md.txt), [Google AI plans](https://ai.google.dev/gemini-api/docs/google-ai-plans), [Forum: free tier limits](https://discuss.ai.google.dev/t/gemini-api-rate-limits-free-tier/125968), [Forum: shared quota](https://discuss.ai.google.dev/t/does-gemini-api-free-tier-have-a-shared-quota-across-models/184308), [Forum: 92% quota cut](https://discuss.ai.google.dev/t/do-they-really-think-we-wouldnt-notice-a-92-free-tier-quota/111262), [Forum: rate-limits tag](https://discuss.ai.google.dev/tag/rate-limits/415), [Forum: limit is 0](https://discuss.ai.google.dev/t/gemini-api-free-tier-limit-is-0-cannot-use-api-despite-valid-key/114215), [Forum: quota limit 50](https://discuss.ai.google.dev/t/quota-exceeded-for-metric-generativelanguage-googleapis-com-generate-content-free-tier-requests-limit-50/105796), [Forum: API vs AI Studio](https://discuss.ai.google.dev/t/limits-of-free-tier-api-vs-ai-studio/94918).

## 6. Conclusions

| # | Statement | Label |
|---|---|---|
| 1 | DuckDB is the only candidate whose multi-version docs are plain-Markdown source in one commit | VERIFIED for the six candidates examined |
| 2 | Every corpus version boundary has at least one behaviour change observed in the pinned packages | VERIFIED-RUNTIME (§4) |
| 3 | Each of those changes is described by a release post or migration guide at the matching boundary | VERIFIED-TEXT + VERIFIED-RUNTIME, per claim (§4.1) |
| 4 | Version pages can contradict released behaviour | VERIFIED: one case (§3.3). How often it happens is unknown |
| 5 | Version pages for 1.2–2.0 match their packages | **Not claimed.** ASSUMPTION A3; 2 pages checked (1 agrees, 1 conflict, in 1.1) |
| 6 | Consecutive versions share most pages nearly verbatim (except 1.5→2.0) | VERIFIED (§3.4, upper-bound similarity) |
| 7 | The control probe behaves identically across 1.1.3–2.0.1, by design default | VERIFIED-RUNTIME (§4.2) |
| 8 | Docs version X.Y describes the latest X.Y.z patch | ASSUMPTION A1 |
| 9 | Probe results are identical on Linux | ASSUMPTION A2 (check at RAG-M2) |
| 10 | DuckDB, versions 1.2 / 1.3 / 1.4 / 1.5 / 2.0, manifest pins | Owner decision, 2026-11-20 (approved with conditions) |
| 11 | pandas is the runner-up | PROPOSAL |
