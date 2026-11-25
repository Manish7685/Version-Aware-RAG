"""IR-3 probe executor (RAG-M4 S1b). Run with an isolated interpreter inside one hash-pinned
DuckDB environment: ``python -I run_probe_suite.py < suite.json``.

Standard library and ``duckdb`` only. Reads the suite on stdin as JSON
``{"probes": [{"id", "language", "capture", "code"}, ...]}`` and writes one JSON object to stdout:
``{"duckdb", "python", "platform", "results": {id: {"status", "value"}}}``.

Every probe runs on a fresh in-memory connection. A result is the ``repr`` of the first value
(``first_value``), of all rows (``all_rows``) or of the Python expression (``repr``). An error is
captured in full as ``"<ExceptionType>: <complete message>"``: never truncated, multi-line
messages kept exactly. The RAG-M1 script (``rag_m1_probes.py``) is separate and unchanged.
"""

import json
import platform
import sys

import duckdb


def run_probe(probe):
    con = duckdb.connect()
    try:
        if probe["language"] == "sql":
            *setup, final = [s for s in probe["code"].split(";") if s.strip()]
            for statement in setup:
                con.execute(statement)
            rows = con.execute(final).fetchall()
            if probe["capture"] == "first_value":
                value = repr(rows[0][0])
            elif probe["capture"] == "all_rows":
                value = repr(rows)
            else:
                raise ValueError(f"unknown capture {probe['capture']!r}")
        elif probe["language"] == "python":
            # Repository-controlled probe code: one expression, no builtins, only the connection.
            value = repr(eval(probe["code"], {"__builtins__": {}}, {"con": con}))  # noqa: S307
        else:
            raise ValueError(f"unknown language {probe['language']!r}")
        return {"status": "ok", "value": value}
    except Exception as exc:  # record the full error, not a crash
        return {"status": "error", "value": f"{type(exc).__name__}: {exc}"}
    finally:
        con.close()


def main():
    suite = json.loads(sys.stdin.read())
    results = {probe["id"]: run_probe(probe) for probe in suite["probes"]}
    out = {
        "duckdb": duckdb.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "results": results,
    }
    sys.stdout.write(json.dumps(out, sort_keys=True, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    main()
