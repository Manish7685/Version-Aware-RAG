"""The probe artefacts copied from RAG-M1 must stay byte-identical to the version manifest."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

PROBES = Path("scripts/probes")

# Values recorded in docs/design/RAG-M1_version_manifest.md (§4, §5.1).
PINNED_SHA256 = {
    PROBES / "rag_m1_probes.py": "732c93e29abc589710a7d1438c8f32db64e1f3a9163968a23d845f5705ad7afc",
    PROBES / "expected/rag_m1_windows_probe_results.jsonl": (
        "65cb54b8aa7f0ae7389d809ce3fcecca550ab74fc1505cf4f2faa4848e50621f"
    ),
}

# Wheel hashes from manifest §2: version -> (win_amd64, manylinux x86_64).
WHEEL_SHA256 = {
    "1.1.3": (
        "e86006958e84c5c02f08f9b96f4bc26990514eab329b1b4f71049b3727ce5989",
        "4ebf5f60ddbd65c13e77cddb85fe4af671d31b851f125a4d002a313696af43f1",
    ),
    "1.2.2": (
        "b1c0c4d737fd2ab9681e4e78b9f361e0a827916a730e84fa91e76dca451b14d5",
        "e5c1556775a9ebaa49b5c8d64718f155ac3e05b34a49e9c99443cf105e8b0371",
    ),
    "1.3.2": (
        "11af73963ae174aafd90ea45fb0317f1b2e28a7f1d9902819d47c67cc957d49c",
        "36abdfe0d1704fe09b08d233165f312dad7d7d0ecaaca5fb3bb869f4838a2d0b",
    ),
    "1.4.5": (
        "6b8d992d957c89e83d697756f6c5b5aea910d6bf16e2666da4c508f891932ae2",
        "aa294d028c149ca21110e366eaffcb4fc9ab11d7d203d50f7bc49a07ab34b960",
    ),
    "1.5.6": (
        "09ff51b230219f0d8b47fc8a1e17fb595ba9fab0c3d96a6de4d00b8ff86b3cf1",
        "bc9619ed7d4ffa117b5155d84b44794366bb6635178d78ed5e13a6024845c757",
    ),
    "2.0.1": (
        "67a7dab509897ec1f1d46283783edb8fdc005f02fde6e263fb77a8a3bf7f1b85",
        "39ffeb136abed770a496e5592a464f18a07b1543e7ddb1ff6453215e179f1647",
    ),
}


@pytest.mark.parametrize("relpath", sorted(PINNED_SHA256), ids=lambda p: p.as_posix())
def test_pinned_file_hash(repo_root: Path, relpath: Path) -> None:
    digest = hashlib.sha256((repo_root / relpath).read_bytes()).hexdigest()
    assert digest == PINNED_SHA256[relpath]


@pytest.mark.parametrize("version", sorted(WHEEL_SHA256))
def test_requirement_file_pins_manifest_wheels(repo_root: Path, version: str) -> None:
    text = (repo_root / PROBES / f"requirements/duckdb-{version}.txt").read_text(encoding="utf-8")
    assert re.match(rf"^duckdb=={re.escape(version)} ", text)
    assert set(re.findall(r"--hash=sha256:([0-9a-f]{64})", text)) == set(WHEEL_SHA256[version])


def test_runner_script_pins_the_same_probe_hash(repo_root: Path) -> None:
    runner = (repo_root / PROBES / "run_probes.sh").read_text(encoding="utf-8")
    assert PINNED_SHA256[PROBES / "rag_m1_probes.py"] in runner
    assert "\r\n" not in runner  # CRLF would break bash on Linux CI
