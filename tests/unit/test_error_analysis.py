"""Error analysis classification (RAG-M8, design §5.4) on hand-built runs: ids only."""

from __future__ import annotations

from typing import Any

from version_aware_rag.retrieval import errors as e


def _run(ranked: list[str], typ: str = "T3", filtered: bool = True) -> dict[str, object]:
    return {
        "method": "hybrid",
        "filtered": filtered,
        "draft_id": "d1",
        "item_id": "T3-01",
        "type": typ,
        "ranked": ranked,
    }


QRELS = {"g1": 2, "g2": 2, "p1": 1}


def cat(f: dict[str, Any] | None) -> str:
    assert f is not None
    return str(f["category"])


def test_retrieval_categories_in_design_order() -> None:
    assert e.classify_retrieval(_run(["g1", "g2"]), QRELS, set()) is None  # full recall
    f = e.classify_retrieval(_run(["x", "v1", "g1"], filtered=False), QRELS, {"v1"})
    assert f is not None and f["category"] == "wrong_version"
    assert cat(e.classify_retrieval(_run(["g1"], typ="T4"), QRELS, set())) == "missing_hop"
    assert cat(e.classify_retrieval(_run(["g1"]), QRELS, set())) == "chunk_split"
    assert cat(e.classify_retrieval(_run(["p1"]), QRELS, set())) == "wrong_section"
    miss = e.classify_retrieval(_run(["x"] * 10 + ["g2"]), QRELS, set())
    assert miss is not None and miss["category"] == "lexical_miss" and miss["near_miss"] is True


def test_answer_categories() -> None:
    def s(**kw: object) -> dict[str, object]:
        return {"draft_id": "d", "type": "T3", "abstain": False, "correct": False, **kw}

    assert e.classify_answer(s(correct=True), None, None) is None
    assert cat(e.classify_answer(s(correct=None), None, None)) == "unresolved"
    assert cat(e.classify_answer(s(type="T5"), None, None)) == "failed_abstention"
    assert cat(e.classify_answer(s(abstain=True), None, None)) == "over_abstention"
    assert cat(e.classify_answer(s(version_outcome="wrong_version"), None, None)) == "wrong_version"
    assert cat(e.classify_answer(s(evidence_recall=0.0), None, None)) == "retrieval_miss"
    near = e.classify_answer(
        s(evidence_recall=1.0), {"short_answer": "throws an error"}, "throw an error"
    )
    assert near is not None and near["category"] == "possible_matcher_gap"
    other = e.classify_answer(s(evidence_recall=1.0), {"short_answer": "BIGINT"}, "VARCHAR")
    assert other is not None and other["category"] == "wrong_answer"


def test_analyse_counts_and_excludes_unresolved_from_the_total() -> None:
    info = {"qrels": {"d1": QRELS}, "hard_negatives": {"d1": {"version_confusers": ["v1"]}}}
    runs = [_run(["v1"], filtered=False), _run(["g1", "g2"])]
    scores = [
        {"draft_id": "d1", "type": "T3", "abstain": True, "correct": False},
        {"draft_id": "d2", "type": "T3", "abstain": False, "correct": None},
    ]
    out = e.analyse(runs, info, scores, {}, {})
    assert out["total_failures"] == 2 and out["distinct_items"] == 1
    assert out["by_category"] == {"over_abstention": 1, "wrong_version": 1}
    assert "## Failures" in e.render(out, "test")
