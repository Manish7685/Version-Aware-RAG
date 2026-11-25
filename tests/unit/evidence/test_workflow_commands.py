"""The S1c CLI: claim add, observations ingest, conflict suggest/add/review. Fixture sources only
(the corpus index is injected); exit codes 0/1/2; nothing written unless 0; no span text printed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus.acquire import GitRepo
from version_aware_rag.evidence import workflow_commands
from version_aware_rag.evidence.store import load_evidence
from version_aware_rag.probes.pins import load_pins

from .conftest import FIXTURE_SOURCE, ROOT, SPAN, fixture_index, make_obs

HEAD = GitRepo(ROOT).run("rev-parse", "HEAD").decode("utf-8").strip()


@pytest.fixture
def evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "evidence"
    pins = load_pins(ROOT / "configs/probes.toml")
    monkeypatch.setattr(workflow_commands, "_inputs", lambda args: (target, fixture_index(), pins))
    return target


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(["evidence", *args, "--root", str(ROOT)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def _snapshot(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def _add_claim(capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    start = FIXTURE_SOURCE.index(SPAN)
    return _run(
        capsys,
        "claim",
        "add",
        "--source-kind",
        "docs_page",
        "--source-path",
        "docs/1.4/sql/functions/date.md",
        "--start",
        str(start),
        "--end",
        str(start + len(SPAN)),
        "--summary",
        "date_trunc on DATE returns DATE",
        "--url",
        "https://duckdb.org/docs/1.4/sql/functions/date.html",
        "--heading",
        "Title",
        *extra,
    )


def test_claim_add(evidence: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _add_claim(capsys, "--claimed-value", "'DATE'")
    assert code == 0 and "CLAIM ADDED dc-" in out and "(V1; span text in the committed file)" in out
    assert SPAN not in out  # never printed
    claim = load_evidence(evidence).evidence.claims[0]
    assert claim.span_text == SPAN and claim.heading_path == ["Title"]
    code, out = _add_claim(capsys)
    assert code == 1 and "already exists" in out and "nothing written" in out


def test_claim_add_refuses_bad_offsets(evidence: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _run(
        capsys,
        "claim",
        "add",
        "--source-kind",
        "docs_page",
        "--source-path",
        "docs/1.4/sql/functions/date.md",
        "--start",
        "0",
        "--end",
        "999999",
        "--summary",
        "s",
        "--url",
        "https://duckdb.org/",
    )
    assert code == 1 and "outside the source" in out and _snapshot(evidence) == {}


def _ingest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, *observations: Any
) -> tuple[int, str]:
    file = tmp_path / "incoming.jsonl"
    file.write_bytes("".join(o.model_dump_json() + "\n" for o in observations).encode())
    return _run(capsys, "observations", "ingest", str(file))


def test_full_workflow_through_the_cli(
    evidence: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert _add_claim(capsys, "--claimed-value", "'DATE'")[0] == 0
    claim_id = load_evidence(evidence).evidence.claims[0].claim_id
    agree, differ = (
        make_obs(runner_commit=HEAD),
        make_obs(probe_id="P03", observed_value="'TIMESTAMP'", runner_commit=HEAD),
    )
    code, out = _ingest(capsys, tmp_path, agree, differ)
    assert code == 0 and "OBSERVATIONS INGESTED (2 read; 2 in total)" in out
    code, out = _run(capsys, "conflict", "suggest", "--claim", claim_id, "--obs", agree.obs_id)
    assert code == 0 and "NO DISAGREEMENT" in out
    assert load_evidence(evidence).evidence.conflicts == ()
    code, out = _run(
        capsys,
        "conflict",
        "suggest",
        "--claim",
        claim_id,
        "--obs",
        agree.obs_id,
        "--obs",
        differ.obs_id,
    )
    assert code == 0 and "SUSPECTED CONFLICT ec-" in out and "owner review needed" in out
    conflict = load_evidence(evidence).evidence.conflicts[0]
    assert conflict.status == "suspected" and conflict.obs_ids == [differ.obs_id]
    before = _snapshot(evidence)
    code, out = _run(
        capsys,
        "conflict",
        "review",
        conflict.conflict_id,
        "--status",
        "confirmed",
        "--reviewer",
        "gemini",
    )
    assert code == 1 and "only the owner" in out and _snapshot(evidence) == before
    code, out = _run(
        capsys,
        "conflict",
        "review",
        conflict.conflict_id,
        "--status",
        "dismissed",
        "--reviewer",
        "owner",
        "--note",
        "probe P03 does not test this claim",
    )
    assert code == 0 and f"CONFLICT DISMISSED {conflict.conflict_id}" in out
    final = load_evidence(evidence).evidence
    assert final.conflicts[0].status == "dismissed" and final.conflicts[0].reviewed_by == "owner"
    assert final.claims[0].span_text == SPAN and len(final.observations) == 2  # untouched


def test_owner_conflict_add_is_suspected(
    evidence: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _add_claim(capsys)
    claim_id = load_evidence(evidence).evidence.claims[0].claim_id
    obs = make_obs(runner_commit=HEAD)
    _ingest(capsys, tmp_path, obs)
    code, out = _run(
        capsys,
        "conflict",
        "add",
        "--claim",
        claim_id,
        "--obs",
        obs.obs_id,
        "--description",
        "the page and the 1.4.5 package disagree",
    )
    assert code == 0 and "detected by owner" in out
    conflict = load_evidence(evidence).evidence.conflicts[0]
    assert (conflict.status, conflict.detected_by) == ("suspected", "owner")


def test_ingest_refusals(
    evidence: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = _ingest(capsys, tmp_path, make_obs(runner_commit="f" * 40))
    assert code == 1 and "not a commit of this repository" in out and _snapshot(evidence) == {}
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b'{"obs_id": "x"}\n')
    code, out = _run(capsys, "observations", "ingest", str(bad))
    assert code == 1 and "bad.jsonl:1: invalid observation" in out
    code, out = _run(capsys, "observations", "ingest", str(tmp_path / "missing.jsonl"))
    assert code == 2 and "cannot be loaded" in out


def test_unloadable_inputs_are_exit_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(
        [
            "evidence",
            "conflict",
            "review",
            "ec-000000000000",
            "--status",
            "confirmed",
            "--reviewer",
            "owner",
            "--root",
            str(tmp_path),
        ]
    )
    assert code == 2 and "configuration or pins" in capsys.readouterr().out
