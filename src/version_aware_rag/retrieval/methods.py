"""Retrieval methods behind one interface (design §4): BM25, dense, hybrid RRF and cross-encoder
reranking. Each runs unfiltered (every version) or version-filtered (compatible chunks only)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from version_aware_rag.retrieval.corpus import RetrievalCorpus

INDEX_DIR = Path("data/build/index")
DENSE_MODEL = (
    "BAAI/bge-small-en-v1.5"  # MIT licence; small enough for CPU (design §4: chosen on dev)
)
DENSE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # Apache-2.0
# The embedder fine-tuned on Colab (scripts/colab/finetune_embedder.py) from dev-split pairs only;
# copied back here by the owner. Git-ignored like every other build output.
FINETUNED_MODEL_DIR = Path("data/build/models/embedder-ft")
FINETUNED_ID = "finetuned:bge-small-en-v1.5"
RRF_K = 60  # Cormack et al. 2009


class Retriever(Protocol):
    name: str

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]: ...


def _top(
    scores: np.ndarray, corpus: RetrievalCorpus, version: str | None, k: int
) -> list[tuple[str, float]]:
    s = scores.astype(np.float64).copy()
    if version is not None:
        s[~np.asarray(corpus.mask(version))] = -np.inf
    k = min(k, int(np.isfinite(s).sum()))
    if k <= 0:
        return []
    idx = np.argpartition(-s, k - 1)[:k]
    idx = idx[np.argsort(-s[idx], kind="stable")]
    return [(corpus.chunks[i].chunk_id, float(s[i])) for i in idx]


_TOKEN = re.compile(r"[a-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class BM25Retriever:
    corpus: RetrievalCorpus
    name: str = "bm25"
    _model: Any = field(default=None, repr=False)

    def build(self) -> None:
        import bm25s

        self._model = bm25s.BM25()
        self._model.index([tokenize(c.content) for c in self.corpus.chunks], show_progress=False)

    def save(self, path: Path) -> None:
        self._model.save(str(path))

    def load(self, path: Path) -> None:
        import bm25s

        self._model = bm25s.BM25.load(str(path))

    def scores(self, query: str) -> np.ndarray:
        toks = [t for t in tokenize(query) if t in self._model.vocab_dict]
        return (
            np.asarray(self._model.get_scores(toks), dtype=np.float64)
            if toks
            else np.zeros(len(self.corpus.chunks))
        )

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        return _top(self.scores(query), self.corpus, version, k)


@dataclass
class DenseRetriever:
    corpus: RetrievalCorpus
    model_name: str = DENSE_MODEL
    name: str = "dense"
    model_id: str | None = None  # recorded in meta.json instead of a machine-specific path
    _model: Any = field(default=None, repr=False)
    _emb: np.ndarray | None = field(default=None, repr=False)
    _index: Any = field(default=None, repr=False)

    def _encoder(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def build(self, batch_size: int = 64) -> None:
        texts = [c.content for c in self.corpus.chunks]
        emb = self._encoder().encode(
            texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        self._set(np.asarray(emb, dtype=np.float32))

    def _set(self, emb: np.ndarray) -> None:
        import faiss

        self._emb = emb
        self._index = faiss.IndexFlatIP(emb.shape[1])
        self._index.add(emb)

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if self._emb is None:
            raise RuntimeError("dense index not built or loaded")
        np.save(path / "embeddings.npy", self._emb)
        (path / "meta.json").write_text(
            json.dumps({"model": self.model_id or self.model_name, "n": len(self._emb)}), "utf-8"
        )

    def load(self, path: Path) -> None:
        meta = json.loads((path / "meta.json").read_text("utf-8"))
        if meta["model"] != (self.model_id or self.model_name) or meta["n"] != len(
            self.corpus.chunks
        ):
            raise ValueError("dense index does not match the corpus or model; rebuild it")
        self._set(np.load(path / "embeddings.npy"))

    def scores(self, query: str) -> np.ndarray:
        if self._emb is None:
            raise RuntimeError("dense index not built or loaded")
        q = self._encoder().encode(
            [DENSE_QUERY_PREFIX + query], normalize_embeddings=True, show_progress_bar=False
        )
        return np.asarray(self._emb @ np.asarray(q[0], dtype=np.float32), dtype=np.float64)

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        if version is None and self._index is not None:
            q = self._encoder().encode(
                [DENSE_QUERY_PREFIX + query], normalize_embeddings=True, show_progress_bar=False
            )
            d, i = self._index.search(np.asarray(q, dtype=np.float32), k)
            return [
                (self.corpus.chunks[j].chunk_id, float(s))
                for s, j in zip(d[0], i[0], strict=True)
                if j >= 0
            ]
        return _top(self.scores(query), self.corpus, version, k)


def finetuned_retriever(root: Path, corpus: RetrievalCorpus) -> DenseRetriever:
    """Dense retrieval with the fine-tuned embedder (P2-R11); same index type and query prefix."""
    model_dir = root / FINETUNED_MODEL_DIR
    if not (model_dir / "config.json").is_file():
        raise FileNotFoundError(
            f"fine-tuned embedder not found at {FINETUNED_MODEL_DIR.as_posix()}"
        )
    return DenseRetriever(corpus, str(model_dir), name="finetuned", model_id=FINETUNED_ID)


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(d) = sum over rankings of 1 / (k + rank(d))."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, 1):
            scores[doc] = scores.get(doc, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


@dataclass
class HybridRetriever:
    bm25: BM25Retriever
    dense: DenseRetriever
    rrf_k: int = RRF_K
    depth: int = 100
    name: str = "hybrid"

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        a = [c for c, _ in self.bm25.retrieve(query, version, self.depth)]
        b = [c for c, _ in self.dense.retrieve(query, version, self.depth)]
        return rrf([a, b], self.rrf_k)[:k]


@dataclass
class RerankRetriever:
    base: HybridRetriever
    corpus: RetrievalCorpus
    model_name: str = RERANK_MODEL
    depth: int = 50
    name: str = "rerank"
    _model: Any = field(default=None, repr=False)

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name, device="cpu")
        cands = [c for c, _ in self.base.retrieve(query, version, self.depth)]
        if not cands:
            return []
        texts = [self.corpus.chunks[self.corpus.by_id[c]].content for c in cands]
        scores = self._model.predict([(query, t) for t in texts], show_progress_bar=False)
        order = sorted(
            zip(cands, (float(s) for s in scores), strict=True), key=lambda x: (-x[1], x[0])
        )
        return order[:k]


# ColBERT-style checkpoint; its model card declares no licence (see THIRD_PARTY_NOTICES.md)
LATE_MODEL = "lightonai/answerai-colbert-small-v1"


def maxsim(query_tokens: np.ndarray, doc_tokens: np.ndarray) -> float:
    """ColBERT late interaction: sum over query tokens of the best-matching document token."""
    return float((query_tokens @ doc_tokens.T).max(axis=1).sum())


def _token_id(tokenizer: Any, token: str) -> int:
    ids: list[int] = tokenizer.convert_tokens_to_ids([token])
    return int(ids[0])


class ColBERTEncoder:
    """Token-level ColBERT encoder on transformers, following the checkpoint's own configuration
    (``config_sentence_transformers.json``): a prefix token after [CLS] (``query_prefix`` /
    ``document_prefix``), queries padded with [MASK] to ``query_length`` and the expansion tokens
    kept for scoring but not attended to, documents truncated to ``document_length`` with
    ``skiplist_words`` and padding dropped, then the bias-free projection and L2 normalisation.
    (PyLate was not used: it pins an older torch/transformers stack; see the tracker.)"""

    def __init__(self, model_name: str = LATE_MODEL) -> None:
        import torch
        from huggingface_hub import snapshot_download
        from safetensors.torch import load_file
        from transformers import AutoModel, AutoTokenizer

        path = Path(
            snapshot_download(model_name, allow_patterns=["*.json", "*.txt", "*.safetensors"])
        )
        cfg = json.loads((path / "config_sentence_transformers.json").read_text("utf-8"))
        self._torch = torch
        self.tok: Any = AutoTokenizer.from_pretrained(path)
        self.model = AutoModel.from_pretrained(path).eval()
        self.proj = load_file(str(path / "1_Dense" / "model.safetensors"))["linear.weight"]
        self.q_prefix = _token_id(self.tok, cfg["query_prefix"])
        self.d_prefix = _token_id(self.tok, cfg["document_prefix"])
        self.q_len, self.d_len = int(cfg["query_length"]), int(cfg["document_length"])
        self.attend_expansion = bool(cfg["attend_to_expansion_tokens"])
        self.skip_ids = {
            i for w in cfg["skiplist_words"] for i in self.tok.encode(w, add_special_tokens=False)
        }

    def _ids(self, text: str, prefix: int, max_len: int) -> list[int]:
        body = self.tok.encode(
            text, add_special_tokens=False, truncation=True, max_length=max_len - 3
        )
        return [self.tok.cls_token_id, prefix, *body, self.tok.sep_token_id]

    def _forward(self, ids: list[list[int]], attn: list[list[int]]) -> Any:
        torch = self._torch
        with torch.no_grad():
            out = self.model(
                input_ids=torch.tensor(ids), attention_mask=torch.tensor(attn)
            ).last_hidden_state
            emb = out @ self.proj.T
            return torch.nn.functional.normalize(emb, dim=-1)

    def encode_query(self, text: str) -> np.ndarray:
        ids = self._ids(text, self.q_prefix, self.q_len)
        n = len(ids)
        ids = ids + [self.tok.mask_token_id] * (self.q_len - n)
        attn = [1] * len(ids) if self.attend_expansion else [1] * n + [0] * (self.q_len - n)
        return np.asarray(self._forward([ids], [attn])[0].numpy(), dtype=np.float32)

    def encode_documents(self, texts: Sequence[str], batch_size: int = 16) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for b in range(0, len(texts), batch_size):
            seqs = [self._ids(t, self.d_prefix, self.d_len) for t in texts[b : b + batch_size]]
            width = max(len(x) for x in seqs)
            ids = [x + [self.tok.pad_token_id] * (width - len(x)) for x in seqs]
            attn = [[1] * len(x) + [0] * (width - len(x)) for x in seqs]
            emb = self._forward(ids, attn).numpy()
            for row, seq in zip(emb, seqs, strict=True):
                keep = [j for j, t in enumerate(seq) if t not in self.skip_ids]
                out.append(np.asarray(row[keep], dtype=np.float32))
        return out


@dataclass
class LateInteractionRetriever:
    """Late interaction (ColBERT MaxSim) reranking the hybrid top ``depth``.

    A full token-level index of every chunk does not fit a CPU budget (about 1.8 GB of token
    vectors), so locally late interaction rescores hybrid candidates; full-corpus late-interaction
    retrieval is the Colab variant. Document token embeddings are cached per process.
    """

    base: HybridRetriever
    corpus: RetrievalCorpus
    model_name: str = LATE_MODEL
    depth: int = 100
    name: str = "late"
    _enc: ColBERTEncoder | None = field(default=None, repr=False)
    _doc_cache: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    def retrieve(self, query: str, version: str | None, k: int) -> list[tuple[str, float]]:
        cands = [c for c, _ in self.base.retrieve(query, version, self.depth)]
        if not cands:
            return []
        if self._enc is None:
            self._enc = ColBERTEncoder(self.model_name)
        missing = [c for c in cands if c not in self._doc_cache]
        if missing:
            texts = [self.corpus.chunks[self.corpus.by_id[c]].content for c in missing]
            self._doc_cache.update(zip(missing, self._enc.encode_documents(texts), strict=True))
        q = self._enc.encode_query(query)
        scored = [(c, maxsim(q, self._doc_cache[c])) for c in cands]
        return sorted(scored, key=lambda x: (-x[1], x[0]))[:k]
