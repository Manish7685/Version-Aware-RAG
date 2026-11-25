"""Deterministic JSONL storage and the restricted-text sidecar (owner decision D1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from version_aware_rag.evidence.models import DocClaim
from version_aware_rag.evidence.store import (
    CLAIMS_FILE,
    CONFLICTS_FILE,
    OBSERVATIONS_FILE,
    SIDECAR_FILE,
    EvidenceSet,
    EvidenceStoreError,
    load_evidence,
    write_evidence,
)

from .conftest import make_claim, make_conflict, make_obs

RESTRICTED_TEXT = "RESTRICTED-MARKER release text that must never be committed"


def _restricted_claim() -> DocClaim:
    return make_claim(
        source_kind="github_release",
        source_repo="duckdb/duckdb",
        source_commit=None,
        source_path="v1.3.2",
        source_url="https://github.com/duckdb/duckdb/releases/tag/v1.3.2",
        doc_version="1.3",
        introduced_in="1.3.2",
        span_text=RESTRICTED_TEXT,
        span_char_start=10,
        span_char_end=10 + len(RESTRICTED_TEXT),
    )


def _sample() -> EvidenceSet:
    claim = make_claim()
    obs_a = make_obs()
    obs_b = make_obs(probe_id="P03", probe_kind="sql", observed_value="inf")
    return EvidenceSet(
        claims=(claim, _restricted_claim()),
        observations=(obs_b, obs_a),
        conflicts=(make_conflict(claim, [obs_a]),),
    )


def _write(tmp: Path, ev: EvidenceSet) -> None:
    write_evidence(tmp, claims=ev.claims, observations=ev.observations, conflicts=ev.conflicts)


def _files(tmp: Path) -> dict[str, bytes]:
    return {
        n: (tmp / n).read_bytes()
        for n in (CLAIMS_FILE, OBSERVATIONS_FILE, CONFLICTS_FILE, SIDECAR_FILE)
    }


def test_round_trip_is_exact_and_deterministic(tmp_path: Path) -> None:
    ev = _sample()
    _write(tmp_path / "a", ev)
    reversed_ev = EvidenceSet(
        tuple(reversed(ev.claims)), tuple(reversed(ev.observations)), ev.conflicts
    )
    _write(tmp_path / "b", reversed_ev)
    assert _files(tmp_path / "a") == _files(tmp_path / "b")  # input order does not matter
    loaded = load_evidence(tmp_path / "a")
    assert loaded.problems == [] and loaded.absent == []
    assert sorted(loaded.evidence.claims, key=lambda c: c.claim_id) == sorted(
        ev.claims, key=lambda c: c.claim_id
    )
    assert set(loaded.evidence.observations) == set(ev.observations)
    assert loaded.evidence.conflicts == ev.conflicts
    for name, data in _files(tmp_path / "a").items():
        text = data.decode("utf-8")
        assert "\r" not in text and (text == "" or text.endswith("\n")), name
        ids = [
            next(v for k, v in json.loads(line).items() if k.endswith("_id"))
            for line in text.splitlines()
        ]
        assert ids == sorted(ids), name


def test_restricted_text_is_only_in_the_local_sidecar(tmp_path: Path) -> None:
    _write(tmp_path, _sample())
    committed = b"".join(
        (tmp_path / n).read_bytes() for n in (CLAIMS_FILE, OBSERVATIONS_FILE, CONFLICTS_FILE)
    )
    assert RESTRICTED_TEXT.encode() not in committed
    restricted_line = next(
        json.loads(line)
        for line in (tmp_path / CLAIMS_FILE).read_text("utf-8").splitlines()
        if json.loads(line)["source_kind"] == "github_release"
    )
    assert "span_text" not in restricted_line
    assert RESTRICTED_TEXT in (tmp_path / SIDECAR_FILE).read_text("utf-8")
    unrestricted = [json.loads(x) for x in (tmp_path / CLAIMS_FILE).read_text("utf-8").splitlines()]
    assert any("span_text" in d for d in unrestricted)  # docs text is committed (MIT)


def _problems(tmp: Path) -> list[str]:
    return [str(p) for p in load_evidence(tmp).problems]


def test_restricted_span_text_in_the_committed_file_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, _sample())
    path = tmp_path / CLAIMS_FILE
    lines = path.read_text("utf-8").splitlines()
    patched = []
    for line in lines:
        d = json.loads(line)
        if d["source_kind"] == "github_release":
            d["span_text"] = RESTRICTED_TEXT
        patched.append(json.dumps(d, sort_keys=True, ensure_ascii=False))
    path.write_text("\n".join(patched) + "\n", encoding="utf-8", newline="\n")
    problems = _problems(tmp_path)
    assert any("restricted span_text must not be in the committed file" in p for p in problems)
    assert not any(RESTRICTED_TEXT in p for p in problems)


@pytest.mark.parametrize("sidecar", ["missing file", "empty file", "other claim"])
def test_missing_sidecar_text_fails(tmp_path: Path, sidecar: str) -> None:
    _write(tmp_path, _sample())
    side = tmp_path / SIDECAR_FILE
    if sidecar == "missing file":
        side.unlink()
    elif sidecar == "empty file":
        side.write_bytes(b"")
    else:
        side.write_bytes(
            (json.dumps({"claim_id": "dc-000000000000", "span_text": "x"}) + "\n").encode()
        )
    problems = _problems(tmp_path)
    assert any("restricted span text is missing from the local sidecar" in p for p in problems)
    if sidecar == "other claim":
        assert any("unexpected entry dc-000000000000 (no such claim)" in p for p in problems)


def test_sidecar_entry_for_an_unrestricted_claim_is_rejected(tmp_path: Path) -> None:
    ev = _sample()
    _write(tmp_path, ev)
    docs_claim = next(c for c in ev.claims if c.source_kind == "docs_page")
    with (tmp_path / SIDECAR_FILE).open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"claim_id": docs_claim.claim_id, "span_text": "x"}) + "\n")
    assert any("its source is not restricted" in p for p in _problems(tmp_path))


def test_mismatched_sidecar_text_fails_validation(tmp_path: Path) -> None:
    _write(tmp_path, _sample())
    side = tmp_path / SIDECAR_FILE
    # A same-length edit (offsets still consistent): the claim_id, a hash of the text, breaks.
    side.write_bytes(side.read_bytes().replace(b"RESTRICTED-MARKER", b"EDITED-MARKER-XYZ"))
    problems = _problems(tmp_path)
    assert any("claim_id" in p for p in problems)
    assert not any("EDITED-MARKER-XYZ" in p or RESTRICTED_TEXT in p for p in problems)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("crlf", "CR characters"),
        ("no final newline", "does not end with a newline"),
        ("not json", "not valid JSON"),
        ("not object", "not a JSON object"),
        ("reformatted", "not in canonical form"),
        ("unsorted", "not sorted by id"),
        ("duplicate", "duplicate id"),
        ("invalid record", "invalid record"),
        ("not utf-8", "not UTF-8"),
    ],
)
def test_non_canonical_or_broken_files_are_problems(
    tmp_path: Path, damage: str, message: str
) -> None:
    _write(tmp_path, _sample())
    path = tmp_path / OBSERVATIONS_FILE
    text = path.read_text("utf-8")
    lines = text.splitlines()
    damaged: dict[str, str | bytes] = {
        "crlf": text.replace("\n", "\r\n"),
        "no final newline": text.rstrip("\n"),
        "not json": text + "{oops\n",
        "not object": text + "[1, 2]\n",
        "reformatted": text.replace('", "', '",  "', 1),
        "unsorted": "\n".join(reversed(lines)) + "\n",
        "duplicate": text + lines[0] + "\n",
        "invalid record": text.replace('"sql"', '"SECRET-VALUE"', 1),
        "not utf-8": b"\xff\xfe" + text.encode(),
    }
    new = damaged[damage]
    if isinstance(new, bytes):
        path.write_bytes(new)
    else:
        path.write_bytes(new.encode("utf-8"))
    problems = _problems(tmp_path)
    assert any(message in p for p in problems), problems
    assert not any("SECRET-VALUE" in p for p in problems)  # no input values in messages


def test_absent_files_are_empty_not_errors(tmp_path: Path) -> None:
    loaded = load_evidence(tmp_path)
    assert loaded.problems == []
    assert loaded.absent == [CLAIMS_FILE, OBSERVATIONS_FILE, CONFLICTS_FILE]
    assert loaded.evidence == EvidenceSet()


def test_writing_duplicate_ids_is_refused(tmp_path: Path) -> None:
    obs = make_obs()
    with pytest.raises(EvidenceStoreError, match="duplicate observation id"):
        write_evidence(tmp_path, observations=[obs, obs])
