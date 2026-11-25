"""RAG-M3 S5a on the real corpus: two independent builds, S4 equivalence, committed manifest.

The builds write into temporary roots only; the real caches and the repository's outputs are read,
never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from version_aware_rag.corpus import validate
from version_aware_rag.corpus.build import (
    ALL_OUTPUTS,
    BUILD_MANIFEST,
    BUILD_REPORT,
    S4C_OUTPUTS,
    S4D_OUTPUTS,
    UNRESOLVED_FILE,
    BuildInputs,
    run_build,
)
from version_aware_rag.corpus.config import (
    load_chunking_config,
    load_config,
    load_parsing_config,
)
from version_aware_rag.corpus.manifest import load_manifest

pytestmark = pytest.mark.corpus

REPO = Path(__file__).resolve().parents[2]


def _inputs(out_root: Path) -> BuildInputs:
    cfg, cfg_sha = load_config(REPO / "configs/corpus.toml")
    cache = cfg.cache_path(REPO)
    if not (cache / ".git").is_dir():
        pytest.skip(f"corpus cache not fetched at {cache}")
    parsing, parsing_sha = load_parsing_config(REPO / "configs/parsing.toml")
    chunking, chunking_sha = load_chunking_config(REPO / "configs/chunking.toml")
    return BuildInputs(
        root=out_root,
        cfg=cfg,
        cfg_sha256=cfg_sha,
        cache=cache,
        release_cache=REPO / cfg.release_notes.cache_dir,
        manifest_path=REPO / cfg.paths.manifest,
        parsing=parsing,
        parsing_sha256=parsing_sha,
        chunking=chunking,
        chunking_sha256=chunking_sha,
    )


@pytest.fixture(scope="module")
def two_builds(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    a, b = tmp_path_factory.mktemp("build-a"), tmp_path_factory.mktemp("build-b")
    for root in (a, b):
        result = run_build(_inputs(root), check=False)
        assert result.written == list(ALL_OUTPUTS)
    return a, b


def test_two_independent_real_builds_are_byte_identical(two_builds: tuple[Path, Path]) -> None:
    a, b = two_builds
    assert {p: (a / p).read_bytes() for p in ALL_OUTPUTS} == {
        p: (b / p).read_bytes() for p in ALL_OUTPUTS
    }


def test_s4_outputs_equal_those_of_the_s4_commands(two_builds: tuple[Path, Path]) -> None:
    a, _ = two_builds
    for rel in (*S4C_OUTPUTS, *S4D_OUTPUTS):
        repo_file = REPO / rel
        if not repo_file.is_file():
            pytest.skip(f"{rel} has not been generated in this checkout")
        assert (a / rel).read_bytes() == repo_file.read_bytes(), rel


def test_committed_build_manifest_equals_a_fresh_build(two_builds: tuple[Path, Path]) -> None:
    a, _ = two_builds
    assert (REPO / BUILD_MANIFEST).read_bytes() == (a / BUILD_MANIFEST).read_bytes()
    manifest = json.loads((a / BUILD_MANIFEST).read_bytes())
    assert manifest["counts"] == {
        "by_version": {"1.2": 4757, "1.3": 5500, "1.4": 5794, "1.5": 6456, "2.0": 9151},
        "empty_sections": 193,
        "logical_corpus_chunks": 31680,
        "pages": 2022,
        "release_note_chunks": 22,
        "unresolved": 0,
        "version_file_chunks": 31658,
    }
    assert (a / UNRESOLVED_FILE).read_bytes() == b""  # no unresolved source in the real corpus


def _git_digest(git_dir: Path) -> dict[str, str]:
    return {
        p.relative_to(git_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(git_dir.rglob("*"))
        if p.is_file()
    }


def test_check_mode_on_the_repository_changes_nothing() -> None:
    if not (REPO / "data/build/chunks/1.2.jsonl").is_file():
        pytest.skip("chunk outputs not generated in this checkout")
    inputs = _inputs(REPO)
    before = {p: (REPO / p).read_bytes() for p in ALL_OUTPUTS if (REPO / p).is_file()}
    build_tree = {p.relative_to(REPO).as_posix() for p in (REPO / "data/build").rglob("*")}
    git_before = _git_digest(inputs.cache / ".git")  # read only; never modified by this test
    result = run_build(inputs, check=True)
    assert result.problems == [] and result.written == [] and result.recovered == []
    assert {p: (REPO / p).read_bytes() for p in before} == before
    assert {p.relative_to(REPO).as_posix() for p in (REPO / "data/build").rglob("*")} == build_tree
    assert _git_digest(inputs.cache / ".git") == git_before  # no lazy fetch, no git writes
    assert "GIT_NO_LAZY_FETCH" not in os.environ


# ---------------------------------------------------------------- S5c extended build report


def test_s5c_extended_report_on_the_real_corpus(two_builds: tuple[Path, Path]) -> None:
    a, _ = two_builds
    report = json.loads((a / BUILD_REPORT).read_bytes())
    assert report["schema_version"] == 2
    warnings = report["warnings"]
    assert warnings["oversize"]["count"] == 171
    assert warnings["oversize"]["by_reason"] == {
        "api_entry_block": 4,
        "code_block": 164,
        "list_item": 3,
    }
    assert [(p["source_path"], p["words"]) for p in warnings["near_empty_pages"]["pages"]] == [
        ("docs/1.2/extensions/troubleshooting.md", 19),
        ("docs/1.2/sql/statements/create_secret.md", 12),
        ("docs/1.3/extensions/troubleshooting.md", 19),
        ("docs/1.3/sql/statements/create_secret.md", 12),
        ("docs/1.5/quack/setup/overview.md", 11),
        ("docs/2.0/quack/setup/overview.md", 11),
    ]  # S5-D7: listed, their chunks kept
    assert warnings["section_path_fallback"]["count"] == 22
    duplicates = report["duplicates"]
    assert (duplicates["count"], duplicates["chunks_in_groups"]) == (6283, 23691)
    assert duplicates["by_version_count"] == {"2": 2199, "3": 1304, "4": 1203, "5": 1577}
    assert sum(duplicates["by_version_pair"].values()) == 29099
    hist = report["sizes"]["token_histogram"]
    assert [h["chunks"] for h in hist["logical_corpus"]] == [
        7078,
        8754,
        7843,
        4513,
        1911,
        1404,
        6,
        171,
    ]
    parse = report["parse_warnings"]
    assert (parse["count"], parse["pages"]) == (52, 24)
    assert parse["by_code"] == {
        "api_dt_without_id": 14,
        "front_matter_missing": 4,
        "html_block_generic_text": 30,
        "liquid_for_loop_removed": 1,
        "liquid_tag_unsupported": 1,
        "markdown_link_unparsed": 2,
    }
    kinds = Counter(r["source_kind"] for r in parse["records"])
    assert kinds == {"docs_page": 42, "github_release": 4, "release_post": 6}  # restricted kept
    includes = report["includes"]
    assert (includes["dropped"]["count"], includes["partial"]["count"]) == (31, 86)
    assert {k: (v["class"], v["count"]) for k, v in includes["rules"].items()} == {
        "include_filterbar_removed": ("dropped", 1),
        "include_iframe_marked": ("partial", 8),
        "include_latest_client_removed": ("dropped", 30),
        "include_tooltip_label_kept": ("partial", 78),
    }
    records = includes["dropped"]["records"] + includes["partial"]["records"]
    assert all(isinstance(r["include_in_corpus"], bool) for r in records)
    excluded = [r for r in records if not r["include_in_corpus"]]
    assert [r["code"] for r in excluded] == ["include_filterbar_removed"]  # on an excluded page


def test_s5c_report_agrees_with_s5b_validation(two_builds: tuple[Path, Path]) -> None:
    a, _ = two_builds
    report = json.loads((a / BUILD_REPORT).read_bytes())
    checked = validate.ValidationReport()
    validate.add_warnings(validate.load_chunks(a), checked)
    assert report["warnings"]["oversize"]["by_reason"] == checked.counts["oversize"]
    assert report["warnings"]["near_empty_pages"]["count"] == checked.counts["near_empty_pages"]
    assert (
        report["warnings"]["section_path_fallback"]["count"]
        == checked.counts["section_path_fallback"]
    )
    assert report["duplicates"]["count"] == checked.counts["cross_version_duplicates"]


def test_s5c_report_passes_the_s5b_leak_scan_and_holds_no_corpus_text(
    two_builds: tuple[Path, Path],
) -> None:
    a, _ = two_builds
    inp = _inputs(a)
    manifest, problems = load_manifest(inp.manifest_path)
    assert manifest is not None and problems == []
    found = validate.ValidationReport()
    leak_inputs = validate.ValidateInputs(
        root=a,
        cfg=inp.cfg,
        cfg_sha256=inp.cfg_sha256,
        manifest_path=inp.manifest_path,
        parsing=inp.parsing,
        parsing_sha256=inp.parsing_sha256,
        chunking=inp.chunking,
        chunking_sha256=inp.chunking_sha256,
        cache=inp.cache,
        release_cache=inp.release_cache,
        committable_files=[a / BUILD_REPORT],
    )
    validate.check_leaks(leak_inputs, manifest, found)
    assert found.findings == []  # no release-note line, release-post line or handle
    leak = found.counts["leak_scan"]
    assert isinstance(leak, dict) and leak["files_scanned"] == 1
    # Stricter than the leak scan: no chunk text at all, MIT docs included.
    text = (a / BUILD_REPORT).read_text(encoding="utf-8")
    lines = {
        s
        for lc in validate.load_chunks(a)
        for ln in lc.chunk.content.splitlines()
        if len(s := ln.strip()) > 25
    }
    assert lines and not [s for s in lines if s in text]
