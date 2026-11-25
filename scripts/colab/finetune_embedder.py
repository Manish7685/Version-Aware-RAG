"""Fine-tune the dense embedder on dev-split pairs (RAG-M6). Runs on Colab (free T4), not locally.

The step-by-step notebook is ``scripts/colab/finetune_embedder.ipynb``. Prerequisites (local, after
the owner review and ``vrag benchmark pilot freeze``):
    python -m uv run vrag index finetune-data
    # writes data/build/finetune/train_pairs.jsonl and train_pairs.manifest.json
Upload both files to Drive (``MyDrive/vrag/``), then in Colab:
    !pip install "sentence-transformers==5.7.0" "datasets>=3,<5"
    from google.colab import drive; drive.mount("/content/drive")
    !python finetune_embedder.py --pairs /content/drive/MyDrive/vrag/train_pairs.jsonl \
        --out /content/drive/MyDrive/vrag/embedder-ft
Checkpoints are written to ``--out`` every ``--save-steps`` and training resumes from the latest
one, so a disconnected session loses at most one interval. Copy ``--out``/final back to
``data/build/models/embedder-ft/`` and run ``vrag index check-model``, then
``vrag index build --methods finetuned`` and the usual evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

BASE_MODEL = "BAAI/bge-small-en-v1.5"
SEED = 20261123


def load_rows(pairs: Path) -> tuple[list[dict[str, object]], str]:
    """Rows and the sha256 of the file; refuses a file that does not match its manifest."""
    raw = pairs.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    manifest_path = pairs.with_name("train_pairs.manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if manifest.get("pairs_sha256") != digest:
            raise SystemExit(
                "train_pairs.jsonl does not match train_pairs.manifest.json; re-upload"
            )
    else:
        raise SystemExit("train_pairs.manifest.json not found next to the pairs file")
    rows = [json.loads(ln) for ln in raw.decode("utf-8").splitlines() if ln.strip()]
    return rows, digest


def training_rows(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    """(anchor, positive, hard negative) triples. A row without a mined negative is dropped: using
    the positive as its own negative would give the loss a contradictory target."""
    out = []
    for r in rows:
        negatives = r.get("negatives") or []
        if negatives:
            out.append(
                {
                    "anchor": str(r["anchor"]),
                    "positive": str(r["positive"]),
                    "negative": str(negatives[0]),
                }  # type: ignore[index]
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--save-steps", type=int, default=200)
    args = ap.parse_args()

    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
    )
    from sentence_transformers.losses import MultipleNegativesRankingLoss
    from sentence_transformers.training_args import BatchSamplers

    rows, digest = load_rows(args.pairs)
    triples = training_rows(rows)
    if not triples:
        raise SystemExit("no training rows with a hard negative")
    data = Dataset.from_list(triples)  # in-batch negatives supply the rest (MNRL)
    model = SentenceTransformer(BASE_MODEL)
    targs = SentenceTransformerTrainingArguments(
        output_dir=str(args.out / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=0.1,
        fp16=True,
        batch_sampler=BatchSamplers.NO_DUPLICATES,  # no false in-batch negatives
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=2,
        logging_steps=20,
        seed=SEED,
        report_to="none",
    )
    trainer = SentenceTransformerTrainer(
        model=model, args=targs, train_dataset=data, loss=MultipleNegativesRankingLoss(model)
    )
    ckpts = sorted(
        (args.out / "checkpoints").glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[1])
    )  # by step number, not string order
    trainer.train(resume_from_checkpoint=str(ckpts[-1]) if ckpts else None)
    model.save(str(args.out / "final"))
    (args.out / "final" / "training.json").write_text(
        json.dumps(
            {
                "base": BASE_MODEL,
                "pairs_sha256": digest,
                "pairs": len(rows),
                "training_rows": len(triples),
                "dropped_without_negative": len(rows) - len(triples),
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "lr": args.lr,
                "seed": SEED,
            },
            indent=1,
        ),
        "utf-8",
    )
    print(f"saved {args.out / 'final'} ({len(triples)} training rows of {len(rows)} pairs)")


if __name__ == "__main__":
    main()
