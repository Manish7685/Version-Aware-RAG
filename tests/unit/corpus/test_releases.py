"""RAG-M3 S2: release-note fetching, hash verification, failure handling and manifest metadata."""

from __future__ import annotations

import hashlib
import io
import json
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from version_aware_rag.corpus import releases as rel_mod
from version_aware_rag.corpus.config import APPROVED_RELEASE_TAGS, ReleaseNotesConfig
from version_aware_rag.corpus.manifest import (
    UnverifiedReleaseCacheError,
    build_release_section,
)
from version_aware_rag.corpus.releases import (
    ReleaseMismatch,
    ReleaseUnavailable,
    api_url,
    body_path,
    fetch_all,
    fetch_record_path,
    fetch_release,
    github_fetcher,
    verify_releases,
)

from .conftest import RELEASE_IDS, release_body, release_notes_config, write_release_cache

NOW = "2026-11-21T09:00:00Z"


def _notes(**overrides: Any) -> ReleaseNotesConfig:
    return ReleaseNotesConfig.model_validate(release_notes_config(**overrides))


def _response(tag: str, **changes: Any) -> bytes:
    data: dict[str, Any] = {
        "id": RELEASE_IDS[tag],
        "tag_name": tag,
        "published_at": "2025-03-05T18:03:02Z",
        "draft": False,
        "prerelease": False,
        "body": release_body(tag),
        "author": {"login": "someone"},
    }
    data.update(changes)
    return json.dumps(data).encode()


class FakeGitHub:
    """Serves release JSON by URL and records every requested URL."""

    def __init__(self, notes: ReleaseNotesConfig, **per_tag_changes: dict[str, Any]) -> None:
        self.by_url = {
            api_url(notes, r): _response(r.tag, **per_tag_changes.get(r.tag, {}))
            for r in notes.included
        }
        self.requested: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.requested.append(url)
        if url not in self.by_url:
            raise ReleaseUnavailable(f"{url}: HTTP 404 Not Found")
        return self.by_url[url]


# ---------------------------------------------------------------- successful fetch


def test_fetch_writes_raw_body_unchanged_and_records_metadata(tmp_path: Path) -> None:
    notes = _notes()
    fake = FakeGitHub(notes)
    results = fetch_all(notes, tmp_path, fake)
    assert [r.tag for r in results] == list(APPROVED_RELEASE_TAGS)
    for r in notes.included:
        raw = body_path(tmp_path, r.tag).read_bytes()
        assert raw == release_body(r.tag).encode("utf-8")  # CRLF and non-ASCII preserved
        assert b"\r\n" in raw and "café".encode() in raw
        assert hashlib.sha256(raw).hexdigest() == r.body_sha256
        record = json.loads(fetch_record_path(tmp_path, r.tag).read_text(encoding="utf-8"))
        assert record["release_id"] == r.release_id
        assert record["body_sha256"] == r.body_sha256
        assert record["source_url"] == f"https://github.com/duckdb/duckdb/releases/tag/{r.tag}"
        assert record["api_url"].endswith(f"/releases/{r.release_id}")
    assert verify_releases(notes, tmp_path).ok


def test_releases_are_fetched_by_recorded_id_not_by_tag(tmp_path: Path) -> None:
    notes = _notes()
    fake = FakeGitHub(notes)
    fetch_all(notes, tmp_path, fake)
    assert fake.requested == [
        f"https://api.example.invalid/repos/duckdb/duckdb/releases/{RELEASE_IDS[t]}"
        for t in APPROVED_RELEASE_TAGS
    ]


def test_fetch_records_retrieval_time(tmp_path: Path) -> None:
    notes = _notes()
    result = fetch_release(notes, notes.included[0], tmp_path, FakeGitHub(notes), now=lambda: NOW)
    assert result.retrieved_at == NOW
    record = json.loads(fetch_record_path(tmp_path, result.tag).read_text(encoding="utf-8"))
    assert record["retrieved_at"] == NOW


# ---------------------------------------------------------------- changed sources


def test_changed_body_stops_without_overwriting_and_shows_diff(tmp_path: Path) -> None:
    notes = _notes()
    tag = "v1.2.2"
    write_release_cache(tmp_path)  # a previously verified local copy exists
    before = body_path(tmp_path, tag).read_bytes()
    changed = release_body(tag).replace("Faster joins", "Much faster joins")
    fake = FakeGitHub(notes, **{tag: {"body": changed}})
    rel = next(r for r in notes.included if r.tag == tag)
    with pytest.raises(ReleaseMismatch) as info:
        fetch_release(notes, rel, tmp_path, fake, now=lambda: NOW)
    exc = info.value
    assert any("body sha256" in p for p in exc.problems)
    assert body_path(tmp_path, tag).read_bytes() == before  # never overwritten
    assert exc.saved is not None and exc.saved.read_bytes() == changed.encode("utf-8")
    assert exc.diff is not None
    assert "-* Faster joins" in exc.diff and "+* Much faster joins" in exc.diff


def test_changed_body_without_local_copy_saves_text_and_writes_nothing_else(
    tmp_path: Path,
) -> None:
    notes = _notes()
    tag = "v1.3.1"
    fake = FakeGitHub(notes, **{tag: {"body": "edited upstream"}})
    rel = next(r for r in notes.included if r.tag == tag)
    with pytest.raises(ReleaseMismatch) as info:
        fetch_release(notes, rel, tmp_path, fake)
    assert info.value.diff is None
    assert not body_path(tmp_path, tag).exists()
    assert not fetch_record_path(tmp_path, tag).exists()
    assert info.value.saved is not None and info.value.saved.is_file()


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"id": 1}, "id is 1"),
        ({"tag_name": "v9.9.9"}, "tag_name"),
        ({"published_at": "2025-03-06T00:00:00Z"}, "published_at"),
        ({"draft": True}, "draft"),
        ({"prerelease": True}, "prerelease"),
        ({"body": ""}, "body is missing or empty"),
        ({"body": None}, "body is missing or empty"),
    ],
)
def test_metadata_differences_stop_the_fetch(
    tmp_path: Path, changes: dict[str, Any], fragment: str
) -> None:
    notes = _notes()
    tag = "v1.2.1"
    fake = FakeGitHub(notes, **{tag: changes})
    with pytest.raises(ReleaseMismatch) as info:
        fetch_release(notes, notes.included[0], tmp_path, fake)
    assert any(fragment in p for p in info.value.problems), info.value.problems
    assert not body_path(tmp_path, tag).exists()


# ---------------------------------------------------------------- unavailable sources


def test_unavailable_source_fails_clearly_and_stops(tmp_path: Path) -> None:
    notes = _notes()
    fake = FakeGitHub(notes)
    del fake.by_url[api_url(notes, notes.included[1])]  # second release returns 404
    with pytest.raises(ReleaseUnavailable, match="HTTP 404"):
        fetch_all(notes, tmp_path, fake)
    assert len(fake.requested) == 2  # nothing after the failure is fetched
    assert body_path(tmp_path, "v1.2.1").is_file()
    assert not body_path(tmp_path, "v1.3.1").exists()


def test_non_json_response_is_unavailable(tmp_path: Path) -> None:
    notes = _notes()
    with pytest.raises(ReleaseUnavailable, match="not JSON"):
        fetch_release(notes, notes.included[0], tmp_path, lambda url: b"<html>oops</html>")


def test_github_fetcher_maps_http_and_network_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def http_404(req: Request, timeout: float) -> Any:
        raise HTTPError(req.full_url, 404, "Not Found", Message(), io.BytesIO())

    monkeypatch.setattr(rel_mod, "urlopen", http_404)
    with pytest.raises(ReleaseUnavailable, match="HTTP 404 Not Found"):
        github_fetcher()("https://api.github.com/repos/x/y/releases/1")

    def http_403(req: Request, timeout: float) -> Any:
        raise HTTPError(req.full_url, 403, "Forbidden", Message(), io.BytesIO())

    monkeypatch.setattr(rel_mod, "urlopen", http_403)
    with pytest.raises(ReleaseUnavailable, match="rate limit"):
        github_fetcher()("https://api.github.com/repos/x/y/releases/1")

    def offline(req: Request, timeout: float) -> Any:
        raise URLError("no route to host")

    monkeypatch.setattr(rel_mod, "urlopen", offline)
    with pytest.raises(ReleaseUnavailable, match="network error"):
        github_fetcher()("https://api.github.com/repos/x/y/releases/1")


def test_github_fetcher_refuses_plain_http() -> None:
    with pytest.raises(ReleaseUnavailable, match="non-https"):
        github_fetcher()("http://api.github.com/repos/x/y/releases/1")


def test_github_fetcher_sends_token_only_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Request] = []

    class Resp(io.BytesIO):
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def capture(req: Request, timeout: float) -> Resp:
        seen.append(req)
        return Resp(b"{}")

    monkeypatch.setattr(rel_mod, "urlopen", capture)
    github_fetcher()("https://api.github.com/a")
    github_fetcher(token="t0k3n")("https://api.github.com/b")  # noqa: S106 (fake test value)
    assert seen[0].get_header("Authorization") is None
    assert seen[1].get_header("Authorization") == "Bearer t0k3n"
    assert seen[0].get_header("User-agent") == "version-aware-rag-corpus"


# ---------------------------------------------------------------- offline verification


def test_verify_reports_changed_and_missing_cache_files(tmp_path: Path) -> None:
    notes = _notes()
    write_release_cache(tmp_path)
    body_path(tmp_path, "v1.2.2").write_bytes(b"tampered")
    body_path(tmp_path, "v1.3.2").unlink()
    report = verify_releases(notes, tmp_path)
    status = {c.tag: c.status for c in report.checks}
    assert status == {
        "v1.2.1": "verified",
        "v1.2.2": "changed",
        "v1.3.1": "verified",
        "v1.3.2": "missing",
    }
    assert not report.ok


def test_verify_rejects_newline_conversion(tmp_path: Path) -> None:
    """A CRLF->LF conversion (for example by an editor or git) must count as changed."""
    notes = _notes()
    write_release_cache(tmp_path)
    p = body_path(tmp_path, "v1.2.1")
    p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    assert {c.tag: c.status for c in verify_releases(notes, tmp_path).checks}["v1.2.1"] == "changed"


def test_empty_cache_is_not_ok(tmp_path: Path) -> None:
    assert not verify_releases(_notes(), tmp_path).ok


# ---------------------------------------------------------------- manifest metadata


def test_release_section_metadata(tmp_path: Path) -> None:
    cache = write_release_cache(tmp_path / "rel")
    section = build_release_section(_notes(), cache)
    assert section.licence is None
    assert section.licence_status == "unverified"
    assert section.redistributable is False
    assert [r.tag for r in section.included] == list(APPROVED_RELEASE_TAGS)
    for r in section.included:
        assert r.licence is None and r.licence_status == "unverified"
        assert r.redistributable is False
        assert r.attribution == f"DuckDB release notes, github.com/duckdb/duckdb, release {r.tag}"
        assert r.source_url == f"https://github.com/duckdb/duckdb/releases/tag/{r.tag}"
        assert r.api_url.endswith(f"/releases/{r.release_id}")
        assert r.first_retrieved_at == "2026-11-20T17:48:36Z"
        assert r.version == r.tag[1:4] and r.introduced_in == r.tag[1:]
        assert r.status == "included" and r.parse_status == "not_parsed"
        assert r.cache_path == f"data/cache/github_releases/{r.tag}.md"
    (inv,) = section.investigated_not_included
    assert inv.status == "investigated_not_included" and inv.tag == "v1.2.0"


def test_release_section_contains_no_release_text(tmp_path: Path) -> None:
    cache = write_release_cache(tmp_path / "rel")
    text = build_release_section(_notes(), cache).model_dump_json()
    for tag in APPROVED_RELEASE_TAGS:
        assert "Faster joins" not in text and "@alice" not in text and tag in text
    assert "What's Changed" not in text


def test_unverified_cache_blocks_the_manifest(tmp_path: Path) -> None:
    cache = write_release_cache(tmp_path / "rel")
    body_path(cache, "v1.3.1").write_bytes(b"changed")
    with pytest.raises(UnverifiedReleaseCacheError, match=r"v1\.3\.1=changed"):
        build_release_section(_notes(), cache)


def test_committed_manifest_has_release_metadata_but_no_text(repo_root: Path) -> None:
    """Checks the real committed manifest file without needing any cache."""
    text = (repo_root / "data/manifest/corpus_manifest.json").read_text(encoding="utf-8")
    data = json.loads(text)
    section = data["release_notes"]
    assert section["licence"] is None and section["licence_status"] == "unverified"
    assert [r["tag"] for r in section["included"]] == list(APPROVED_RELEASE_TAGS)
    assert len(section["investigated_not_included"]) == 17
    assert all(r["redistributable"] is False for r in section["included"])
    assert "What's Changed" not in text and "bug fix release" not in text.lower()


# ---------------------------------------------------------------- CLI: `vrag corpus releases`


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v)  # JSON string escapes are valid TOML basic-string escapes
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def _cli_root(repo_root: Path, tmp_path: Path) -> Path:
    """A temporary repo root: production docs config + fixture release-notes section."""
    production = (repo_root / "configs/corpus.toml").read_text(encoding="utf-8")
    docs_part = production[: production.index("\n[release_notes]")]
    posts_part = production[production.index("\n[release_posts]") :]
    notes = release_notes_config()
    lines = ["", "[release_notes]"]
    lines += [f"{k} = {_toml_value(v)}" for k, v in notes.items() if k != "included"]
    for rec in notes["included"]:
        lines += ["", "[[release_notes.included]]"]
        lines += [f"{k} = {_toml_value(v)}" for k, v in rec.items()]
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/corpus.toml").write_text(
        docs_part + "\n" + posts_part + "\n" + "\n".join(lines) + "\n", encoding="utf-8"
    )
    return tmp_path


def _run_cli(root: Path, *args: str) -> int:
    from version_aware_rag.cli import main

    return main(["corpus", "releases", *args, "--root", str(root)])


def test_cli_fetch_stops_on_changed_release_with_diff(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cli_root(repo_root, tmp_path)
    cache = write_release_cache(root / "data/cache/github_releases")  # verified local copies
    before = {p.name: p.read_bytes() for p in cache.glob("v*.md")}
    tag = "v1.2.2"
    changed = release_body(tag).replace("Faster joins", "Much faster joins")
    fake = FakeGitHub(_notes(), **{tag: {"body": changed}})
    monkeypatch.setattr("version_aware_rag.corpus.commands.default_fetcher", lambda: fake)

    exit_code = _run_cli(root, "fetch")
    out = capsys.readouterr().out

    assert exit_code == 1  # unsuccessful
    assert f"STOPPED: {tag} differs from the recorded evidence; nothing was overwritten." in out
    assert "body sha256 is" in out
    assert "fetched text saved for review:" in out
    saved = [p for p in cache.iterdir() if p.name.startswith(f"{tag}.mismatch-")]
    assert len(saved) == 1 and saved[0].name in out
    assert "diff against the verified local copy:" in out
    assert "-* Faster joins" in out and "+* Much faster joins" in out
    after = {p.name: p.read_bytes() for p in cache.glob("v*.md") if ".mismatch-" not in p.name}
    assert after == before  # verified cache not overwritten
    assert len(fake.requested) == 2  # stopped at v1.2.2; v1.3.x never requested


def test_cli_fetch_without_local_copy_reports_saved_file(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cli_root(repo_root, tmp_path)
    fake = FakeGitHub(_notes(), **{"v1.2.1": {"body": "edited upstream"}})
    monkeypatch.setattr("version_aware_rag.corpus.commands.default_fetcher", lambda: fake)

    assert _run_cli(root, "fetch") == 1
    out = capsys.readouterr().out
    assert "STOPPED: v1.2.1" in out
    assert "no verified local copy to diff against" in out
    assert not (root / "data/cache/github_releases/v1.2.1.md").exists()


def test_cli_fetch_unavailable_source_fails_clearly(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cli_root(repo_root, tmp_path)

    def offline(url: str) -> bytes:
        raise ReleaseUnavailable(f"{url}: network error: no route to host")

    monkeypatch.setattr("version_aware_rag.corpus.commands.default_fetcher", lambda: offline)
    assert _run_cli(root, "fetch") == 2
    assert "FAILED:" in capsys.readouterr().out


def test_cli_fetch_and_verify_success(
    repo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _cli_root(repo_root, tmp_path)
    monkeypatch.setattr(
        "version_aware_rag.corpus.commands.default_fetcher", lambda: FakeGitHub(_notes())
    )
    assert _run_cli(root, "fetch") == 0
    assert _run_cli(root, "verify") == 0
    body_path(root / "data/cache/github_releases", "v1.3.2").write_bytes(b"tampered")
    assert _run_cli(root, "verify") == 1
    out = capsys.readouterr().out
    assert "v1.3.2   CHANGED" in out and "RESULT: FAILED" in out
