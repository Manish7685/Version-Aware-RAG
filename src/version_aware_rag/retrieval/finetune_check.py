"""Validate the fine-tuned embedder copied back from Colab before it is indexed (RAG-M6).

Checks, in order: the model folder exists at ``data/build/models/embedder-ft/``; its
``training.json`` names the expected base model and the sha256 of the training pairs; that sha256
matches the local ``train_pairs.manifest.json`` (so the model was trained on exactly the
leakage-checked dev pairs); and, unless ``load`` is false, the model loads on CPU and produces
384-dimensional normalised embeddings with the same query prefix as the base model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from version_aware_rag.retrieval.methods import DENSE_MODEL, DENSE_QUERY_PREFIX, FINETUNED_MODEL_DIR

PAIRS_MANIFEST = Path("data/build/finetune/train_pairs.manifest.json")
EMBEDDING_DIM = 384


def check_finetuned_model(root: Path, load: bool = True) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    facts: dict[str, Any] = {}
    model_dir = root / FINETUNED_MODEL_DIR
    if not (model_dir / "config.json").is_file():
        return [
            f"no model at {FINETUNED_MODEL_DIR.as_posix()} (copy the Colab 'final' folder)"
        ], facts
    try:
        training = json.loads((model_dir / "training.json").read_text("utf-8"))
    except (OSError, ValueError):
        return [
            "training.json missing or unreadable (was the model saved by the Colab script?)"
        ], facts
    facts["base"] = training.get("base")
    facts["pairs"] = training.get("pairs")
    facts["epochs"] = training.get("epochs")
    facts["pairs_sha256"] = str(training.get("pairs_sha256", ""))[:12]
    if training.get("base") != DENSE_MODEL:
        problems.append(f"base model {training.get('base')!r} is not {DENSE_MODEL}")
    try:
        manifest = json.loads((root / PAIRS_MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        problems.append(f"{PAIRS_MANIFEST.as_posix()} missing: run vrag index finetune-data first")
        manifest = {}
    if manifest and training.get("pairs_sha256") != manifest.get("pairs_sha256"):
        problems.append("the model was trained on a different pairs file than the local manifest")
    if load and not problems:
        import numpy as np
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(str(model_dir), device="cpu")
        emb = np.asarray(
            model.encode(
                [DENSE_QUERY_PREFIX + "date_trunc on a DATE", "date_trunc truncates a date"],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        )
        facts["embedding_dim"] = int(emb.shape[1])
        facts["query_doc_cosine"] = round(float(emb[0] @ emb[1]), 4)
        if emb.shape[1] != EMBEDDING_DIM:
            problems.append(f"embedding dimension {emb.shape[1]} != {EMBEDDING_DIM}")
        if not np.allclose(np.linalg.norm(emb, axis=1), 1.0, atol=1e-3):
            problems.append("embeddings are not normalised")
    return problems, facts
