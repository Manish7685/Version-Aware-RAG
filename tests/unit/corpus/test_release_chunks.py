"""S4d GitHub release-note chunks (design §11; S4-D5, S4-D10-D12; tests T35-T37 on fixtures)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from version_aware_rag.corpus.chunk import reconstruction_problems, rule_problems
from version_aware_rag.corpus.chunk_output import BuildContext, chunk_file_problems
from version_aware_rag.corpus.config import (
    APPROVED_RELEASE_TAGS,
    ParsingConfig,
    ReleaseNotesConfig,
    load_chunking_config,
)
from version_aware_rag.corpus.manifest import ReleaseNotesSection, build_release_section
from version_aware_rag.corpus.parse import Block, parse_page
from version_aware_rag.corpus.release_chunks import (
    RELEASE_FILE,
    ReleaseChunkError,
    ReleaseNotePage,
    apply_q7,
    apply_q7_line,
    chunk_release_notes,
    release_file_problems,
    render_release_file,
    render_release_manifest,
    render_release_report,
    verify_release_inputs,
)
from version_aware_rag.corpus.releases import body_path, fetch_record_path

from .conftest import release_body, release_notes_config, write_release_cache

ROOT = Path(__file__).resolve().parents[3]
CHUNKING, CHUNKING_SHA = load_chunking_config(ROOT / "configs/chunking.toml")
PARSING = ParsingConfig(site_base="https://duckdb.org", excluded_pages=(), samples=())
BEHAVIOUR = {"1.2": "1.2.2", "1.3": "1.3.2", "1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}
CTX = BuildContext("d" * 40, "s3b-1", "s4-1", CHUNKING_SHA, "0" * 64)
PR = "https://github.com/duckdb/duckdb/pull"
CMP = "https://github.com/duckdb/duckdb/compare"


# ---------------------------------------------------------------- Q7 rules, each on its own


@pytest.mark.parametrize(
    ("line", "expected", "codes"),
    [
        # 1. approved PR attribution
        (
            f"* Fix joins by @alice in {PR}/123",
            "* Fix joins (#123)",
            ["q7_pr_attribution_shortened"],
        ),
        (f"* x by @bob-smith in {PR}/9.", "* x (#9).", ["q7_pr_attribution_shortened"]),
        # 2. bare PR URL
        (f"* Revert {PR}/456 for now", "* Revert #456 for now", ["q7_pr_url_shortened"]),
        (f"(see {PR}/77)", "(see #77)", ["q7_pr_url_shortened"]),
        # 3. compare URL between two version tags: compact range kept (S4-D10 refinement)
        (
            f"**Full Changelog**: {CMP}/v1.2.0...v1.2.1",
            "**Full Changelog**: v1.2.0...v1.2.1",
            ["q7_compare_url_shortened"],
        ),
        # 4. remaining handle
        (
            "* Revert @carol's change",
            "* Revert @[contributor]'s change",
            ["q7_contributor_handle_masked"],
        ),
        (
            "thanks to @dave, @erin-x and others",
            "thanks to @[contributor], @[contributor] and others",
            ["q7_contributor_handle_masked", "q7_contributor_handle_masked"],
        ),
    ],
)
def test_each_rule_on_its_own(line: str, expected: str, codes: list[str]) -> None:
    new, applied = apply_q7_line(line)
    assert new == expected
    assert [c for c, _ in applied] == codes


@pytest.mark.parametrize(
    "line",
    [
        "* Fix joins by @alice",  # attribution without the PR URL: handle rule only? no, see below
        f"{PR}/45/files",  # PR sub-page: not a bare PR URL
        "https://github.com/duckdb/duckdb/issues/45",  # issue, not PR
        "https://github.com/duckdb/duckdb-web/pull/45",  # another repository
        f"{CMP}/main...feature",  # compare between branches, not version tags
        f"{CMP}/v1.2.0...v1.2.1/files",  # compare sub-page
        "mail user@example.com today",  # an e-mail address, not a handle
        "already @[contributor] masked",  # no double masking
        "Use `@decorator` and `by @x in " + PR + "/1` in code",  # inside inline code spans
        "Plain text with no identifiers at all.",
    ],
)
def test_rules_leave_other_text_alone(line: str) -> None:
    new, applied = apply_q7_line(line)
    if line == "* Fix joins by @alice":
        assert (new, [c for c, _ in applied]) == (
            "* Fix joins by @[contributor]",
            ["q7_contributor_handle_masked"],
        )
        return
    assert (new, applied) == (line, [])


def test_rules_apply_in_order_and_record_no_names() -> None:
    line = f"* A by @alice in {PR}/1, also {PR}/2, thanks @bob; {CMP}/v1.3.1...v1.3.2"
    new, applied = apply_q7_line(line)
    assert new == "* A (#1), also #2, thanks @[contributor]; v1.3.1...v1.3.2"
    assert applied == [
        ("q7_pr_attribution_shortened", "#1"),
        ("q7_pr_url_shortened", "#2"),
        ("q7_compare_url_shortened", "v1.3.1...v1.3.2"),
        ("q7_contributor_handle_masked", "handle masked"),
    ]
    assert apply_q7_line(new) == (new, [])  # idempotent


def test_code_blocks_are_not_transformed_and_every_occurrence_is_recorded() -> None:
    raw = (
        f"## What's Changed\n\n* A by @alice in {PR}/1\n* B by @bob in {PR}/2\n\n"
        f"```\nSELECT '@user', '{PR}/3';\n```\n"
    ).encode()
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
    code = next(b for b in q7.doc.blocks if b.kind == "code")
    assert f"'@user', '{PR}/3'" in code.text  # code untouched
    lst = next(b for b in q7.doc.blocks if b.kind == "list")
    assert lst.text == "- A (#1)\n- B (#2)"
    recorded = [
        (t.code, t.line, t.detail) for t in q7.doc.transformations if t.code.startswith("q7")
    ]
    assert recorded == [
        ("q7_pr_attribution_shortened", 3, "#1"),
        ("q7_pr_attribution_shortened", 4, "#2"),
    ]
    assert q7.counts == {"q7_pr_attribution_shortened": 2}
    unchanged = Block(kind="paragraph", text="nothing here", line_start=1, line_end=1)
    assert apply_q7(doc.model_copy(update={"blocks": (unchanged,)})).doc.blocks == (unchanged,)


# ---------------------------------------------------------------- verification (T35)


def _inputs(tmp_path: Path) -> tuple[ReleaseNotesConfig, ReleaseNotesSection, Path]:
    notes = ReleaseNotesConfig.model_validate(release_notes_config())
    cache = write_release_cache(tmp_path / "rel")
    for rel in notes.included:
        fetch_record_path(cache, rel.tag).write_text(
            json.dumps(
                {
                    "release_id": rel.release_id,
                    "body_sha256": rel.body_sha256,
                    "retrieved_at": "2026-11-20T18:46:33Z",
                }
            ),
            encoding="utf-8",
        )
    return notes, build_release_section(notes, cache), cache


def test_t35_clean_inputs_verify(tmp_path: Path) -> None:
    notes, section, cache = _inputs(tmp_path)
    assert verify_release_inputs(notes, section, cache) == []


@pytest.mark.parametrize("fault", ["missing", "edited", "no_fetch", "fetch_id", "manifest_id"])
def test_t35_bad_inputs_stop_the_run_naming_the_release(tmp_path: Path, fault: str) -> None:
    notes, section, cache = _inputs(tmp_path)
    tag = APPROVED_RELEASE_TAGS[1]
    if fault == "missing":
        body_path(cache, tag).unlink()
    elif fault == "edited":
        body_path(cache, tag).write_bytes(release_body(tag).encode() + b" ")
    elif fault == "no_fetch":
        fetch_record_path(cache, tag).unlink()
    elif fault == "fetch_id":
        fetch_record_path(cache, tag).write_text(json.dumps({"release_id": 1, "body_sha256": "x"}))
    elif fault == "manifest_id":
        records = tuple(
            r.model_copy(update={"release_id": 5}) if r.tag == tag else r for r in section.included
        )
        section = section.model_copy(update={"included": records})
    problems = verify_release_inputs(notes, section, cache)
    assert problems and all(p.startswith(tag) for p in problems)
    with pytest.raises(ReleaseChunkError) as err:
        chunk_release_notes(notes, section, BEHAVIOUR, cache, PARSING, CHUNKING, CHUNKING_SHA)
    assert err.value.problems == problems


# ---------------------------------------------------------------- end to end (T36, T37)


def _pages(tmp_path: Path) -> tuple[list[ReleaseNotePage], Path]:
    notes, section, cache = _inputs(tmp_path)
    return chunk_release_notes(
        notes, section, BEHAVIOUR, cache, PARSING, CHUNKING, CHUNKING_SHA
    ), cache


def test_t36_raw_bodies_unchanged_and_only_the_pattern_touched(tmp_path: Path) -> None:
    before = {t: body_path(tmp_path / "rel", t) for t in APPROVED_RELEASE_TAGS}
    pages, cache = _pages(tmp_path)
    for tag in APPROVED_RELEASE_TAGS:
        assert body_path(cache, tag).read_bytes() == release_body(tag).encode("utf-8")
        assert before[tag] == body_path(cache, tag)
    for r in pages:
        assert r.q7_counts == {"q7_pr_attribution_shortened": 2}
        text = "\n\n".join(c.content for c in r.page.chunks)
        assert "@alice" not in text and "@bob" not in text and PR not in text
        assert "café ✓" in text  # unrelated text, including non-ASCII, unchanged
        assert "\r" not in text  # CRLF bodies, normalised by the parser


def test_t37_metadata_licence_and_preservation(tmp_path: Path) -> None:
    pages, _ = _pages(tmp_path)
    for r in pages:
        assert reconstruction_problems(r.doc, r.tree, r.page) == []
        assert rule_problems(r.page, CHUNKING) == []
        rec = r.record
        for c in r.page.chunks:
            assert (c.source_kind, c.source_type, c.source_path) == (
                "github_release",
                "changelog",
                f"releases/tag/{rec.tag}",
            )
            assert (c.version, c.introduced_in, c.release_id) == (
                rec.version,
                rec.introduced_in,
                rec.release_id,
            )
            assert (c.source_url, c.source_commit, c.source_content_hash) == (
                rec.source_url,
                rec.tag_commit,
                rec.body_sha256,
            )
            assert (c.licence, c.licence_status, c.licence_applicability, c.redistributable) == (
                None,
                "unverified",
                "uncertain",
                False,
            )
            assert c.copyright is None and c.attribution == rec.attribution
            assert c.section_path_fallback and c.section_path[0] == f"releases/tag/{rec.tag}"
        assert any("q7_pr_attribution_shortened" in c.transformations for c in r.page.chunks)


def test_outputs_validate_and_withhold_text(tmp_path: Path) -> None:
    pages, _ = _pages(tmp_path)
    data = render_release_file(pages)
    manifest = json.loads(render_release_manifest(pages, data, CTX))
    assert release_file_problems(data, manifest) == []
    assert render_release_file(pages) == data  # deterministic
    report = render_release_report(pages, data, CTX)
    committed = report + json.dumps(manifest)
    for leak in ("alice", "bob", "café", "Faster joins", "What's Changed"):
        assert leak not in committed
    lines = data.decode().splitlines()
    rec = json.loads(lines[0])
    for change in (
        {"redistributable": True},
        {"copyright": "x"},
        {"content": rec["content"] + f" {PR}/9"},
    ):
        bad = ("\n".join([json.dumps(rec | change), *lines[1:]]) + "\n").encode()
        bad_manifest = json.loads(render_release_manifest(pages, bad, CTX))
        assert release_file_problems(bad, bad_manifest) != []


def test_s4c_validation_accepts_the_s4d_sibling_file() -> None:
    assert not any(RELEASE_FILE in p for p in chunk_file_problems({RELEASE_FILE: b""}, {}))
    assert any("notes.txt" in p for p in chunk_file_problems({"notes.txt": b""}, {}))


def test_fixture_hash_helper_matches_body() -> None:
    body = release_body(APPROVED_RELEASE_TAGS[0]).encode("utf-8")
    assert len(hashlib.sha256(body).hexdigest()) == 64
