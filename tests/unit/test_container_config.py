"""Container configuration (RAG-M12): restricted and built data never enter the image, the key is
never baked in, and compose binds locally with the documented mounts."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _dockerignore() -> set[str]:
    lines = (ROOT / ".dockerignore").read_text("utf-8").splitlines()
    return {ln.strip().rstrip("/") for ln in lines if ln.strip() and not ln.startswith("#")}


def test_restricted_and_built_data_are_excluded_from_the_build_context() -> None:
    ignored = _dockerignore()
    for path in ("data/evidence", "data/cache", "data/build", "benchmark/drafts", ".env", ".git"):
        assert path in ignored, path


def test_dockerfile_copies_no_data_beyond_the_committed_manifest_and_bakes_no_secret() -> None:
    text = (ROOT / "Dockerfile").read_text("utf-8")
    copies = re.findall(r"^COPY (?!--from)(.+)$", text, flags=re.MULTILINE)
    data_sources = [c for c in copies if "data/" in c]
    assert data_sources == ["data/manifest/ data/manifest/"]
    assert "VRAG_GEMINI_API_KEY" not in text.split("\nFROM ", 1)[1].replace(
        "-e VRAG_GEMINI_API_KEY", ""
    )
    assert re.search(r"^USER app$", text, flags=re.MULTILINE)  # not root at run time


def test_compose_binds_locally_mounts_data_and_passes_the_key_through_only() -> None:
    text = (ROOT / "compose.yaml").read_text("utf-8")
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert re.search(r"^    build: \.$", body, flags=re.MULTILINE)
    assert re.findall(r'^      - "([^"]+)"$', body, flags=re.MULTILINE) == ["127.0.0.1:8000:8000"]
    assert re.findall(r"^      VRAG_[A-Z_]+: .*$", body, flags=re.MULTILINE) == [
        "      VRAG_GEMINI_API_KEY: ${VRAG_GEMINI_API_KEY:-}"
    ]
    mounts = re.findall(r"^      - (\S+:/\S+)$", body, flags=re.MULTILINE)
    assert {m.rsplit(":", 1)[1] for m in mounts} == {
        "/app/data",
        "/app/benchmark/drafts",
        "/home/app/.cache/huggingface",
    }
    assert "http://127.0.0.1:8000/health" in body
