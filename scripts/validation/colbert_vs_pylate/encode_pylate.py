"""Encode the shared inputs with PyLate 1.6.0, the reference implementation (reference venv)."""

import json
import sys
from pathlib import Path

import numpy as np
from pylate import models

inputs = json.loads(Path(sys.argv[1]).read_text("utf-8"))
model = models.ColBERT(model_name_or_path="lightonai/answerai-colbert-small-v1", device="cpu")
qs = model.encode(inputs["queries"], is_query=True, show_progress_bar=False)
ds = model.encode(inputs["docs"], is_query=False, show_progress_bar=False)
arrays = {f"q{i}": np.asarray(q, dtype=np.float32) for i, q in enumerate(qs)}
arrays.update({f"d{i}": np.asarray(d, dtype=np.float32) for i, d in enumerate(ds)})
np.savez(sys.argv[2], **arrays)
print("pylate:", {k: v.shape for k, v in list(arrays.items())[:3]}, "...")
