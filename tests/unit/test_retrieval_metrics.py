"""Retrieval metrics, RRF, qrels and the harness against hand-computed values (design §5.4)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from version_aware_rag.retrieval import harness
from version_aware_rag.retrieval import metrics as m
from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus
from version_aware_rag.retrieval.methods import rrf

PKG = {"1.2": "1.2.2", "1.3": "1.3.2", "1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}


def test_recall_mrr_ndcg_by_hand() -> None:
    q = {"a": 2, "b": 2, "c": 1}
    ranked = ["x", "a", "c", "y", "b"]
    assert m.recall_at_k(ranked, q, 2) == 0.5  # a of {a, b}
    assert m.recall_at_k(ranked, q, 5) == 1.0
    assert m.mrr_at_k(ranked, q, 10) == 0.5  # first grade-2 at rank 2
    assert m.mrr_at_k(["x", "y"], q, 10) == 0.0
    # DCG = 3/log2(3) + 1/log2(4) + 3/log2(6); IDCG = 3/1 + 3/log2(3) + 1/log2(4)
    dcg = 3 / math.log2(3) + 1 / 2 + 3 / math.log2(6)
    idcg = 3 + 3 / math.log2(3) + 1 / 2
    assert m.ndcg_at_k(ranked, q, 10) == pytest.approx(dcg / idcg)
    assert math.isnan(m.recall_at_k(ranked, {"c": 1}, 5))  # no grade-2 chunk: excluded


def test_version_confusion_rate() -> None:
    assert m.vcr_at_k(["a", "b", "c", "d"], lambda c: c in {"a", "b", "c"}, 4) == 0.25


def test_rrf_by_hand() -> None:
    fused = dict(rrf([["a", "b"], ["b", "c"]], k=60))
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["a"] == pytest.approx(1 / 61) and fused["c"] == pytest.approx(1 / 62)
    assert next(d for d, _ in rrf([["a", "b"], ["b", "c"]], k=60)) == "b"


def test_bootstrap_is_seeded_paired_and_cluster_aware() -> None:
    vals = {"a": [1.0, 0.0, 1.0, 0.0], "b": [1.0, 1.0, 1.0, 1.0]}
    clusters = ["s1", "s1", "x", "y"]
    r1 = m.paired_cluster_bootstrap(vals, clusters, 2000, seed=1)
    r2 = m.paired_cluster_bootstrap(vals, clusters, 2000, seed=1)
    assert r1 == r2 and r1["a"]["mean"] == 0.5 and r1["b"]["ci_low"] == r1["b"]["ci_high"] == 1.0
    d = m.paired_difference_ci(vals["a"], vals["b"], clusters, 2000, seed=1)
    assert d["mean"] == 0.5 and d["ci_low"] >= 0.0


def _corpus() -> RetrievalCorpus:
    def chunk(cid: str, v: str, text: str, page: str = "sql/f.md") -> Chunk:
        return Chunk(cid, v, "docs_page", "documentation", None, f"docs/{v}/{page}", ("F",), text)

    return RetrievalCorpus(
        [
            chunk("c15", "1.5", "frob_count counts the frobs in a list."),
            chunk("c15b", "1.5", "Other content on the same page."),
            chunk("c20", "2.0", "frob_count counts the frobs in a list."),
            chunk("c15x", "1.5", "Unrelated page.", page="sql/g.md"),
        ],
        PKG,
    )


def _item(target: str = "1.5") -> dict[str, object]:
    return {
        "draft_id": "d1",
        "item_id": "T3-01",
        "type": "T3",
        "target_version": target,
        "question": "frob count",
        "evidence_spans": [
            {"text": "frob_count counts the frobs", "source_path": f"docs/{target}/sql/f.md"}
        ],
    }


def test_qrels_are_graded_and_version_compatible() -> None:
    q = harness.qrels_for(_item("1.5"), _corpus())
    assert q == {"c15": 2, "c15b": 1}  # the 2.0 chunk is incompatible with target 1.5


def test_a_span_with_a_source_path_keeps_its_path_and_scores() -> None:
    corpus = _corpus()
    span = {"text": "frob_count counts the frobs", "source_path": "docs/1.5/sql/f.md"}
    # The recorded path wins, even when a passage id points elsewhere.
    assert harness.span_source_path({**span, "passage_id": "chunk:c15x"}, corpus) == (
        "docs/1.5/sql/f.md"
    )
    assert harness.qrels_for(_item("1.5"), corpus) == {"c15": 2, "c15b": 1}


def test_an_edited_span_without_a_source_path_resolves_through_its_chunk_id() -> None:
    # The review importer stores edited evidence as {passage_id, text, version} only.
    corpus = _corpus()
    span = {"passage_id": "chunk:c15", "text": "frob_count counts the frobs", "version": "1.5"}
    assert harness.span_source_path(span, corpus) == "docs/1.5/sql/f.md"
    item = _item("1.5")
    item["evidence_spans"] = [span]
    # Same qrels as the equivalent span with a recorded path.
    assert harness.qrels_for(item, corpus) == harness.qrels_for(_item("1.5"), corpus)


def test_chunk_id_resolution_requires_a_recorded_and_matching_version() -> None:
    corpus = _corpus()
    text = "frob_count counts the frobs"  # present verbatim in both c15 (1.5) and c20 (2.0)
    # Missing version: the chunk id alone is not enough, even though the id exists.
    with pytest.raises(harness.UnresolvedEvidenceError, match="no recorded span version"):
        harness.span_source_path({"passage_id": "chunk:c15", "text": text}, corpus, "T3-01")
    # Mismatched version: matching text does not rescue a span pointing at the wrong version.
    with pytest.raises(harness.UnresolvedEvidenceError, match=r"is version 2\.0, span says 1\.5"):
        harness.span_source_path(
            {"passage_id": "chunk:c20", "text": text, "version": "1.5"}, corpus, "T3-01"
        )
    # Matching version: resolved through the exact chunk id.
    span = {"passage_id": "chunk:c20", "text": text, "version": "2.0"}
    assert harness.span_source_path(span, corpus, "T3-01") == "docs/2.0/sql/f.md"


@pytest.mark.parametrize(
    ("span", "reason"),
    [
        ({"passage_id": "chunk:missing", "text": "x", "version": "1.5"}, "not in the corpus"),
        ({"passage_id": "chunk:c20", "text": "x", "version": "1.5"}, "is version 2.0"),
        (
            {"passage_id": "chunk:c15", "text": "frob_count counts the frobs"},
            "no recorded span version",
        ),
        ({"passage_id": "chunk:c15", "text": "x", "version": ""}, "no recorded span version"),
        ({"passage_id": "src:docs/1.5/sql/f.md:1-2", "text": "x"}, "without source_path"),
        ({"text": "frob_count counts the frobs"}, "without source_path"),
    ],
)
def test_an_unresolvable_span_fails_explicitly(span: dict[str, str], reason: str) -> None:
    item = _item("1.5")
    item["evidence_spans"] = [span]
    with pytest.raises(harness.UnresolvedEvidenceError, match=reason) as err:
        harness.qrels_for(item, _corpus())
    assert str(err.value).startswith("T3-01:")  # the item id, never the span text


class _Fake:
    name = "fake"

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        ranked = ["c20", "c15", "c15b"] if version is None else ["c15", "c15b"]
        return [(c, 1.0) for c in ranked[:k]]


def test_excluded_items_are_listed_with_their_reason() -> None:
    t5 = {
        **_item(),
        "draft_id": "d5",
        "item_id": "T5-01",
        "type": "T5",
        "answerable": False,
        "evidence_spans": [],
    }
    lost = {
        **_item(),
        "draft_id": "d6",
        "item_id": "T3-09",
        "evidence_spans": [{"text": "text in no chunk", "source_path": "docs/1.5/sql/f.md"}],
    }
    _evaluated, _runs, info = harness.evaluate([_item(), t5, lost], _corpus(), [_Fake()])
    assert info["evaluated"] == 1 and info["excluded_no_grade2"] == 2
    assert info["excluded_reasons"] == {
        "T5-01": "unanswerable (no evidence spans)",
        "T3-09": "evidence not found in a compatible chunk",
    }


def test_harness_reports_filtered_and_unfiltered_runs() -> None:
    _evaluated, runs, info = harness.evaluate([_item()], _corpus(), [_Fake()])
    assert info["evaluated"] == 1 and len(runs) == 2
    unfiltered, filtered = runs
    assert unfiltered.per_item["mrr@10"] == [0.5] and filtered.per_item["mrr@10"] == [1.0]
    assert unfiltered.per_item["vcr@10"][0] == pytest.approx(1 / 3) and filtered.per_item[
        "vcr@10"
    ] == [0.0]


def test_qrels_fall_back_to_tokens_then_to_the_cited_source_lines() -> None:
    def chunk(cid: str, text: str, lines: tuple[int, int]) -> Chunk:
        return Chunk(
            cid,
            "1.5",
            "docs_page",
            "documentation",
            None,
            "docs/1.5/sql/f.md",
            ("F",),
            text,
            *lines,
        )

    corpus = RetrievalCorpus(
        [
            chunk("a", "The `frob_count` function **counts** frobs.", (1, 9)),
            chunk("b", "Rendered table text.", (10, 20)),
            chunk("c", "Other text.", (21, 30)),
        ],
        PKG,
    )
    item = {
        "draft_id": "d",
        "item_id": "T3-01",
        "type": "T3",
        "target_version": "1.5",
        "question": "q",
        "evidence_spans": [
            {
                "text": "frob_count function counts",
                "source_path": "docs/1.5/sql/f.md",
                "passage_id": "src:docs/1.5/sql/f.md:1-9",
            },
            {
                "text": "| raw | markdown |",
                "source_path": "docs/1.5/sql/f.md",
                "passage_id": "src:docs/1.5/sql/f.md:12-14",
            },
        ],
    }
    assert harness.qrels_for(item, corpus) == {"a": 2, "b": 2, "c": 1}


def test_finetune_pairs_use_dev_only_and_refuse_leakage() -> None:
    from version_aware_rag.retrieval.finetune import LeakageError, build_pairs

    class _Miner:
        name = "miner"

        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return [(c, 1.0) for c in ["c15", "c15b", "c15x"][:k]]

    corpus = _corpus()
    dev = [_item()]
    pairs = build_pairs(dev, [], corpus, _Miner(), n_negatives=2)
    # c15 is the grade-2 positive; c15b (grade 1, same page) is never a negative; c15x is.
    assert [(p.positive, p.negatives) for p in pairs] == [("c15", ("c15x",))]
    held = [{**_item(), "item_id": "T3-99"}]  # a held-out item on the same page
    with pytest.raises(LeakageError, match="held-out page"):
        build_pairs(dev, held, corpus, _Fake())
    with pytest.raises(LeakageError, match="both dev"):
        build_pairs(dev, [_item()], corpus, _Fake())


def test_maxsim_sums_the_best_document_token_per_query_token() -> None:
    import numpy as np

    from version_aware_rag.retrieval.methods import maxsim

    q = np.array([[1.0, 0.0], [0.0, 1.0]])
    d = np.array([[0.9, 0.1], [0.2, 0.8], [0.0, 0.0]])
    assert maxsim(q, d) == pytest.approx(0.9 + 0.8)


def test_late_interaction_reranks_hybrid_candidates_with_maxsim() -> None:
    import numpy as np

    from version_aware_rag.retrieval.methods import LateInteractionRetriever

    class _Enc:
        def encode_query(self, text: str) -> np.ndarray:
            return np.array([[1.0, 0.0]])

        def encode_documents(self, texts: list[str]) -> list[np.ndarray]:
            return [
                np.array([[1.0, 0.0]]) if "frob_count" in t else np.array([[0.0, 1.0]])
                for t in texts
            ]

    class _Base:
        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return [("c15b", 1.0), ("c15", 0.5)]

    late = LateInteractionRetriever(_Base(), _corpus())  # type: ignore[arg-type]
    late._enc = _Enc()  # type: ignore[assignment]
    assert [c for c, _ in late.retrieve("q", "1.5", 2)] == ["c15", "c15b"]


def test_the_retrieval_corpus_never_loads_restricted_chunks(tmp_path: Path) -> None:
    import json as _json

    from version_aware_rag.retrieval.corpus import load_corpus

    def row(cid: str, kind: str) -> str:
        return _json.dumps(
            {
                "chunk_id": cid,
                "version": "1.5",
                "source_kind": kind,
                "source_type": "documentation",
                "introduced_in": None,
                "source_path": "docs/1.5/x.md",
                "section_path": ["X"],
                "content": "synthetic",
                "line_start": 1,
                "line_end": 2,
            }
        )

    (tmp_path / "1.5.jsonl").write_text(
        "\n".join([row("d", "docs_page"), row("m", "migration_guide"), row("r", "release_post")])
        + "\n",
        "utf-8",
    )
    corpus = load_corpus(tmp_path, PKG, versions=("1.5",))
    assert [c.chunk_id for c in corpus.chunks] == ["d", "m"]


# ------------------------------------------- hard negatives, slices and Holm (P2-R04, R27-R29)


def test_version_confusers_are_the_same_page_and_section_in_incompatible_versions() -> None:
    corpus = _corpus()
    q = harness.qrels_for(_item("1.5"), corpus)
    assert harness.version_confusers(_item("1.5"), corpus, q) == ["c20"]
    # Target 2.0: both 1.5 chunks of the page share section ("F",) with the relevant 2.0 chunk;
    # the 1.5 chunk of another page is not a confuser.
    q20 = harness.qrels_for(_item("2.0"), corpus)
    assert harness.version_confusers(_item("2.0"), corpus, q20) == ["c15", "c15b"]


def test_lexical_confuser_candidates_are_unconfirmed_non_relevant_bm25_hits() -> None:
    class _Bm25:
        name = "bm25"

        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            assert version == "1.5" and k == harness.LEXICAL_DEPTH
            return [("c15", 3.0), ("c15x", 2.0), ("c15b", 1.0)]

    corpus = _corpus()
    tags = harness.hard_negative_tags(
        [_item()], corpus, {"d1": harness.qrels_for(_item(), corpus)}, _Bm25()
    )
    assert tags["d1"]["lexical_confuser_candidates"] == ["c15x"]  # grade 0 only
    assert tags["d1"]["lexical_confirmed"] is False


def test_slices_and_confuser_rate_are_reported() -> None:
    evaluated, runs, info = harness.evaluate([_item()], _corpus(), [_Fake()])
    assert info["slices"]["change_introduced"] == ["d1"]
    assert info["slices"]["hard_negative"] == ["d1"]
    assert info["slices"]["multi_hop"] == [] and info["slices"]["version_specific"] == []
    unfiltered, filtered = runs
    assert unfiltered.per_item["confuser@10"] == [pytest.approx(1 / 3)]  # c20 in the top 3
    assert filtered.per_item["confuser@10"] == [0.0]
    summary = harness.summarise(
        evaluated, runs, baseline="fake", n_resamples=50, slices=info["slices"]
    )
    assert set(summary["runs"][0]["slices"]) == {"change_introduced", "hard_negative"}
    md = harness.render_markdown(summary, info, "test")
    assert "## Slices" in md and "hard_negative" in md


def test_holm_step_down_by_hand() -> None:
    # sorted p: 0.01 <= 0.05/3, 0.02 <= 0.05/2, 0.04 > 0.05/1? no: 0.04 <= 0.05 -> all rejected
    assert m.holm({"a": 0.01, "b": 0.04, "c": 0.02}) == {"a": True, "b": True, "c": True}
    # 0.03 > 0.05/2 stops the procedure: b and c are kept even though 0.04 < 0.05
    assert m.holm({"a": 0.01, "b": 0.03, "c": 0.04}) == {"a": True, "b": False, "c": False}
    assert m.holm({"a": math.nan, "b": 0.001}) == {"a": False, "b": True}


def test_bootstrap_p_value_is_seeded_and_extreme_for_a_constant_shift() -> None:
    clusters = [str(i) for i in range(20)]
    assert m.bootstrap_p_value([0.5] * 20, clusters, 500) == 0.0  # every resample is > 0
    assert m.bootstrap_p_value([0.0] * 20, clusters, 500) == 1.0
    mixed = [0.3, -0.1] * 10
    assert m.bootstrap_p_value(mixed, clusters, 500) == m.bootstrap_p_value(mixed, clusters, 500)


def test_eval_uses_the_frozen_test_split_once_it_exists(tmp_path: Path) -> None:
    import json

    from version_aware_rag.retrieval.commands import eval_items

    with pytest.raises(FileNotFoundError):
        eval_items(tmp_path, "test")  # not frozen yet: never silently falls back
    splits = tmp_path / "benchmark" / "splits"
    splits.mkdir(parents=True)
    (splits / "test.jsonl").write_text(json.dumps({"draft_id": "t1"}) + "\n", "utf-8")
    (splits / "dev.jsonl").write_text(json.dumps({"draft_id": "d1"}) + "\n", "utf-8")
    (splits / "test_manifest.json").write_text("{}", "utf-8")
    items, label = eval_items(tmp_path)  # auto
    assert [x["draft_id"] for x in items] == ["t1"] and label.startswith("frozen test split")
    assert [x["draft_id"] for x in eval_items(tmp_path, "dev")[0]] == ["d1"]
