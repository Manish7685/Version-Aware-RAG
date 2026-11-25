"""Pilot workflow (protocol v1): V1 checks, selection, review records and report. Synthetic data."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path
from typing import Any

from version_aware_rag.benchmark import pilot as p
from version_aware_rag.benchmark.inventory import ChangeRecord

ROOT = Path(__file__).resolve().parents[3]
PKG = {"1.2": "1.2.2", "1.3": "1.3.2", "1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}


def _record(cid: str = "chg-1.5-01") -> ChangeRecord:
    return ChangeRecord.model_validate(
        {
            "change_id": cid,
            "version": "1.5",
            "previous_version": "1.4",
            "feature": "frob",
            "before": "a 1.4.5",
            "after": "b 1.5.6",
            "sources": [
                {
                    "source_kind": "docs_page",
                    "source_path": "docs/1.5/frob.md",
                    "line_start": 1,
                    "line_end": 2,
                    "note": "docs",
                }
            ],
            "probe_ids": ["P01"],
            "runtime_evidence": "scratch_only",
            "canonical_observations": "pending",
            "status": "pilot_candidate",
            "status_reason": "S2 scratch run (not canonical evidence)",
            "owner_review": "pending",
        }
    )


def _draft(n: int, kind: str = "T3", target: str = "1.5", **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "draft_id": f"dq-{n:012d}",
        "item_id": f"{kind}-{n:02d}",
        "status": "draft",
        "rejection_reasons": [],
        "type": kind,
        "target_version": target,
        "version_set": [target],
        "change_id": "chg-1.5-01",
        "boundary": "1.4->1.5",
        "question": f"Unique question number {n} about frob counting {n * 7}?",
        "gold_short_answer": "frob_count",
        "answer_type": "identifier",
        "accepted_aliases": [],
        "other_version_answers": {},
        "answerable": True,
        "abstain_reason": None,
        "evidence_spans": [
            {
                "passage_id": "chunk:c-1",
                "version": target,
                "source_kind": "docs_page",
                "source_path": f"docs/{target}/frob.md",
                "text": "frob_count counts frobs",
            }
        ],
        "restricted_refs": [],
        "probe_ids": ["P01"],
        "runtime_observations": [],
        "gold_basis": "docs",
        "verification_level": "V0",
        "human_verified": False,
        "gold": False,
        "generation": {},
        "review_hold": "",
    }
    base.update(kw)
    return base


def _lookup() -> p.CorpusLookup:
    chunks = {
        "c-1": {
            "version": "1.5",
            "source_type": "documentation",
            "introduced_in": None,
            "content": "Intro. frob_count counts frobs in a list.",
        }
    }
    return p.CorpusLookup(chunks, {}, PKG)


def _v1(drafts: list[dict[str, Any]]) -> dict[str, p.V1Result]:
    return p.v1_check(drafts, _lookup(), {"chg-1.5-01": _record()}, set())


def test_protocol_v1_files_match_the_selection_composition() -> None:
    v1 = tomllib.loads((ROOT / "benchmark/protocol/protocol_v1.toml").read_text("utf-8"))
    assert {
        k: v for k, v in v1["pilot_composition"].items() if k.startswith("T")
    } == p.PROTOCOL_V1_COMPOSITION
    assert sum(p.PROTOCOL_V1_COMPOSITION.values()) == 50
    for name, digest in v1["prompts"].items():
        assert (
            hashlib.sha256((ROOT / "benchmark/protocol/prompts" / name).read_bytes()).hexdigest()
            == digest
        ), name
    v0 = tomllib.loads((ROOT / "benchmark/protocol/protocol_v0.toml").read_text("utf-8"))
    assert v0["pilot_composition"]["T2"] == 15  # protocol v0 unchanged


def test_a_valid_draft_passes_with_the_probe_check_pending() -> None:
    res = _v1([_draft(1)])["dq-000000000001"]
    assert res.status == "pass_pending_probe" and res.checks["probe_consistency"] == "pending"
    assert all(
        res.checks[c] == "pass"
        for c in ("schema", "spans_verbatim", "version_compatible", "near_duplicate", "inventory")
    )


def test_each_v1_failure_is_caught() -> None:
    bad_span = _draft(
        2, evidence_spans=[{**_draft(2)["evidence_spans"][0], "text": "invented text"}]
    )
    wrong_version = _draft(3, target="2.0")
    t5_span = _draft(
        4,
        kind="T5",
        answerable=False,
        gold_short_answer=None,
        answer_type="none",
        abstain_reason="absent",
    )
    wrong_inventory = _draft(5, probe_ids=["P99"])
    res = _v1([bad_span, wrong_version, t5_span, wrong_inventory])
    assert "span_not_verbatim_in_corpus" in res[bad_span["draft_id"]].reasons
    assert "span_version_incompatible" in res[wrong_version["draft_id"]].reasons
    assert res[t5_span["draft_id"]].checks["schema"] == "fail"
    assert res[wrong_inventory["draft_id"]].checks["inventory"] == "fail"
    assert all(r.status == "fail" for r in res.values())


def test_near_duplicates_fail_but_t2_siblings_do_not() -> None:
    a = _draft(6, question="Which function counts the frobs in a list?")
    b = _draft(7, question="Which function counts the frobs in a list ?")
    s1 = _draft(8, kind="T2", item_id="T2-01@1.5", question="What does frobbing return?")
    s2 = _draft(9, kind="T2", item_id="T2-01@1.5x", question="What does frobbing return?")
    res = _v1([a, b, s1, s2])
    assert res[b["draft_id"]].checks["near_duplicate"] == "fail"
    assert res[s1["draft_id"]].checks["near_duplicate"] == "pass"


def test_held_and_rejected_drafts_are_never_checked_or_selected() -> None:
    held = _draft(10, review_hold="conflict")
    rejected = _draft(11, status="rejected", rejection_reasons=["type_mismatch"])
    assert _v1([held, rejected]) == {}


def test_selection_meets_the_composition_and_keeps_t2_sets_whole() -> None:
    drafts = [_draft(i, kind="T1") for i in range(20, 25)]
    drafts += [
        _draft(30, kind="T2", item_id="T2-01@1.4", target="1.5"),
        _draft(31, kind="T2", item_id="T2-01@1.5"),
    ]
    drafts += [_draft(32, kind="T2", item_id="T2-02@1.5")]
    v1 = {x["draft_id"]: p.V1Result(x["draft_id"], {"schema": "pass"}) for x in drafts}
    pilot, gaps = p.select_pilot(drafts, v1, {"T1": 3, "T2": 2})
    assert sum(1 for x in pilot if x["type"] == "T1") == 3 and gaps == {}
    assert sorted(x["item_id"] for x in pilot if x["type"] == "T2") == ["T2-01@1.4", "T2-01@1.5"]
    v1[drafts[0]["draft_id"]].checks["schema"] = "fail"
    _, gaps = p.select_pilot(drafts, v1, {"T1": 5})
    assert gaps == {"T1": (4, 5)}  # a V1 failure is never selected; the shortfall is reported


def test_review_records_are_complete_only_with_valid_decisions(tmp_path: Path) -> None:
    pilot = [_draft(40), _draft(41)]
    v1 = _v1(pilot)
    p.write_review_batches(pilot, v1, tmp_path)
    path = tmp_path / p.REVIEW_RECORDS
    records = [json.loads(ln) for ln in path.read_text("utf-8").splitlines()]
    assert all(r["decision"] is None for r in records) and len(p.check_review_records(records)) == 2
    records[0].update(decision="reject", reviewer="owner")
    records[1].update(decision="accept", reason_codes=["R9"], reviewer="owner")
    problems = p.check_review_records(records)
    assert any("rejection needs a reason code" in x for x in problems) and any(
        "R9 needs a comment" in x for x in problems
    )
    records[0]["reason_codes"] = ["R2"]
    records[1]["comment"] = "fine"
    assert p.check_review_records(records) == []
    path.write_text("".join(json.dumps(r) + "\n" for r in records), "utf-8")
    p.write_review_batches(pilot, v1, tmp_path)  # rerun: recorded decisions are kept
    again = [json.loads(ln) for ln in path.read_text("utf-8").splitlines()]
    assert [r["decision"] for r in again] == ["reject", "accept"]
    assert (tmp_path / p.REVIEW_DIR / "pilot_review_batch_01.md").is_file()


def test_the_pilot_report_counts_only_review_records() -> None:
    pilot = [_draft(50), _draft(51, kind="T1")]
    records = [
        {
            "draft_id": pilot[0]["draft_id"],
            "item_id": "T3-50",
            "type": "T3",
            "decision": "reject",
            "reason_codes": ["R2"],
            "comment": "wrong for 1.5",
            "time_spent_seconds": 40,
            "reviewer": "owner",
        },
        {
            "draft_id": pilot[1]["draft_id"],
            "item_id": "T1-51",
            "type": "T1",
            "decision": "accept",
            "reason_codes": [],
            "comment": "",
            "time_spent_seconds": 20,
            "reviewer": "owner",
        },
    ]
    report = p.render_pilot_report(
        records, {pilot[0]["draft_id"]: {"status": "pass_pending_probe"}}, pilot
    )
    assert "Reviewed items: 2" in report and "accept 1" in report and "reject 1" in report
    assert "V1 false passes (passed V1, rejected in review): 1" in report and "R2 1" in report
