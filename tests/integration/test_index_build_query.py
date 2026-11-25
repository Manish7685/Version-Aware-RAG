"""Integration: build, save, reload and query every index type on a small corpus (roadmap P2
testing requirements: "integration tests for index build and query"). BM25 and FAISS are real; the
sentence encoders are deterministic stand-ins so no model is downloaded."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus
from version_aware_rag.retrieval.methods import (
    DENSE_QUERY_PREFIX,
    FINETUNED_ID,
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    RerankRetriever,
    finetuned_retriever,
    rrf,
)

PKG = {"1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}
TEXTS = {
    ("1.4", "a"): "date_trunc returns a TIMESTAMP when applied to a DATE",
    ("1.5", "a"): "date_trunc returns a DATE when applied to a DATE value",
    ("2.0", "a"): "date_trunc returns a DATE when applied to a DATE value",
    ("1.5", "b"): "list_sort sorts the elements of a list",
    ("2.0", "b"): "list_sort sorts the elements of a list ascending by default",
    ("1.5", "c"): "the httpfs extension reads remote parquet files",
}


def _corpus() -> RetrievalCorpus:
    chunks = [
        Chunk(f"{v}-{p}", v, "docs_page", "documentation", None, f"docs/{v}/{p}.md", (p,), t)
        for (v, p), t in TEXTS.items()
    ]
    return RetrievalCorpus(chunks, PKG)


class _Encoder:
    """Bag-of-hashed-words embedding, L2-normalised: deterministic and lexical enough to test."""

    def encode(self, texts: Sequence[str], **_: Any) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.removeprefix(DENSE_QUERY_PREFIX).lower().split():
                out[i, int(hashlib.sha256(w.encode()).hexdigest(), 16) % 64] += 1.0
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def _dense(corpus: RetrievalCorpus) -> DenseRetriever:
    d = DenseRetriever(corpus)
    d._model = _Encoder()
    return d


def test_bm25_build_save_load_and_version_filtered_query(tmp_path: Path) -> None:
    corpus = _corpus()
    bm25 = BM25Retriever(corpus)
    bm25.build()
    bm25.save(tmp_path / "bm25")
    loaded = BM25Retriever(corpus)
    loaded.load(tmp_path / "bm25")
    q = "date_trunc DATE"
    assert loaded.retrieve(q, None, 6) == bm25.retrieve(q, None, 6)
    hits = loaded.retrieve(q, "1.4", 3)
    assert hits[0][0] == "1.4-a"
    assert all(corpus.compatible(corpus.by_id[c], "1.4") for c, _ in hits)
    assert loaded.retrieve("zzzunknown", "1.5", 5)[0][1] == 0.0  # no matching token: zero scores


def test_dense_build_save_load_rejects_a_mismatched_index(tmp_path: Path) -> None:
    corpus = _corpus()
    dense = _dense(corpus)
    dense.build()
    dense.save(tmp_path / "dense")
    meta = json.loads((tmp_path / "dense" / "meta.json").read_text("utf-8"))
    assert meta == {"model": dense.model_name, "n": len(corpus.chunks)}
    loaded = _dense(corpus)
    loaded.load(tmp_path / "dense")
    unfiltered = loaded.retrieve("list_sort sorts a list", None, 3)  # FAISS path
    assert unfiltered[0][0] in {"1.5-b", "2.0-b"}
    filtered = loaded.retrieve("list_sort sorts a list", "1.5", 3)  # masked exhaustive path
    assert filtered[0][0] == "1.5-b"
    assert all(corpus.compatible(corpus.by_id[c], "1.5") for c, _ in filtered)
    smaller = RetrievalCorpus(corpus.chunks[:-1], PKG)
    with pytest.raises(ValueError, match="does not match"):
        _dense(smaller).load(tmp_path / "dense")


def test_hybrid_fuses_both_rankings_with_rrf(tmp_path: Path) -> None:
    corpus = _corpus()
    bm25 = BM25Retriever(corpus)
    bm25.build()
    dense = _dense(corpus)
    dense.build()
    hybrid = HybridRetriever(bm25, dense, depth=6)
    q, v = "date_trunc DATE", "1.5"
    expected = rrf(
        [[c for c, _ in bm25.retrieve(q, v, 6)], [c for c, _ in dense.retrieve(q, v, 6)]]
    )[:3]
    assert hybrid.retrieve(q, v, 3) == expected
    assert all(corpus.compatible(corpus.by_id[c], v) for c, _ in hybrid.retrieve(q, v, 6))


def test_rerank_rescoring_reorders_hybrid_candidates(tmp_path: Path) -> None:
    corpus = _corpus()
    bm25 = BM25Retriever(corpus)
    bm25.build()
    dense = _dense(corpus)
    dense.build()

    class _CrossEncoder:
        def predict(self, pairs: Sequence[tuple[str, str]], **_: Any) -> list[float]:
            return [1.0 if "httpfs" in t else 0.0 for _, t in pairs]

    rr = RerankRetriever(HybridRetriever(bm25, dense, depth=6), corpus, depth=6)
    rr._model = _CrossEncoder()
    hits = rr.retrieve("remote parquet files and date_trunc", "1.5", 3)
    assert hits[0][0] == "1.5-c"

    class _Empty(HybridRetriever):
        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return []

    empty = RerankRetriever(_Empty(bm25, dense), corpus)
    empty._model = _CrossEncoder()
    assert empty.retrieve("anything", "1.5", 3) == []  # no candidate: nothing to rerank


def test_finetuned_index_records_a_portable_model_id(tmp_path: Path) -> None:
    corpus = _corpus()
    with pytest.raises(FileNotFoundError):
        finetuned_retriever(tmp_path, corpus)
    model_dir = tmp_path / "data/build/models/embedder-ft"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", "utf-8")
    ft = finetuned_retriever(tmp_path, corpus)
    assert ft.name == "finetuned" and ft.model_id == FINETUNED_ID
    ft._model = _Encoder()
    ft.build()
    ft.save(tmp_path / "idx")
    assert json.loads((tmp_path / "idx" / "meta.json").read_text("utf-8"))["model"] == FINETUNED_ID
    other = finetuned_retriever(tmp_path, corpus)
    other._model = _Encoder()
    other.load(tmp_path / "idx")  # same id on any machine, whatever the absolute path
    assert other.retrieve("list_sort", "2.0", 1)[0][0] == "2.0-b"
