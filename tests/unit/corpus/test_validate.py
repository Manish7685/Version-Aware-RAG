"""S5b corpus validation (design docs/design/RAG-M3_S5_build.md §3), on fixture corpora.

Every tampering happens in a copied fixture folder under ``tmp_path``; the real outputs and caches
are never touched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus import commands
from version_aware_rag.corpus.acquire import fetch_pinned, open_cache
from version_aware_rag.corpus.build import BUILD_MANIFEST, BUILD_REPORT, BuildInputs, run_build
from version_aware_rag.corpus.chunk import chunk_page
from version_aware_rag.corpus.config import CorpusConfig, ParsingConfig, load_chunking_config
from version_aware_rag.corpus.manifest import build_manifest, write_manifest
from version_aware_rag.corpus.parse import parse_page
from version_aware_rag.corpus.release_chunks import apply_q7
from version_aware_rag.corpus.releases import fetch_record_path
from version_aware_rag.corpus.sections import build_tree
from version_aware_rag.corpus.validate import (
    _POST_130_HEADING,
    _POST_150_HEADING,
    LEAK_EXCEPTIONS,
    LEAK_LINE_CHARS,
    Finding,
    LeakException,
    ValidateInputs,
    ValidationReport,
    ValidationUsageError,
    contains_phrase,
    count_leaks,
    docs_corpus_lines,
    fenced_code_lines,
    post_restricted_lines,
    quote_key,
    quoted_phrases,
    release_restricted_lines,
    restricted_quotes,
    shared_with_docs,
    source_coverage,
    validate_corpus,
)

from .conftest import Upstream, make_config, release_body, write_release_cache
from .test_chunk import source as page_source_fixture

ROOT = Path(__file__).resolve().parents[3]
CHUNKING, CHUNKING_SHA = load_chunking_config(ROOT / "configs/chunking.toml")
PARSING = ParsingConfig(site_base="https://example.invalid", excluded_pages=(), samples=())
CFG_SHA = "c" * 64
PARSING_SHA = "p" * 64
_TEMPLATE: dict[str, Path] = {}


def _prepare(up: Upstream, root: Path, cfg: CorpusConfig) -> BuildInputs:
    cache = cfg.cache_path(root)
    if not (cache / ".git").is_dir():
        fetch_pinned(cfg, cache)
    rel = root / cfg.release_notes.cache_dir
    if not rel.is_dir():
        write_release_cache(rel)
        for r in cfg.release_notes.included:
            record = {
                "release_id": r.release_id,
                "body_sha256": r.body_sha256,
                "retrieved_at": "2026-11-20T18:46:33Z",
            }
            fetch_record_path(rel, r.tag).write_text(json.dumps(record), encoding="utf-8")
    manifest_path = root / cfg.paths.manifest
    if not manifest_path.is_file():
        write_manifest(build_manifest(cfg, open_cache(cfg, cache), CFG_SHA, rel), manifest_path)
    return BuildInputs(
        root=root,
        cfg=cfg,
        cfg_sha256=CFG_SHA,
        cache=cache,
        release_cache=rel,
        manifest_path=manifest_path,
        parsing=PARSING,
        parsing_sha256=PARSING_SHA,
        chunking=CHUNKING,
        chunking_sha256=CHUNKING_SHA,
    )


@pytest.fixture(autouse=True)
def _template(upstream: Upstream, tmp_path_factory: pytest.TempPathFactory) -> None:
    """One built fixture corpus per session; every test works on its own copy."""
    if "root" not in _TEMPLATE:
        base = tmp_path_factory.mktemp("validate-template")
        run_build(_prepare(upstream, base, make_config(upstream, upstream.commit1)), check=False)
        _TEMPLATE["root"] = base


def _inputs(
    up: Upstream, root: Path, cfg: CorpusConfig | None = None, **changes: Any
) -> ValidateInputs:
    if not (root / BUILD_MANIFEST).exists():
        shutil.copytree(_TEMPLATE["root"], root, dirs_exist_ok=True)
    cfg = cfg or make_config(up, up.commit1)
    fields: dict[str, Any] = {
        "root": root,
        "cfg": cfg,
        "cfg_sha256": CFG_SHA,
        "manifest_path": root / cfg.paths.manifest,
        "parsing": PARSING,
        "parsing_sha256": PARSING_SHA,
        "chunking": CHUNKING,
        "chunking_sha256": CHUNKING_SHA,
        "cache": cfg.cache_path(root),
        "release_cache": root / cfg.release_notes.cache_dir,
        "committable_files": [],
    }
    fields.update(changes)
    return ValidateInputs(**fields)


def _rules(report: ValidationReport) -> set[str]:
    return {f.rule for f in report.errors}


def _rewrite(root: Path, rel: str, edit: Any) -> None:
    """Apply ``edit(records) -> records`` to a JSONL output."""
    path = root / rel
    records = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    records = edit(records)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), "utf-8")


def _first(records: list[dict[str, Any]], **match: Any) -> dict[str, Any]:
    return next(r for r in records if all(r.get(k) == v for k, v in match.items()))


def _set(rel: str, changes: dict[str, Any], **match: Any) -> Any:
    def edit(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _first(records, **match).update(changes)
        return records

    return rel, edit


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------- clean corpus, read-only


def test_clean_fixture_corpus_validates_and_nothing_is_written(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    before = _snapshot(tmp_path)
    report = validate_corpus(inp)
    assert report.errors == [] and report.missing == [] and report.exit_code == 0
    assert report.counts["unresolved"] == 0 and report.counts["chunks"]
    coverage = report.counts["coverage"]
    assert isinstance(coverage, dict) and coverage["pages"] and coverage["q7_transformations"] == 8
    assert _snapshot(tmp_path) == before  # read-only, including the caches


def test_without_caches_the_cache_tier_is_a_warning(upstream: Upstream, tmp_path: Path) -> None:
    report = validate_corpus(
        _inputs(upstream, tmp_path, cache=None, release_cache=None, committable_files=None)
    )
    skipped = [f.message for f in report.warnings if "skipped" in f.message]
    assert len(skipped) == 3  # docs coverage, release coverage, leak scan
    # R7e fails closed (owner, 2026-11-23): without the file list it is an error, not a skip.
    assert [(f.rule, f.message) for f in report.errors] == [
        ("R7e", "short-quotation scan cannot run: the committable file list is unavailable")
    ]
    assert report.exit_code == 1


# ---------------------------------------------------------------- build manifest and outputs


def test_missing_build_manifest_or_listed_output_is_exit_2(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    (tmp_path / BUILD_REPORT).unlink()  # listed in the build manifest
    report = validate_corpus(inp)
    assert report.exit_code == 2 and BUILD_REPORT in report.missing
    (tmp_path / BUILD_MANIFEST).unlink()
    assert validate_corpus(inp).missing[0] == BUILD_MANIFEST


def test_missing_chunk_file_is_exit_2(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    (tmp_path / "data/build/chunks/1.4.jsonl").unlink()
    report = validate_corpus(inp)
    assert report.exit_code == 2 and "data/build/chunks/1.4.jsonl" in report.missing


def test_hash_mismatch_of_a_listed_output_is_exit_1(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    (tmp_path / BUILD_REPORT).write_bytes((tmp_path / BUILD_REPORT).read_bytes() + b" ")
    report = validate_corpus(inp)
    assert report.exit_code == 1
    assert any(f.rule == "R9c" and "build_report.json: sha256" in f.message for f in report.errors)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda m: m.update(schema_version=2), "schema_version"),
        (lambda m: m["outputs"].append(dict(m["outputs"][0])), "listed twice"),
        (lambda m: m["outputs"].pop(), "does not list"),
        (lambda m: m["outputs"][0].update(path="../escape.jsonl"), "unsafe path"),
        (lambda m: m["outputs"][0].update(path="data/build/other.jsonl"), "unknown output"),
        (lambda m: m["outputs"][1].update(records=1), "record count"),
        (lambda m: m["outputs"][1].update(bytes=1), "byte count"),
        (lambda m: m["inputs"].update(chunking_config_sha256="0" * 64), "chunking_config"),
        (lambda m: m.update(logical_corpus=[]), "logical_corpus"),
    ],
)
def test_build_manifest_schema_paths_counts_and_hashes(
    upstream: Upstream, tmp_path: Path, edit: Any, expected: str
) -> None:
    inp = _inputs(upstream, tmp_path)
    path = tmp_path / BUILD_MANIFEST
    manifest = json.loads(path.read_bytes())
    edit(manifest)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = validate_corpus(inp)
    messages = [f.message for f in report.errors] + report.missing
    assert any(expected in m for m in messages), messages
    assert report.exit_code in (1, 2)


def test_unparseable_present_output_is_an_error_not_exit_2(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    (tmp_path / "data/manifest/chunk_manifest.json").write_text("{not json", encoding="utf-8")
    report = validate_corpus(inp)
    assert report.exit_code == 1 and report.missing == []
    assert any("chunk_manifest.json: unreadable" in f.message for f in report.errors)


# ---------------------------------------------------------------- per-chunk and corpus rules

V20 = "data/build/chunks/2.0.jsonl"
REL = "data/build/chunks/github_releases.jsonl"


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        (_set(V20, {"source_path": "docs/2.0/nowhere.md"}, source_kind="docs_page"), "R2a"),
        (
            _set(V20, {"source_url": "https://example.invalid/x.html"}, source_kind="docs_page"),
            "R2b",
        ),
        (_set(V20, {"source_url": "https://example.invalid/x"}, source_kind="release_post"), "R2b"),
        (_set(REL, {"source_url": "https://github.com/x/releases/tag/v1"}), "R2b"),
        (_set(V20, {"section_path": ["Wrong root"]}, source_kind="docs_page"), "R3"),
        (_set(V20, {"behaviour_package": "2.0.0"}, source_kind="docs_page"), "R5"),
        (_set(V20, {"introduced_in": "2.0.0"}, source_kind="docs_page"), "R6"),
        (_set(V20, {"introduced_in": "1.5.6"}, source_kind="release_post"), "R6"),
        (_set(V20, {"licence": "MIT"}, source_kind="release_post"), "R7b"),
        (_set(V20, {"redistributable": True}, source_kind="release_post"), "R7c"),
        (_set(REL, {"copyright": "invented"}), "R7c"),
        (_set(V20, {"attribution": " "}, source_kind="docs_page"), "R7a"),
        (_set(V20, {"unit_kind": "api_entry", "api_signature": None}, unit_kind="prose"), "R8a"),
        (
            _set(
                V20,
                {"unit_kind": "prose_merged", "merged_section_paths": [["x"]]},
                unit_kind="prose",
            ),
            "R8b",
        ),
        (_set(V20, {"token_count_approx": 600}, oversize=False), "R8d"),
        (_set(V20, {"oversize": True}, oversize=False), "R8d"),
    ],
)
def test_each_record_rule_is_enforced(
    upstream: Upstream, tmp_path: Path, change: Any, rule: str
) -> None:
    inp = _inputs(upstream, tmp_path)
    _rewrite(tmp_path, *change)
    report = validate_corpus(inp)
    assert rule in _rules(report), sorted(f"{f.rule}: {f.message}" for f in report.errors)
    assert report.exit_code == 1


def test_chunk_in_the_wrong_version_file_and_duplicate_ids(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    moved = (tmp_path / "data/build/chunks/1.2.jsonl").read_text("utf-8").splitlines()[0]
    with (tmp_path / "data/build/chunks/1.3.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(moved + "\n")
    rules = _rules(validate_corpus(inp))
    assert {"R1a", "R12a"} <= rules


def test_duplicate_or_missing_parts_are_detected(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    _rewrite(tmp_path, V20, lambda rs: [*rs, dict(rs[0], chunk_id="c-" + "0" * 20)])
    assert "R8c" in _rules(validate_corpus(inp))


def test_unresolved_source_appearing_as_a_chunk_is_an_error(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    path = json.loads((tmp_path / V20).read_text("utf-8").splitlines()[0])["source_path"]
    record = {
        "detecting_step": "test",
        "kind": "release_post",
        "path": path,
        "reason": "unmapped_release_post",
        "sha256": "0" * 64,
    }
    (tmp_path / "data/build/unresolved.jsonl").write_text(json.dumps(record) + "\n", "utf-8")
    assert "R1c" in _rules(validate_corpus(inp))


def test_manifest_completeness_and_m1_evidence(upstream: Upstream, tmp_path: Path) -> None:
    bad = make_config(upstream, upstream.commit1, **{"1.4": {"content_hash": "0" * 64}})
    inp = _inputs(upstream, tmp_path, cfg=bad)
    assert "R9a" in _rules(validate_corpus(inp))
    good = _inputs(upstream, tmp_path)
    main = json.loads(good.manifest_path.read_bytes())
    main["excluded_files"][0]["reason"] = " "
    good.manifest_path.write_text(json.dumps(main), encoding="utf-8")
    assert "R9b" in _rules(validate_corpus(good))


# ---------------------------------------------------------------- source coverage (S5b-D1)


def _chunked(body: str, path: str = "docs/2.0/page.md") -> tuple[Any, Any]:
    doc = parse_page(
        raw=("---\ntitle: Page\n---\n" + body).encode(),
        version="2.0",
        path=path,
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
    )
    tree = build_tree(doc, CHUNKING)
    page = chunk_page(doc, tree, page_source_fixture(path=path), CHUNKING, CHUNKING_SHA)
    return doc, page


def _words(n: int, tag: str) -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def _long_page() -> str:
    table = "\n".join(
        ["| k | v |", "|---|---|", *(f"| r{i} | {_words(18, f'r{i}c')} |" for i in range(60))]
    )
    items = "\n".join(f"- item{i} {_words(20, f'i{i}x')}" for i in range(40))
    paras = "\n\n".join(_words(n, f"p{i}x") for i, n in enumerate([200, 140, 30, 20, 200]))
    return f"## T\n\n{table}\n\n## L\n\n{items}\n\n## O\n\n{paras}\n\n## Empty\n\n## Last\n\nend\n"


def test_coverage_accepts_legitimate_transformations() -> None:
    doc, page = _chunked(_long_page())
    assert any(c.table_header_repeated for c in page.chunks)  # split table, repeated header
    assert any(c.overlap_prev_blocks for c in page.chunks)  # overlap
    cov = source_coverage(doc, page.chunks, page.empty_sections)
    assert cov.problems == [] and cov.split == 2 and cov.verbatim > 0 and cov.headings == 5


def test_coverage_reports_a_missing_table_row_or_list_item() -> None:
    doc, page = _chunked(_long_page())
    for target, needle in ((True, "| r37 |"), (False, "- item23 ")):
        chunks = [
            c.model_copy(
                update={
                    "content": "\n".join(
                        ln for ln in c.content.split("\n") if not ln.startswith(needle)
                    )
                }
            )
            if (c.has_table == target and needle in c.content)
            else c
            for c in page.chunks
        ]
        problems = source_coverage(doc, chunks, page.empty_sections).problems
        assert len(problems) == 1 and "1 of" in problems[0] and "parts missing" in problems[0]


def test_coverage_reports_a_missing_chunk_heading_and_order() -> None:
    doc, page = _chunked(_long_page())
    without_last = [c for c in page.chunks if c.section_path[-1] != "Last"]
    problems = source_coverage(doc, without_last, page.empty_sections).problems
    assert any("heading not represented" in p for p in problems)
    assert any("paragraph block not represented" in p for p in problems)
    reordered = [page.chunks[-1], *page.chunks[:-1]]
    reordered = [c.model_copy(update={"unit_ordinal": i}) for i, c in enumerate(reordered)]
    assert any(
        "out of source order" in p
        for p in source_coverage(doc, reordered, page.empty_sections).problems
    )


def test_coverage_distinguishes_q7_transformations_from_missing_text() -> None:
    raw = release_body("v1.2.1").encode("utf-8")
    doc = parse_page(
        raw=raw,
        version="1.2",
        path="releases/tag/v1.2.1",
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
        source_kind="github_release",
        url="https://github.com/duckdb/duckdb/releases/tag/v1.2.1",
    )
    q7 = apply_q7(doc, raw.decode("utf-8"))
    page = chunk_page(
        q7.doc,
        build_tree(q7.doc, CHUNKING),
        page_source_fixture(path="releases/tag/v1.2.1"),
        CHUNKING,
        CHUNKING_SHA,
    )
    assert source_coverage(q7.doc, page.chunks, page.empty_sections).problems == []
    # Compared with the untransformed text the PR lines differ: the transformation is visible.
    assert source_coverage(doc, page.chunks, page.empty_sections).problems


# ---------------------------------------------------------------- leak scan (S5b-D5)


def test_leak_scan_finds_release_text_post_text_and_handles_without_printing_them(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    assert inp.cache is not None
    post = next((inp.cache / "_posts").glob("*announcing-duckdb-201.md"))
    post_line = next(
        ln
        for ln in post.read_text("utf-8").splitlines()
        if len(ln.strip()) > 25 and not ln.startswith(("title:", "---"))
    )
    release_line = "Faster joins by @bob in https://github.com/duckdb/duckdb/pull/2121"
    planted = tmp_path / "planted.md"
    planted.write_text(f"{release_line}\n{post_line}\nthanks @alice\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("nothing restricted here\n", encoding="utf-8")
    report = validate_corpus(_inputs(upstream, tmp_path, committable_files=[planted, clean]))
    leaks = [f.message for f in report.errors if f.rule == "R7d"]
    assert any("release-note line" in m for m in leaks)
    assert any("release-post line" in m for m in leaks)
    assert any("contributor handle" in m for m in leaks)
    assert all(m.startswith("planted.md:") for m in leaks)
    assert not any(release_line in m or post_line in m or "@alice" in m for m in leaks)
    assert report.exit_code == 1


# ---------------------------------------------------------------- CLI exit codes (S5-D4)


@pytest.mark.parametrize(
    ("report", "code"),
    [
        (ValidationReport(), 0),
        (ValidationReport(findings=[Finding("warning", "R11", "near-empty")]), 0),
        (ValidationReport(findings=[Finding("error", "R4", "hash")]), 1),
        (ValidationReport(missing=["data/build/build_report.json"]), 2),
    ],
)
def test_cli_exit_codes_follow_the_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    report: ValidationReport,
    code: int,
) -> None:
    monkeypatch.setattr(commands, "validate_corpus", lambda inputs: report)
    assert main(["corpus", "validate", "--root", str(ROOT)]) == code
    out = capsys.readouterr().out
    assert ("PASSED" in out) == (code == 0)


def test_cli_unloadable_manifest_and_missing_config_are_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def unloadable(inputs: ValidateInputs) -> ValidationReport:
        raise ValidationUsageError("corpus manifest unreadable")

    monkeypatch.setattr(commands, "validate_corpus", unloadable)
    assert main(["corpus", "validate", "--root", str(ROOT)]) == 2
    assert main(["corpus", "validate", "--root", str(tmp_path)]) == 2  # no configs there


# ---------------------------------------------------------------- leak-scan policy (S5b review)
# No real release-post text appears in this file: the documented quotations are recovered at
# test time from the immutable committed files by their recorded sha256 fingerprints.


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _excluded(post_text: str, docs_text: str) -> set[str]:
    """Post lines that the approved rule treats as shared with the docs (not restricted)."""
    return shared_with_docs(
        post_restricted_lines(post_text),
        fenced_code_lines(post_text),
        docs_corpus_lines(docs_text),
        fenced_code_lines(docs_text),
    )


def _quoted(exc: LeakException) -> tuple[str, str]:
    """(whole file line, restricted substring) for an exception, found by fingerprint."""
    line = (ROOT / exc.path).read_text(encoding="utf-8").splitlines()[exc.line - 1]
    assert _sha(line) == exc.line_sha256, f"{exc.path}:{exc.line} changed"
    for i in range(len(line)):
        for j in range(i + LEAK_LINE_CHARS + 1, len(line) + 1):
            if _sha(line[i:j]) == exc.matched_sha256:
                return line, line[i:j]
    raise AssertionError(f"{exc.path}:{exc.line}: quoted text not found")


def test_lines_shared_with_the_versioned_docs_are_not_restricted() -> None:
    # Shapes of the three corpus-overlap false positives: a SQL clause and a markdownlint
    # comment that occur both in a release post and in the MIT versioned docs.
    sql = "WHEN MATCHED THEN UPDATE SET"
    lint = "<!-- markdownlint-disable MD001 -->"
    post_only = "Fixture Heading That Exists Only In The Post"
    post = f"---\ntitle: x\n---\n\n## {post_only}\n\n{lint}\n\n```sql\n{sql}\n    v = 1;\n```\n"
    # As in the real corpus: the clause ends a post line but starts a longer docs line.
    docs = f"# Merge\n\n{lint}\n\n```sql\nMERGE INTO t USING s\n    {sql} v = s.v;\n```\n"
    restricted = post_restricted_lines(post)
    assert {sql, lint, post_only} <= restricted
    assert sql not in docs_corpus_lines(docs)  # not an identical docs line
    remaining = restricted - _excluded(post, docs)
    assert sql not in remaining and lint not in remaining and post_only in remaining
    for rel, text in [
        ("scripts/probes/x.py", f'    "MERGE INTO t USING s ON t.id = s.id {sql} v = s.v;"\n'),
        ("docs/design/m1.md", f'        "MERGE INTO t USING s ON t.id = s.id {sql} v = s.v;"\n'),
        ("docs/reports/s.md", f" 6  {lint}\n| 6 | html_comment_removed | {lint}  |\n"),
    ]:
        assert count_leaks(rel, text, remaining, ()) == (0, 0)
        assert count_leaks(rel, text, restricted, ())[0] == 1  # without the rule: a finding
    # Release-note lines get the same rule (here a code line of a release body).
    body = f"```sql\n{sql}\n```\n"
    release = release_restricted_lines(body)
    lines, code = docs_corpus_lines(docs), fenced_code_lines(docs)
    assert shared_with_docs(release, fenced_code_lines(body), lines, code) == {sql}
    assert shared_with_docs(release, set(), lines, code) == set()  # not code in its source


def test_no_leak_exceptions_remain() -> None:
    """Owner 2026-11-23: every heading locator was replaced by a neutral section identifier."""
    assert LEAK_EXCEPTIONS == ()


def test_an_exception_covers_only_its_exact_line() -> None:
    heading = "Fixture Heading Text For The Exact Line Rule"
    line = f"- cites `### {heading}` as the source"
    exc = LeakException("docs/x.md", 2, _sha(line), _sha(heading), "fixture")
    lines = ["intro", line, "outro"]
    restricted = {heading}
    assert count_leaks("docs/x.md", "\n".join(lines), restricted, (exc,)) == (0, 1)
    assert count_leaks("docs/x.md", "\n".join(lines), restricted, ()) == (1, 0)
    # another file
    assert count_leaks("docs/other.md", line + "\n", restricted, (exc,)) == (1, 0)
    # the line moved down by one
    assert count_leaks("docs/x.md", "\n".join(["", *lines]), restricted, (exc,)) == (1, 0)
    # the line edited
    assert count_leaks(
        "docs/x.md", "\n".join(["intro", line + " (edited)", "outro"]), restricted, (exc,)
    ) == (1, 0)
    # a second, uncovered occurrence
    assert count_leaks("docs/x.md", "\n".join([*lines, line]), restricted, (exc,)) == (1, 1)


def test_similar_but_unapproved_headings_are_detected() -> None:
    approved = "Breaking Change for Fixture Function"
    similar = "Breaking Changes for Fixture Function"
    line = f"- quotes the heading `### {approved}` in the evidence"
    exc = LeakException("docs/x.md", 1, _sha(line), _sha(approved), "fixture")
    assert count_leaks("docs/x.md", line, {approved}, (exc,)) == (0, 1)
    # a similar heading is a different restricted line: not covered by the exception
    line2 = f"- quotes the heading `### {similar}` in the evidence"
    assert count_leaks("docs/x.md", line2, {approved, similar}, (exc,)) == (1, 0)
    # a second restricted line on the excepted line itself is not covered either
    both = f"{line} and {similar}"
    exc2 = LeakException("docs/x.md", 1, _sha(both), _sha(approved), "fixture")
    assert count_leaks("docs/x.md", both, {approved, similar}, (exc2,)) == (1, 1)


def test_cache_absent_leak_warnings_are_unchanged(upstream: Upstream, tmp_path: Path) -> None:
    no_list = validate_corpus(_inputs(upstream, tmp_path, committable_files=None))
    assert [f.message for f in no_list.warnings if f.rule == "R7d"] == [
        "leak scan skipped: the committable file list is unavailable"
    ]
    no_caches = validate_corpus(
        _inputs(upstream, tmp_path, cache=None, release_cache=None, committable_files=[])
    )
    assert [f.message for f in no_caches.warnings if f.rule == "R7d"] == [
        "leak scan for release notes skipped: release cache unavailable",
        "leak scan for release posts skipped: docs cache unavailable",
    ]
    # R7e fails closed without the release posts; nothing else becomes an error.
    assert [(f.rule, f.message) for f in no_caches.errors] == [
        ("R7e", "short-quotation scan cannot run: release posts unavailable")
    ]


# ---------------------------------------------------------------- docs-overlap rule: adversarial
# The approved rule (owner, S5b review): identical normalised lines, or code-block lines contained
# token-aligned, with identical whitespace, in a docs code-block line. These cases were first run
# against the earlier plain-containment rule, which got five of them wrong.


def test_adversarial_real_legitimate_containment_shapes_are_excluded() -> None:
    # The three real cases (#1, #4, #5): a post wraps a code line that a docs example continues.
    cases = [
        (
            "FROM 'https://blobs.duckdb.org/data/shakespeare.parquet';",
            "    SELECT * FROM 'https://blobs.duckdb.org/data/shakespeare.parquet';",
        ),
        ("WHEN MATCHED THEN UPDATE SET", "    WHEN MATCHED THEN UPDATE SET salary = s.salary;"),
        ("WHEN NOT MATCHED THEN INSERT", "    WHEN NOT MATCHED THEN INSERT BY NAME"),
    ]
    for post_line, docs_line in cases:
        post = f"```sql\nSELECT *\n        {post_line}\n```\n"
        docs = f"```sql\n{docs_line}\n```\n"
        assert _excluded(post, docs) == {post_line}


def test_adversarial_real_cases_no_longer_shared_stay_restricted() -> None:
    # Real cases #2 and #3: code in the post, a blockquote (not code) in the docs.
    line = "SET lambda_syntax = 'fixture_value_here';"
    assert _excluded(f"```sql\n{line}\n```\n", f"> {line}\n") == set()
    # Code in the docs but prose in the post is not shared either.
    assert _excluded(f"{line} is new\n", f"```sql\nRESET x; {line}\n```\n") == set()


def test_adversarial_post_only_sentence_inside_a_longer_docs_sentence_is_detected() -> None:
    sentence = "Joins on wide tables are now considerably faster."
    docs = f"Note that {sentence} The guide below explains why.\n"
    assert sentence not in _excluded(f"{sentence}\n", docs)


def test_adversarial_post_only_heading_inside_a_longer_docs_line_is_detected() -> None:
    heading = "Breaking Change for Fixture Function"
    docs = f"See {heading} in the migration notes for details.\n"
    assert heading not in _excluded(f"## {heading}\n", docs)


def test_adversarial_sql_fragment_of_a_docs_code_example_is_excluded() -> None:
    post = "```sql\nMERGE INTO t USING s\n    WHEN NOT MATCHED THEN INSERT\n```\n"
    docs = "```sql\nMERGE INTO t USING s\n    WHEN NOT MATCHED THEN INSERT BY NAME;\n```\n"
    assert _excluded(post, docs) == {"WHEN NOT MATCHED THEN INSERT"}


def test_adversarial_sql_fragment_with_a_changed_token_is_detected() -> None:
    post = "```sql\n    WHEN MATCHED THEN DELETE WHERE v > 0\n```\n"
    docs = "```sql\n    WHEN MATCHED THEN UPDATE SET v = s.v;\n```\n"
    assert _excluded(post, docs) == set()


def test_adversarial_partial_token_overlap_is_not_a_match() -> None:
    # "> 10" is a prefix of "> 100": a different query, matched only mid-token.
    post = "```sql\nSELECT count(*) FROM tbl WHERE a > 10\n```\n"
    docs = "```sql\nSELECT count(*) FROM tbl WHERE a > 100;\n```\n"
    assert _excluded(post, docs) == set()


def test_adversarial_box_border_of_another_width_is_not_a_match() -> None:
    # The shape of real containment match 6: a narrower table border inside a wider one.
    narrow = "─" * 14 + " main " + "─" * 14
    wide = "─" * 20 + " main " + "─" * 20
    assert _excluded(f"```\n{narrow}\n```\n", f"```\n{wide}\n```\n") == set()


def test_adversarial_stripped_markup_does_not_create_a_match() -> None:
    # The post heading loses "### "; the docs line loses its leading "**": the remaining texts
    # overlap only because of normalisation plus containment.
    text = "Fixture post-only heading text here"
    assert _excluded(f"### {text}\n", f"**{text}** appears in bold in the docs\n") == set()


def test_adversarial_text_spanning_two_docs_lines_is_not_a_match() -> None:
    post = "SELECT a, b FROM wide_table_name WHERE x > 1\n"
    docs = "```sql\nSELECT a, b\nFROM wide_table_name WHERE x > 1\n```\n"
    assert _excluded(post, docs) == set()


def test_adversarial_identical_lines_are_shared_whatever_their_markup() -> None:
    # Exact equality after normalisation (the 118 identical-line cases).
    text = "Fixture line that both sources contain verbatim"
    assert _excluded(f"## {text}\n", f"{text}\n") == {text}


def test_rag_m1_lines_107_and_109_cite_sections_without_their_headings() -> None:
    """Owner 2026-11-23: the heading locators were replaced by neutral section identifiers; no
    substring of either line may match a release-post heading fingerprint again."""
    lines = (ROOT / "docs/design/RAG-M1_research_evidence.md").read_text("utf-8").splitlines()
    fingerprints = {_POST_130_HEADING, _POST_150_HEADING}
    other = (ROOT / "docs/design/RAG-M3_S3_parsing.md").read_text("utf-8").splitlines()
    for line, section in (
        (lines[106], "release-post section A"),
        (lines[108], "release-post section B"),
        (lines[216], "release-post section B"),
        (other[358], "release-post section B"),
    ):
        found = any(
            _sha(line[i:j]) in fingerprints
            for i in range(len(line))
            for j in range(i + LEAK_LINE_CHARS + 1, len(line) + 1)
        )
        assert section in line and not found, section


# ---------------------------------------------------------------- R7e: short quotations
# Synthetic text only: no real release-post wording appears in these tests.
POST = quote_key(
    "Intro. The frobnicator now rejects every zorbled widget by default. "
    "Shared sentence about fixture widgets and gadgets. "
    "Runtime says: Invalid Input Error: fixture zorble overflow detected."
)
DOCS = quote_key("Docs page. Shared sentence about fixture widgets and gadgets. More docs.")
RUNTIME = quote_key("Invalid Input Error: fixture zorble overflow detected")
PERMITTED = DOCS + " \x00 " + RUNTIME


def test_r7e_finds_a_genuine_short_quotation() -> None:
    text = 'line one\nThe post said "rejects every zorbled widget" here.\n'
    assert restricted_quotes(text, POST, PERMITTED) == [(2, 4)]


def test_r7e_handles_edge_attached_quotes_and_punctuation() -> None:
    for line in (
        'As written:"The frobnicator now rejects."',  # quote attached, full stop inside
        "It reads \u201cthe frobnicator now rejects every\u201d, then more.",  # curly quotes
        '("Rejects every zorbled widget by default.")',  # brackets and full stop
        '"`The frobnicator` now rejects"',  # backticks inside the quotation
    ):
        assert restricted_quotes(line, POST, PERMITTED) == [(1, 4)] or restricted_quotes(
            line, POST, PERMITTED
        ) == [(1, len(quoted_phrases(line)[0].split()))], line


def test_r7e_ignores_phrases_shared_with_the_mit_docs() -> None:
    line = 'The docs say "shared sentence about fixture widgets and gadgets".'
    assert contains_phrase(POST, quoted_phrases(line)[0])
    assert restricted_quotes(line, POST, PERMITTED) == []


def test_r7e_ignores_confirmed_runtime_output() -> None:
    line = 'DuckDB prints "Invalid Input Error: fixture zorble overflow detected".'
    assert contains_phrase(POST, quoted_phrases(line)[0])
    assert restricted_quotes(line, POST, PERMITTED) == []


def test_r7e_needs_four_words_and_word_boundaries() -> None:
    assert restricted_quotes('"rejects every zorbled"', POST, PERMITTED) == []  # 3 words
    assert quoted_phrases('"a b c d"') == ["a b c d"]
    assert not contains_phrase(
        quote_key("xrejects every zorbled widget"), "rejects every zorbled widget"
    )
    assert not contains_phrase(
        quote_key("rejects every zorbled widgets"), "rejects every zorbled widget"
    )


def test_r7e_fails_closed_when_a_release_post_changed(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    assert inp.cache is not None
    post = next(p for p in sorted((inp.cache / "_posts").glob("*.md")))
    post.write_bytes(post.read_bytes() + b"\nchanged\n")
    errors = [f for f in validate_corpus(inp).errors if f.rule == "R7e"]
    assert len(errors) == 1 and "missing or changed" in errors[0].message


def test_r7e_reports_file_line_and_word_count_only(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    assert inp.cache is not None
    post = next(p for p in sorted((inp.cache / "_posts").glob("*.md")))
    # the fixture post's own sentence (synthetic): "DuckDB <release> is out."
    phrase = next(ln for ln in post.read_text("utf-8").splitlines() if ln.endswith(" is out."))
    doc = tmp_path / "docs" / "note.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(f'intro\nquoting "{phrase}" here\n', encoding="utf-8")
    report = validate_corpus(_inputs(upstream, tmp_path, committable_files=[doc]))
    found = [f.message for f in report.errors if f.rule == "R7e"]
    words = len(quote_key(phrase).split())
    expected = f"docs/note.md:2: quoted phrase of {words} words matches release-post text"
    assert found == [expected] and phrase not in found[0]
