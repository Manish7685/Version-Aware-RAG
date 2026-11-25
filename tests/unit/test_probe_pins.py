"""``configs/probes.toml`` equals the RAG-M1 manifest §2 pins and the hash-pinned requirements
files used by the probe runner (owner decision D2)."""

from __future__ import annotations

import re
from pathlib import Path

from version_aware_rag.probes.pins import load_pins

ROOT = Path(__file__).resolve().parents[2]


def _manifest_table() -> dict[str, tuple[str, str, str]]:
    """version -> (docs version, windows sha256, linux sha256), from the approved manifest §2."""
    text = (ROOT / "docs/design/RAG-M1_version_manifest.md").read_text(encoding="utf-8")
    section = text[text.index("## 2. Behavioural-test packages") : text.index("## 3. Toolchain")]
    row = (
        r"^\| (?:\(ref\) )?([\d.]+)(?: LTS)? \| `duckdb==([\d.]+)` \| [\d-]+ "
        r"\| `([0-9a-f]{64})` \| `([0-9a-f]{64})` \|$"
    )
    rows = re.findall(row, section, flags=re.MULTILINE)
    return {v: ("ref" if docs == "1.1" else docs, win, linux) for docs, v, win, linux in rows}


def test_pins_equal_the_rag_m1_manifest() -> None:
    pins = load_pins(ROOT / "configs/probes.toml")
    table = _manifest_table()
    assert len(table) == 6
    assert {
        p.version: (p.applies_to_doc_version, p.wheel_sha256.windows, p.wheel_sha256.linux)
        for p in pins.packages
    } == table
    assert pins.python_version == "3.12.16"


def test_pins_equal_the_requirements_files_and_the_runner_versions() -> None:
    pins = load_pins(ROOT / "configs/probes.toml")
    for p in pins.packages:
        req = (ROOT / f"scripts/probes/requirements/duckdb-{p.version}.txt").read_text("utf-8")
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", req))
        assert req.startswith(f"duckdb=={p.version} ")
        assert hashes == {p.wheel_sha256.windows, p.wheel_sha256.linux}, p.version
    runner = (ROOT / "scripts/probes/run_probes.sh").read_text("utf-8")
    versions = re.search(r"^VERSIONS=\(([^)]*)\)$", runner, flags=re.MULTILINE)
    assert versions is not None
    assert versions.group(1).split() == [p.version for p in pins.packages]
