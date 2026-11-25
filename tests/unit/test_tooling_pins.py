"""Tool versions declared outside uv.lock (pre-commit, CI) must agree with uv.lock."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _locked_versions(repo_root: Path) -> dict[str, str]:
    lock = tomllib.loads((repo_root / "uv.lock").read_text(encoding="utf-8"))
    return {pkg["name"]: pkg["version"] for pkg in lock["package"] if "version" in pkg}


def test_precommit_ruff_rev_matches_lock(repo_root: Path) -> None:
    config = (repo_root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    match = re.search(r"ruff-pre-commit\s*\n\s*rev:\s*v(\S+)", config)
    assert match is not None
    assert match.group(1) == _locked_versions(repo_root)["ruff"]


def test_precommit_mypy_dependencies_match_lock(repo_root: Path) -> None:
    config = (repo_root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    pins = dict(re.findall(r"^\s*-\s*([a-z][a-z0-9-]*)==(\S+)\s*$", config, flags=re.M))
    locked = _locked_versions(repo_root)
    assert set(pins) == {
        "mypy",
        "pydantic",
        "pydantic-settings",
        "pytest",
        "markdown-it-py",
        "mdurl",
        # The API (RAG-M11) and retrieval code need these for the hook's isolated mypy run.
        "fastapi",
        "starlette",
        "uvicorn",
        "numpy",
        "httpx",
        "httpcore",
        "anyio",
        "typing-inspection",
        "annotated-doc",
    }
    assert {name: locked[name] for name in pins} == pins


def test_ci_uv_version_matches_documented_toolchain(repo_root: Path) -> None:
    ci = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert re.search(r'UV_VERSION:\s*"0\.13\.9"', ci)
    assert (repo_root / ".python-version").read_text(encoding="utf-8").strip() == "3.12.16"


def test_ci_actions_are_pinned_to_commit_shas(repo_root: Path) -> None:
    ci = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)", ci)
    assert uses, "no actions found"
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses
