"""The IR-3 suite definition, probe hashes and the per-run control rule (D3)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from version_aware_rag.probes.pins import load_pins
from version_aware_rag.probes.suite import (
    VERSION_PLACEHOLDER,
    ProbeDefinition,
    ProbeResult,
    ProbeSuite,
    comparison_value,
    evaluate_controls,
    load_suite,
)

from .conftest import CONFIG, P09_ERROR, ROOT, c2_value, good_results

RAG_M1 = ROOT / "scripts/probes/rag_m1_probes.py"


def _rag_m1_sql() -> dict[str, str]:
    """SQL_PROBES from the hash-pinned RAG-M1 script, read without running it."""
    tree = ast.parse(RAG_M1.read_text(encoding="utf-8"))
    node = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SQL_PROBES"
    )
    return ast.literal_eval(node)  # type: ignore[no-any-return]


def test_suite_carries_the_rag_m1_probes_forward_exactly(suite: ProbeSuite) -> None:
    rag_m1 = _rag_m1_sql()
    by_id = {p.id: p for p in suite.probes}
    ids = [p.id for p in suite.probes]
    assert ids[:12] == [f"P{i:02d}" for i in range(1, 11)] + ["C1", "C2"]  # S1b suite, unchanged
    assert ids[12:] == [f"P{i:02d}" for i in range(11, 27)]  # S2 pilot probes, appended
    for probe in suite.probes:
        if probe.language == "sql" and probe.rag_m1_name:
            assert probe.code == rag_m1[probe.rag_m1_name], probe.id  # identical SQL
            assert probe.capture == "first_value"  # RAG-M1: repr(fetchall()[0][0])
    assert set(rag_m1) == {
        p.rag_m1_name for p in suite.probes if p.language == "sql" and p.rag_m1_name
    }
    assert by_id["P10"].code in RAG_M1.read_text(
        encoding="utf-8"
    )  # con.sql("SELECT 42").fetchone()
    assert [p.id for p in suite.controls] == ["P09", "C1", "C2"]
    assert by_id["P10"].kind == "python_api" and by_id["P09"].kind == "control"


def test_controls_have_deterministic_expectations(suite: ProbeSuite) -> None:
    by_id = {p.id: p for p in suite.probes}
    assert (by_id["C1"].expected_status, by_id["C1"].expected_value) == ("ok", "0")
    c2 = by_id["C2"]
    assert c2.normalize == "package_version" and c2.expected_status == "ok"
    assert (
        c2.expected_value == "[(1, 'a', None, '<duckdb-version>'), (2, 'b', 7, '<duckdb-version>')]"
    )
    assert by_id["P09"].normalize is None and by_id["C1"].normalize is None


def test_rag_m1_script_is_untouched() -> None:
    pinned = "732c93e29abc589710a7d1438c8f32db64e1f3a9163968a23d845f5705ad7afc"
    import hashlib

    assert hashlib.sha256(RAG_M1.read_bytes()).hexdigest() == pinned


def test_probe_sha256_covers_exactly_the_executable_definition(suite: ProbeSuite) -> None:
    p = suite.probes[0]
    assert re.fullmatch(r"[0-9a-f]{64}", p.sha256)
    assert p.model_copy(update={"rag_m1_name": "x"}).sha256 == p.sha256  # labels do not count
    for change in ({"code": p.code + " "}, {"capture": "all_rows"}):
        assert p.model_copy(update=change).sha256 != p.sha256
    assert len({q.sha256 for q in suite.probes}) == len(suite.probes)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"language": "python", "capture": "first_value"}, "python probes capture repr"),
        ({"kind": "sql", "expected_status": "ok", "expected_value": "0"}, "only controls"),
        ({"kind": "control", "expected_status": "ok"}, "set together"),
        ({"kind": "control", "normalize": "package_version"}, "needs a declared expected"),
        ({"capture": "everything"}, "capture"),
    ],
)
def test_definition_rules(data: dict[str, object], message: str) -> None:
    base: dict[str, object] = {
        "id": "X1",
        "kind": "sql",
        "language": "sql",
        "capture": "first_value",
        "code": "SELECT 1",
    }
    with pytest.raises(ValidationError, match=re.escape(message)):
        ProbeDefinition.model_validate({**base, **data})


def test_pins_loader_accepts_the_suite_but_rejects_unknown_keys(tmp_path: Path) -> None:
    assert len(load_pins(CONFIG).packages) == 6
    bad = tmp_path / "probes.toml"
    bad.write_bytes(b'surprise = "x"\n' + CONFIG.read_bytes())  # a top-level key
    with pytest.raises(ValueError, match="unknown keys"):
        load_pins(bad)
    # Appended after the last [[probes]] table, TOML puts the key inside that probe entry,
    # which the suite model rejects.
    inside = tmp_path / "inside.toml"
    inside.write_bytes(CONFIG.read_bytes() + b'\nsurprise = "x"\n')
    with pytest.raises(ValidationError, match="surprise"):
        load_suite(inside)


# ---------------------------------------------------------------- C2: only the version identifier


def _c2(suite: ProbeSuite) -> ProbeDefinition:
    return next(p for p in suite.probes if p.id == "C2")


def test_c2_replaces_only_the_packages_own_version_identifier(suite: ProbeSuite) -> None:
    c2 = _c2(suite)
    raw = "[(1, 'a', None, 'v1.4.5'), (2, 'b', 7, 'v1.4.5')]"
    assert comparison_value(c2, ProbeResult("ok", raw), "1.4.5") == (
        f"[(1, 'a', None, '{VERSION_PLACEHOLDER}'), (2, 'b', 7, '{VERSION_PLACEHOLDER}')]"
    )
    untouched = [
        "'1.4.5'",  # the bare version number is not the identifier
        "'v1.4.50'",  # a longer version
        "'xv1.4.5'",  # inside a longer token
        "'v1.4.5.1'",
        "'v1.5.6'",  # another package's identifier
        "7, 7.0, Decimal('7')",  # numbers are never normalised
    ]
    for value in untouched:
        assert comparison_value(c2, ProbeResult("ok", value), "1.4.5") == value


def test_only_normalised_controls_are_normalised(suite: ProbeSuite) -> None:
    for probe in suite.controls:
        if probe.normalize is None:
            value = "v1.4.5 appears here"
            assert comparison_value(probe, ProbeResult("ok", value), "1.4.5") == value


# ---------------------------------------------------------------- the control rule


def _results(suite: ProbeSuite, pins_versions: list[str]) -> dict[str, dict[str, ProbeResult]]:
    return {v: good_results(v, suite) for v in pins_versions}


VERSIONS = ["1.1.3", "1.2.2", "1.3.2", "1.4.5", "1.5.6", "2.0.1"]


def test_identical_controls_make_a_valid_run(suite: ProbeSuite) -> None:
    assert evaluate_controls(suite, _results(suite, VERSIONS)) == []


@pytest.mark.parametrize(
    ("probe", "version", "result", "reason"),
    [
        (
            "P09",
            "1.3.2",
            ProbeResult("error", P09_ERROR.replace("single row", "single  row")),
            "differs",
        ),
        ("P09", "2.0.1", ProbeResult("error", P09_ERROR.split("\n")[0]), "differs"),  # truncated
        ("P09", "1.1.3", ProbeResult("ok", "0"), "differs"),
        ("C1", "1.5.6", ProbeResult("ok", "1"), "differs"),
        ("C1", "1.5.6", ProbeResult("error", "0"), "differs"),
        ("C2", "1.4.5", ProbeResult("ok", c2_value("1.4.5").replace(", 7,", ", 8,")), "differs"),
        ("C2", "1.4.5", ProbeResult("ok", c2_value("1.5.6")), "differs"),  # another package's id
        ("C2", "1.4.5", ProbeResult("ok", c2_value("1.4.5").replace("None", "'NULL'")), "differs"),
    ],
)
def test_any_control_difference_invalidates_the_run(
    suite: ProbeSuite, probe: str, version: str, result: ProbeResult, reason: str
) -> None:
    results = _results(suite, VERSIONS)
    results[version][probe] = result
    failures = evaluate_controls(suite, results)
    assert failures and all(f.probe_id == probe for f in failures)
    assert any(reason in f.reason for f in failures)


def test_identical_but_unexpected_controls_also_invalidate_the_run(suite: ProbeSuite) -> None:
    results = _results(suite, VERSIONS)
    for v in VERSIONS:
        results[v]["C1"] = ProbeResult("ok", "1")  # the same everywhere, but not the expected 0
    failures = evaluate_controls(suite, results)
    assert [f.probe_id for f in failures] == ["C1"]
    assert "not the declared expected result in 1.1.3, 1.2.2" in failures[0].reason


def test_change_probes_may_differ_across_versions(suite: ProbeSuite) -> None:
    results = _results(suite, VERSIONS)
    results["2.0.1"]["P04"] = ProbeResult("error", "InvalidInputException: Division by zero")
    assert evaluate_controls(suite, results) == []
