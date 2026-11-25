"""CI gate decision rule (design §6.2) and the answer pipeline contract (design §5.2-§5.3)."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.answer.pipeline import (
    AnswerPipeline,
    aggregate,
    matches,
    parse_answer,
    score_item,
)
from version_aware_rag.retrieval import gate
from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus

# --------------------------------------------------------------------------------------- gate


@pytest.mark.parametrize(
    ("band", "significant", "expected"),
    [
        ("none", False, "PASS"),
        ("none", True, "PASS"),
        ("negligible", False, "PASS"),
        ("negligible", True, "PASS + note"),
        ("meaningful", False, "WARN"),
        ("meaningful", True, "BLOCK"),
        ("gross", False, "BLOCK"),
        ("gross", True, "BLOCK"),
    ],
)
def test_outcome_table(band: str, significant: bool, expected: str) -> None:
    assert gate.outcome(band, significant) == expected


def test_band_boundaries_are_exact() -> None:
    tau, cap = Fraction("0.025"), Fraction("0.060")
    assert gate.band(Fraction(0), tau, cap) == "none"
    assert gate.band(Fraction("0.024"), tau, cap) == "negligible"
    assert gate.band(Fraction("0.025"), tau, cap) == "meaningful"  # exactly tau
    assert gate.band(Fraction("0.060"), tau, cap) == "gross"  # exactly C


def _scores(n: int, recall: str, ndcg: float, vcr: str) -> dict[str, dict[str, Any]]:
    return {
        f"T{1 + i % 2}-{i:02d}": {"recall@10": recall, "ndcg@10": ndcg, "vcr@10": vcr}
        for i in range(n)
    }


def test_identical_scores_pass_and_the_result_is_deterministic() -> None:
    base = _scores(20, "1/2", 0.5, "1/10")
    clusters = {i: i for i in base}
    types = {i: i.split("-")[0] for i in base}
    r1 = gate.decide(base, base, clusters, types, resamples=500)
    r2 = gate.decide(base, base, clusters, types, resamples=500)
    assert r1[0] == "PASS" and r1 == r2


def test_a_gross_drop_blocks_and_stale_or_nan_is_error() -> None:
    base = _scores(20, "1", 0.9, "0")
    cand = _scores(20, "1/2", 0.4, "3/10")
    clusters = {i: i for i in base}
    types = {i: i.split("-")[0] for i in base}
    overall, results, _ = gate.decide(base, cand, clusters, types, resamples=500)
    assert overall == "BLOCK" and all(r.outcome == "BLOCK" for r in results)
    assert results[0].worst_items == tuple(sorted(base)[:10])  # ties broken by item_id
    assert gate.decide(base, {**cand, "extra": cand["T1-00"]}, clusters, types)[0] == "ERROR"
    bad = {**cand, "T1-00": {**cand["T1-00"], "ndcg@10": float("nan")}}
    assert gate.decide(base, bad, clusters, types, resamples=200)[0] == "ERROR"
    assert gate.EXIT["BLOCK"] == 1 and gate.EXIT["WARN"] == 0 and gate.EXIT["ERROR"] == 2


# ------------------------------------------------------------------------------------ answers

PKG = {"1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}


def _corpus() -> RetrievalCorpus:
    return RetrievalCorpus(
        [
            Chunk(
                "c1",
                "1.5",
                "docs_page",
                "documentation",
                None,
                "docs/1.5/f.md",
                ("F",),
                "frob_count returns BIGINT.",
            )
        ],
        PKG,
    )


class _Retriever:
    name = "fake"

    def __init__(self, hits: list[str]) -> None:
        self.hits = hits

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        return [(h, 1.0) for h in self.hits]


class _Gen:
    def __init__(self, response: str) -> None:
        self.response, self.calls = response, 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self.response


def _ok(**kw: Any) -> str:
    return json.dumps(
        {
            "abstain": False,
            "short_answer": "BIGINT",
            "explanation": "It returns BIGINT.",
            "citations": ["c1"],
            **kw,
        }
    )


def test_a_grounded_answer_is_returned_and_cached(tmp_path: Path) -> None:
    gen = _Gen(_ok())
    pipe = AnswerPipeline(
        _Retriever(["c1"]), _corpus(), gen, lambda p: [], tmp_path / "cache.jsonl"
    )
    a = pipe.answer("What does frob_count return?", "1.5")
    assert (a.abstain, a.short_answer, a.citations, a.flags) == (False, "BIGINT", ["c1"], [])
    again = AnswerPipeline(
        _Retriever(["c1"]), _corpus(), _Gen("unused"), lambda p: [], tmp_path / "cache.jsonl"
    )
    assert (
        again.answer("What does frob_count return?", "1.5").short_answer == "BIGINT"
        and gen.calls == 1
    )


def test_no_evidence_or_a_privacy_block_abstains_without_calling_the_model() -> None:
    gen = _Gen(_ok())
    assert AnswerPipeline(_Retriever([]), _corpus(), gen, lambda p: []).answer(
        "q?", "1.5"
    ).flags == ["no_evidence"]
    blocked = AnswerPipeline(
        _Retriever(["c1"]), _corpus(), gen, lambda p: ["contains a restricted line"]
    ).answer("q?", "1.5")
    assert blocked.abstain and blocked.flags == ["privacy_blocked"] and gen.calls == 0


def test_invalid_citations_and_malformed_responses_never_become_answers() -> None:
    a = parse_answer(_ok(citations=["c9"]), "q", "1.5", ["c1"])
    assert a.abstain and "invalid_citation" in a.flags and "ungrounded_answer" in a.flags
    assert parse_answer("not json", "q", "1.5", ["c1"]).flags == ["malformed_response"]


def test_deterministic_matcher_and_metrics() -> None:
    assert matches("bigint", "BIGINT", [], "type_name") is True
    assert matches("true", "yes", [], "yes_no") is True
    assert matches("maybe", "no", [], "yes_no") is False  # unclear is wrong, not "not yes"
    assert matches("False.", "no", [], "yes_no") is True
    assert matches("SELECT 1", "SELECT 1", [], "sql_expression") is True  # identical
    assert matches("SELECT 2 - 1", "SELECT 1", [], "sql_expression") is None  # owner-graded
    t2 = {
        "draft_id": "d",
        "type": "T2",
        "gold_short_answer": "BIGINT",
        "accepted_aliases": [],
        "answer_type": "type_name",
        "other_version_answers": {"1.4": "INTEGER"},
    }
    wrong = parse_answer(_ok(short_answer="INTEGER"), "q", "1.5", ["c1"])
    s = score_item(t2, wrong, {"c1": 2})
    assert s["version_outcome"] == "wrong_version" and s["citation_precision"] == 1.0
    t5 = {"draft_id": "e", "type": "T5"}
    agg = aggregate(
        [s, score_item(t5, parse_answer(json.dumps({"abstain": True}), "q", "1.4", []), {})]
    )
    assert (
        agg["t2_wrong_version_rate"] == 1.0
        and agg["abstention_recall"] == 1.0
        and agg["abstention_precision"] == 1.0
    )


def test_a_tripping_passage_is_dropped_and_the_rest_is_reaudited() -> None:
    corpus = RetrievalCorpus(
        [
            *_corpus().chunks,
            Chunk(
                "bad",
                "1.5",
                "docs_page",
                "documentation",
                None,
                "docs/1.5/g.md",
                ("G",),
                "RESTRICTED words",
            ),
        ],
        PKG,
    )
    gen = _Gen(_ok())

    def audit(prompt: str) -> list[str]:
        return ["contains a restricted 10-word sequence"] if "RESTRICTED" in prompt else []

    a = AnswerPipeline(_Retriever(["c1", "bad"]), corpus, gen, audit).answer("q?", "1.5")
    assert a.short_answer == "BIGINT" and a.retrieved == ["c1"] and a.flags == ["context_filtered"]
    only_bad = AnswerPipeline(_Retriever(["bad"]), corpus, gen, audit).answer("q?", "1.5")
    assert only_bad.flags == ["privacy_blocked"] and gen.calls == 1


def _corpus_with(*extra: tuple[str, str]) -> RetrievalCorpus:
    chunks = [
        Chunk(cid, "1.5", "docs_page", "documentation", None, f"docs/1.5/{cid}.md", ("X",), text)
        for cid, text in extra
    ]
    return RetrievalCorpus([*_corpus().chunks, *chunks], PKG)


class _Recorder(_Gen):
    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return super().generate(prompt)


def test_the_dropped_passage_never_reaches_the_generator() -> None:
    corpus = _corpus_with(("bad", "SECRET-RELEASE-TEXT here"))
    gen = _Recorder(_ok())

    def audit(prompt: str) -> list[str]:
        return ["contains a restricted line"] if "SECRET-RELEASE-TEXT" in prompt else []

    a = AnswerPipeline(_Retriever(["c1", "bad"]), corpus, gen, audit).answer("q?", "1.5")
    assert a.flags == ["context_filtered"] and gen.calls == 1
    assert all("SECRET-RELEASE-TEXT" not in p and "bad" not in p.split() for p in gen.prompts)
    assert not audit(gen.prompts[0])  # the prompt actually sent passes the full audit


def test_a_sequence_spanning_two_passages_blocks_the_whole_prompt() -> None:
    # Each passage passes alone; only their concatenation trips the audit (a restricted
    # sequence split across passages). Nothing may be sent.
    corpus = _corpus_with(("p1", "ALPHA half"), ("p2", "BETA half"))
    gen = _Recorder(_ok(citations=["p1"]))

    def audit(prompt: str) -> list[str]:
        return (
            ["contains a restricted 10-word sequence"]
            if "ALPHA" in prompt and "BETA" in prompt
            else []
        )

    a = AnswerPipeline(_Retriever(["p1", "p2"]), corpus, gen, audit).answer("q?", "1.5")
    assert a.abstain and a.flags == ["privacy_blocked"] and gen.calls == 0


def test_an_answer_citing_a_dropped_passage_becomes_an_abstention() -> None:
    # The model cannot see the dropped chunk, but if it names it anyway the citation is
    # invalid (not among the passages sent) and no unsupported answer is returned.
    corpus = _corpus_with(("bad", "SECRET-RELEASE-TEXT"))
    gen = _Recorder(_ok(citations=["bad"]))

    def audit(prompt: str) -> list[str]:
        return ["contains a restricted line"] if "SECRET-RELEASE-TEXT" in prompt else []

    a = AnswerPipeline(_Retriever(["c1", "bad"]), corpus, gen, audit).answer("q?", "1.5")
    assert a.abstain and a.short_answer == "" and a.citations == []
    assert "invalid_citation" in a.flags and "context_filtered" in a.flags


# ------------------------------------------------------------- baseline protection (IR-8)


def test_enforced_gate_needs_the_owner_approved_baseline_sha256(tmp_path: Path) -> None:
    import hashlib

    from version_aware_rag.retrieval.gate_commands import approved_baseline_problem

    raw = b'{"scores": {}}\n'
    digest = hashlib.sha256(raw).hexdigest()
    cfg = tmp_path / "configs" / "gate.toml"
    assert "unreadable" in str(approved_baseline_problem(tmp_path, raw))
    cfg.parent.mkdir()
    cfg.write_text('[gate]\nbaseline_sha256 = ""\n', "utf-8")
    assert "no owner-approved baseline" in str(approved_baseline_problem(tmp_path, raw))
    cfg.write_text(f'[gate]\nbaseline_sha256 = "{digest}"\n', "utf-8")
    assert approved_baseline_problem(tmp_path, raw) is None
    edited = raw.replace(b"{}", b'{"x": 1}')  # an accidental edit in an ordinary PR
    assert "does not match" in str(approved_baseline_problem(tmp_path, edited))


def test_repo_gate_config_has_no_approved_baseline_yet() -> None:
    import tomllib

    root = Path(__file__).resolve().parents[2]
    cfg = tomllib.loads((root / "configs/gate.toml").read_text("utf-8"))
    # Only the owner records an approved baseline (D14); until then the enforced gate is ERROR.
    assert cfg["gate"]["baseline_sha256"] == "" or (root / "benchmark/gate/baseline.json").is_file()


def test_a_config_change_is_gated_not_treated_as_stale() -> None:
    from version_aware_rag.retrieval.gate_commands import STALE_KEYS

    assert STALE_KEYS == ("items_sha256", "corpus_sha256")  # design §6.1
