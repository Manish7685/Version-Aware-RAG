"""``scripts/probes/run_probe_suite.py`` with a stub ``duckdb`` module: capture modes and full,
untruncated multi-line error capture (IR-3)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from typing import Any, ClassVar

import pytest

from .conftest import ROOT

EXECUTOR = ROOT / "scripts/probes/run_probe_suite.py"
LONG_ERROR = (
    "Binder Error: Deprecated lambda arrow (->) detected. Please transition to the new lambda "
    "SYNTHETIC filler, not DuckDB or release text, keeping this error long; "
    "lambda_syntax='ENABLE_SINGLE_ARROW' to revert to the deprecated behavior.\n"
    "LINE 1: SELECT list_transform([1, 2], x -> x + 1)\n                                     ^"
    + " trailing context "
    * 20  # far longer than the old 90-character cut
)


class BinderException(Exception):
    pass


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...]:
        return self._rows[0]


class _Connection:
    executed: ClassVar[list[str]] = []

    def execute(self, sql: str) -> _Cursor:
        _Connection.executed.append(sql.strip())
        if "x -> x" in sql:
            raise BinderException(LONG_ERROR)
        if "two rows" in sql:
            return _Cursor([(1, "a", None, "v9.9.9"), (2, "b", 7, "v9.9.9")])
        return _Cursor([(0,)])

    def sql(self, sql: str) -> _Cursor:
        return _Cursor([(42,)])

    def close(self) -> None:
        pass


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    stub = types.ModuleType("duckdb")
    stub.__version__ = "9.9.9"  # type: ignore[attr-defined]
    stub.connect = _Connection  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "duckdb", stub)
    spec = importlib.util.spec_from_file_location("run_probe_suite", EXECUTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _Connection.executed = []
    return module


def _probe(code: str, capture: str = "first_value", language: str = "sql") -> dict[str, str]:
    return {"id": "X", "language": language, "capture": capture, "code": code}


def test_errors_are_captured_in_full_never_truncated(executor: types.ModuleType) -> None:
    out = executor.run_probe(_probe("SELECT list_transform([1, 2], x -> x + 1)"))
    assert out == {"status": "error", "value": f"BinderException: {LONG_ERROR}"}
    assert "\n" in out["value"] and len(out["value"]) > 300


def test_capture_modes(executor: types.ModuleType) -> None:
    assert executor.run_probe(_probe("SELECT 0")) == {"status": "ok", "value": "0"}
    rows = executor.run_probe(_probe("SELECT two rows", capture="all_rows"))
    assert rows == {"status": "ok", "value": "[(1, 'a', None, 'v9.9.9'), (2, 'b', 7, 'v9.9.9')]"}
    py = executor.run_probe(_probe('con.sql("SELECT 42").fetchone()', "repr", "python"))
    assert py == {"status": "ok", "value": "(42,)"}


def test_setup_statements_run_before_the_final_query(executor: types.ModuleType) -> None:
    executor.run_probe(_probe("CREATE TABLE t(i INTEGER); INSERT INTO t VALUES (1);SELECT 0"))
    assert _Connection.executed == [
        "CREATE TABLE t(i INTEGER)",
        "INSERT INTO t VALUES (1)",
        "SELECT 0",
    ]


def test_python_probes_get_no_builtins(executor: types.ModuleType) -> None:
    out = executor.run_probe(_probe("open('x')", "repr", "python"))
    assert out["status"] == "error" and out["value"].startswith("NameError:")


def test_main_round_trips_every_character_exactly(
    executor: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    suite = {"probes": [_probe("SELECT list_transform([1, 2], x -> x + 1)"), _probe("SELECT 0")]}
    suite["probes"][1]["id"] = "Y"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(suite)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    executor.main()
    text = out.getvalue()
    assert text.isascii()  # safe on any console encoding; json escapes keep the text exact
    data = json.loads(text)
    assert data["duckdb"] == "9.9.9" and set(data) == {"duckdb", "python", "platform", "results"}
    assert data["results"]["X"]["value"] == f"BinderException: {LONG_ERROR}"
    assert data["results"]["Y"] == {"status": "ok", "value": "0"}


def test_executor_imports_only_the_standard_library_and_duckdb() -> None:
    import ast

    tree = ast.parse(Path(EXECUTOR).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported == {"json", "platform", "sys", "duckdb"}
