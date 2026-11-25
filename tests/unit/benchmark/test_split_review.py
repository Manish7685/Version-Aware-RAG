"""Review sheet import/export, validation sample, dev/test split, leakage, freezing and preflight.
Synthetic data only."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.benchmark import pilot as p
from version_aware_rag.benchmark import split as sp
from version_aware_rag.probes.preflight import PackageStatus, Preflight

from .test_pilot import _draft, _lookup, _v1

TYPES = ["value", "type_name", "identifier", "version", "yes_no", "sql_expression", "none"]


def _sheet(
    tmp: Path, pilot: list[dict[str, Any]], edit: dict[str, dict[str, str]] | None = None
) -> Path:
    path = tmp / "sheet.csv"
    p.export_review_sheet(pilot, _v1(pilot), [], path)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    for r in rows:
        r.update((edit or {}).get(r["item_id"], {}))
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def _import(
    tmp: Path, pilot: list[dict[str, Any]], edit: dict[str, dict[str, str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    return p.import_review_sheet(_sheet(tmp, pilot, edit), pilot, _lookup(), TYPES)


def test_an_untouched_sheet_imports_as_undecided(tmp_path: Path) -> None:
    pilot = [_draft(1), _draft(2)]
    records, problems = _import(tmp_path, pilot, {})
    assert problems == [] and [r["decision"] for r in records] == [None, None]


def test_valid_decisions_and_edits_are_recorded_without_touching_the_draft(tmp_path: Path) -> None:
    pilot = [_draft(1), _draft(2), _draft(3)]
    original = json.dumps(pilot, sort_keys=True)
    records, problems = _import(
        tmp_path,
        pilot,
        {
            "T3-01": {"decision": "accept", "reviewer": "owner", "time_spent_seconds": "30"},
            "T3-02": {
                "decision": "accept_minor_edit",
                "reviewer": "owner",
                "edit_question": "Which function counts frobs in DuckDB 1.5?",
                "edit_evidence": "chunk:c-1::frob_count counts frobs",
            },
            "T3-03": {
                "decision": "reject",
                "reviewer": "owner",
                "reason_codes": "R2",
                "comment": "wrong answer",
            },
        },
    )
    assert problems == []
    by = {r["item_id"]: r for r in records}
    assert (
        by["T3-02"]["edits"][0]["field"] == "question"
        and by["T3-02"]["edits"][1]["field"] == "evidence_spans"
    )
    assert by["T3-01"]["time_spent_seconds"] == 30 and by["T3-03"]["reason_codes"] == ["R2"]
    assert json.dumps(pilot, sort_keys=True) == original  # drafts are never modified


@pytest.mark.parametrize(
    ("edit", "rule"),
    [
        ({"decision": "approve", "reviewer": "owner"}, "decision must be one of"),
        (
            {"decision": "accept", "reviewer": "owner", "edit_question": "Changed?"},
            "'accept' with edits",
        ),
        ({"decision": "accept_major_edit", "reviewer": "owner"}, "needs at least one edit"),
        ({"decision": "reject", "reviewer": "owner"}, "rejection needs a reason code"),
        ({"decision": "accept", "reviewer": "owner", "reason_codes": "R9"}, "R9 needs a comment"),
        ({"decision": "accept"}, "reviewer is required"),
        (
            {"decision": "accept_minor_edit", "reviewer": "owner", "edit_answer_type": "prose"},
            "not a protocol answer type",
        ),
        (
            {
                "decision": "accept_minor_edit",
                "reviewer": "owner",
                "edit_gold_short_answer": "a or b",
            },
            "edited gold answer is invalid",
        ),
        (
            {
                "decision": "accept_minor_edit",
                "reviewer": "owner",
                "edit_evidence": "chunk:c-1::invented words",
            },
            "not verbatim",
        ),
        (
            {"decision": "accept", "reviewer": "owner", "time_spent_seconds": "a while"},
            "must be a number",
        ),
        ({"reason_codes": "R2"}, "without a decision"),
    ],
)
def test_invalid_rows_are_rejected(tmp_path: Path, edit: dict[str, str], rule: str) -> None:
    _, problems = _import(tmp_path, [_draft(1)], {"T3-01": edit})
    assert any(rule in x for x in problems), problems


def test_unknown_and_missing_rows_are_rejected(tmp_path: Path) -> None:
    pilot = [_draft(1), _draft(2)]
    path = _sheet(tmp_path, pilot)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    rows[1]["draft_id"] = "dq-unknown"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    _, problems = p.import_review_sheet(path, pilot, _lookup(), TYPES)
    assert any("unknown or duplicate" in x for x in problems) and any(
        "missing from the sheet" in x for x in problems
    )


def test_re_export_keeps_recorded_decisions(tmp_path: Path) -> None:
    pilot = [_draft(1)]
    records, _ = _import(
        tmp_path,
        pilot,
        {"T3-01": {"decision": "reject", "reviewer": "owner", "reason_codes": "R6"}},
    )
    out = tmp_path / "again.csv"
    p.export_review_sheet(pilot, _v1(pilot), records, out)
    row = next(csv.DictReader(out.open(encoding="utf-8-sig")))
    assert (row["decision"], row["reason_codes"], row["reviewer"]) == ("reject", "R6", "owner")


def test_the_validation_sample_needs_a_complete_review() -> None:
    pilot = [_draft(1), _draft(2, change_id="chg-2.0-01")]
    records = [
        {
            "draft_id": pilot[0]["draft_id"],
            "item_id": "T3-01",
            "decision": "reject",
            "reason_codes": ["R2"],
            "reviewer": "o",
        },
        {"draft_id": pilot[1]["draft_id"], "item_id": "T3-02", "decision": None, "reviewer": None},
    ]
    with pytest.raises(ValueError):
        p.validation_sample(records, pilot)
    records[1].update(decision="reject", reason_codes=["R3"], reviewer="o")
    assert p.validation_sample(records, pilot) == ["chg-1.5-01", "chg-2.0-01"]


def _items() -> list[dict[str, Any]]:
    out = []
    for n in range(24):
        out.append(
            _draft(
                n,
                kind=("T1", "T3", "T5")[n % 3],
                change_id=f"chg-x-{n // 2:02d}",
                question=f"Distinct question {n} about topic {n * 13}?",
                evidence_spans=[
                    {
                        "passage_id": f"chunk:c-{n}",
                        "version": "1.5",
                        "source_kind": "docs_page",
                        "source_path": "docs/1.5/x.md",
                        "text": f"span text {n}",
                    }
                ],
            )
        )
    return out


def test_the_split_is_by_change_deterministic_and_balanced() -> None:
    items = _items()
    a, b = sp.split_by_change(items), sp.split_by_change(list(reversed(items)))
    assert {x["draft_id"] for x in a["test"]} == {
        x["draft_id"] for x in b["test"]
    }  # order-independent
    assert not ({x["change_id"] for x in a["dev"]} & {x["change_id"] for x in a["test"]})
    assert 12 <= len(a["test"]) <= 16
    for t in ("T1", "T3", "T5"):
        assert sum(1 for x in a["dev"] if x["type"] == t) >= 2  # every type on both sides


def test_leakage_checks_catch_each_case() -> None:
    a = _draft(1, question="Which function counts the frobs?")
    b = _draft(2, change_id="chg-2.0-01", question="Which function counts the frobs ?")
    report = sp.leakage_checks([a], [b])
    assert (
        report.near_duplicate_pairs == 1 and report.shared_evidence_texts == 1 and not report.passed
    )
    assert sp.leakage_checks([a], [a]).shared_changes == 1


def test_edits_are_applied_to_a_copy_and_hashes_are_stable() -> None:
    draft = _draft(1)
    record = {
        "decision": "accept_minor_edit",
        "edits": [{"field": "question", "from": draft["question"], "to": "New?"}],
        "reason_codes": [],
        "reviewer": "owner",
        "review_batch": 1,
    }
    item = sp.apply_edits(draft, record)
    assert item["question"] == "New?" and draft["question"] != "New?"
    assert item["verification_level"] == "V2" and item["human_verified"] is True
    m1, m2 = sp.test_manifest([item]), sp.test_manifest([item])
    assert m1 == m2 and len(m1["test_set_sha256"]) == 64


def test_freezing_requires_a_complete_review() -> None:
    records = [{"draft_id": "dq-1", "item_id": "T3-01", "decision": None, "reviewer": None}]
    assert sp.freeze_preconditions(records, {"dq-1", "dq-2"})
    done = [
        {
            "draft_id": "dq-1",
            "item_id": "T3-01",
            "decision": "accept",
            "reason_codes": [],
            "reviewer": "owner",
        }
    ]
    assert sp.freeze_preconditions(done, {"dq-1"}) == []


def test_the_test_manifest_is_written_with_lf_line_endings(tmp_path: Path) -> None:
    out = tmp_path / "test_manifest.json"
    manifest = sp.test_manifest([_draft(1, "T1"), _draft(2, "T3")])
    sp.write_test_manifest(out, manifest)
    raw = out.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"}\n")
    assert json.loads(raw) == manifest


def test_the_datasheet_status_separates_reviewed_retained_and_frozen_counts() -> None:
    status = sp.datasheet_status(50, 2, 40, True, {"dev": 15, "test": 25})
    assert status == (
        "pilot (protocol v1): 52 reviewed records (50 selected + 2 released from hold); "
        "owner review complete, 40 retained (V2); test split frozen (dev 15, test 25)"
    )
    assert sp.datasheet_status(50, 0, 0, False, None).endswith(
        "owner review pending; nothing is human-verified; test split not frozen"
    )
    page = sp.render_datasheet(
        {
            "status": status,
            "splits_by_type": {"dev": {"T1": 15}, "test": {"T1": 25}},
            "by_type": {"T1": 52},
            "by_boundary": {},
            "by_target": {},
            "by_level": {"V2 (owner accepted)": 40, "rejected in review": 12},
            "model": "m",
            "temperature": 0.2,
            "max_output_tokens": 1,
            "prompts": {},
            "cached_requests": 0,
            "corpus_manifest_sha256": "0" * 64,
            "limitations": [],
            "commands": [],
        }
    )
    assert "| Frozen test split, type | count |" in page and "| T1 | 25 |" in page


def test_preflight_blockers() -> None:
    ok = PackageStatus("2.0.1", True, True, "2.0.1", "3.12.16", True)
    pf = Preflight("abc1234", 0, True, "windows", "3.12.16", [ok])
    assert pf.blockers() == []
    pf.dirty_entries = 3
    pf.packages.append(PackageStatus("1.5.6", False, True, "1.5.5", "3.12.16", False))
    blockers = pf.blockers()
    assert any("uncommitted" in x for x in blockers) and any("does not pin" in x for x in blockers)
    assert any("not satisfiable offline" in x for x in blockers) and any(
        "reports duckdb 1.5.5" in x for x in blockers
    )
