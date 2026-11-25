"""``vrag evidence validate``: exit codes 0/1/2, read-only, and no record text in the output."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.store import OBSERVATIONS_FILE, write_evidence

from .conftest import ROOT, make_claim, make_obs

MARKER = "RESTRICTED-MARKER fixture release text"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(["evidence", "validate", *args])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_no_evidence_yet_passes(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, out = _run(capsys, "--root", str(ROOT), "--evidence-dir", str(tmp_path))
    assert code == 0 and "RESULT: PASSED (0 warnings)" in out
    assert "doc_claims.jsonl does not exist yet" in out


def test_valid_observation_passes_and_nothing_is_written(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    write_evidence(tmp_path, observations=[make_obs()])
    before = _snapshot(tmp_path)
    code, out = _run(capsys, "--root", str(ROOT), "--evidence-dir", str(tmp_path))
    assert code == 0 and "runtime_observations=1" in out
    assert _snapshot(tmp_path) == before


def test_invalid_evidence_fails(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    write_evidence(tmp_path, observations=[make_obs(python_version="3.12.15")])
    code, out = _run(capsys, "--root", str(ROOT), "--evidence-dir", str(tmp_path))
    assert code == 1 and "[E-pin]" in out and "RESULT: FAILED (1 errors" in out
    (tmp_path / OBSERVATIONS_FILE).write_bytes(b"{oops\n")
    code, out = _run(capsys, "--root", str(ROOT), "--evidence-dir", str(tmp_path))
    assert code == 1 and "[E-load]" in out


def _root_without_cache(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "configs", root / "configs")
    shutil.copytree(ROOT / "data/manifest", root / "data/manifest")
    return root


def _manifest_claim(**over: Any):  # type: ignore[no-untyped-def]
    manifest, problems = load_manifest(ROOT / "data/manifest/corpus_manifest.json")
    assert manifest is not None and problems == []
    rec = next(r for r in manifest.release_notes.included if r.tag == "v1.3.2")
    return make_claim(
        source_kind="github_release",
        source_repo="duckdb/duckdb",
        source_commit=None,
        source_path=rec.tag,
        source_url=rec.source_url,
        source_sha256=rec.body_sha256,
        doc_version=rec.version,
        span_text=MARKER,
        span_char_start=0,
        span_char_end=len(MARKER),
        **over,
    )


def test_claims_without_a_cached_source_are_incomplete_and_never_printed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = _root_without_cache(tmp_path)
    evidence = tmp_path / "evidence"
    write_evidence(evidence, claims=[_manifest_claim()])
    before = _snapshot(tmp_path)
    code, out = _run(capsys, "--root", str(root), "--evidence-dir", str(evidence))
    assert code == 2 and "RESULT: INCOMPLETE (1 checks could not be run)" in out
    assert "cached source not available" in out
    assert MARKER not in out  # the restricted text is never printed
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("broken", ["config", "pins"])
def test_unreadable_inputs_are_incomplete(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, broken: str
) -> None:
    root = _root_without_cache(tmp_path)
    target = root / ("configs/corpus.toml" if broken == "config" else "configs/probes.toml")
    target.write_bytes(b"not = [valid toml")
    code, out = _run(capsys, "--root", str(root), "--evidence-dir", str(tmp_path / "ev"))
    label = "configuration" if broken == "config" else "wheel pins"
    assert code == 2 and f"RESULT: INCOMPLETE ({label} cannot be loaded)" in out


def test_unreadable_manifest_with_claims_is_incomplete(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = _root_without_cache(tmp_path)
    shutil.rmtree(root / "data/manifest")
    evidence = tmp_path / "evidence"
    write_evidence(evidence, claims=[make_claim()])
    code, out = _run(capsys, "--root", str(root), "--evidence-dir", str(evidence))
    assert code == 2 and "RESULT: INCOMPLETE (corpus manifest cannot be loaded)" in out
