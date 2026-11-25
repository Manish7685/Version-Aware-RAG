# Pilot review batch 2 of 2 (protocol v1)

V0 drafts: not gold, not human-verified. Decisions (accept | accept_minor_edit | accept_major_edit | reject), reason codes R1-R9 (protocol v0 §7), comment, edits and time spent go in the review records.

### T3-11 (dq-3213034c012e)

- type T3; target version 1.5; change `chg-1.5-04` (boundary 1.4->1.5)
- question: Which function was introduced in DuckDB 1.5 to return the number of days in the month for a given date?
- gold_short_answer (identifier): days_in_month
- accepted_aliases: days_in_month(date)
- evidence (1.5, `docs/1.5/sql/functions/date.md`, src:docs/1.5/sql/functions/date.md:105-112): #### `days_in_month(date)`
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-12 (dq-16221748d2bd)

- type T3; target version 2.0; change `chg-2.0-01` (boundary 1.5->2.0)
- question: In DuckDB 2.0, what happens when you perform an integer division by zero, such as `1 // 0`?
- gold_short_answer (identifier): throw an error
- accepted_aliases: errors, throw an error, errors by default
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:167-176): Integer division (`//`) and modulo (`%`) by zero, e.g., `1 // 0` and `1 % 0`, now throw an error instead of returning `NULL`:
- evidence (2.0, `docs/2.0/configuration/pragmas.md`, chunk:c-220862e4ad5151194902): Integer division by zero (`1 // 0`, `1 % 0`, `INTERVAL '1' DAY / 0`) errors by default.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-14 (dq-15774bdcc3ec)

- type T3; target version 2.0; change `chg-2.0-03` (boundary 1.5->2.0)
- question: What type does an untyped NULL column keep instead of defaulting to INTEGER in DuckDB 2.0?
- gold_short_answer (type_name): "NULL"
- accepted_aliases: NULL
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:183-188): An untyped `NULL` now keeps the `NULL` type instead of defaulting to `INTEGER`.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-15 (dq-8ca695356937)

- type T3; target version 2.0; change `chg-2.0-04` (boundary 1.5->2.0)
- question: In DuckDB 2.0, what happened to the fetchone() method on Python relations?
- gold_short_answer (identifier): gone
- accepted_aliases: removed, they are gone
- evidence (2.0, `docs/2.0/clients/python/migration.md`, src:docs/2.0/clients/python/migration.md:14-14): 2.0: these methods are gone, because a relation keeps no result between calls and runs its query again on every read.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-16 (dq-ac7d6ab1edde)

- type T3; target version 2.0; change `chg-2.0-05` (boundary 1.5->2.0)
- question: In DuckDB 2.0, what type of regular expression matching do the `~` and `!~` operators perform?
- gold_short_answer (value): partial regular expression matching
- accepted_aliases: partial regex matching, partial
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:108-108): The `~` and `!~` operators now perform [partial regular expression matching]({% link docs/2.0/sql/functions/pattern_matching.md %}#regular-expressions), following PostgreSQL: `'abc' ~ 'b'` now returns `true` (previously `false`). The deprecated `regex_match_operator_semantics` setting restores full matching: `SET regex_match_operator_semantics = 'full';`{:.language-sql .highlight}
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-17 (dq-9860a4b77ad3)

- type T3; target version 2.0; change `chg-2.0-06` (boundary 1.5->2.0)
- question: In DuckDB 2.0, how does the precedence of the factorial operator change compared to earlier versions such as v1.5?
- gold_short_answer (value): binds more tightly
- accepted_aliases: binds more tightly than in earlier versions
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:109-109): The [factorial operator `!`]({% link docs/2.0/sql/functions/numeric.md %}#factorialx) binds more tightly: `2 * 3!` now returns `12` (previously `720`).
- evidence (2.0, `docs/2.0/sql/functions/numeric.md`, chunk:c-6fb4a60bfeef18cbc002): Note In DuckDB 2.0, the `!` operator binds more tightly than in earlier versions.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-01 (dq-e95964448191)

- type T4; target version 1.2; change `chg-1.2-01` (boundary ref->1.2)
- question: Which function can be used as an alternative to bracket notation to retrieve a map value as a list?
- gold_short_answer (identifier): element_at
- accepted_aliases: element_at(map, key), element_at()
- evidence (1.2, `docs/1.2/sql/data_types/map.md`, chunk:c-2f9233cacdf3d2c2e8fd): The `element_at` function can be used to retrieve a map value as a list:
- evidence (1.2, `docs/1.2/sql/functions/map.md`, chunk:c-6966c55ce35ff8f31f61): | `element_at(map, key)` | Return the value for a given key, or `NULL` if the key is not contained in the map. The type of the key provided in the second parameter must match the type of the map's keys else an error is returned. |
- restricted sources (reference only): release_post:_posts/2025-02-05-announcing-duckdb-120.md:29-31
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-04 (dq-6dad8743fc00)

- type T4; target version 1.5; change `chg-1.5-01` (boundary 1.4->1.5)
- question: Which function for a TIMESTAMP WITH TIME ZONE and which function for a date both return the last day of the corresponding month?
- gold_short_answer (identifier): last_day
- accepted_aliases: last_day(timestamptz), last_day(date)
- evidence (1.5, `docs/1.5/sql/functions/timestamptz.md`, chunk:c-0989f106c25f1875dc89): | `last_day(timestamptz)` | The last day of the month. |
- evidence (1.5, `docs/1.5/sql/functions/date.md`, chunk:c-15a5b0b988b4743da38e): | `last_day(date)` | The last day of the corresponding month in the date. |
- restricted sources (reference only): release_post:_posts/2026-03-09-announcing-duckdb-150.md:284-292
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-05 (dq-6b42f04b3fba)

- type T4; target version 1.5; change `chg-1.5-03` (boundary 1.4->1.5)
- question: Which extension contains the associated functions for working with geometries, such as ST_RotateX?
- gold_short_answer (identifier): spatial
- accepted_aliases: spatial extension
- evidence (1.5, `docs/1.5/sql/data_types/geometry.md`, src:docs/1.5/sql/data_types/geometry.md:10-12): almost all of the associated functions for working with geometries (e.g., calculating distances, areas, intersections) are still part of `spatial`
- evidence (1.5, `docs/1.5/core_extensions/spatial/functions.md`, chunk:c-06011858421da72c4cc2): GEOMETRY ST_RotateX (geom GEOMETRY, radians double)
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-06 (dq-593f1d586de2)

- type T4; target version 2.0; change `chg-2.0-01` (boundary 1.5->2.0)
- question: Which configuration setting can be used to revert integer division by zero back to returning NULL in DuckDB 2.0, even though it is deprecated?
- gold_short_answer (identifier): error_on_division_by_zero
- accepted_aliases: error_on_division_by_zero = false, SET error_on_division_by_zero=false
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:167-176): SET error_on_division_by_zero=false
- evidence (2.0, `docs/2.0/configuration/pragmas.md`, chunk:c-220862e4ad5151194902): The `error_on_division_by_zero` setting is deprecated and will be removed in a future release.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-08 (dq-30edfc4974cf)

- type T4; target version 2.0; change `chg-2.0-03` (boundary 1.5->2.0)
- question: When performing a query that unpacks columns to get a result like 42, what specific data type does an untyped NULL default to in this version of DuckDB instead of INTEGER?
- gold_short_answer (type_name): "NULL"
- accepted_aliases: NULL
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:183-188): An untyped `NULL` now keeps the `NULL` type instead of defaulting to `INTEGER`.
- evidence (2.0, `docs/2.0/sql/expressions/star.md`, chunk:c-478348f6e28c26bc084b): SELECT coalesce(UNPACK(COLUMNS(['a', 'b', 'c']))) AS result
FROM (SELECT NULL AS a, 42 AS b, true AS c);
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-13 (dq-4fe3a9225e06)

- type T4; target version 2.0; change `chg-2.0-08` (boundary 1.5->2.0)
- question: In DuckDB 2.0, what data type does the configuration option responsible for controlling identifier casing use?
- gold_short_answer (type_name): VARCHAR
- accepted_aliases: varchar
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:189-193): The [`preserve_identifier_case` setting]({% link docs/2.0/configuration/overview.md %}) changed from a `BOOLEAN` to a `VARCHAR` with the values `'preserve_case'` (default), `'lowercase'`, and `'uppercase'`.
- evidence (2.0, `docs/2.0/sql/dialect/keywords_and_identifiers.md`, chunk:c-1150ae08d5965a44bb22): To change this behavior, use the `preserve_identifier_case` configuration option, see below.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T4-14 (dq-f3aeb6370e62)

- type T4; target version 2.0; change `chg-2.0-10` (boundary 1.5->2.0)
- question: Which table function provides metadata about triggers, such as the table a trigger is attached to, introduced in DuckDB 2.0?
- gold_short_answer (identifier): duckdb_triggers()
- accepted_aliases: duckdb_triggers
- evidence (2.0, `docs/2.0/sql/statements/create_trigger.md`, src:docs/2.0/sql/statements/create_trigger.md:47-58): CREATE TABLE t (id INTEGER);
- evidence (2.0, `docs/2.0/sql/meta/duckdb_table_functions.md`, chunk:c-ec4f29537112823adf37): The `duckdb_triggers()` function was introduced in DuckDB 2.0.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-01 (dq-76ab233bcd1a)

- type T5; target version 1.2; change `chg-1.3-01` (boundary 1.2->1.3)
- question: How do nested lambda functions confirm to scoping rules in DuckDB 1.2?
- unanswerable; abstain_reason: Python-style lambda syntax and scoping rules for lambda functions were introduced in version 1.3, so they are not available or documented for version 1.2.
- restricted sources (reference only): release_post:_posts/2025-05-21-announcing-duckdb-130.md:41-53
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-02 (dq-eb3dbae91e8b)

- type T5; target version 1.2; change `chg-1.3-02` (boundary 1.2->1.3)
- question: Which function can be used in DuckDB 1.2 to extract the version of a UUID?
- unanswerable; abstain_reason: The function uuid_extract_version was introduced in DuckDB 1.3 and does not exist in version 1.2.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-03 (dq-22c8f2f91d73)

- type T5; target version 1.3; change `chg-1.4-01` (boundary 1.3->1.4)
- question: How can I use the MERGE INTO statement to perform upserts in DuckDB 1.3?
- unanswerable; abstain_reason: The MERGE statement was introduced in version 1.4.0 and is not supported in DuckDB 1.3.
- restricted sources (reference only): release_post:_posts/2025-09-16-announcing-duckdb-140.md:93-124
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-04 (dq-97e44579c29e)

- type T5; target version 1.3; change `chg-1.4-02` (boundary 1.3->1.4)
- question: Which function converts milliseconds since the epoch to a timestamp in DuckDB 1.3?
- unanswerable; abstain_reason: The make_timestamp_ms function was introduced in DuckDB 1.4 and does not exist in version 1.3.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-05 (dq-78cc7b4a39c2)

- type T5; target version 1.3; change `chg-1.4-03` (boundary 1.3->1.4)
- question: What is the function name of the alias for `struct_contains` in DuckDB 1.3?
- unanswerable; abstain_reason: The struct_contains function was introduced in DuckDB 1.4 and does not exist in version 1.3.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-07 (dq-714801b8bce9)

- type T5; target version 1.4; change `chg-1.5-04` (boundary 1.4->1.5)
- question: Which function can I use to get the number of days in the month of a given date in DuckDB 1.4?
- unanswerable; abstain_reason: The function days_in_month was introduced in version 1.5 and does not exist in DuckDB 1.4.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-08 (dq-c847aaeed6a0)

- type T5; target version 1.5; change `chg-2.0-10` (boundary 1.5->2.0)
- question: How can I use the CREATE TRIGGER statement to automatically log insertions in DuckDB 1.5?
- unanswerable; abstain_reason: The CREATE TRIGGER feature was introduced in DuckDB 2.0 and does not exist in DuckDB 1.5.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-09 (dq-36f5bf970749)

- type T5; target version 1.2; change `chg-1.4-01` (boundary 1.3->1.4)
- question: How can I use the MERGE INTO statement to perform upserts in DuckDB 1.2?
- unanswerable; abstain_reason: The MERGE statement and MERGE INTO statement were introduced in v1.4.0 and do not exist in DuckDB 1.2.
- restricted sources (reference only): release_post:_posts/2025-09-16-announcing-duckdb-140.md:93-124
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-10 (dq-6eba707eea70)

- type T5; target version 1.2; change `chg-1.4-02` (boundary 1.3->1.4)
- question: Which function converts milliseconds since the epoch to a timestamp in DuckDB 1.2?
- unanswerable; abstain_reason: The function make_timestamp_ms was introduced in DuckDB 1.4 and is not present in version 1.2.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-11 (dq-9a1f01cb3ce5)

- type T5; target version 1.2; change `chg-1.4-03` (boundary 1.3->1.4)
- question: What is the function name for checking if a struct contains a specific entry in DuckDB 1.2?
- unanswerable; abstain_reason: The struct_contains function was introduced in DuckDB 1.4 and does not exist in version 1.2.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-12 (dq-bc4f90e0bdd7)

- type T5; target version 1.3; change `chg-1.5-02` (boundary 1.4->1.5)
- question: How can I list all schemas across non-internal databases using a SHOW statement in DuckDB 1.3?
- unanswerable; abstain_reason: The SHOW SCHEMAS statement was introduced in DuckDB 1.5 and does not exist in version 1.3.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T5-13 (dq-debfd589d9ed)

- type T5; target version 1.3; change `chg-1.5-04` (boundary 1.4->1.5)
- question: What function can I use in DuckDB 1.3 to find the number of days in the month for a given date?
- unanswerable; abstain_reason: The days_in_month function was introduced in DuckDB 1.5 and does not exist in version 1.3.
- V1: pass_pending_probe (schema pass, spans_verbatim not_applicable, version_compatible not_applicable, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 2: record the decision in `benchmark/review/pilot_review_records.jsonl`
