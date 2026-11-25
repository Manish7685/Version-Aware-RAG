"""S4c chunk outputs: files, chunk manifest, validation and report (fixtures)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from version_aware_rag.corpus.chunk import ChunkedPage, PageSource, chunk_page
from version_aware_rag.corpus.chunk_output import (
    CHUNK_FILES,
    EMPTY_FILE,
    BuildContext,
    chunk_file_problems,
    render_chunk_files,
    render_chunk_manifest,
    render_chunk_report,
)
from version_aware_rag.corpus.config import load_chunking_config
from version_aware_rag.corpus.parse import ParsedDocument, parse_page
from version_aware_rag.corpus.sections import SectionTree, build_tree

ROOT = Path(__file__).resolve().parents[3]
CFG, CFG_SHA = load_chunking_config(ROOT / "configs/chunking.toml")
Pages = list[tuple[ParsedDocument, SectionTree, ChunkedPage]]
CTX = BuildContext("d" * 40, "s3b-1", "s4-1", CFG_SHA, "0" * 64)
POST_MARKER = "POSTMARKERTEXT"


def _source(version: str, path: str, post: bool) -> PageSource:
    return PageSource(
        version=version,
        behaviour_package={"1.4": "1.4.5", "2.0": "2.0.1"}[version],
        source_type="changelog" if post else "documentation",
        source_kind="release_post" if post else "docs_page",
        introduced_in="2.0.1" if post else None,
        source_path=path,
        source_url=f"https://duckdb.org/{path}",
        source_repo="duckdb/duckdb-web",
        source_commit="d" * 40,
        source_git_blob="b" * 40,
        source_content_hash="0" * 64,
        release_id=None,
        source_published_at="2026-11-20" if post else None,
        source_retrieved_at=None,
        licence=None if post else "MIT",
        licence_status="unverified" if post else "verified",
        licence_applicability="uncertain" if post else None,
        redistributable=not post,
        repository_licence="MIT",
        licence_basis="fixture",
        copyright="fixture copyright",
        attribution="fixture attribution",
        copyright_date_discrepancy=True if post else None,
    )


def _page(
    body: str, version: str, path: str, post: bool = False
) -> tuple[ParsedDocument, SectionTree, ChunkedPage]:
    doc = parse_page(
        raw=f"---\ntitle: Page {path}\n---\n{body}".encode(),
        version=version,
        path=path,
        source_sha256="0" * 64,
        site_base="https://duckdb.org",
        source_kind="release_post" if post else "docs_page",
    )
    tree = build_tree(doc, CFG)
    return doc, tree, chunk_page(doc, tree, _source(version, path, post), CFG, CFG_SHA)


@pytest.fixture
def pages() -> list[tuple[ParsedDocument, SectionTree, ChunkedPage]]:
    return [
        _page(
            "## A\n\nalpha text\n\n## Empty\n\n## B\n\n```sql\nSELECT 1;\n```\n",
            "2.0",
            "docs/2.0/b.md",
        ),
        _page("## C\n\ngamma\n", "1.4", "docs/1.4/c.md"),
        _page(
            f"## Hidden Heading\n\n{POST_MARKER} body\n\n"
            f"## Hidden Empty\n\n## More\n\n{POST_MARKER}\n",
            "2.0",
            "_posts/2026-11-20-announcing-duckdb-201.md",
            post=True,
        ),
        _page("## A2\n\nmore alpha\n", "2.0", "docs/2.0/a.md"),
    ]


def _render(
    pages: list[tuple[ParsedDocument, SectionTree, ChunkedPage]],
) -> tuple[dict[str, bytes], dict[str, object]]:
    files = render_chunk_files(pages)
    return files, json.loads(render_chunk_manifest(pages, files, CTX))


def test_files_are_complete_ordered_and_deterministic(pages: Pages) -> None:
    files, manifest = _render(pages)
    assert list(files) == list(CHUNK_FILES)
    assert render_chunk_files(pages) == files
    paths = [json.loads(line)["source_path"] for line in files["2.0.jsonl"].decode().splitlines()]
    assert paths == sorted(paths)  # by source path, posts' "_posts/" first
    assert files["1.2.jsonl"] == b"" and files["1.4.jsonl"].count(b"\n") == 1
    empties = [json.loads(line) for line in files[EMPTY_FILE].decode().splitlines()]
    assert [e["section_path"][-1] for e in empties] == ["Hidden Empty", "Empty"]
    assert chunk_file_problems(files, manifest) == []
    counts = manifest["counts"]
    assert isinstance(counts, dict)
    assert counts["chunks"] == sum(len(p.chunks) for *_, p in pages)
    assert counts["not_redistributable_chunks"] == len(pages[2][2].chunks)
    assert POST_MARKER not in json.dumps(manifest)  # the manifest has hashes and counts only


@pytest.mark.parametrize(
    "tamper",
    ["missing", "unexpected", "content", "duplicate", "wrong_version", "manifest_sha", "licence"],
)
def test_validation_detects_problems(pages: Pages, tamper: str) -> None:
    files, manifest = _render(pages)
    lines = files["2.0.jsonl"].decode().splitlines()
    if tamper == "missing":
        del files["1.4.jsonl"]
    elif tamper == "unexpected":
        files["notes.txt"] = b"x"
    elif tamper == "content":
        rec = json.loads(lines[0])
        rec["content"] += " edited"
        files["2.0.jsonl"] = ("\n".join([json.dumps(rec), *lines[1:]]) + "\n").encode()
    elif tamper == "duplicate":
        files["2.0.jsonl"] = ("\n".join([lines[0], *lines]) + "\n").encode()
    elif tamper == "wrong_version":
        files["1.4.jsonl"] += (lines[-1] + "\n").encode()
    elif tamper == "manifest_sha":
        entries = manifest["files"]
        assert isinstance(entries, list)
        entries[0]["sha256"] = "f" * 64
    elif tamper == "licence":
        post = next(json.loads(x) for x in lines if '"release_post"' in x)
        post["redistributable"] = True
        files["2.0.jsonl"] = (json.dumps(post) + "\n").encode()
    assert chunk_file_problems(files, manifest) != []


def test_report_withholds_release_post_text(pages: Pages) -> None:
    files, _ = _render(pages)
    report = render_chunk_report(pages, files, CTX)
    assert (
        POST_MARKER not in report
        and "Hidden Heading" not in report
        and "Hidden Empty" not in report
    )
    assert "[withheld: licence applicability uncertain, redistributable=false]" in report
    assert "Page docs/2.0/b.md > Empty" in report  # docs headings are shown
    assert report == render_chunk_report(pages, files, CTX)
    assert all(line == line.rstrip() for line in report.split("\n"))
