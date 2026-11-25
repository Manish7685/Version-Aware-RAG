"""RAG-M1 behavioural probes. Run with an isolated interpreter: python -I rag_m1_probes.py"""
import json, platform, sys
import duckdb

SQL_PROBES = {
    "P01_map_bracket":        "SELECT map(['k'], ['v'])['k']",
    "P02_date_trunc_type":    "SELECT typeof(date_trunc('month', DATE '2026-03-27'))",
    "P03_float_div_zero":     "SELECT 1 / 0",
    "P04_int_div_zero":       "SELECT 1 // 0",
    "P05_arrow_lambda":       "SELECT list_transform([1, 2], x -> x + 1)",
    "P06_python_lambda":      "SELECT list_transform([1, 2], lambda x: x + 1)",
    "P07_merge_into": (
        "CREATE TABLE t(id INTEGER, v INTEGER); INSERT INTO t VALUES (1, 10);"
        "CREATE TABLE s(id INTEGER, v INTEGER); INSERT INTO s VALUES (1, 99);"
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET v = s.v;"
        "SELECT v FROM t"),
    "P08_untyped_null_column": (
        "CREATE TABLE n AS SELECT NULL AS x;"
        "SELECT column_type FROM (DESCRIBE n)"),
    "P09_control_scalar_subquery_multirow": "SELECT (SELECT * FROM range(2))",
}

def run_sql(sql: str) -> str:
    con = duckdb.connect()
    try:
        *setup, final = [s for s in sql.split(";") if s.strip()]
        for stmt in setup:
            con.execute(stmt)
        return repr(con.execute(final).fetchall()[0][0])
    except Exception as e:  # record the error type, not a crash
        return f"ERROR {type(e).__name__}: {str(e).splitlines()[0][:90]}"
    finally:
        con.close()

def run_python_relation_fetchone() -> str:
    con = duckdb.connect()
    try:
        return repr(con.sql("SELECT 42").fetchone())
    except Exception as e:
        return f"ERROR {type(e).__name__}: {str(e).splitlines()[0][:90]}"
    finally:
        con.close()

results = {name: run_sql(sql) for name, sql in SQL_PROBES.items()}
results["P10_python_relation_fetchone"] = run_python_relation_fetchone()
print(json.dumps({"duckdb": duckdb.__version__, "python": platform.python_version(),
                  "platform": platform.platform(), "results": results}))
