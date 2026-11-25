# Pilot review batch 1 of 2 (protocol v1)

V0 drafts: not gold, not human-verified. Decisions (accept | accept_minor_edit | accept_major_edit | reject), reason codes R1-R9 (protocol v0 §7), comment, edits and time spent go in the review records.

### T1-01 (dq-06c77bb62956)

- type T1; target version 1.3; change `chg-1.5-01` (boundary 1.4->1.5)
- question: Which function in DuckDB is used to truncate a timestamp to a specified precision?
- gold_short_answer (identifier): date_trunc
- accepted_aliases: date_trunc(part, timestamp)
- evidence (1.3, `docs/1.3/sql/functions/timestamp.md`, chunk:c-95e0d509543c2d79aafb): #### `date_trunc(part, timestamp)`
- evidence (1.3, `docs/1.3/sql/functions/timestamp.md`, chunk:c-95e0d509543c2d79aafb): | Description | Truncate to specified precision. |
- restricted sources (reference only): release_post:_posts/2026-03-09-announcing-duckdb-150.md:284-292
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-03 (dq-9d0c141b00d1)

- type T1; target version 1.5; change `chg-2.0-01` (boundary 1.5->2.0)
- question: Which standard for floating-point arithmetic does DuckDB conform to for both division by zero and operations involving infinity values?
- gold_short_answer (identifier): IEEE 754
- accepted_aliases: IEEE Standard for Floating-Point Arithmetic (IEEE 754)
- evidence (1.5, `docs/1.5/sql/dialect/postgresql_compatibility.md`, chunk:c-8513b291edfbdef050ee): DuckDB conforms to the IEEE Standard for Floating-Point Arithmetic (IEEE 754) for both division by zero and operations involving infinity values.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-04 (dq-a1f4478e1112)

- type T1; target version 2.0; change `chg-2.0-05` (boundary 1.5->2.0)
- question: Which option can be passed to regular expression functions to enable case-insensitive matching in DuckDB?
- gold_short_answer (value): 'i'
- accepted_aliases: i
- evidence (2.0, `docs/2.0/sql/functions/regular_expressions.md`, chunk:c-f79358849e9ce067b829): | `'i'` | Case-insensitive matching |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-05 (dq-b0c83044ef1d)

- type T1; target version 1.2; change `chg-2.0-06` (boundary 1.5->2.0)
- question: Which function in DuckDB interpolates the factorial of x minus 1?
- gold_short_answer (identifier): gamma
- accepted_aliases: gamma(x)
- evidence (1.2, `docs/1.2/sql/functions/numeric.md`, chunk:c-8b3606bde34adb652811): #### `gamma(x)`
- evidence (1.2, `docs/1.2/sql/functions/numeric.md`, chunk:c-8b3606bde34adb652811): | Description | Interpolation of the factorial of `x - 1`. Fractional inputs are allowed. |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-09 (dq-940dd5da69e2)

- type T1; target version 2.0; change `chg-2.0-05` (boundary 1.5->2.0)
- question: Which underlying library does DuckDB utilize for processing regular expressions?
- gold_short_answer (identifier): RE2
- accepted_aliases: RE2 library
- evidence (2.0, `docs/2.0/sql/functions/regular_expressions.md`, chunk:c-85947cea9750d622ebda): DuckDB uses the RE2 library as its regular expression engine.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-11 (dq-e83a93a1fbdb)

- type T1; target version 1.3; change `chg-1.5-03` (boundary 1.4->1.5)
- question: Which function converts a polygon geometry into an SVG path data string?
- gold_short_answer (identifier): ST_AsSVG
- accepted_aliases: st_assvg
- evidence (1.3, `docs/1.3/core_extensions/spatial/functions.md`, chunk:c-e9cc6f5e352f96b00a85): SELECT ST_AsSVG('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))'::GEOMETRY, false, 15);
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-12 (dq-459da27f7f72)

- type T1; target version 1.4; change `chg-2.0-05` (boundary 1.5->2.0)
- question: Which flag should be passed to the regexp_replace function to replace all occurrences of a regular expression match rather than just the first one?
- gold_short_answer (identifier): g
- accepted_aliases: global replace, global replace (g) flag
- evidence (1.4, `docs/1.4/sql/functions/regular_expressions.md`, chunk:c-e11c5d593c80d50105c0): To replace all occurrences, use the global replace (`g`) flag.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-14 (dq-8540c5a17e3a)

- type T1; target version 2.0; change `chg-1.5-03` (boundary 1.4->1.5)
- question: Which function is used in DuckDB to convert a geometry object into its Well-Known Binary representation?
- gold_short_answer (identifier): ST_AsWKB
- accepted_aliases: st_aswkb
- evidence (2.0, `docs/2.0/core_extensions/spatial/functions.md`, chunk:c-24652c3604d7391c201f): SELECT ST_AsWKB('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))'::GEOMETRY)::BLOB;
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-15 (dq-955ccb78f32a)

- type T1; target version 1.2; change `chg-2.0-05` (boundary 1.5->2.0)
- question: Which function splits a string along a regular expression and returns a row for each part?
- gold_short_answer (identifier): regexp_split_to_table
- accepted_aliases: regexp_split_to_table(string, regex[, options])
- evidence (1.2, `docs/1.2/sql/functions/regular_expressions.md`, chunk:c-8d9baca219d89eb3482e): #### `regexp_split_to_table(string, regex[, options])`
- evidence (1.2, `docs/1.2/sql/functions/regular_expressions.md`, chunk:c-8d9baca219d89eb3482e): | Description | Splits the `string` along the `regex` and returns a row for each part. A set of optional `options` can be set. |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-16 (dq-bfafa501a58b)

- type T1; target version 1.3; change `chg-2.0-07` (boundary 1.5->2.0)
- question: In the Node.js neo client, what special function is used to construct values of the MAP data type?
- gold_short_answer (identifier): mapValue
- accepted_aliases: mapValue
- evidence (1.3, `docs/1.3/clients/node_neo/overview.md`, chunk:c-2f0b4d762810b2db235a): | `MAP` | `mapValue` |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-18 (dq-88f9c169fdfa)

- type T1; target version 1.5; change `chg-2.0-05` (boundary 1.5->2.0)
- question: Which pattern matching operators does DuckDB offer alongside regular expression functions?
- gold_short_answer (value): LIKE, SIMILAR TO, GLOB
- accepted_aliases: LIKE, SIMILAR TO, and GLOB, LIKE, SIMILAR TO, GLOB
- evidence (1.5, `docs/1.5/sql/functions/regular_expressions.md`, chunk:c-381985343771fe8b2da6): DuckDB offers pattern matching operators (`LIKE`, `SIMILAR TO`, `GLOB`), as well as support for regular expressions via functions.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-19 (dq-3612b9f76919)

- type T1; target version 2.0; change `chg-2.0-07` (boundary 1.5->2.0)
- question: Which format specifier is used with the printf function to convert an integer into its hexadecimal representation?
- gold_short_answer (value): %x
- accepted_aliases: %x
- evidence (2.0, `docs/2.0/sql/functions/text.md`, chunk:c-869a8b335cc11b31249f): Convert to hexadecimal:

SELECT printf('%x', 123_456_789); -- 75bcd15
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T1-20 (dq-d3755cea69aa)

- type T1; target version 1.2; change `chg-1.5-03` (boundary 1.4->1.5)
- question: Which spatial function in DuckDB returns true when two geometry arguments do not intersect or touch?
- gold_short_answer (identifier): ST_Disjoint
- accepted_aliases: st_disjoint
- evidence (1.2, `docs/1.2/extensions/spatial/functions.md`, chunk:c-8c7a65236e4b2225c869): BOOLEAN ST_Disjoint (geom1 GEOMETRY, geom2 GEOMETRY)
- evidence (1.2, `docs/1.2/extensions/spatial/functions.md`, chunk:c-8c7a65236e4b2225c869): Returns true if the geometries are disjoint
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-05@1.4 (dq-1e9371aa513e-1.4)

- type T2; target version 1.4; change `chg-2.0-05` (boundary 1.5->2.0)
- question: What is the equivalent expression for the PostgreSQL-style tilde operator in DuckDB?
- gold_short_answer (sql_expression): regexp_full_match
- accepted_aliases: regexp_matches
- other_version_answers: 1.5: regexp_full_match; 2.0: regexp_matches
- evidence (1.4, `docs/1.4/sql/functions/pattern_matching.md`, chunk:c-8aebaccd07421277b370): `regexp_full_match`
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-05@1.5 (dq-1e9371aa513e-1.5)

- type T2; target version 1.5; change `chg-2.0-05` (boundary 1.5->2.0)
- question: What is the equivalent expression for the PostgreSQL-style tilde operator in DuckDB?
- gold_short_answer (sql_expression): regexp_full_match
- accepted_aliases: regexp_matches
- other_version_answers: 1.4: regexp_full_match; 2.0: regexp_matches
- evidence (1.5, `docs/1.5/sql/functions/pattern_matching.md`, chunk:c-a9eb409c864818aa79b1): `regexp_full_match`
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-05@2.0 (dq-1e9371aa513e-2.0)

- type T2; target version 2.0; change `chg-2.0-05` (boundary 1.5->2.0)
- question: What is the equivalent expression for the PostgreSQL-style tilde operator in DuckDB?
- gold_short_answer (sql_expression): regexp_matches
- accepted_aliases: regexp_matches
- other_version_answers: 1.4: regexp_full_match; 1.5: regexp_full_match
- evidence (2.0, `docs/2.0/sql/functions/pattern_matching.md`, chunk:c-a47cfef3114ddb7e266f): `regexp_matches`
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-07@1.4 (dq-1cbb87f01104-1.4)

- type T2; target version 1.4; change `chg-2.0-08` (boundary 1.5->2.0)
- question: What data type does the preserve_identifier_case configuration option use in DuckDB?
- gold_short_answer (type_name): BOOLEAN
- accepted_aliases: varchar
- other_version_answers: 1.5: BOOLEAN; 2.0: VARCHAR
- evidence (1.4, `docs/1.4/sql/dialect/keywords_and_identifiers.md`, chunk:c-9f1cd641fcf4e2cb3dde): With the `preserve_identifier_case` configuration option set to `false`, all identifiers are turned into lowercase:
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-07@1.5 (dq-1cbb87f01104-1.5)

- type T2; target version 1.5; change `chg-2.0-08` (boundary 1.5->2.0)
- question: What data type does the preserve_identifier_case configuration option use in DuckDB?
- gold_short_answer (type_name): BOOLEAN
- accepted_aliases: varchar
- other_version_answers: 1.4: BOOLEAN; 2.0: VARCHAR
- evidence (1.5, `docs/1.5/sql/dialect/keywords_and_identifiers.md`, chunk:c-0416464e5bb9b86625b2): With the `preserve_identifier_case` configuration option set to `false`, all identifiers are turned into lowercase:
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T2-07@2.0 (dq-1cbb87f01104-2.0)

- type T2; target version 2.0; change `chg-2.0-08` (boundary 1.5->2.0)
- question: What data type does the preserve_identifier_case configuration option use in DuckDB?
- gold_short_answer (type_name): VARCHAR
- accepted_aliases: varchar
- other_version_answers: 1.4: BOOLEAN; 1.5: BOOLEAN
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:189-193): The [`preserve_identifier_case` setting]({% link docs/2.0/configuration/overview.md %}) changed from a `BOOLEAN` to a `VARCHAR` with the values `'preserve_case'` (default), `'lowercase'`, and `'uppercase'`.
- evidence (2.0, `docs/2.0/sql/dialect/keywords_and_identifiers.md`, chunk:c-f3619e8232ecc4ac6381): New In DuckDB 2.0, `preserve_identifier_case` is a `VARCHAR` option. Previously, it was a `BOOLEAN` option.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-01 (dq-578127284633)

- type T3; target version 2.0; change `chg-2.0-08` (boundary 1.5->2.0)
- question: What data type did the preserve_identifier_case setting change to in DuckDB 2.0?
- gold_short_answer (type_name): VARCHAR
- accepted_aliases: varchar
- evidence (2.0, `docs/2.0/operations_manual/migration.md`, src:docs/2.0/operations_manual/migration.md:189-193): The [`preserve_identifier_case` setting]({% link docs/2.0/configuration/overview.md %}) changed from a `BOOLEAN` to a `VARCHAR` with the values `'preserve_case'` (default), `'lowercase'`, and `'uppercase'`.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-02 (dq-65f263a0f5fc)

- type T3; target version 1.2; change `chg-1.2-01` (boundary ref->1.2)
- question: Does selecting a value from a MAP using bracket notation in DuckDB 1.2 return a LIST or an individual value?
- gold_short_answer (type_name): LIST
- accepted_aliases: a LIST
- evidence (1.2, `docs/1.2/sql/data_types/map.md`, chunk:c-2f9233cacdf3d2c2e8fd): Selecting from a `MAP` returns a `LIST` rather than an individual value, with an empty `LIST` meaning that the key was not found.
- restricted sources (reference only): release_post:_posts/2025-02-05-announcing-duckdb-120.md:29-31
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-06 (dq-960631fc32c8)

- type T3; target version 1.4; change `chg-1.4-02` (boundary 1.3->1.4)
- question: Does DuckDB 1.4 provide a function to convert milliseconds since the epoch into a timestamp?
- gold_short_answer (yes_no): yes
- accepted_aliases: true
- evidence (1.4, `docs/1.4/sql/functions/timestamp.md`, src:docs/1.4/sql/functions/timestamp.md:262-269): | **Description** | Converts milliseconds since the epoch to a timestamp. |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-07 (dq-e264164d1531)

- type T3; target version 1.4; change `chg-1.4-03` (boundary 1.3->1.4)
- question: Does DuckDB 1.4 provide an alias function named `struct_has` for checking if a `STRUCT` contains a specified entry?
- gold_short_answer (yes_no): yes
- accepted_aliases: True, y
- evidence (1.4, `docs/1.4/sql/functions/struct.md`, src:docs/1.4/sql/functions/struct.md:64-71): | **Alias** | `struct_has` |
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-09 (dq-be3c4bd5153e)

- type T3; target version 1.5; change `chg-1.5-02` (boundary 1.4->1.5)
- question: In which version of DuckDB was the SHOW SCHEMAS statement introduced?
- gold_short_answer (version): 1.5
- accepted_aliases: DuckDB 1.5, v1.5
- evidence (1.5, `docs/1.5/sql/statements/show.md`, src:docs/1.5/sql/statements/show.md:34-38): This statement was introduced in DuckDB 1.5.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`

### T3-10 (dq-da458a197e6d)

- type T3; target version 1.5; change `chg-1.5-03` (boundary 1.4->1.5)
- question: Which release introduced the GEOMETRY data type as a built-in type in DuckDB instead of requiring the spatial extension?
- gold_short_answer (version): 1.5
- accepted_aliases: DuckDB 1.5, v1.5
- evidence (1.5, `docs/1.5/sql/data_types/geometry.md`, src:docs/1.5/sql/data_types/geometry.md:10-12): The `GEOMETRY` type was part of the [`spatial` extension]({% link docs/1.5/core_extensions/spatial/overview.md %}) but became a built-in data type in DuckDB 1.5.
- V1: pass_pending_probe (schema pass, spans_verbatim pass, version_compatible pass, inventory pass, probe_consistency pending, near_duplicate pass)
- review batch 1: record the decision in `benchmark/review/pilot_review_records.jsonl`
