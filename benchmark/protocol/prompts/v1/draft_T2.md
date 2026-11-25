# Drafting prompt v1: T2 Version-specific

Use together with draft_common.md (v0, unchanged). Prompt v1 changes only this T2 template: it makes the cross-version fields explicit.

The answer differs across versions. Change: {{change_feature}}. Before: {{change_before}}. After: {{change_after}}. Draft one question that is asked identically for every version in the set {{version_set}}. The target version is {{target_version}}. Choose a question whose answer the given passages document for every version in the set: a T2 item is accepted only when every version is "documented", with at least one evidence span from that version's own passages.

In addition to the keys listed above, the JSON object must contain these keys, and these rules apply:

- "type": exactly "T2".
- "version_answers": an object with exactly one entry for every version in the set {{version_set}}, keyed by the version string. Each entry is an object with:
  - "status": "documented" when the given passages for that version state the answer, otherwise "not_documented";
  - "answer": the shortest complete answer for that version, of the same answer type as gold_short_answer; null when the status is "not_documented";
  - "evidence_spans": a list of objects with "passage_id" (the identifier shown in square brackets before the passage) and "exact_text" (copied character for character from that passage). Cite only passages of that same version. Use an empty list when the status is "not_documented".
- "behaviour": "changed" when the documented answers differ across the versions, otherwise "unchanged".
- "gold_short_answer": must equal the answer given for {{target_version}} in version_answers.
- "other_version_answers": an object mapping every other version in the set to its answer, equal to the answers in version_answers.

Never fill a version's answer from outside knowledge. If the passages do not document some version, still report that version honestly as "not_documented": the item is then rejected, not repaired. If the behaviour does not change across the set, say "unchanged" rather than inventing a difference: the item is then rejected as well.
