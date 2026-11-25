"""Citation support and faithfulness (Tier M, design §5.3) with a stub NLI judge: no model."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from version_aware_rag.answer import support as s
from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus

PKG = {"1.5": "1.5.6"}


def _corpus() -> RetrievalCorpus:
    def c(cid: str, text: str) -> Chunk:
        return Chunk(cid, "1.5", "docs_page", "documentation", None, "docs/1.5/x.md", ("X",), text)

    return RetrievalCorpus([c("a", "frob returns BIGINT."), c("b", "Unrelated text.")], PKG)


def _judge(pairs: Sequence[tuple[str, str]]) -> list[float]:
    # "entailed" exactly when the premise contains the hypothesis's first word.
    return [0.9 if h.split()[0].lower() in p.lower() else 0.1 for p, h in pairs]


def test_sentence_split() -> None:
    assert s.split_sentences("Frob returns BIGINT. It was added in 1.5! `x` too.") == [
        "Frob returns BIGINT.",
        "It was added in 1.5!",
        "`x` too.",
    ]
    assert s.split_sentences("  ") == []
    assert s.split_sentences("Use 1.5. version.") == ["Use 1.5. version."]  # no capital: one


def test_citation_support_uses_cited_and_faithfulness_uses_retrieved() -> None:
    ans = {
        "draft_id": "d1",
        "abstain": False,
        "explanation": "Frob returns BIGINT. Unrelated claim here.",
        "citations": ["a"],
        "retrieved": ["a", "b"],
    }
    r = s.score_answer(ans, _corpus(), _judge, 0.5)
    assert r is not None
    assert r["citation_support"] == 0.5  # only sentence 1 entailed by the cited chunk
    assert r["faithfulness"] == 1.0  # sentence 2 entailed by retrieved chunk b
    assert r["sentences"] == 2


def test_abstentions_and_missing_citations() -> None:
    assert s.score_answer({"abstain": True, "explanation": "X y."}, _corpus(), _judge, 0.5) is None
    r = s.score_answer(
        {"abstain": False, "explanation": "Frob x.", "citations": ["zzz"], "retrieved": []},
        _corpus(),
        _judge,
        0.5,
    )
    assert r is not None and r["citation_support"] == 0.0 and r["faithfulness"] == 0.0


def test_calibration_picks_theta_on_calibration_and_reports_agreement() -> None:
    rows = [
        {"set": "calibration", "label": "1"},
        {"set": "calibration", "label": "1"},
        {"set": "calibration", "label": "0"},
        {"set": "agreement", "label": "1"},
        {"set": "agreement", "label": "0"},
    ]
    probs = [0.8, 0.6, 0.3, 0.7, 0.2]
    out = s.calibrate(rows, probs)
    assert out["theta"] == 0.31  # smallest θ separating 0.3 from 0.6 on the calibration set
    assert out["agreement_accuracy"] == 1.0 and out["agreement_kappa"] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        s.calibrate(rows[:3], probs[:3])


def test_theta_is_labelled_uncalibrated_without_owner_labels(tmp_path: Path) -> None:
    theta, label = s.theta_for(tmp_path)
    assert theta == s.DEFAULT_THETA and label.startswith("UNCALIBRATED")


def test_candidates_cover_cited_and_other_retrieved_chunks_once() -> None:
    ans = [
        {
            "draft_id": "d1",
            "abstain": False,
            "explanation": "Frob returns BIGINT.",
            "citations": ["a"],
            "retrieved": ["a", "b", "zzz"],
        },
        {
            "draft_id": "d2",
            "abstain": True,
            "explanation": "No.",
            "citations": [],
            "retrieved": ["a"],
        },
    ]
    cands = s.candidate_pairs(ans, _corpus())
    assert [(c["kind"], c["chunk_id"]) for c in cands] == [("cited", "a"), ("retrieved", "b")]


def test_stratified_sample_is_deterministic_balanced_and_bounded() -> None:
    cands = [
        {
            "pair_id": f"{i:04x}",
            "kind": "cited" if i % 3 == 0 else "retrieved",
            "draft_id": "d",
            "chunk_id": "a",
            "sentence": "S.",
            "premise": "P",
        }
        for i in range(60)
    ]
    probs = [(i % 10) / 10 for i in range(60)]
    rows = s.select_pairs(cands, probs, 20)
    assert len(rows) == 20 and rows == s.select_pairs(cands, probs, 20)
    assert len({r["pair_id"] for r in rows}) == 20
    assert {r["kind"] for r in rows} == {"cited", "retrieved"}
    n_cal = sum(r["set"] == "calibration" for r in rows)
    assert abs(n_cal - (20 - n_cal)) <= 10  # alternating inside every stratum
    assert all(r["label"] == "" and r["synthetic"] == "" for r in rows)
    assert len(s.select_pairs(cands, probs, 500)) == 60  # never more than the candidates


def test_label_checks_refuse_synthetic_unlabelled_and_tiny_sets() -> None:
    def row(i: int, st: str, label: str, syn: str = "") -> dict[str, str]:
        return {"pair_id": str(i), "set": st, "label": label, "synthetic": syn}

    good = [row(i, "calibration", str(i % 2)) for i in range(40)] + [
        row(100 + i, "agreement", str(i % 2)) for i in range(40)
    ]
    assert s.label_problems(good) == []
    assert any(
        "synthetic" in p
        for p in s.label_problems([*good[:-1], row(999, "agreement", "1", "DEV-ONLY")])
    )
    assert any("unlabelled" in p for p in s.label_problems([*good, row(500, "agreement", "")]))
    assert any("< 30" in p for p in s.label_problems(good[:20] + good[40:]))
    assert any("duplicate" in p for p in s.label_problems([*good, good[0]]))
    one_class = [row(i, "calibration", "1") for i in range(40)] + good[40:]
    assert any("both supported" in p for p in s.label_problems(one_class))
