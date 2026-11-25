# Labelling guide: citation support and faithfulness pairs (Tier M calibration)

Design §5.3 calibrates the NLI judge on human labels: about 100 **calibration** pairs set the
entailment threshold θ, and a **separate** set of about 100 **agreement** pairs measures how often
the judge agrees with you. Until you label them, every citation-support and faithfulness number is
marked UNCALIBRATED.

## The sheet

`results/answers/nli_pairs_to_label.csv` (git-ignored; open in Excel or LibreOffice, save as CSV
UTF-8 under the same name). 200 rows, sampled deterministically by `vrag eval support
export-pairs`, stratified over the model's confidence and over pair kind so that the hard cases
near the threshold are covered. The model's scores are **not** in the sheet, so they cannot
influence you.

| Column | Meaning |
|---|---|
| `pair_id` | Stable id; do not edit |
| `set` | `calibration` or `agreement`; do not edit |
| `kind` | `cited` (the answer cited this passage) or `retrieved` (retrieved but not cited) |
| `sentence` | One sentence of the answer's explanation (the hypothesis) |
| `premise` | One documentation passage (MIT-licensed DuckDB docs) |
| `label` | **Fill in:** `1` or `0` |
| `synthetic` | Leave empty (only machine-labelled development copies use it) |

## How to decide

Label **1 (supported)** when the passage alone, read by a careful DuckDB user, establishes
everything the sentence claims. Otherwise label **0**.

- Judge only against this one passage. Ignore what you know from elsewhere and what other passages
  might say.
- Paraphrase is fine: "returns a TIMESTAMP" supports "the result type is TIMESTAMP".
- Partial support is **0**: if the sentence makes two claims and the passage supports one, label 0.
- A version claim ("in DuckDB 2.0 …") must be supported too: a passage from the 2.0 docs supports
  a 2.0 claim only if the passage itself makes it clear the behaviour applies (for example a "New
  in 2.0" note, or a page of that version describing current behaviour).
- Hedged or vague sentences ("this is useful for …") are 0 unless the passage states the same.
- If the passage is cut off and the needed part is missing, label 0.
- Take about 20-40 seconds per pair; skip nothing (every row needs a label).

## Then

```
python -m uv run vrag eval support calibrate --method hybrid   # sets θ, reports agreement
python -m uv run vrag eval support score --method hybrid       # numbers now say "calibrated"
```

`calibrate` refuses the sheet if any label is missing or not 0/1, if a set has fewer than 30
pairs or only one class, if pair ids repeat, or if any row is marked synthetic.

**Development only:** `vrag eval support dev-labels` writes `nli_pairs_DEV_SYNTHETIC.csv`
labelled by the model itself, marked `synthetic = DEV-ONLY`. It exists to exercise the code; it is
circular as evidence and `calibrate` refuses it.

## Size note

The current 37 non-abstained provisional answers yield 311 candidate pairs, of which 58 are cited
pairs. After the benchmark review the answers are regenerated on verified items; if many answers
change, re-export (delete or rename the old sheet first; the command never overwrites a sheet that
may hold labels).
