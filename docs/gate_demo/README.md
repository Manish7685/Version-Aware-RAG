# CI gate demonstration patches (RAG-M9, local demonstration only)

**Status (owner decision, small-pilot scope):** the gate is demonstrated locally only. There is no
separate ci-gate split, no approved baseline (`configs/gate.toml` has an empty `baseline_sha256`)
and the `eval-gate` GitHub Actions job is dormant. No demonstration branches or pull requests were
created, and none are planned.

Two patches against the code, used for the local demonstration on the pre-review pilot:

| Patch | Change | Expected gate outcome | Verified locally (pre-review pilot, 2026-11-23) |
|---|---|---|---|
| `regressing_remove_version_filter.patch` | `_top` in `retrieval/methods.py` stops masking incompatible chunks: the version filter is gone (the design §6.4 acceptance demo) | **BLOCK, exit 1**: the check fails | BLOCK, exit 1 (nDCG@10 R̄ +0.256, Recall@10 R̄ +0.136, VCR@10 R̄ +0.737) |
| `neutral_docstring.patch` | adds a docstring to `tokenize`; behaviour identical | **PASS, exit 0** | PASS, exit 0 (R̄ = 0 on every metric) |

Each patch was applied to the working tree, checked with `vrag eval gate check --provisional
--method hybrid` against a hybrid baseline on the pre-review pilot (38 retrieval-eligible V0
drafts), and reversed; `methods.py` was byte-identical afterwards. These commands are a historical
record: since the freeze, `--provisional` evaluates the frozen test split's items, so they are not
re-run (see `REPRODUCE.md` §6).

## Historical: the planned GitHub demonstration (not part of the small-pilot scope)

The original plan was to open two pull requests, one regressing and one neutral, and record that the
`eval-gate` check fails and passes. That workflow needs a separate ci-gate split and an
owner-approved baseline, which the small-pilot decision does not include, so it was not carried out
and must not be run as written: with no ci-gate split the job would only report ERROR. The steps are
kept here as a record of the original design only:

```text
# HISTORICAL PLAN, NOT CARRIED OUT. Requires a ci-gate split and an approved baseline (not built).
git switch -c demo/gate-regression main
git apply docs/gate_demo/regressing_remove_version_filter.patch
git commit -am "demo: remove the version filter (must fail the eval gate)"
git push -u origin demo/gate-regression      # a PR whose eval-gate check would fail

git switch -c demo/gate-neutral main
git apply docs/gate_demo/neutral_docstring.patch
git commit -am "demo: docstring only (must pass the eval gate)"
git push -u origin demo/gate-neutral         # a PR whose eval-gate check would pass
```
