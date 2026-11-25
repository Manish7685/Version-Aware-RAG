"""``vrag benchmark inventory validate`` exit codes without the corpus cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from version_aware_rag.cli import main

ROOT = Path(__file__).resolve().parents[3]


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(["benchmark", "inventory", "validate", *args])
    return code, capsys.readouterr().out


def test_invalid_inventory_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    bad = tmp_path / "inv.jsonl"
    bad.write_bytes(b'{"change_id": "chg-1.5-01"}\n')
    code, out = _run(capsys, "--root", str(ROOT), "--inventory", str(bad))
    assert code == 1 and "invalid record" in out and "RESULT: FAILED" in out


def test_missing_inputs_are_incomplete(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out = _run(capsys, "--root", str(tmp_path))
    assert code == 2 and "configuration or probe suite cannot be loaded" in out
    code, out = _run(capsys, "--root", str(ROOT), "--inventory", str(tmp_path / "none.jsonl"))
    assert code == 2 and "cannot be read" in out


def test_an_empty_inventory_fails_boundary_coverage(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    empty = tmp_path / "inv.jsonl"
    empty.write_bytes(b"")
    code, out = _run(capsys, "--root", str(ROOT), "--inventory", str(empty))
    assert code == 1 and "records: 0" in out
    for boundary in ("1.2->1.3", "1.3->1.4", "1.4->1.5", "1.5->2.0"):
        assert f"no pilot candidate covers the corpus boundary {boundary}" in out
