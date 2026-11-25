"""The pilot change inventory and protocol v0 against the real cached sources (RAG-M4 S2): every
cited line range exists, no committed S2 file quotes a restricted source, and the CLI passes.
Skipped unless the caches have been fetched; the CI corpus-rebuild job fetches them.

Restricted text must never reach test output: assertions use precomputed booleans and counts."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus.config import load_config

pytestmark = pytest.mark.corpus

S2_FILES = (
    "benchmark/inventory/pilot_change_inventory.jsonl",
    "benchmark/protocol/protocol_v0.md",
    "benchmark/protocol/protocol_v0.toml",
    *(
        f"benchmark/protocol/prompts/{n}"
        for n in (
            "draft_common.md",
            "draft_T1.md",
            "draft_T2.md",
            "draft_T3.md",
            "draft_T4.md",
            "draft_T5.md",
        )
    ),
)


def test_inventory_validates_against_the_cache_and_no_s2_file_quotes_restricted_text(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    cache = cfg.cache_path(repo_root)
    releases = repo_root / cfg.release_notes.cache_dir
    if not (cache / ".git").is_dir() or not releases.is_dir():
        pytest.skip("corpus or release-note cache not fetched")
    code = main(["benchmark", "inventory", "validate", "--root", str(repo_root)])
    out = capsys.readouterr().out
    passed = code == 0 and "RESULT: PASSED (32 changes)" in out
    assert passed, f"inventory validation exit {code}"
    corpus = tomllib.loads((repo_root / "configs/corpus.toml").read_text(encoding="utf-8"))
    restricted = [
        *sorted(releases.glob("v*.md")),
        *(cache / p["path"] for p in corpus["release_posts"]["posts"]),
    ]
    lines = {
        ln.strip()
        for f in restricted
        for ln in f.read_text("utf-8").splitlines()
        if len(ln.strip()) >= 30
    }
    s2_text = "\n".join((repo_root / f).read_text(encoding="utf-8") for f in S2_FILES)
    hits = sum(1 for ln in lines if ln in s2_text)
    assert len(lines) > 1000 and hits == 0, f"{hits} restricted lines found in S2 files"
