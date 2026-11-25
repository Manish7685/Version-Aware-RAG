"""The corpus config: approved version set, and expected values equal to the RAG-M1 manifest."""

from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.corpus.config import APPROVED_VERSIONS, CorpusConfig, load_config

CONFIG = Path("configs/corpus.toml")
M1_MANIFEST = Path("docs/design/RAG-M1_version_manifest.md")


def _raw(repo_root: Path) -> dict[str, Any]:
    return tomllib.loads((repo_root / CONFIG).read_text(encoding="utf-8"))


def test_production_config_loads(repo_root: Path) -> None:
    cfg, sha = load_config(repo_root / CONFIG)
    assert [v.version for v in cfg.versions] == ["1.2", "1.3", "1.4", "1.5", "2.0"]
    assert [v.behaviour_package for v in cfg.versions] == [
        "1.2.2",
        "1.3.2",
        "1.4.5",
        "1.5.6",
        "2.0.1",
    ]
    assert cfg.docs_source.commit == "d01484462710d49206816c9eeaeb822361ebe81c"
    assert len(sha) == 64


def test_expected_values_equal_rag_m1_manifest(repo_root: Path) -> None:
    """Guards against transcription errors: config must equal the approved M1 table."""
    text = (repo_root / M1_MANIFEST).read_text(encoding="utf-8")
    rows = re.findall(
        r"^\| (\d\.\d)(?: LTS)? \| `docs/\d\.\d` \| `([0-9a-f]{40})` \| ([\d,]+) \| ([\d,]+) \| "
        r"`([0-9a-f]{64})` \|$",
        text,
        flags=re.M,
    )
    m1 = {
        v: {
            "git_tree": t,
            "md_files": int(n.replace(",", "")),
            "words": int(w.replace(",", "")),
            "content_hash": h,
        }
        for v, t, n, w, h in rows
    }
    assert sorted(m1) == list(APPROVED_VERSIONS)
    cfg, _ = load_config(repo_root / CONFIG)
    for spec in cfg.versions:
        assert spec.expected.model_dump() == m1[spec.version], spec.version


def test_source_values_equal_rag_m1_manifest(repo_root: Path) -> None:
    text = (repo_root / M1_MANIFEST).read_text(encoding="utf-8")
    cfg, _ = load_config(repo_root / CONFIG)
    src = cfg.docs_source
    assert f"| Commit | `{src.commit}`" in text
    assert (
        f"`LICENSE` blob `{src.licence_file_git_blob}`, sha256 `{src.licence_file_sha256}`" in text
    )


def test_reference_package_1_1_3_is_not_a_corpus_version(repo_root: Path) -> None:
    raw = _raw(repo_root)
    extra = copy.deepcopy(raw["versions"][0])
    extra.update(version="1.1", docs_dir="docs/1.1", behaviour_package="1.1.3")
    raw["versions"].insert(0, extra)
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("index", "changes"),
    [
        (0, {"behaviour_package": "1.2.1"}),  # wrong package for an approved version
        (1, {"docs_dir": "docs/1.2"}),  # docs_dir doesn't match version
    ],
)
def test_inconsistent_version_rejected(
    repo_root: Path, index: int, changes: dict[str, str]
) -> None:
    raw = _raw(repo_root)
    raw["versions"][index].update(changes)
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


def test_missing_or_reordered_versions_rejected(repo_root: Path) -> None:
    raw = _raw(repo_root)
    raw["versions"] = raw["versions"][:-1]
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)
    raw = _raw(repo_root)
    raw["versions"].reverse()
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


@pytest.mark.parametrize("bad", ["main", "d0148446", "D01484462710D49206816C9EEAEB822361EBE81C"])
def test_commit_must_be_a_full_sha(repo_root: Path, bad: str) -> None:
    raw = _raw(repo_root)
    raw["docs_source"]["commit"] = bad
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


def test_unknown_keys_rejected(repo_root: Path) -> None:
    raw = _raw(repo_root)
    raw["docs_source"]["branch"] = "main"
    with pytest.raises(ValidationError):
        CorpusConfig.model_validate(raw)


def test_cache_path_is_keyed_by_commit(repo_root: Path) -> None:
    cfg, _ = load_config(repo_root / CONFIG)
    assert cfg.cache_path(Path("/r")).name == "duckdb-web-d01484462710"
