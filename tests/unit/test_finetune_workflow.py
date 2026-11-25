"""Fine-tune workflow (RAG-M6): provenance manifest, the Colab script's row handling and the local
model check, without a GPU or a model download."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from version_aware_rag.retrieval import finetune as ft
from version_aware_rag.retrieval.finetune_check import PAIRS_MANIFEST, check_finetuned_model
from version_aware_rag.retrieval.methods import DENSE_MODEL, FINETUNED_MODEL_DIR

ROOT = Path(__file__).resolve().parents[2]


def _colab() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "finetune_embedder", ROOT / "scripts/colab/finetune_embedder.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_records_hashes_counts_and_missing_negatives() -> None:
    pairs = [ft.Pair("q1", "c1", ("n1",)), ft.Pair("q2", "c2", ())]
    dev = [{"item_id": "T1-01"}, {"item_id": "T3-02"}]
    held = [{"item_id": "T1-09", "evidence_spans": [{"source_path": "docs/1.5/sql/a.md"}]}]
    m = ft.pairs_manifest(pairs, dev, held, b"bytes")
    assert m["pairs_sha256"] == hashlib.sha256(b"bytes").hexdigest()
    assert (m["pairs"], m["pairs_without_negatives"], m["dev_items"], m["held_out_pages"]) == (
        2,
        1,
        2,
        1,
    )
    assert m["base_model"] == DENSE_MODEL


def test_colab_script_refuses_a_mismatched_pairs_file_and_drops_rows_without_negatives(
    tmp_path: Path,
) -> None:
    colab = _colab()
    pairs = tmp_path / "train_pairs.jsonl"
    rows = [
        {"anchor": "q1", "positive": "p1", "negatives": ["n1", "n2"]},
        {"anchor": "q2", "positive": "p2", "negatives": []},
    ]
    pairs.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    with pytest.raises(SystemExit, match="manifest"):
        colab.load_rows(pairs)  # no manifest next to it
    (tmp_path / "train_pairs.manifest.json").write_text('{"pairs_sha256": "x"}', "utf-8")
    with pytest.raises(SystemExit, match="does not match"):
        colab.load_rows(pairs)
    digest = hashlib.sha256(pairs.read_bytes()).hexdigest()
    (tmp_path / "train_pairs.manifest.json").write_text(
        json.dumps({"pairs_sha256": digest}), "utf-8"
    )
    loaded, got = colab.load_rows(pairs)
    assert got == digest and len(loaded) == 2
    # never the positive as its own negative
    assert colab.training_rows(loaded) == [{"anchor": "q1", "positive": "p1", "negative": "n1"}]


def test_model_check_requires_matching_provenance(tmp_path: Path) -> None:
    problems, _ = check_finetuned_model(tmp_path, load=False)
    assert problems and "no model" in problems[0]
    model = tmp_path / FINETUNED_MODEL_DIR
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", "utf-8")
    assert "training.json" in check_finetuned_model(tmp_path, load=False)[0][0]
    (model / "training.json").write_text(
        json.dumps({"base": DENSE_MODEL, "pairs_sha256": "abc", "pairs": 3, "epochs": 3}), "utf-8"
    )
    problems, _ = check_finetuned_model(tmp_path, load=False)
    assert any("manifest" in p for p in problems)  # no local manifest yet
    (tmp_path / PAIRS_MANIFEST).parent.mkdir(parents=True)
    (tmp_path / PAIRS_MANIFEST).write_text(json.dumps({"pairs_sha256": "other"}), "utf-8")
    assert any("different pairs file" in p for p in check_finetuned_model(tmp_path, load=False)[0])
    (tmp_path / PAIRS_MANIFEST).write_text(json.dumps({"pairs_sha256": "abc"}), "utf-8")
    problems, facts = check_finetuned_model(tmp_path, load=False)
    assert problems == [] and facts["pairs"] == 3
