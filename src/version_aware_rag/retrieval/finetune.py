"""Embedder fine-tuning data (RAG-M6; design §4: "fine-tuned on dev page groups only").

Training pairs come only from the frozen **dev** split: (question, grade-2 chunk) positives plus
BM25-mined hard negatives that are not relevant to the question. Any test or ci-gate item, or any
chunk whose page appears in a test or ci-gate item, is refused, so the test set never leaks into
training. Training itself (sentence-transformers, MultipleNegativesRankingLoss) needs a GPU and
runs on Colab (``scripts/colab/finetune_embedder.py``); this module builds and checks the data.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from version_aware_rag.retrieval import harness
from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.retrieval.methods import Retriever

SPLIT_DIR = Path("benchmark/splits")


class LeakageError(ValueError):
    """Training data would touch held-out (test or ci-gate) items or pages."""


@dataclass(frozen=True)
class Pair:
    question: str
    positive: str  # chunk id
    negatives: tuple[str, ...]  # chunk ids


def _page(source_path: str) -> str:
    return "/".join(source_path.split("/")[2:])  # version prefix stripped


def held_out_pages(held_out: Sequence[dict[str, Any]]) -> set[str]:
    return {_page(sp["source_path"]) for x in held_out for sp in x.get("evidence_spans") or []}


def build_pairs(
    dev: Sequence[dict[str, Any]],
    held_out: Sequence[dict[str, Any]],
    corpus: RetrievalCorpus,
    miner: Retriever,
    n_negatives: int = 3,
) -> list[Pair]:
    """One pair per (dev item, grade-2 chunk). Raises ``LeakageError`` if a dev item is also held
    out or a positive lies on a held-out page; negatives on held-out pages are skipped."""
    held_ids = {str(x["item_id"]) for x in held_out}
    if overlap := sorted(held_ids & {str(x["item_id"]) for x in dev}):
        raise LeakageError(f"{len(overlap)} items are in both dev and a held-out split")
    banned = held_out_pages(held_out)
    pairs: list[Pair] = []
    for x in dev:
        q = harness.qrels_for(x, corpus)
        positives = sorted(c for c, g in q.items() if g >= 2)
        if not positives:
            continue
        for c in positives:
            if _page(corpus.chunks[corpus.by_id[c]].source_path) in banned:
                raise LeakageError(f"a positive for {x['item_id']} lies on a held-out page")
        negs: list[str] = []
        for c, _ in miner.retrieve(x["question"], x["target_version"], 50):
            if q.get(c, 0) == 0 and _page(corpus.chunks[corpus.by_id[c]].source_path) not in banned:
                negs.append(c)
            if len(negs) == n_negatives:
                break
        pairs.extend(Pair(x["question"], c, tuple(negs)) for c in positives)
    return pairs


def load_split(root: Path, name: str) -> list[dict[str, Any]]:
    path = root / SPLIT_DIR / f"{name}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"{path.as_posix()} (the split is frozen after the owner review)")
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]


def pairs_manifest(
    pairs: Sequence[Pair],
    dev: Sequence[dict[str, Any]],
    held_out: Sequence[dict[str, Any]],
    pairs_bytes: bytes,
) -> dict[str, Any]:
    """Provenance for the Colab run: what the training file is and what it was checked against.
    The Colab script refuses a pairs file whose sha256 differs, and the trained model records it."""
    import hashlib

    def sha(items: Sequence[dict[str, Any]]) -> str:
        ids = sorted(str(x["item_id"]) for x in items)
        return hashlib.sha256("\n".join(ids).encode()).hexdigest()

    return {
        "pairs_sha256": hashlib.sha256(pairs_bytes).hexdigest(),
        "pairs": len(pairs),
        "pairs_without_negatives": sum(1 for p in pairs if not p.negatives),
        "dev_items": len(dev),
        "dev_items_sha256": sha(dev),
        "held_out_items": len(held_out),
        "held_out_items_sha256": sha(held_out),
        "held_out_pages": len(held_out_pages(held_out)),
        "leakage_checks": "passed (no shared items; no positive on a held-out page; negatives on "
        "held-out pages skipped)",
        "base_model": "BAAI/bge-small-en-v1.5",
    }


def write_pairs(pairs: Sequence[Pair], corpus: RetrievalCorpus, path: Path) -> None:
    """Training file for Colab: question, positive text, negative texts (permitted chunks only)."""
    text = {c.chunk_id: c.content for c in corpus.chunks}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(
                {
                    "anchor": p.question,
                    "positive": text[p.positive],
                    "negatives": [text[n] for n in p.negatives],
                },
                ensure_ascii=False,
            )
            + "\n"
            for p in pairs
        ),
        "utf-8",
    )
