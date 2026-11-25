"""The pilot change inventory: record rules, canonical storage, and validation against the corpus
manifest, cached sources and probe suite (RAG-M4 S2). Fixture sources, plus the real inventory
file checked without needing the cache."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.benchmark.inventory import (
    DEFAULT_PATH,
    ChangeRecord,
    InventoryReport,
    SuiteFacts,
    crossed_heading,
    dumps_record,
    load_inventory,
    validate_inventory,
)
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.verify import ClaimSource, build_source_index
from version_aware_rag.probes.pins import load_pins
from version_aware_rag.probes.suite import load_suite

ROOT = Path(__file__).resolve().parents[3]
PKG = {
    "ref": "1.1.3",
    "1.2": "1.2.2",
    "1.3": "1.3.2",
    "1.4": "1.4.5",
    "1.5": "1.5.6",
    "2.0": "2.0.1",
}
SOURCE = (
    "line one\nline two\nRESTRICTED-FIXTURE: a release-post sentence that must not be quoted\nend\n"
)


def _data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "change_id": "chg-1.5-01",
        "version": "1.5",
        "previous_version": "1.4",
        "feature": "a feature",
        "before": "Up to 1.4.5 the old behaviour applies.",
        "after": "From 1.5.6 the new behaviour applies.",
        "sources": [
            {
                "source_kind": "docs_page",
                "source_path": "docs/1.5/x.md",
                "line_start": 1,
                "line_end": 2,
                "note": "MIT docs page",
            }
        ],
        "probe_ids": ["P02"],
        "runtime_evidence": "rag_m1_verified",
        "canonical_observations": "pending",
        "status": "pilot_candidate",
        "status_reason": "RAG-M1 research evidence: verified boundary.",
        "owner_review": "pending",
    }
    data.update(over)
    return data


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"change_id": "chg-1.4-01"}, "change_id"),
        ({"change_id": "change-1"}, "change_id"),
        ({"previous_version": "1.3"}, "previous_version"),
        ({"sources": []}, "at least one source"),
        ({"probe_ids": ["P05", "P02"]}, "sorted and unique"),
        ({"probe_ids": []}, "needs at least one probe"),
        ({"runtime_evidence": "none"}, "needs runtime evidence"),
        ({"canonical_observations": "done"}, "canonical_observations"),
        ({"owner_review": "accepted"}, "owner_review"),
        ({"feature": " "}, "feature"),
        (
            {
                "sources": [
                    {
                        "source_kind": "docs_page",
                        "source_path": "x",
                        "line_start": 3,
                        "line_end": 2,
                        "note": "n",
                    }
                ]
            },
            "line range",
        ),
    ],
)
def test_record_rules(over: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(message)):
        ChangeRecord.model_validate(_data(**over))


def test_unresolved_and_excluded_records_may_lack_probes() -> None:
    for status in ("unresolved", "excluded"):
        r = ChangeRecord.model_validate(_data(status=status, probe_ids=[], runtime_evidence="none"))
        assert r.status == status


def _write(tmp: Path, records: list[ChangeRecord]) -> Path:
    path = tmp / "inv.jsonl"
    path.write_bytes("".join(dumps_record(r) for r in records).encode())
    return path


def _report(path: Path) -> InventoryReport:
    report = InventoryReport()
    load_inventory(path, report)
    return report


def test_canonical_storage_is_enforced(tmp_path: Path) -> None:
    a = ChangeRecord.model_validate(_data())
    b = ChangeRecord.model_validate(_data(change_id="chg-1.5-02"))
    assert _report(_write(tmp_path, [a, b])).errors == []
    assert "not sorted" in " ".join(_report(_write(tmp_path, [b, a])).errors)
    assert "duplicate change_id" in " ".join(_report(_write(tmp_path, [a, a])).errors)
    path = tmp_path / "inv.jsonl"
    path.write_bytes(
        json.dumps(a.model_dump(mode="json"), indent=1).replace("\n", " ").encode() + b"\n"
    )
    assert "not in canonical form" in " ".join(_report(path).errors)
    path.write_bytes(dumps_record(a).replace("\n", "\r\n").encode())
    assert "LF line endings" in " ".join(_report(path).errors)
    path.write_bytes(b'{"change_id": "chg-1.5-01"}\n')
    assert "invalid record" in " ".join(_report(path).errors)
    assert _report(tmp_path / "missing.jsonl").exit_code == 2


def _index(text: str | None = SOURCE, **over: Any) -> dict[tuple[str, str], ClaimSource]:
    data = text.encode() if text is not None else None
    fields: dict[str, Any] = {
        "kind": "docs_page",
        "path": "docs/1.5/x.md",
        "repo": "duckdb/duckdb-web",
        "commit": "d" * 40,
        "sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
        "doc_version": "1.5",
        "read_bytes": lambda: data,
    }
    fields.update(over)
    return {(fields["kind"], fields["path"]): ClaimSource(**fields)}


def _validated(
    records: list[ChangeRecord], index: Any, probes: set[str] | None = None
) -> InventoryReport:
    report = InventoryReport(records=records)
    ids = frozenset(probes if probes is not None else {"P02"})
    facts = SuiteFacts(probe_ids=ids, rag_m1_probe_ids=frozenset({"P02"}), packages=PKG)
    validate_inventory(report, index, facts)
    return report


def _one_per_boundary(**over: Any) -> list[ChangeRecord]:
    """One valid pilot candidate per corpus boundary, so coverage passes."""
    out = []
    for prev, ver in (("1.2", "1.3"), ("1.3", "1.4"), ("1.4", "1.5"), ("1.5", "2.0")):
        path = f"docs/{ver}/x.md"
        out.append(
            ChangeRecord.model_validate(
                _data(
                    change_id=f"chg-{ver}-01",
                    version=ver,
                    previous_version=prev,
                    before=f"Up to {PKG[prev]} the old behaviour applies.",
                    after=f"From {PKG[ver]} the new behaviour applies.",
                    sources=[
                        {
                            "source_kind": "docs_page",
                            "source_path": path,
                            "line_start": 1,
                            "line_end": 2,
                            "note": "n",
                        }
                    ],
                )
                | {}
            )
        )
    return out


def _boundary_index() -> dict[tuple[str, str], ClaimSource]:
    index: dict[tuple[str, str], ClaimSource] = {}
    for ver in ("1.3", "1.4", "1.5", "2.0"):
        index.update(_index(path=f"docs/{ver}/x.md", doc_version=ver))
    return index


def test_a_valid_inventory_passes() -> None:
    report = _validated(_one_per_boundary(), _boundary_index())
    assert report.errors == [] and report.incomplete == [] and report.exit_code == 0
    assert report.coverage() == {
        "ref->1.2": 0,
        "1.2->1.3": 1,
        "1.3->1.4": 1,
        "1.4->1.5": 1,
        "1.5->2.0": 1,
    }


def test_validation_errors() -> None:
    records = _one_per_boundary()
    assert any(
        "probe P02 is not in the probe suite" in e
        for e in _validated(records, _boundary_index(), set()).errors
    )
    index = _boundary_index()
    del index[("docs_page", "docs/1.5/x.md")]
    assert any("is not in the corpus manifest" in e for e in _validated(records, index).errors)
    long = [
        r
        if r.version != "1.5"
        else r.model_copy(update={"sources": (r.sources[0].model_copy(update={"line_end": 99}),)})
        for r in records
    ]
    assert any("has only 4 lines" in e for e in _validated(long, _boundary_index()).errors)
    missing_boundary = [r for r in records if r.version != "1.4"]
    assert any(
        "no pilot candidate covers the corpus boundary 1.3->1.4" in e
        for e in _validated(missing_boundary, _boundary_index()).errors
    )


def test_restricted_sources_may_not_be_quoted() -> None:
    restricted_line = SOURCE.splitlines()[2]
    rec = ChangeRecord.model_validate(
        _data(
            sources=[
                {
                    "source_kind": "release_post",
                    "source_path": "_posts/p.md",
                    "line_start": 3,
                    "line_end": 3,
                    "note": "release post (cited by reference only)",
                }
            ],
            before=f"the post says {restricted_line}",
        )
    )
    index = _index(kind="release_post", path="_posts/p.md")
    report = _validated(
        [*[r for r in _one_per_boundary() if r.version != "1.5"], rec],
        {**_boundary_index(), **index},
    )
    assert any("quotes restricted _posts/p.md" in e for e in report.errors)
    assert not any(restricted_line in e for e in report.errors)  # the error never quotes it


def test_missing_cache_is_incomplete() -> None:
    index = {k: v for k, v in _boundary_index().items()}
    index.update(_index(text=None))
    report = _validated(_one_per_boundary(), index)
    assert report.errors == [] and report.exit_code == 2


# ---------------------------------------------------------------- the real inventory file


def _facts() -> SuiteFacts:
    suite = load_suite(ROOT / "configs/probes.toml")
    pins = load_pins(ROOT / "configs/probes.toml")
    return SuiteFacts(
        probe_ids=frozenset(p.id for p in suite.probes),
        rag_m1_probe_ids=frozenset(p.id for p in suite.probes if p.rag_m1_name),
        packages={pkg.applies_to_doc_version: pkg.version for pkg in pins.packages},
    )


def _real() -> tuple[InventoryReport, SuiteFacts]:
    report = InventoryReport()
    load_inventory(ROOT / DEFAULT_PATH, report)
    return report, _facts()


def test_real_inventory_is_canonical_and_covers_every_boundary() -> None:
    report, probes = _real()
    assert (
        report.errors == [] and len(report.records) == 32
    )  # 24 approved + 8 T2 candidates pending review
    cfg, _ = load_config(ROOT / "configs/corpus.toml")
    chunking, _ = load_chunking_config(ROOT / "configs/chunking.toml")
    manifest, problems = load_manifest(ROOT / cfg.paths.manifest)
    assert manifest is not None and problems == []
    index = build_source_index(ROOT, cfg, chunking, manifest)
    no_cache = {
        k: ClaimSource(v.kind, v.path, v.repo, v.commit, v.sha256, v.doc_version, lambda: None)
        for k, v in index.items()
    }
    validate_inventory(report, no_cache, probes)
    assert (
        report.errors == []
    )  # sources in the manifest, probes in the suite, every boundary covered
    assert report.coverage() == {
        "ref->1.2": 1,
        "1.2->1.3": 2,
        "1.3->1.4": 3,
        "1.4->1.5": 4,
        "1.5->2.0": 10,
    }


def test_real_inventory_keeps_facts_and_open_items_apart() -> None:
    report, _probes = _real()
    by_id = {r.change_id: r for r in report.records}
    assert all(
        r.canonical_observations == "pending" and r.owner_review == "pending"
        for r in report.records
    )
    rag_m1 = {"P01", "P02", "P04", "P05", "P06", "P07", "P08", "P10"}
    for r in report.records:
        if r.runtime_evidence == "rag_m1_verified":
            assert set(r.probe_ids) & rag_m1, r.change_id  # verified only through RAG-M1 probes
        if r.runtime_evidence == "scratch_only":
            assert "not canonical" in r.status_reason, r.change_id
    assert (
        by_id["chg-2.0-11"].status == "unresolved"
        and "Not recorded as an EvidenceConflict" in by_id["chg-2.0-11"].status_reason
    )
    assert by_id["chg-1.5-05"].status == "unresolved"
    assert {r.change_id for r in report.records if r.status == "excluded"} == {
        "chg-1.5-06",
        "chg-2.0-12",
    }
    used = {p for r in report.records for p in r.probe_ids}
    assert {
        f"P{i}" for i in range(11, 27)
    } <= used  # every pilot probe observes an inventory change


# ---------------------------------------------------------------- S2 review: boundary consistency


def test_runtime_evidence_needs_a_suite_probe() -> None:
    for status in ("unresolved", "excluded"):
        with pytest.raises(ValidationError, match="at least one suite probe"):
            ChangeRecord.model_validate(
                _data(status=status, probe_ids=[], runtime_evidence="scratch_only")
            )
    ok = ChangeRecord.model_validate(
        _data(status="excluded", probe_ids=[], runtime_evidence="none")
    )
    assert ok.probe_ids == ()  # an excluded change with no runtime evidence needs no probe


def _with(records: list[ChangeRecord], version: str, **update: Any) -> list[ChangeRecord]:
    return [r.model_copy(update=update) if r.version == version else r for r in records]


def _fixture_facts(probes: set[str], rag_m1: set[str]) -> SuiteFacts:
    return SuiteFacts(probe_ids=frozenset(probes), rag_m1_probe_ids=frozenset(rag_m1), packages=PKG)


def _errors(records: list[ChangeRecord], facts: SuiteFacts) -> str:
    report = InventoryReport(records=records)
    validate_inventory(report, _boundary_index(), facts)
    return " | ".join(report.errors)


def test_evidence_labels_must_match_the_probes_and_reason() -> None:
    base = _one_per_boundary()
    facts = _fixture_facts({"P02", "P90"}, {"P02"})
    assert _errors(base, facts) == ""
    scratch_probe = _with(base, "1.5", probe_ids=("P90",))  # labelled rag_m1_verified
    assert "rag_m1_verified, but probe(s) P90 have no RAG-M1 evidence" in _errors(
        scratch_probe, facts
    )
    no_cite = _with(base, "1.5", status_reason="verified somewhere")
    assert "status_reason does not cite RAG-M1" in _errors(no_cite, facts)
    only_rag_m1 = _with(
        base,
        "1.5",
        runtime_evidence="scratch_only",
        status_reason="S2 scratch run (not canonical evidence)",
    )
    assert "scratch_only, but every probe has RAG-M1 evidence" in _errors(only_rag_m1, facts)
    unmarked = _with(
        base,
        "1.5",
        runtime_evidence="scratch_only",
        probe_ids=("P90",),
        status_reason="scratch run",
    )
    assert "does not say it is not canonical evidence" in _errors(unmarked, facts)


def test_pilot_descriptions_must_name_the_boundary_packages() -> None:
    base = _one_per_boundary()
    facts = _fixture_facts({"P02"}, {"P02"})
    wrong_before = _with(base, "1.5", before="Up to 1.3.2 the old behaviour applies.")
    assert "'before' does not name the 1.4 package 1.4.5" in _errors(wrong_before, facts)
    wrong_after = _with(base, "1.5", after="From 2.0.1 the new behaviour applies.")
    assert "'after' does not name the 1.5 package 1.5.6" in _errors(wrong_after, facts)
    # unresolved and excluded records describe open evidence: no naming rule
    open_item = _with(
        base, "1.5", before="The docs say one thing.", after="The package does another."
    )
    open_item = [
        *open_item,
        open_item[2].model_copy(update={"change_id": "chg-1.5-02", "status": "unresolved"}),
    ]
    assert "chg-1.5-02" not in _errors(open_item, facts)


def test_crossed_heading() -> None:
    lines = ["#### `f(x)`", "", "| Description |", "", "#### `version()`", "text", "## Next"]
    assert crossed_heading(lines, 1, 3) is None
    assert crossed_heading(lines, 1, 5) == 5  # the old chg-1.3-02 overrun: a sibling entry
    assert crossed_heading(["### A", "#### sub", "text"], 1, 3) is None  # a subsection is inside
    assert crossed_heading(["text", "## H"], 1, 2) == 2  # a range not starting at a heading
    assert crossed_heading(["---", "title: x", "---", "", "intro"], 2, 5) is None


def test_a_range_running_into_another_section_is_rejected() -> None:
    text = "#### `uuid_extract_version(uuid)`\n\n| Description |\n\n#### `version()`\n\nmore\n"
    index = _boundary_index()
    index.update(_index(text=text, sha256=hashlib.sha256(text.encode()).hexdigest()))
    base = _with(
        _one_per_boundary(),
        "1.5",
        sources=(
            ChangeRecord.model_validate(_data())
            .sources[0]
            .model_copy(update={"line_start": 1, "line_end": 6}),
        ),
    )
    report = InventoryReport(records=base)
    validate_inventory(report, index, _fixture_facts({"P02"}, {"P02"}))
    assert any("1-6 runs into another section at line 5" in e for e in report.errors)


def test_rag_m1_probes_match_the_pinned_rag_m1_run() -> None:
    """Every RAG-M1 probe referenced by an inventory record, whatever the record label, changes
    exactly at the record's boundary in the hash-pinned RAG-M1 expected run (status changes, or
    ok values change; error text alone does not count)."""
    report, _ = _real()
    suite = {p.id: p for p in load_suite(ROOT / "configs/probes.toml").probes}
    lines = (
        (ROOT / "scripts/probes/expected/rag_m1_windows_probe_results.jsonl")
        .read_text("utf-8")
        .splitlines()
    )
    expected = {json.loads(ln)["duckdb"]: json.loads(ln)["results"] for ln in lines}
    versions = ["1.1.3", "1.2.2", "1.3.2", "1.4.5", "1.5.6", "2.0.1"]
    docs = dict(zip(versions, ["ref", "1.2", "1.3", "1.4", "1.5", "2.0"], strict=True))
    checked = 0
    for r in report.records:
        for pid in r.probe_ids:
            name = suite[pid].rag_m1_name
            if name is None:
                continue
            keys = [
                ("error",) if expected[v][name].startswith("ERROR ") else ("ok", expected[v][name])
                for v in versions
            ]
            changes = [
                f"{docs[versions[i]]}->{docs[versions[i + 1]]}"
                for i in range(5)
                if keys[i] != keys[i + 1]
            ]
            assert changes == [f"{r.previous_version}->{r.version}"], (r.change_id, pid, changes)
            checked += 1
    assert checked == 8


def test_mixed_probe_provenance_cannot_be_labelled_rag_m1_verified() -> None:
    """Regression (S2 review): a record with one RAG-M1 probe and one scratch-only probe must be
    labelled with the weaker provenance, scratch_only, and its reason must say why."""
    base = _one_per_boundary()
    facts = _fixture_facts({"P02", "P90"}, {"P02"})
    mixed = _with(base, "1.5", probe_ids=("P02", "P90"))  # labelled rag_m1_verified
    assert "rag_m1_verified, but probe(s) P90 have no RAG-M1 evidence" in _errors(mixed, facts)
    honest = _with(
        base,
        "1.5",
        probe_ids=("P02", "P90"),
        runtime_evidence="scratch_only",
        status_reason="P02: RAG-M1 verified. P90: S2 scratch run only (not canonical evidence).",
    )
    assert _errors(honest, facts) == ""


def test_real_mixed_record_is_labelled_with_the_weaker_provenance() -> None:
    report, facts = _real()
    mixed = [
        r
        for r in report.records
        if len(set(r.probe_ids) & facts.rag_m1_probe_ids) not in (0, len(r.probe_ids))
    ]
    assert [r.change_id for r in mixed] == ["chg-2.0-01"]  # the only record mixing provenance
    record = mixed[0]
    assert record.probe_ids == ("P04", "P17") and record.runtime_evidence == "scratch_only"
    assert record.canonical_observations == "pending"
    assert "P04 (integer division): RAG-M1" in record.status_reason
    assert "P17 (modulo): S2 scratch run only (not canonical evidence)" in record.status_reason
    for r in report.records:
        if r.runtime_evidence == "rag_m1_verified":
            assert set(r.probe_ids) <= facts.rag_m1_probe_ids, r.change_id


def test_t2_expansion_candidates_stay_pending_and_out_of_drafting() -> None:
    """Owner 2026-11-23: the eight documentation-grounded T2 candidates are pending review, carry no
    runtime evidence, cite only MIT pages of their own versions, and are not pilot candidates."""
    report, _ = _real()
    added = [
        r
        for r in report.records
        if "Pending owner review (T2 inventory expansion" in r.status_reason
    ]
    assert len(added) == 8
    assert all(
        r.status == "unresolved" and r.runtime_evidence == "none" and not r.probe_ids for r in added
    )
    assert all(
        s.source_kind == "docs_page" and s.source_path.startswith(f"docs/{v}/")
        for r in added
        for s, v in zip(r.sources, (r.previous_version, r.version), strict=True)
    )
