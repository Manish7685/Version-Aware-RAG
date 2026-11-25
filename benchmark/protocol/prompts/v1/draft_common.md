# Drafting prompt v1: common instructions

You draft candidate benchmark questions about the DuckDB documentation. Every draft is a V0 candidate that a human reviews; it is never treated as correct.

Rules:
1. Use only the evidence passages given below. Do not use outside knowledge, and do not invent behaviour, versions or values.
2. Every question has exactly one target version: {{target_version}}. The question must read naturally for that version and must not fit another version equally well.
3. Do not copy the identifiers or values of the answer into the question. Paraphrase.
4. The gold answer is the shortest complete answer, typed as one of: value, type_name, identifier, version, yes_no, sql_expression, none.
5. Every evidence span you cite must be copied exactly, character for character, from one of the given passages.
6. Write a question a real user of DuckDB {{target_version}} could plausibly ask.
7. If the passages do not support an answer, say so instead of guessing.

Prompt v1 adds two exact-value rules (protocol v1 §2.4): the "type" value is exactly the item type named in the type instructions below, written as one of "T1", "T2", "T3", "T4", "T5", with no other words; and the "target_version" value is exactly {{target_version}}.

Return one JSON object with the keys: question, type, target_version, gold_short_answer, gold_answer_type, accepted_aliases, evidence_spans (each with passage_id and exact text), answerable, abstain_reason.

Evidence passages:
{{passages}}
