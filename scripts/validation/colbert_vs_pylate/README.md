# ColBERT encoder vs PyLate (reference check)

Checks that `retrieval.methods.ColBERTEncoder` reproduces PyLate's encoding of
`lightonai/answerai-colbert-small-v1`. PyLate is not a project dependency (it pins an older
torch/transformers stack), so it runs in a throwaway virtualenv.

```bash
python -m uv venv --python 3.12 /tmp/pylate-ref
python -m uv pip install --python /tmp/pylate-ref/bin/python "pylate==1.6.0"   # Scripts\python.exe on Windows
python -m uv run python scripts/validation/colbert_vs_pylate/inputs.py . /tmp/inputs.json
python -m uv run python scripts/validation/colbert_vs_pylate/encode_ours.py /tmp/inputs.json /tmp/ours.npz
/tmp/pylate-ref/bin/python -I scripts/validation/colbert_vs_pylate/encode_pylate.py /tmp/inputs.json /tmp/ref.npz
python -m uv run python scripts/validation/colbert_vs_pylate/compare.py /tmp/ours.npz /tmp/ref.npz
```

Inputs: 4 synthetic queries, 2 synthetic passages and 6 permitted MIT documentation chunks
(DuckDB 1.5), one longer than the 300-token document limit.

Result on 2026-11-23 (project: torch 2.15.0+cpu, transformers 5.24.0; reference: PyLate 1.6.0 on
torch 2.11.0):

- identical shapes for all 12 inputs;
- max |difference| 1.64e-07 and min per-token cosine 1.000000;
- max MaxSim score difference 3.81e-06;
- identical document ranking for every query.

This is evidence on this sample, not a proof for all inputs.
