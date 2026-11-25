"""Compare the two encodings: shapes, element differences, MaxSim scores and rankings."""

import sys

import numpy as np

ours, ref = np.load(sys.argv[1]), np.load(sys.argv[2])
qk = sorted((k for k in ours.files if k.startswith("q")), key=lambda k: int(k[1:]))
dk = sorted((k for k in ours.files if k.startswith("d")), key=lambda k: int(k[1:]))
shape_ok = all(ours[k].shape == ref[k].shape for k in ours.files)
print("shapes identical for every input:", shape_ok)
for k in ours.files:
    if ours[k].shape != ref[k].shape:
        print("  shape differs:", k, ours[k].shape, ref[k].shape)
diffs = [float(np.abs(ours[k] - ref[k]).max()) for k in ours.files if ours[k].shape == ref[k].shape]
cos = [float((ours[k] * ref[k]).sum(1).min()) for k in ours.files if ours[k].shape == ref[k].shape]
print(
    f"max |difference| over all token vectors: {max(diffs):.2e}; "
    f"min per-token cosine: {min(cos):.6f}"
)


def maxsim(q: np.ndarray, d: np.ndarray) -> float:
    return float((q @ d.T).max(axis=1).sum())


so = np.array([[maxsim(ours[q], ours[d]) for d in dk] for q in qk])
sr = np.array([[maxsim(ref[q], ref[d]) for d in dk] for q in qk])
print(f"max |MaxSim score difference|: {np.abs(so - sr).max():.2e} (scores ~{sr.mean():.1f})")
same_rank = all(
    (np.argsort(-so[i], kind="stable") == np.argsort(-sr[i], kind="stable")).all()
    for i in range(len(qk))
)
print("document ranking identical for every query:", same_rank)
