"""FastAPI app (RAG-M11): ``/health``, ``/versions``, ``/search``, ``/answer``, ``/compare``.

Only permitted chunks (MIT documentation and migration guides) are ever returned or sent to the
generator. The Gemini key is read from ``VRAG_GEMINI_API_KEY`` on the server only; without it,
``/answer`` returns 503 while search and compare keep working. The built frontend
(``frontend/dist``) is served at ``/`` when present.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from version_aware_rag.retrieval.corpus import VERSIONS, RetrievalCorpus

Method = Literal["bm25", "dense", "finetuned", "hybrid", "rerank", "late"]


class AnswerRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    version: str | None = None
    method: Method = "hybrid"

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        return _clean_query(v, 3)


def _clean_query(q: str, min_len: int) -> str:
    """Strip whitespace; reject queries that are blank or too short once stripped (422)."""
    stripped = " ".join(q.split())
    if len(stripped) < min_len:
        raise ValueError(f"query must contain at least {min_len} non-blank characters")
    return stripped


def _query(q: str) -> str:
    try:
        return _clean_query(q, 2)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@dataclass
class Engine:
    """Lazily loaded corpus, retrievers and answer pipeline (one per process)."""

    root: Path
    corpus: RetrievalCorpus | None = None
    retrievers: dict[str, Any] = field(default_factory=dict)
    pipelines: dict[str, Any] = field(default_factory=dict)
    audit: Any = None

    def load(self) -> RetrievalCorpus:
        if self.corpus is None:
            from version_aware_rag.retrieval.commands import corpus_from, load_retrievers
            from version_aware_rag.retrieval.methods import INDEX_DIR

            self.corpus = corpus_from(self.root)
            dense_built = (self.root / INDEX_DIR / "dense" / "meta.json").is_file()
            names = ["bm25", "dense", "hybrid", "rerank", "late"] if dense_built else ["bm25"]
            if (self.root / INDEX_DIR / "finetuned" / "meta.json").is_file():
                names.append("finetuned")  # only once the Colab model is back and indexed
            self.retrievers = dict(
                zip(names, load_retrievers(self.root, self.corpus, names), strict=True)
            )
        return self.corpus

    def retriever(self, method: str) -> Any:
        self.load()
        if method not in self.retrievers:
            raise HTTPException(503, f"retrieval method {method!r} is not built on this server")
        return self.retrievers[method]

    def pipeline(self, method: str) -> Any:
        if method not in self.pipelines:
            from version_aware_rag.answer.commands import (
                ANSWER_CACHE,
                LedgerGenerator,
                restricted_audit,
            )
            from version_aware_rag.answer.pipeline import AnswerPipeline
            from version_aware_rag.settings import get_settings

            key = get_settings().gemini_api_key
            if key is None or not key.get_secret_value():
                raise HTTPException(
                    503,
                    "Answer generation is not configured (set VRAG_GEMINI_API_KEY on the server).",
                )
            if self.audit is None:
                try:
                    self.audit = restricted_audit(self.root)
                except Exception as exc:  # fail closed: no audit, no generation
                    raise HTTPException(
                        503, f"privacy audit unavailable ({type(exc).__name__}); nothing was sent"
                    ) from None
            self.pipelines[method] = AnswerPipeline(
                self.retriever(method),
                self.load(),
                LedgerGenerator(self.root, key.get_secret_value()),
                self.audit,
                self.root / ANSWER_CACHE,
            )
        return self.pipelines[method]

    def run_answer(self, method: str, question: str, version: str | None) -> Any:
        """Answer one question. A generator that needs binding (``LedgerGenerator``) is bound under
        the quota run lock for this request only, exactly as ``vrag eval answers`` does: acquire
        the lock, ``bind(lock.refresh)``, answer, release. The lock is non-blocking, so a request
        that finds it held (another Gemini run, or a concurrent request) gets 503 and sends
        nothing, instead of waiting."""
        pipe = self.pipeline(method)
        bind = getattr(pipe.generator, "bind", None)
        if bind is None:
            return pipe.answer(question, version)
        from version_aware_rag.benchmark.drafting import QUOTA_LOCK_PATH
        from version_aware_rag.benchmark.quota import ProcessLock, QuotaError

        try:
            with ProcessLock(self.root / QUOTA_LOCK_PATH) as lock:
                bind(lock.refresh)
                return pipe.answer(question, version)
        except QuotaError:
            raise HTTPException(
                503, "Gemini is busy or over quota (another run holds the lock); try again later."
            ) from None


def _hit(corpus: RetrievalCorpus, cid: str, score: float, version: str | None) -> dict[str, Any]:
    c = corpus.chunks[corpus.by_id[cid]]
    return {
        "chunk_id": cid,
        "version": c.version,
        "source_path": c.source_path,
        "section": " > ".join(c.section_path),
        "snippet": c.content[:600],
        "score": round(score, 4),
        "compatible": None if version is None else corpus.compatible(corpus.by_id[cid], version),
    }


def create_app(root: Path = Path(), engine: Engine | None = None) -> FastAPI:
    app = FastAPI(
        title="Version-Aware RAG",
        version="0.1.0",
        description="Version-aware retrieval and answers over the DuckDB documentation.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    eng = engine or Engine(root)

    def check_version(version: str | None) -> None:
        if version is not None and version not in VERSIONS:
            raise HTTPException(
                422, f"unknown version {version!r}; expected one of {', '.join(VERSIONS)}"
            )

    @app.get("/health")
    def health() -> dict[str, Any]:
        corpus = eng.load()
        return {"status": "ok", "chunks": len(corpus.chunks), "methods": sorted(eng.retrievers)}

    @app.get("/versions")
    def versions() -> dict[str, Any]:
        corpus = eng.load()
        return {"versions": [{"version": v, "package": corpus.packages.get(v)} for v in VERSIONS]}

    @app.get("/search")
    def search(
        q: str = Query(min_length=2, max_length=500),
        version: str | None = None,
        method: Method = "hybrid",
        k: int = Query(10, ge=1, le=50),
        filter: bool = True,
    ) -> dict[str, Any]:
        """With ``filter=false`` and a version, every version is searched and each hit is marked
        compatible or not with that version: the version confusion the filter prevents."""
        q = _query(q)
        check_version(version)
        scope = version if filter else None
        corpus = eng.load()
        t0 = time.perf_counter()
        retriever = eng.retriever(method)
        hits = retriever.retrieve(q, scope, k)
        latency = round((time.perf_counter() - t0) * 1000, 1)
        out = [_hit(corpus, c, s, version) for c, s in hits]
        base = getattr(retriever, "base", None)
        if base is not None:
            # Reranking inspection (P2-R46): each hit's rank in the first-stage candidate list
            # that the reranker rescored (hybrid RRF), so the UI can show how ranks moved.
            depth = int(getattr(retriever, "depth", 50))
            first = {c: n for n, (c, _) in enumerate(base.retrieve(q, scope, depth), 1)}
            for h in out:
                h["base_rank"] = first.get(h["chunk_id"])
        return {
            "query": q,
            "version": version,
            "filtered": scope is not None,
            "method": method,
            "first_stage": getattr(base, "name", None),
            "latency_ms": latency,
            "hits": out,
        }

    @app.post("/answer")
    def answer(req: AnswerRequest) -> dict[str, Any]:
        check_version(req.version)
        corpus = eng.load()
        t0 = time.perf_counter()
        ans = eng.run_answer(req.method, req.question, req.version)
        out: dict[str, Any] = ans.as_dict()
        out["citations"] = [_hit(corpus, c, 0.0, req.version) for c in ans.citations]
        out["retrieved"] = [_hit(corpus, c, 0.0, req.version) for c in ans.retrieved]
        out["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    @app.get("/compare")
    def compare(
        q: str = Query(min_length=2, max_length=500),
        versions: str = "1.4,1.5,2.0",
        method: Method = "hybrid",
        k: int = Query(5, ge=1, le=20),
    ) -> dict[str, Any]:
        q = _query(q)
        vs = list(dict.fromkeys(v.strip() for v in versions.split(",") if v.strip()))
        if not vs:
            raise HTTPException(422, "versions must name at least one version")
        for v in vs:
            check_version(v)
        corpus = eng.load()
        r = eng.retriever(method)
        return {
            "query": q,
            "method": method,
            "results": {v: [_hit(corpus, c, s, v) for c, s in r.retrieve(q, v, k)] for v in vs},
        }

    dist = root / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
