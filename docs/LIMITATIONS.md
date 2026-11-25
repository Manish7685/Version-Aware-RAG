# Scope and limitations (final, small-pilot scope)

P2 is finished as a **small pilot benchmark** by owner decision (2026-11-24; decision sheets D3
revision and tracker D82). This page lists, in one place, what the project deliberately does not
do, where it falls short of the roadmap design, and how each shortfall affects the results.
Nothing here is hidden from the reported numbers: every result file states the split it was
computed on.

## 1. Owner scope decisions (deliberate, not defects)

| Area | Roadmap / design target | What P2 delivers | Decision |
|---|---|---|---|
| Benchmark size (M4) | about 500 items (design), later about 200 (planning) | 52 reviewed items, 40 retained after review: test 25, dev 15 (split by change, 0 leakage) | Small pilot; no expansion |
| CI-gate split (M9) | 120 V2 items in a separate split | none; the gate is demonstrated locally with neutral, benign and injected scenarios and two demo patches (`docs/gate_demo/`); the CI job `eval-gate` stays dormant | Local demonstration only; the test split is not reused as a gate |
| Fine-tuned embedder (M6) | bge-small fine-tuned on dev-split pairs (Colab) | not trained; the code path (`finetune-data`, Colab notebook, `index check-model`, `finetuned` method) exists and is tested but has no model | Documented as not trained |
| Citation support / faithfulness (M7, Tier M) | NLI judge calibrated on owner labels with agreement reported | scores from the local NLI model at an **uncalibrated** threshold θ = 0.5; reported as diagnostics only | No labelling; uncalibrated |
| Deployment (M12) | public deployment answering a real question | local Docker (`docker compose up --build`, 127.0.0.1:8000) is the official deployment | Local Docker accepted |

## 2. Benchmark limitations

- **Size and confidence intervals.** 25 test items give wide 95% intervals; only large differences
  between methods are detectable, and per-slice numbers (version-specific, change-introduced,
  multi-hop, stable, hard-negative) rest on a handful of items each. Treat slice results as
  descriptive.
- **Type coverage below the protocol targets.** After review, T4 (multi-hop) kept 4 of 7 planned
  items and T5 (abstention) 5 of 12; their accept-or-minor-edit rates (57% and 42%) are below the
  pilot's 80% threshold. Replacements were not drafted. Abstention precision/recall and multi-hop
  results are therefore especially uncertain.
- **AI-assisted review.** The owner reviewed and confirmed all 52 decisions with AI assistance;
  the decisions agree with the AI-generated recommendations on every row. Two AI systems shaped
  the labels, so the benchmark is not an independent human annotation. Review times are estimates,
  not measurements.
- **Drafted by Gemini.** Items were drafted by `gemini-3.5-flash-lite` from permitted documentation
  only, then checked automatically (V1) and reviewed; wording and coverage reflect that model.
- **Strict answer matching.** The matcher accepts only the gold answer and reviewed aliases, so
  correct answers in another surface form count as wrong: answer accuracy is a lower bound.

## 3. Method and evaluation limitations

- Late interaction reranks the hybrid top 100 instead of searching a full token-level index (about
  1.8 GB of vectors does not fit the CPU budget).
- The NLI judge (`cross-encoder/nli-deberta-v3-xsmall`) is small and uncalibrated; support and
  faithfulness numbers are not comparable to calibrated judges.
- The CI gate's thresholds (τ, C) are the design defaults; the scaling direction was approved (D5)
  but no numbers were calibrated for an enforced split, because none exists.
- The final answer evaluation used cached Gemini responses only (no new requests): 3 of the 25 test
  items had no cached response (the two released T2-H01 items and the edited T3-06) and are reported
  as missing; answer metrics are given on the 22 answered items. No BM25 answer run exists.
- The gate is demonstrated only on the pre-review pilot (38 retrieval-eligible V0 drafts): demo
  patches and the design §6.4 calibration scenarios. It was never run as a gate on the frozen test
  split, and its thresholds were not calibrated for an enforced split.
- Retrieval metrics cover 22 of the 25 test items: the three unanswerable T5 items (T5-02, T5-08,
  T5-12) have no evidence by design and are evaluated only for abstention. Every answerable test
  item is scored.
- Restricted sources (release notes and release posts) are never sent to a model, so the design's
  changelog hop is not available for answering; change questions rely on the documentation and
  the permitted migration guides.

## 4. Runtime evidence

The canonical probe run was recorded on 2026-11-24 at commit `ac9f206` (Windows, Python 3.12.16):
168 observations (28 probes x 6 pinned DuckDB versions, 1.1.3 to 2.0.1) in
`data/evidence/runtime_observations.jsonl`, with all control probes identical across the six
versions; `vrag evidence validate` passes.

Canonical observations were recorded and validated for all probes referenced by the benchmark items. Automatic per-item comparison against runtime observations is not implemented. Items are verified against documentation and owner review; probe consistency remains pending.

**One observed value is stored as a hash.** The raw DuckDB 2.0.1 error message of observation
`ro-P05-2.0.1-windows` matched the project's restricted-text privacy scan (a 10-word sequence shared
with restricted release text), so its `observed_value` is stored as
`redacted-privacy-scan:sha256:<hash of the raw message>` and the raw text was excluded from the
repository. The observation's status (`error`) and all other metadata are unchanged. A later
reproduction on another platform must apply the same redaction for the values to match.

## 5. Corpus limitations

See README §17 (licence of release-note text not established; fresh builds depend on GitHub serving
the pinned docs commit; four known `validate` warnings).

## 6. What would lift these limitations

Scaling the benchmark with an inventory extension and a second review round; a separate ci-gate
split; the Colab fine-tune; owner NLI labels; a hosted deployment with authentication. None is
planned.
