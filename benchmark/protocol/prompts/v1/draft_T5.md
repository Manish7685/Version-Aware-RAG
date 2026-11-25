# Drafting prompt v1: T5 Unanswerable

Use together with draft_common.md.

Draft a question that cannot be answered for {{target_version}}: either the feature does not exist in that version (change: {{change_feature}}, introduced in {{version}}), or the answer is not in the docs. The gold answer type is none; give the abstain_reason.

Prompt v1 rule (protocol v1): a T5 item has no gold evidence span (protocol v0 §5.6). "evidence_spans" must be an empty list, "answerable" must be false and "gold_short_answer" empty; explain in "abstain_reason" why the answer is absent for {{target_version}}. Do not cite passages of other versions as evidence.
