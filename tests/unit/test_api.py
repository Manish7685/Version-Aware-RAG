"""FastAPI endpoints (RAG-M11) against a pre-loaded engine: no models, indexes or network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from version_aware_rag.answer.pipeline import AnswerPipeline
from version_aware_rag.api.app import Engine, create_app
from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus

PKG = {"1.2": "1.2.2", "1.3": "1.3.2", "1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}


def _chunk(cid: str, v: str) -> Chunk:
    return Chunk(
        cid, v, "docs_page", "documentation", None, f"docs/{v}/sql/f.md", ("F",), f"frob {v}"
    )


class _Retriever:
    name = "hybrid"

    def __init__(self, corpus: RetrievalCorpus) -> None:
        self.corpus = corpus

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        hits = [c.chunk_id for c in self.corpus.chunks if version is None or c.version == version]
        return [(h, 1.0 / (n + 1)) for n, h in enumerate(hits[:k])]


class _Gen:
    def generate(self, prompt: str) -> str:
        return json.dumps(
            {"abstain": False, "short_answer": "BIGINT", "explanation": "x", "citations": ["c15"]}
        )


def _client(tmp_path: Path) -> TestClient:
    corpus = RetrievalCorpus([_chunk("c15", "1.5"), _chunk("c20", "2.0")], PKG)
    retriever = _Retriever(corpus)
    engine = Engine(tmp_path, corpus=corpus, retrievers={"hybrid": retriever})
    engine.pipelines["hybrid"] = AnswerPipeline(retriever, corpus, _Gen(), lambda p: [])
    return TestClient(create_app(tmp_path, engine))


def test_health_versions_and_search(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/health").json() == {"status": "ok", "chunks": 2, "methods": ["hybrid"]}
    assert [v["version"] for v in c.get("/versions").json()["versions"]] == list(PKG)
    hits = c.get("/search", params={"q": "frob", "version": "1.5"}).json()["hits"]
    assert [h["chunk_id"] for h in hits] == ["c15"] and hits[0]["compatible"] is True


def test_answer_cites_retrieved_evidence(tmp_path: Path) -> None:
    r = _client(tmp_path).post("/answer", json={"question": "What type?", "version": "1.5"})
    body = r.json()
    assert r.status_code == 200 and body["short_answer"] == "BIGINT"
    assert [h["chunk_id"] for h in body["citations"]] == ["c15"]


def test_compare_and_input_validation(tmp_path: Path) -> None:
    c = _client(tmp_path)
    res = c.get("/compare", params={"q": "frob", "versions": "1.5,2.0"}).json()["results"]
    assert {v: [h["chunk_id"] for h in hs] for v, hs in res.items()} == {
        "1.5": ["c15"],
        "2.0": ["c20"],
    }
    assert c.get("/search", params={"q": "frob", "version": "9.9"}).status_code == 422
    assert c.post("/answer", json={"question": "x"}).status_code == 422  # too short


def test_answer_without_a_key_is_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VRAG_GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file is picked up
    corpus = RetrievalCorpus([_chunk("c15", "1.5")], PKG)
    engine = Engine(tmp_path, corpus=corpus, retrievers={"hybrid": _Retriever(corpus)})
    r = TestClient(create_app(tmp_path, engine)).post("/answer", json={"question": "What type?"})
    assert r.status_code == 503


class _BindingGen:
    """Behaves like ``LedgerGenerator``: refuses to generate unless bound, and checks that the
    quota run lock is held for the whole call."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path, self.calls, self.binds = lock_path, 0, 0
        self.heartbeat: Callable[[], None] | None = None

    def bind(self, heartbeat: Callable[[], None]) -> None:
        self.heartbeat, self.binds = heartbeat, self.binds + 1

    def generate(self, prompt: str) -> str:
        if self.heartbeat is None:
            raise RuntimeError("generator used outside the run lock")
        assert self.lock_path.is_file()  # the lock is held while Gemini would be called
        self.heartbeat()  # the bound heartbeat refreshes the live lock (ProcessLock.refresh)
        self.calls += 1
        return _Gen().generate(prompt)


def _binding_client(tmp_path: Path) -> tuple[TestClient, _BindingGen, Path]:
    lock = tmp_path / "benchmark/drafts/run.lock"
    corpus = RetrievalCorpus([_chunk("c15", "1.5"), _chunk("c20", "2.0")], PKG)
    retriever = _Retriever(corpus)
    gen = _BindingGen(lock)
    engine = Engine(tmp_path, corpus=corpus, retrievers={"hybrid": retriever})
    engine.pipelines["hybrid"] = AnswerPipeline(retriever, corpus, gen, lambda p: [])
    return TestClient(create_app(tmp_path, engine)), gen, lock


def test_answer_binds_the_generator_under_the_run_lock_per_request(tmp_path: Path) -> None:
    c, gen, lock = _binding_client(tmp_path)
    # Two different questions, so neither is served from the pipeline's response cache: every
    # request binds afresh under the lock, and the lock is released after each.
    for q in ("What type?", "Which type is returned?"):
        r = c.post("/answer", json={"question": q, "version": "1.5"})
        assert r.status_code == 200 and r.json()["short_answer"] == "BIGINT"
        assert not lock.exists()
    assert gen.calls == 2 and gen.binds == 2


def test_answer_is_503_and_sends_nothing_while_another_run_holds_the_lock(tmp_path: Path) -> None:
    c, gen, lock = _binding_client(tmp_path)
    lock.parent.mkdir(parents=True)
    lock.write_text("4242", encoding="utf-8")  # a live run (fresh mtime) holds the lock
    r = c.post("/answer", json={"question": "What type?", "version": "1.5"})
    assert r.status_code == 503 and "lock" in r.json()["detail"]
    assert gen.calls == 0 and gen.binds == 0
    assert lock.read_text(encoding="utf-8") == "4242"  # the other run's lock is left alone


# --- RAG-M10 edge cases: empty query, unknown version, no evidence, very long query ---


@pytest.mark.parametrize("q", ["", " ", "     ", "\t\n"])
def test_empty_or_blank_queries_are_rejected_on_every_endpoint(tmp_path: Path, q: str) -> None:
    c = _client(tmp_path)
    assert c.get("/search", params={"q": q}).status_code == 422
    assert c.get("/compare", params={"q": q}).status_code == 422
    assert c.post("/answer", json={"question": q}).status_code == 422


def test_unknown_version_is_422_on_every_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/search", params={"q": "frob", "version": "3.0"}).status_code == 422
    assert c.get("/compare", params={"q": "frob", "versions": "1.5,0.9"}).status_code == 422
    r = c.post("/answer", json={"question": "frob?", "version": "latest"})
    assert r.status_code == 422
    assert "unknown version" in r.json()["detail"]


def test_compare_needs_at_least_one_version_and_dedupes(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/compare", params={"q": "frob", "versions": " , "}).status_code == 422
    res = c.get("/compare", params={"q": "frob", "versions": "1.5,1.5"}).json()["results"]
    assert list(res) == ["1.5"]


def test_no_evidence_abstains_without_calling_the_generator(tmp_path: Path) -> None:
    class _Empty:
        name = "hybrid"

        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return []

    class _NeverCalled:
        def generate(self, prompt: str) -> str:
            raise AssertionError("the generator must not be called without evidence")

    corpus = RetrievalCorpus([_chunk("c15", "1.5")], PKG)
    engine = Engine(tmp_path, corpus=corpus, retrievers={"hybrid": _Empty()})
    engine.pipelines["hybrid"] = AnswerPipeline(_Empty(), corpus, _NeverCalled(), lambda p: [])
    c = TestClient(create_app(tmp_path, engine))
    body = c.post("/answer", json={"question": "what is frob?", "version": "1.5"}).json()
    assert body["abstain"] is True
    assert body["flags"] == ["no_evidence"]
    assert body["citations"] == [] and body["retrieved"] == []
    assert c.get("/search", params={"q": "frob", "version": "1.5"}).json()["hits"] == []


def test_very_long_queries_are_bounded(tmp_path: Path) -> None:
    c = _client(tmp_path)
    at_limit = "frob " * 100  # 500 characters: accepted (stripped to 499)
    assert c.get("/search", params={"q": at_limit}).status_code == 200
    assert c.post("/answer", json={"question": at_limit}).status_code == 200
    too_long = "x" * 501
    assert c.get("/search", params={"q": too_long}).status_code == 422
    assert c.get("/compare", params={"q": too_long}).status_code == 422
    assert c.post("/answer", json={"question": too_long}).status_code == 422
    assert c.get("/search", params={"q": "frob", "k": 51}).status_code == 422


def test_query_whitespace_is_normalised(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/search", params={"q": "  frob   \t x "}).json()["query"] == "frob x"


def test_rerank_search_exposes_first_stage_ranks(tmp_path: Path) -> None:
    corpus = RetrievalCorpus([_chunk("a", "1.5"), _chunk("b", "1.5"), _chunk("c", "1.5")], PKG)

    class _Base:
        name = "hybrid"

        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return [("a", 0.3), ("b", 0.2), ("c", 0.1)][:k]

    class _Rerank:
        name = "rerank"
        depth = 50
        base = _Base()

        def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
            return [("c", 9.0), ("a", 5.0)][:k]

    engine = Engine(tmp_path, corpus=corpus, retrievers={"rerank": _Rerank(), "hybrid": _Base()})
    c = TestClient(create_app(tmp_path, engine))
    body = c.get("/search", params={"q": "frob", "version": "1.5", "method": "rerank"}).json()
    assert body["first_stage"] == "hybrid"
    assert [(h["chunk_id"], h["base_rank"]) for h in body["hits"]] == [("c", 3), ("a", 1)]
    plain = c.get("/search", params={"q": "frob", "method": "hybrid"}).json()
    assert plain["first_stage"] is None and "base_rank" not in plain["hits"][0]


def test_unfiltered_search_marks_compatibility_against_the_target(tmp_path: Path) -> None:
    c = _client(tmp_path)
    on = c.get("/search", params={"q": "frob", "version": "1.5"}).json()
    assert on["filtered"] is True and [h["chunk_id"] for h in on["hits"]] == ["c15"]
    off = c.get("/search", params={"q": "frob", "version": "1.5", "filter": "false"}).json()
    assert off["filtered"] is False
    assert {h["chunk_id"]: h["compatible"] for h in off["hits"]} == {"c15": True, "c20": False}
