"""Protocol v0 (RAG-M4 S2) against the approved design (RAG-M1_design.md §3.1 to §3.4), and the
drafting prompts against their recorded sha256 values."""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL = ROOT / "benchmark/protocol"
DESIGN = (ROOT / "docs/design/RAG-M1_design.md").read_text(encoding="utf-8")


def _toml() -> dict[str, Any]:
    return tomllib.loads((PROTOCOL / "protocol_v0.toml").read_text(encoding="utf-8"))


def test_reason_codes_are_the_design_codes() -> None:
    codes = _toml()["reason_codes"]
    assert isinstance(codes, dict)
    design = dict(re.findall(r"^   - `(R\d)` (.+)$", DESIGN, flags=re.MULTILINE))
    assert codes == design and list(codes) == [f"R{i}" for i in range(1, 10)]


def test_answer_types_and_pilot_composition_are_the_design_values() -> None:
    data = _toml()
    design_types = re.search(r"typed field: one of `([^`]+)`", DESIGN)
    assert design_types is not None
    assert data["answer_types"] == [t.strip() for t in design_types.group(1).split("|")]
    composition = re.search(
        r"\*\*Composition:\*\* T1 (\d+), T2 (\d+) \(at least (\d+) sets"
        r".*?T3 (\d+), T4 (\d+), T5 (\d+)",
        DESIGN,
    )
    assert composition is not None
    t1, t2, sets, t3, t4, t5 = (int(x) for x in composition.groups())
    assert data["pilot_composition"] == {
        "T1": t1,
        "T2": t2,
        "T3": t3,
        "T4": t4,
        "T5": t5,
        "t2_min_sets_spanning_three_versions": sets,
    }


def test_prompt_files_match_their_recorded_hashes() -> None:
    prompts = _toml()["prompts"]
    assert isinstance(prompts, dict)
    files = sorted(p.name for p in (PROTOCOL / "prompts").glob("*.md"))
    assert (
        sorted(prompts)
        == files
        == [
            "draft_T1.md",
            "draft_T2.md",
            "draft_T3.md",
            "draft_T4.md",
            "draft_T5.md",
            "draft_common.md",
        ]
    )
    for name, digest in prompts.items():
        data = (PROTOCOL / "prompts" / name).read_bytes()
        assert b"\r\n" not in data
        assert hashlib.sha256(data).hexdigest() == digest, name


def test_protocol_document_covers_every_code_type_and_rule() -> None:
    text = (PROTOCOL / "protocol_v0.md").read_text(encoding="utf-8")
    data = _toml()
    for code in data["reason_codes"]:
        assert f"| {code} |" in text
    for answer_type in data["answer_types"]:
        assert f"| `{answer_type}` |" in text
    for required in (
        "Verbatim",
        "Minimal and sufficient",
        "Restricted sources",
        "INV-1",
        "suspected",
    ):
        assert required in text


def test_prompts_ask_for_evidence_bound_drafts_only() -> None:
    common = (PROTOCOL / "prompts/draft_common.md").read_text(encoding="utf-8")
    for rule in ("Use only the evidence passages", "exactly one target version", "copied exactly"):
        assert rule in common
    text = "".join(p.read_text(encoding="utf-8") for p in (PROTOCOL / "prompts").glob("*.md"))
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}|api[_-]?key\s*=", text)  # no keys in prompts
