# RAG-M1 Version Manifest

Exact pins for the RAG corpus and the behavioural tests, captured 2026-11-20. RAG-M3 turns this into the machine-readable `data/manifest.json`. **Every value below was produced by the commands in §5 and verified.** Values that are assumptions are marked (A1–A3), and rows still to be filled at a later milestone say so. Revision 3 adds the D13 release-notes table (§1.2), assumption IDs (§2) and the detailed control probe (§5.4).

## 1. Documentation source

| Field | Value |
|---|---|
| Repository | `https://github.com/duckdb/duckdb-web` |
| Commit | `d01484462710d49206816c9eeaeb822361ebe81c` (committed 2026-11-20T15:27:42+01:00) |
| Licence | MIT, `LICENSE` blob `4e1fbb7695a7e4fb679d654ce5391c1a20fa8c5f`, sha256 `7e17fd31249fa875cb3b1c5e05c6c3e99b75509f6a2804ca176c217834de1dcb` |

### 1.1 Documentation snapshots (Markdown pages only)

`content_hash` = sha256 of the newline-joined, sorted lines `"<path>\t<sha256(file bytes)>"` for every `*.md` file under the directory. It doesn't depend on git, so it can be recomputed from any copy of the files. `git_tree` is the git tree object ID of the directory at the pinned commit. Words = Python `len(text.split())` summed over files.

| Version | Directory | git_tree | .md files | Words | content_hash |
|---|---|---|---|---|---|
| 1.2 | `docs/1.2` | `eae1b082acc99caba547054057ec99f482d64e70` | 361 | 339,173 | `b44f6a1f852076d1e0cdaf4aeee0624c4ce3ce860835e8c27e113ca999c9ea30` |
| 1.3 | `docs/1.3` | `86e052ddae4c1184ab720eeb485160fad4e07554` | 370 | 383,442 | `dc5bdadc4d76f3c585f7cfa4a7c59cda43203a07f43598e541122bb00dcfe79c` |
| 1.4 LTS | `docs/1.4` | `9def185c5b336cc36cc7e7a17b639403fe8f27e9` | 385 | 404,413 | `2f644a52296a29d1dfdbe8d38a62253fd0e020979400deb5aee9803e6f5fd65d` |
| 1.5 | `docs/1.5` | `4fe0df42c15fc37519db241e02b51846e20c7868` | 441 | 470,944 | `d4de4f6377dee6422c91f68106d9f089376497cfcdaf4db527ad02c57c9b3dc2` |
| 2.0 | `docs/2.0` | `d39770b3f79840e4e954ef98ed8820a61cd36e94` | 474 | 658,797 | `17860c97ffbdda4f13b458528c9dcb2c160fa335dfc4622621d52515885502a2` |
| **Total** | | | **2,031** | **2,256,769** | |

### 1.2 Change sources (release notes and migration guides)

| File | git blob | sha256 |
|---|---|---|
| `_posts/2025-02-05-announcing-duckdb-120.md` | `2d3c080c5dfbb830ce2dd9d1db44fd381670e848` | `2db3b0c0624d9d14ca19c2199fc3905f2280c60e4d7b699a6c03a379921fadb8` |
| `_posts/2025-05-21-announcing-duckdb-130.md` | `7cc6dd32aabc183aea95bfdcadc8740447e7f0c2` | `35e61a8121e5aad1b4c6e7b0f1a429722b28d21e1266c9803a92fd9aa50b9a96` |
| `_posts/2025-09-16-announcing-duckdb-140.md` | `497cc11b56b2e93e06759064a26cd45db047346d` | `d78e7458ba8de9520695e889299106afa1835834c50a985233319f483af0ff36` |
| `_posts/2025-10-07-announcing-duckdb-141.md` | `94e8b6123f9ca1d0d07fddda1dd7f7b0d50e54dc` | `43c7494e7854dbbae44e1d01a44beade6ca2b376d6ad714af9177bebac72fb75` |
| `_posts/2025-11-12-announcing-duckdb-142.md` | `d78eb58bfdabfe5f111a2e5d27e647e1a9cc4771` | `120e7b736050b9940d4b8ed0cbbdc31075b34688b4d945d7ee0369c9654fdff4` |
| `_posts/2025-12-09-announcing-duckdb-143.md` | `2626b7cef1747d28422f5c7a25b1d2d77cb34e37` | `915167b82a9c485407df1c5d00982cc4c6a09ed60c5461b8ab623b7ee3bac1b9` |
| `_posts/2026-01-26-announcing-duckdb-144.md` | `b08ca78e4ca7f2a06f76bacc7b99bd292bf09f5c` | `f6584c6d27fddf29c19919c153a6654e29eaac1cc13e2431a7e94e63aaa76667` |
| `_posts/2026-06-17-announcing-duckdb-145.md` | `5919df4a3ec953bc67f1d621b9c4ef5584d202c1` | `68f6469ba5cd33b02e17b17744df39b884f374213684e04a0de3ba4bb9601d3d` |
| `_posts/2026-03-09-announcing-duckdb-150.md` | `abcfb83845bb3c56dd67c3251eb0eb51ed055163` | `a161d922de42a4573b36e815448a1d61906146a4dc1360c06f4c8d38af2ae41f` |
| `_posts/2026-03-23-announcing-duckdb-151.md` | `23ddce07c3904d46eaf5a1e6c397d43d456df950` | `b40916c7c63e279a5bfb922bf830bead5205e6b435252efa42920197e8789fd0` |
| `_posts/2026-04-13-announcing-duckdb-152.md` | `0a9ab950766bcf2c1c681a8042f0dc46c4812393` | `8db45969595720511d7aac2304b7ebab7cc3eb1e2bd18ad17d030aa83c461d88` |
| `_posts/2026-05-20-announcing-duckdb-153.md` | `f7481ec72c18d8fc502bb1999011b89ddf8cd12d` | `6556c42d810c5fcfd1ffd83bb50eb724755d3af1993a5fe946a555061042e5e6` |
| `_posts/2026-06-17-announcing-duckdb-154.md` | `3e34298d2d9d45a2ce552d7ae89aa453cd020bb2` | `627a6eb03c9da83b16ad0faac8c9cada1131fdb2de73fe405b5c7f916e87cf40` |
| `_posts/2026-07-22-announcing-duckdb-155.md` | `4a5dc831dfbfeae1261c4690df0ec76cff13f184` | `3fb7aa36e95132fcd50ea487ad1b9793aaa05024bb049f7c9df5ca60b2320944` |
| `_posts/2026-09-28-announcing-duckdb-156.md` | `548f7b1b8e0259af53fa25514d0111595228f14e` | `354da25be41cd3a2d9c58ed49a3fe026c377f46ef6a851bd72cffa29dcf5eeb5` |
| `_posts/2026-10-21-announcing-duckdb-200.md` | `543a58e43eb80b442ddb33ebe926e9b528388c38` | `c5d5ed2cc710eb1e0108911f842378a7b6f9fa623d1cda9bf2d358b5c7d20de0` |
| `_posts/2026-11-20-announcing-duckdb-201.md` | `9d789df6cd121a65d57833c97265fb7efbcd8e0a` | `9efcfe367774d7c97190a564efeb7d2878650b4ed6d745ebfd23ffe240c00550` |
| `docs/2.0/operations_manual/migration.md` | `3195602afef212c738785f5f8890891751c840e0` | `303da835840e6a6fe4160540ef531db76f5f611e4b6b8e3793f43e79e76ace03` |
| `docs/2.0/clients/python/migration.md` | `7bddc8660327bc240c0d2eff8597404877a4b44b` | `b12bdb45ed6537feb534d0b5e82d7db113de3dc7afb71e175f53c2abd25b9f18` |

**Known gap (verified):** the repository has no announcement posts for patches 1.2.1, 1.2.2, 1.3.1 or 1.3.2. Their notes exist only as GitHub releases on `duckdb/duckdb`.

**Owner decision D13 (2026-11-20): add them at RAG-M3.** Not yet fetched. At RAG-M3 each one is recorded in a table here, and each row is filled only from an actual fetch:

| Tag | Source URL | Retrieved (UTC) | Release `published_at` | sha256 of the release body (UTF-8, as fetched) | Stored copy path |
|---|---|---|---|---|---|
| v1.2.1 | *to be recorded at RAG-M3* | | | | |
| v1.2.2 | *to be recorded at RAG-M3* | | | | |
| v1.3.1 | *to be recorded at RAG-M3* | | | | |
| v1.3.2 | *to be recorded at RAG-M3* | | | | |

GitHub release bodies can be edited after publication, unlike a file at a pinned git commit. That's why the fetched text is stored in the repo and hashed, and the stored copy (not the live page) is what gets indexed.

## 2. Behavioural-test packages (DuckDB, PyPI)

One package per documentation version: **the latest patch release of that minor version on PyPI as of 2026-11-20**. 1.1.3 is a reference version for the 1.1 → 1.2 change and is not part of the corpus.

| Docs version | Package | PyPI upload | sha256, cp312 win_amd64 | sha256, cp312 manylinux x86_64 |
|---|---|---|---|---|
| (ref) 1.1 | `duckdb==1.1.3` | 2024-11-04 | `e86006958e84c5c02f08f9b96f4bc26990514eab329b1b4f71049b3727ce5989` | `4ebf5f60ddbd65c13e77cddb85fe4af671d31b851f125a4d002a313696af43f1` |
| 1.2 | `duckdb==1.2.2` | 2025-04-08 | `b1c0c4d737fd2ab9681e4e78b9f361e0a827916a730e84fa91e76dca451b14d5` | `e5c1556775a9ebaa49b5c8d64718f155ac3e05b34a49e9c99443cf105e8b0371` |
| 1.3 | `duckdb==1.3.2` | 2025-07-08 | `11af73963ae174aafd90ea45fb0317f1b2e28a7f1d9902819d47c67cc957d49c` | `36abdfe0d1704fe09b08d233165f312dad7d7d0ecaaca5fb3bb869f4838a2d0b` |
| 1.4 LTS | `duckdb==1.4.5` | 2026-06-17 | `6b8d992d957c89e83d697756f6c5b5aea910d6bf16e2666da4c508f891932ae2` | `aa294d028c149ca21110e366eaffcb4fc9ab11d7d203d50f7bc49a07ab34b960` |
| 1.5 | `duckdb==1.5.6` | 2026-09-28 | `09ff51b230219f0d8b47fc8a1e17fb595ba9fab0c3d96a6de4d00b8ff86b3cf1` | `bc9619ed7d4ffa117b5155d84b44794366bb6635178d78ed5e13a6024845c757` |
| 2.0 | `duckdb==2.0.1` | 2026-11-20 | `67a7dab509897ec1f1d46283783edb8fdc005f02fde6e263fb77a8a3bf7f1b85` | `39ffeb136abed770a496e5592a464f18a07b1543e7ddb1ff6453215e179f1647` |

The 2.0.1 wheels are tagged `cp312-abi3`; earlier ones are `cp312-cp312`. No version listed is yanked.

**Unverified assumptions (owner condition, 2026-11-20: keep these labelled until tested):**
- **A1:** docs version X.Y describes the latest X.Y.z patch listed above. Patch releases normally contain fixes, not documented behaviour changes, but this hasn't been verified for every patch.
- **A3:** the text of the `docs/X.Y` pages matches the behaviour of the pinned package. **Not tested.** One counter-example is already verified (the archived 1.1 `map_extract` page, evidence §3.3). The probes in this manifest test **package behaviour** and compare it with **release-note claims**. They do not establish that every docs page is correct.

**No claim of complete documentation/runtime alignment is made.** Documentation conflicts and runtime behaviour are recorded separately (design §2.1).

## 3. Toolchain

| Tool | Version | Status |
|---|---|---|
| Python (probe environments) | 3.12.16 (uv-managed CPython, `cpython-3.12.16-windows-x86_64-none`) | verified |
| uv | 0.13.9 (6b2d39dcc 2026-11-17) | verified |
| git | 2.47.0.windows.1 | verified |
| OS | Windows 11, `Windows-11-10.0.26200-SP0` (reported by `platform.platform()`) | verified |
| Linux (CI) | Same probe outputs expected on `ubuntu-latest` with the manylinux wheels | **assumption A2, to be verified at RAG-M2 in CI** |

## 4. Probe script

`rag_m1_probes.py`, sha256 `732c93e29abc589710a7d1438c8f32db64e1f3a9163968a23d845f5705ad7afc`. This is a research evidence script, not application code. At RAG-M2 it moves into the repository as `research/rag_m1_probes.py` with a test.

```python
"""RAG-M1 behavioural probes. Run with an isolated interpreter: python -I rag_m1_probes.py"""
import json, platform, sys
import duckdb

SQL_PROBES = {
    "P01_map_bracket":        "SELECT map(['k'], ['v'])['k']",
    "P02_date_trunc_type":    "SELECT typeof(date_trunc('month', DATE '2026-03-27'))",
    "P03_float_div_zero":     "SELECT 1 / 0",
    "P04_int_div_zero":       "SELECT 1 // 0",
    "P05_arrow_lambda":       "SELECT list_transform([1, 2], x -> x + 1)",
    "P06_python_lambda":      "SELECT list_transform([1, 2], lambda x: x + 1)",
    "P07_merge_into": (
        "CREATE TABLE t(id INTEGER, v INTEGER); INSERT INTO t VALUES (1, 10);"
        "CREATE TABLE s(id INTEGER, v INTEGER); INSERT INTO s VALUES (1, 99);"
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;"
        "SELECT v FROM t"),
    "P08_untyped_null_column": (
        "CREATE TABLE n AS SELECT NULL AS x;"
        "SELECT column_type FROM (DESCRIBE n)"),
    "P09_control_scalar_subquery_multirow": "SELECT (SELECT * FROM range(2))",
}

def run_sql(sql: str) -> str:
    con = duckdb.connect()
    try:
        *setup, final = [s for s in sql.split(";") if s.strip()]
        for stmt in setup:
            con.execute(stmt)
        return repr(con.execute(final).fetchall()[0][0])
    except Exception as e:  # record the error type, not a crash
        return f"ERROR {type(e).__name__}: {str(e).splitlines()[0][:90]}"
    finally:
        con.close()

def run_python_relation_fetchone() -> str:
    con = duckdb.connect()
    try:
        return repr(con.sql("SELECT 42").fetchone())
    except Exception as e:
        return f"ERROR {type(e).__name__}: {str(e).splitlines()[0][:90]}"
    finally:
        con.close()

results = {name: run_sql(sql) for name, sql in SQL_PROBES.items()}
results["P10_python_relation_fetchone"] = run_python_relation_fetchone()
print(json.dumps({"duckdb": duckdb.__version__, "python": platform.python_version(),
                  "platform": platform.platform(), "results": results}))
```

## 5. Reproduction commands (Git Bash on Windows; on Linux replace `Scripts/python.exe` with `bin/python`)

### 5.1 Behavioural probes, with hash-verified installs

```bash
# One requirements file per version, with both wheel hashes from §2, e.g. req/duckdb-1.4.5.txt:
# duckdb==1.4.5 --hash=sha256:aa294d028c149ca21110e366eaffcb4fc9ab11d7d203d50f7bc49a07ab34b960 --hash=sha256:6b8d992d957c89e83d697756f6c5b5aea910d6bf16e2666da4c508f891932ae2
python -m uv python install 3.12.16
for v in 1.1.3 1.2.2 1.3.2 1.4.5 1.5.6 2.0.1; do
  python -m uv venv -q --python 3.12.16 "envs/duckdb-$v"
  python -m uv pip install -q --python "envs/duckdb-$v/Scripts/python.exe" --require-hashes -r "req/duckdb-$v.txt"
  "envs/duckdb-$v/Scripts/python.exe" -I rag_m1_probes.py
done > probe_results.jsonl
sha256sum probe_results.jsonl
```

Result on 2026-11-20: `probe_results.jsonl`, 6 lines, sha256 `65cb54b8aa7f0ae7389d809ce3fcecca550ab74fc1505cf4f2faa4848e50621f`. Outputs are tabulated in `RAG-M1_research_evidence.md` §4.

The file hash includes the `platform` string, so on another OS compare the `results` objects, not the file hash.

### 5.2 Corpus snapshot hashes

```bash
git clone --filter=blob:none --no-checkout https://github.com/duckdb/duckdb-web.git
cd duckdb-web
python -I - <<'EOF'
import subprocess, hashlib
C = "d01484462710d49206816c9eeaeb822361ebe81c"
def git(*a): return subprocess.run(["git", *a], capture_output=True, check=True).stdout
proc = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def content(sha):
    proc.stdin.write((sha + "\n").encode()); proc.stdin.flush()
    n = int(proc.stdout.readline().split()[2]); data = proc.stdout.read(n); proc.stdout.read(1); return data
for v in ["1.2", "1.3", "1.4", "1.5", "2.0"]:
    rows = [l.split("\t", 1) for l in git("ls-tree", "-r", C, "--", f"docs/{v}").decode().splitlines()]
    md = [(p, meta.split()[2]) for meta, p in rows if p.endswith(".md")]
    lines = sorted(f"{p}\t{hashlib.sha256(content(s)).hexdigest()}" for p, s in md)
    print(v, git("rev-parse", f"{C}:docs/{v}").decode().strip(), len(md),
          sum(len(content(s).split()) for _, s in md),
          hashlib.sha256("\n".join(lines).encode()).hexdigest())
EOF
```

Expected output: the git_tree, file-count, word and content_hash values in §1.1.

### 5.3 Package versions and wheel hashes

```bash
curl -s https://pypi.org/pypi/duckdb/json -o duckdb_pypi.json
# then read releases[<version>][*].digests.sha256 for the cp312 win_amd64 and manylinux x86_64 files
```

### 5.4 Control probe P09, detailed (added in revision 3)

Script `p09_detail.py` (sha256 `4c56fae46fd71ec462511847e356f0710957773791abdfde2fed37715236fed7`). It records the full error message, the setting that governs the behaviour, the result with that setting disabled, and a one-row positive case:

```python
import duckdb, json
out = {"duckdb": duckdb.__version__}
con = duckdb.connect()
try:
    out["full_error"] = repr(con.sql("SELECT (SELECT * FROM range(2))").fetchall())
except Exception as e:
    out["full_error"] = f"{type(e).__name__}: {e}"
try:
    out["setting"] = con.execute("SELECT name, value FROM duckdb_settings() WHERE name LIKE '%scalar_subquery%'").fetchall()
except Exception as e:
    out["setting"] = f"{type(e).__name__}: {e}"
try:
    con.execute("SET scalar_subquery_error_on_multiple_rows = false")
    out["with_setting_false"] = repr(con.execute("SELECT (SELECT * FROM range(2))").fetchall())
except Exception as e:
    out["with_setting_false"] = f"{type(e).__name__}: {e}"[:200]
try:
    out["single_row_ok"] = repr(duckdb.connect().execute("SELECT (SELECT * FROM range(1))").fetchall())
except Exception as e:
    out["single_row_ok"] = f"{type(e).__name__}: {e}"
print(json.dumps(out, ensure_ascii=True))
```

```bash
for v in 1.1.3 1.2.2 1.3.2 1.4.5 1.5.6 2.0.1; do "envs/duckdb-$v/Scripts/python.exe" -I p09_detail.py; done
```

Output on 2026-11-20 was identical for all six versions apart from the `duckdb` field. The 1.4.5 line:
```
{"duckdb": "1.4.5", "full_error": "InvalidInputException: Invalid Input Error: More than one row returned by a subquery used as an expression - scalar subqueries can only return a single row.\n\nUse \"SET scalar_subquery_error_on_multiple_rows=false\" to revert to previous behavior of returning a random row.", "setting": [["scalar_subquery_error_on_multiple_rows", "true"]], "with_setting_false": "[(0,)]", "single_row_ok": "[(0,)]"}
```
Interpretation: evidence §4.2.
