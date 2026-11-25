# Pilot review (protocol v1)

> **Review complete (2026-11-24).** The review set is **52 items**: the 50 pilot items plus the two
> T2-H01 drafts the owner released from hold (decision D2, `released_holds.json`). The owner
> reviewed and confirmed all 52 decisions with AI assistance (reviewer `owner (AI-assisted)`;
> `time_spent_seconds` are estimates, stated in each comment). `pilot_review_sheet.csv` is the
> imported sheet and `pilot_review_records.jsonl` the stored records. The AI-generated recommendations are
> kept in `proposed/recommendations.json` (the owner's decisions match them on all 52 rows); the
> working copies of the proposed sheet and the advisory screen report stay local (git-ignored).

The 50 pilot items are V0 drafts: **not gold, not human-verified** until you record a decision. Your decisions are stored separately in `pilot_review_records.jsonl`; the drafts and their evidence are never modified.

## 1. Review in the spreadsheet

1. Open `pilot_review_sheet.csv` in Excel (or LibreOffice). It has one row per item: read-only columns on the left (question, proposed answer, type, target version, change, evidence `passage_id::exact text`, V1 status) and editable columns on the right.
2. For each row fill in:
   - `decision`: `accept`, `accept_minor_edit`, `accept_major_edit` or `reject`;
   - `reason_codes`: R1–R9 from protocol v0 §7, separated by spaces (required for `reject`; `R9` needs a `comment`);
   - `comment`, `time_spent_seconds`, `reviewer` (required with a decision);
   - edits only with an `accept_*_edit` decision: `edit_question`, `edit_gold_short_answer`, `edit_answer_type` (`value`, `type_name`, `identifier`, `version`, `yes_no`, `sql_expression`, `none`), `edit_accepted_aliases` (separated by `;`), `edit_evidence` (`passage_id::exact text`, entries separated by ` || `; the text must be copied exactly from the cited corpus passage of a compatible version).
3. Leave a row's `decision` empty if you have not reviewed it yet; you can import partial progress (batch 1 = rows with `review_batch` 1, then batch 2).
4. Save as **CSV UTF-8** with the same file name.

The Markdown sheets `pilot_review_batch_01.md` and `_02.md` show the same items for reading.

## 2. Import and check

```
python -m uv run vrag benchmark pilot import-review
```

The whole sheet is validated first; if any row is invalid (unknown decision or code, `accept` with edits, an edit decision without edits, a rejection without a reason, R9 without a comment, a missing reviewer, an invalid answer type or multi-answer gold, edited evidence that is not verbatim, a changed or missing row), nothing is written and every problem is listed by item id. To refresh the sheet from the stored records: `vrag benchmark pilot export-review`.

## 3. After the review

```
python -m uv run vrag benchmark pilot report             # docs/benchmark/pilot_report.md from your records
python -m uv run vrag benchmark pilot validation-sample  # rejected topics to redraft with the final v1 prompts
python -m uv run vrag benchmark pilot split-plan         # dry run of the dev/test split and leakage checks
python -m uv run vrag benchmark pilot freeze             # only when every item has a decision
python -m uv run vrag benchmark pilot datasheet
```

The report, validation sample and freeze refuse to run until every item has a valid decision.
