# Pilot report (protocol v1)

Every number below is computed from `benchmark/review/pilot_review_records.jsonl`.

Reviewed items: 52; decisions: accept 27, accept_minor_edit 10, accept_major_edit 3, reject 12

| Type | accept | minor edit | major edit | reject |
|---|---|---|---|---|
| T1 | 10 | 2 | 0 | 1 |
| T2 | 1 | 5 | 2 | 0 |
| T3 | 7 | 3 | 1 | 1 |
| T4 | 4 | 0 | 0 | 3 |
| T5 | 5 | 0 | 0 | 7 |

Reason codes: R1 2, R2 5, R3 7, R4 12, R6 2, R7 4, R9 3
V1 false passes (passed V1, rejected in review): 12
Median review time: 65 s

## Rejected items

- T1-18 (T1, target 1.5): codes R9; question: Which pattern matching operators does DuckDB offer alongside regular expression functions?; comment: Rejected because the question requires a multi-part answer that is not reliably handled by the current deterministic answer matcher. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T3-01 (T3, target 2.0): codes R7; question: What data type did the preserve_identifier_case setting change to in DuckDB 2.0?; comment: Rejected because this duplicates the version-specific information already tested by T2-07@2.0. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T4-01 (T4, target 1.2): codes R4; question: Which function can be used as an alternative to bracket notation to retrieve a map value as a list?; comment: Rejected because the first evidence passage alone answers the question. The item does not meet the multi-hop requirement. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T4-08 (T4, target 2.0): codes R4, R6, R7; question: When performing a query that unpacks columns to get a result like 42, what specific data type does an untyped NULL default to in this version of DuckDB instead of INTEGER?; comment: Rejected because the additional UNPACK example is not needed to answer the question, and the item duplicates T3-14's untyped NULL type question. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T4-14 (T4, target 2.0): codes R4; question: Which table function provides metadata about triggers, such as the table a trigger is attached to, introduced in DuckDB 2.0?; comment: Rejected because the evidence directly states that duckdb_triggers() was introduced in DuckDB 2.0, so one passage answers the question without a second retrieval hop. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-01 (T5, target 1.2): codes R2, R4; question: How do nested lambda functions confirm to scoping rules in DuckDB 1.2?; comment: Rejected because DuckDB 1.2 documents the lambda scoping rules, so the requested information is answerable in the target version. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-04 (T5, target 1.3): codes R2, R4; question: Which function converts milliseconds since the epoch to a timestamp in DuckDB 1.3?; comment: Rejected because epoch_ms(ms) already converts milliseconds since the epoch to a timestamp in DuckDB 1.3. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-07 (T5, target 1.4): codes R2; question: Which function can I use to get the number of days in the month of a given date in DuckDB 1.4?; comment: Rejected because last_day(date) is documented in DuckDB 1.4 and can be used with day() to derive the number of days in the month. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-09 (T5, target 1.2): codes R7; question: How can I use the MERGE INTO statement to perform upserts in DuckDB 1.2?; comment: Rejected because this is a redundant version-paired question with T5-03, which already tests the absence of MERGE INTO before 1.4. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-10 (T5, target 1.2): codes R2, R4; question: Which function converts milliseconds since the epoch to a timestamp in DuckDB 1.2?; comment: Rejected because epoch_ms(ms) already provides the requested conversion in DuckDB 1.2. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-11 (T5, target 1.2): codes R7; question: What is the function name for checking if a struct contains a specific entry in DuckDB 1.2?; comment: Rejected because this duplicates T5-05, which already tests the absence of struct_contains before 1.4. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
- T5-13 (T5, target 1.3): codes R2; question: What function can I use in DuckDB 1.3 to find the number of days in the month for a given date?; comment: Rejected because last_day(date) is documented in DuckDB 1.3 and can be used with day() to derive the number of days in the month. Owner confirmed (AI-assisted). time_spent_seconds is an estimate, not measured time.
