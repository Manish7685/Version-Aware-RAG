"""Encode the shared inputs with the project's ColBERTEncoder (run in the project venv)."""

import json
import sys
from pathlib import Path

import numpy as np

from version_aware_rag.retrieval.methods import ColBERTEncoder

inputs = json.loads(Path(sys.argv[1]).read_text("utf-8"))
enc = ColBERTEncoder()
arrays = {f"q{i}": enc.encode_query(q) for i, q in enumerate(inputs["queries"])}
for i, d in enumerate(enc.encode_documents(inputs["docs"])):
    arrays[f"d{i}"] = d
np.savez(sys.argv[2], **arrays)
print("ours:", {k: v.shape for k, v in list(arrays.items())[:3]}, "...")
