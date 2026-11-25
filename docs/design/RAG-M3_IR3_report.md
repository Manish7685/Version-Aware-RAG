# RAG-M3: IR-3 status report (probe suite)

Status: **report only (2026-11-22), prepared before S6a as decided by the owner (S6-D8).** It closes the S5 follow-up "IR-3 is neither marked complete nor dropped: its exact requirement and a proposed owner are reported separately after S5" (S5-D9). Nothing is implemented here and the approved S5 implementation is unchanged. **Owner decision (2026-11-22): Option B (§4).** IR-3 stays **todo, deferred to the first stage of RAG-M4**, where it is implemented together with IR-2 and the `RuntimeObservation` model; it is not implemented in RAG-M3 S6.

## 1. The exact requirement

From `RAG-M1_design.md` §9.4 (approved, immutable):

| ID | Requirement | Milestone | Done when |
|---|---|---|---|
| IR-3 | Probe suite in the repo, with a success-path control; a differing control fails the run; full, untruncated error capture | RAG-M3 | Tests pass; outputs stored as `RuntimeObservation` |

Related, from the same documents:

- `RuntimeObservation` (`RAG-M1_design.md` §2.1) is the record a probe run must produce: `obs_id`, `probe_id`, `probe_sha256`, `probe_kind` (`sql`, `python_api`, `control`), `package`, `package_version`, `wheel_sha256` (must match manifest §2), `applies_to_doc_version`, `python_version`, `platform`, `observed_status` (`ok` or `error`), `observed_value` (the `repr` of the result, or the **full** exception type and message, untruncated), `run_at`, `runner_commit`, `verification_level` (`auto_executed` or `reproduced`).
- `RAG-M1_research_evidence.md` §4.2 (the stable control P09) proposes, for when the suite moves into the repository: a **success-path control** (the one-row variant of P09, verified in all six packages during RAG-M1) and a **whole-result control compared with the version string stripped**, and states that the suite must **fail loudly if any control differs between versions**: a differing control invalidates the run instead of being reported as a version change.
- `DEVELOPMENT.md`: the RAG-M1 probe script and its expected results are hash-pinned and must never be edited; "a new probe suite (IR-3, RAG-M3) goes in a new file".
- IR-2 (`DocClaim`, `RuntimeObservation`, `EvidenceConflict` as Pydantic models with validation) is assigned to RAG-M3/M4 and is **todo**. IR-3's "done when" depends on it.

## 2. What exists today (inspected 2026-11-22)

| Item | State | Evidence |
|---|---|---|
| Probe suite in the repository | **Partly.** `scripts/probes/rag_m1_probes.py` holds the 10 RAG-M1 probes (P01-P08 change probes, P09 control, P10 Python API); it is a byte-identical, hash-pinned copy of the RAG-M1 artefact and cannot be changed | the file; `tests/unit/test_pinned_artifacts.py` |
| Hash-verified per-version runs | **Yes.** `scripts/probes/run_probes.sh` checks the script's sha256, installs each of the six DuckDB packages from hash-pinned requirements into its own environment, runs the probes in isolated mode and compares the result with the expected RAG-M1 run | the script |
| Strict comparison with the expected run | **Yes.** `vrag probes compare` (`repro/probe_check.py`) fails on any difference in any probe, control included, and on missing or unexpected versions; IR-4 (Linux re-run) is done | the module; tracker IR-4 |
| Control on the error path | **Yes, one.** P09 (`SELECT (SELECT * FROM range(2))`), identical error in all six packages | `RAG-M1_research_evidence.md` §4.2 |
| Success-path control | **No.** Proposed in §4.2, not in the suite | the script lists P01-P10 only |
| Version-stripped whole-result control | **No** | as above |
| A differing control fails the run | **Only indirectly.** A differing control fails the comparison with the stored expected file, like any other probe. There is no rule that controls must be identical across versions **within one run**, so a new run without a matching expected file has no control check, and a control difference is not distinguished from a version change | `probe_check.py` compares runs, not controls |
| Full, untruncated error capture | **No.** Errors are recorded as the exception type plus the **first line of the message, cut to 90 characters** (`str(e).splitlines()[0][:90]`); the stored P09 and P10 values are visibly truncated. The full P09 message exists only in the RAG-M1 evidence text (manifest §5.4) | the script; the expected JSONL |
| Outputs stored as `RuntimeObservation` | **No.** Outputs are one JSON line per package (`duckdb`, `python`, `platform`, `results`); there are no observation records, no `probe_sha256` or `wheel_sha256` per observation, no `verification_level`. The `RuntimeObservation` model (IR-2) does not exist in `src/` or `tests/` | search of `src/` and `tests/` |

Missing evidence: none needed for this report. There is no run of a new suite (none exists), so no new probe results are quoted or implied.

## 3. Gap to "done"

1. A **new** probe-suite file (the RAG-M1 file stays untouched), carrying the ten probes forward with their recorded identifiers, plus the success-path control and the version-stripped whole-result control.
2. **Untruncated** capture: full exception type and message, and the `repr` of the result.
3. A **control rule** evaluated per run: every control identical across all six packages, otherwise the run is invalid and exits non-zero, reported as a harness failure rather than a version difference.
4. Output as **`RuntimeObservation`** records with all required fields, which needs at least the `RuntimeObservation` part of IR-2 (Pydantic model with validation, including `wheel_sha256` against the manifest).
5. Tests: model validation; a fixture run where a control differs fails; truncation impossible (a long multi-line error survives intact); the existing RAG-M1 comparison (IR-4) keeps working unchanged.
6. A real run on the six packages (local, and optionally Linux CI as `reproduced`), with the observations stored and the existing RAG-M1 expectations still matching for the original ten probes, apart from the deliberate change from truncated to full messages, which must be recorded explicitly, not silently.

## 4. Placement: owner decision Option B (2026-11-22)

IR-3 is not needed for the RAG-M3 acceptance criterion (roadmap P2-M3: every chunk carries version metadata, and the corpus rebuilds from the manifest), and its "done when" depends on IR-2, whose milestone is RAG-M3/M4. Options:

| Option | Where | For | Against |
|---|---|---|---|
| A | A new RAG-M3 stage **S7** after S6, together with the `RuntimeObservation` part of IR-2, before the RAG-M3 review | keeps the RAG-M1 milestone assignment | adds scope to RAG-M3 that its acceptance criterion does not need; splits IR-2 |
| B (recommended) | **First stage of RAG-M4**, together with all of IR-2 (`DocClaim`, `RuntimeObservation`, `EvidenceConflict`) | RAG-M4 (benchmark construction) is the first consumer: benchmark items reference `runtime_observations` by `obs_id`; IR-2 and IR-3 are built and tested together | moves IR-3 from the milestone named in the RAG-M1 design; recorded as an owner-approved re-assignment in the plan and tracker (the RAG-M1 documents are not edited) |
| C | Mark the RAG-M1 suite as sufficient | none | does not meet three of the four requirements (no success-path control, no per-run control rule, truncated errors) and has no `RuntimeObservation` output; not recommended |

**Owner decision (2026-11-22): Option B.** IR-3 is implemented in the first stage of RAG-M4, together with IR-2 and the `RuntimeObservation` model. Owner's reason: RAG-M4 is the first consumer of benchmark observations by ID, and implementing the observation model and the complete probe suite together avoids an intermediate implementation that would need restructuring.

Consequences:

- IR-3 stays **todo, deferred to RAG-M4 (first stage)**; it is not part of RAG-M3 and is not implemented in S6.
- IR-2 (`DocClaim`, `RuntimeObservation`, `EvidenceConflict`) is implemented in the same RAG-M4 stage.
- The re-assignment from the RAG-M3 milestone named in `RAG-M1_design.md` §9.4 is recorded here, in `RAG-M3_S6_ci.md` and in the execution tracker. The approved RAG-M1 documents are not edited.
- S6 changes no probe file; the RAG-M1 probe script and expected results stay byte-identical and hash-pinned.
