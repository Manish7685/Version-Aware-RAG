"""S6b `vrag corpus check-upstream` (docs/design/RAG-M3_S6_ci.md §4).

All tests use injected fake fetchers or a stubbed ``urlopen``: no network access. The real
configuration and committed manifest are only read; fixture roots are temporary copies.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus import commands, upstream
from version_aware_rag.corpus.config import CorpusConfig, load_config
from version_aware_rag.corpus.manifest import CorpusManifest, load_manifest
from version_aware_rag.corpus.upstream import (
    FetchFailed,
    Response,
    check_upstream,
    github_get,
)

ROOT = Path(__file__).resolve().parents[3]
TOKEN = "fixture-token-not-a-credential"  # noqa: S105 (test value, never a real token)
MARKER = "RELEASE-BODY-MARKER"  # fixture release text that must never be printed


def _cfg() -> CorpusConfig:
    return load_config(ROOT / "configs/corpus.toml")[0]


def _committed_manifest() -> CorpusManifest:
    manifest, problems = load_manifest(ROOT / "data/manifest/corpus_manifest.json")
    assert manifest is not None and problems == []
    return manifest


BODIES = {
    rec.tag: f"{MARKER} fixture release notes for {rec.tag}\n"
    for rec in _committed_manifest().release_notes.included
}


def _matching_manifest() -> CorpusManifest:
    """The committed manifest with each release's hash and size set to the fixture bodies."""
    manifest = _committed_manifest()
    records = tuple(
        rec.model_copy(
            update={
                "body_sha256": hashlib.sha256(BODIES[rec.tag].encode()).hexdigest(),
                "body_bytes": len(BODIES[rec.tag].encode()),
            }
        )
        for rec in manifest.release_notes.included
    )
    notes = manifest.release_notes.model_copy(update={"included": records})
    return manifest.model_copy(update={"release_notes": notes})


def _commit_url(cfg: CorpusConfig) -> str:
    return f"https://api.github.com/repos/{cfg.docs_source.repo}/commits/{cfg.docs_source.commit}"


def _release_url(cfg: CorpusConfig, release_id: int) -> str:
    return f"https://api.github.com/repos/{cfg.release_notes.repo}/releases/{release_id}"


def _release_json(rec: Any, **changes: Any) -> bytes:
    data = {
        "id": rec.release_id,
        "tag_name": rec.tag,
        "published_at": rec.published_at,
        "draft": False,
        "prerelease": False,
        "body": BODIES[rec.tag],
    }
    data.update(changes)
    return json.dumps(data).encode()


class FakeFetch:
    """Serves the upstream state that matches ``_matching_manifest``, with overrides."""

    def __init__(self, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, Response | Exception] = {
            _commit_url(cfg): (200, json.dumps({"sha": cfg.docs_source.commit}).encode())
        }
        for rec in manifest.release_notes.included:
            self.responses[_release_url(cfg, rec.release_id)] = (200, _release_json(rec))

    def __call__(self, url: str) -> Response:
        self.calls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


def _run(edit: Callable[[FakeFetch, CorpusConfig, CorpusManifest], None] | None = None):  # type: ignore[no-untyped-def]
    cfg, manifest = _cfg(), _matching_manifest()
    fetch = FakeFetch(cfg, manifest)
    if edit is not None:
        edit(fetch, cfg, manifest)
    report = check_upstream(cfg, manifest, fetch)
    return report, "\n".join(report.lines()), fetch


def _first(manifest: CorpusManifest) -> Any:
    return manifest.release_notes.included[0]


# ---------------------------------------------------------------- outcomes


def test_no_drift_checks_the_pinned_commit_and_every_approved_release() -> None:
    report, text, fetch = _run()
    cfg, manifest = _cfg(), _matching_manifest()
    assert report.exit_code == 0 and report.findings == []
    assert fetch.calls == [_commit_url(cfg)] + [
        _release_url(cfg, rec.release_id) for rec in manifest.release_notes.included
    ]
    assert len(report.checks) == 1 + len(manifest.release_notes.included) == 5
    assert text.endswith("RESULT: NO DRIFT (5 checks)")
    assert MARKER not in text


@pytest.mark.parametrize("status", [404, 422])
def test_unreachable_pinned_commit_is_drift(status: int) -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, _: CorpusManifest) -> None:
        fetch.responses[_commit_url(cfg)] = (status, b"")

    report, text, _ = _run(edit)
    assert report.exit_code == 1
    assert f"DRIFT      [commit d01484462710] pinned commit not found (HTTP {status})" in text


def test_a_different_commit_sha_is_drift() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, _: CorpusManifest) -> None:
        fetch.responses[_commit_url(cfg)] = (200, json.dumps({"sha": "f" * 40}).encode())

    report, text, _ = _run(edit)
    assert report.exit_code == 1 and f"sha is '{'f' * 40}'" in text


def test_a_changed_release_body_is_drift_reported_by_hash_and_size_only() -> None:
    changed = f"{MARKER} the upstream text was edited\nwith a second line\n"

    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        rec = _first(manifest)
        fetch.responses[_release_url(cfg, rec.release_id)] = (200, _release_json(rec, body=changed))

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    digest = hashlib.sha256(changed.encode()).hexdigest()
    assert report.exit_code == 1
    assert f"body sha256 is {digest}, expected {rec.body_sha256}" in text
    assert f"body byte count is {len(changed.encode())}, expected {rec.body_bytes}" in text
    assert MARKER not in text and "edited" not in text and "second line" not in text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tag_name", "v9.9.9"),
        ("published_at", "2030-01-01T00:00:00Z"),
        ("draft", True),
        ("prerelease", True),
        ("id", 1),
    ],
)
def test_changed_release_metadata_is_drift_with_expected_and_observed(
    field: str, value: object
) -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        rec = _first(manifest)
        fetch.responses[_release_url(cfg, rec.release_id)] = (
            200,
            _release_json(rec, **{field: value}),
        )

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    expected = {
        "tag_name": rec.tag,
        "published_at": rec.published_at,
        "draft": False,
        "prerelease": False,
        "id": rec.release_id,
    }[field]
    assert report.exit_code == 1
    assert f"[release {rec.tag}] {field} is {value!r}, expected {expected!r}" in text
    assert MARKER not in text


def test_a_deleted_release_is_drift() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = (404, b"")

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    assert report.exit_code == 1
    assert f"release id {rec.release_id} not found (HTTP 404)" in text


def _fetch_failed(cause: Exception) -> FetchFailed:
    """A FetchFailed as the real client raises it (``raise FetchFailed(...) from cause``)."""
    failure = FetchFailed("fixed client text")
    failure.__cause__ = cause
    return failure


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (_fetch_failed(URLError("unreachable")), "no response (URLError)"),
        (_fetch_failed(TimeoutError()), "no response (TimeoutError)"),
        ((403, b""), "HTTP 403 (rate limit or access denied)"),
        ((429, b""), "HTTP 429 (rate limit or access denied)"),
        ((500, b""), "HTTP 500"),
        ((503, b""), "HTTP 503"),
        ((200, b"<html>not json</html>"), "response is not JSON"),
        ((200, b"[1, 2]"), "unexpected response shape"),
        ((200, b'{"id": 1, "tag_name": "v1"}'), "unexpected response shape"),
        ((200, b'{"body": 7}'), "unexpected response shape"),
    ],
)
def test_failures_and_malformed_responses_are_incomplete_never_drift(
    response: Response | Exception, detail: str
) -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = response

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    assert report.drift == [] and report.exit_code == 2
    assert f"INCOMPLETE [release {rec.tag}] {detail}" in text
    assert text.endswith("RESULT: INCOMPLETE (1 checks incomplete)")


@pytest.mark.parametrize(
    "response",
    [(500, b""), (200, b"{}"), (200, b"[]"), FetchFailed("network error (OSError)")],
)
def test_commit_check_failures_are_incomplete(response: Response | Exception) -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, _: CorpusManifest) -> None:
        fetch.responses[_commit_url(cfg)] = response

    report, text, _ = _run(edit)
    assert report.drift == [] and report.exit_code == 2
    assert "INCOMPLETE [commit d01484462710]" in text


def test_drift_wins_over_an_incomplete_check_and_both_are_reported() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_commit_url(cfg)] = (404, b"")
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = (503, b"")

    report, text, _ = _run(edit)
    assert report.exit_code == 1  # S6b-D1
    assert "DRIFT      [commit d01484462710]" in text and "INCOMPLETE [release" in text
    assert text.endswith("RESULT: DRIFT (1 findings, 1 checks incomplete)")


# ---------------------------------------------------------------- the HTTP client


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _stub_urlopen(
    monkeypatch: pytest.MonkeyPatch, outcome: Callable[[str], _Resp]
) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []

    def fake_urlopen(request: Any, timeout: float) -> _Resp:
        seen.append({k.lower(): v for k, v in request.header_items()})
        return outcome(request.full_url)

    monkeypatch.setattr(upstream, "urlopen", fake_urlopen)
    return seen


def test_client_sends_the_token_only_as_a_header_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_urlopen(monkeypatch, lambda url: _Resp(200, b"{}"))
    assert github_get(token=TOKEN)("https://api.github.com/x") == (200, b"{}")
    assert github_get(token=None)("https://api.github.com/x") == (200, b"{}")
    assert seen[0]["authorization"] == f"Bearer {TOKEN}"
    assert "authorization" not in seen[1]
    assert seen[0]["user-agent"] == "version-aware-rag-corpus"


def test_default_fetch_reads_github_token_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _stub_urlopen(monkeypatch, lambda url: _Resp(200, b"{}"))
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    upstream.default_fetch()("https://api.github.com/x")
    monkeypatch.delenv("GITHUB_TOKEN")
    upstream.default_fetch()("https://api.github.com/x")
    assert seen[0]["authorization"] == f"Bearer {TOKEN}" and "authorization" not in seen[1]


def test_client_returns_http_errors_and_raises_only_without_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def http_error(url: str) -> _Resp:
        raise HTTPError(url, 429, "Too Many Requests", {}, None)  # type: ignore[arg-type]

    _stub_urlopen(monkeypatch, http_error)
    assert github_get(token=TOKEN)("https://api.github.com/x") == (429, b"")

    def network_error(url: str) -> _Resp:
        raise URLError("unreachable")

    _stub_urlopen(monkeypatch, network_error)
    with pytest.raises(FetchFailed) as err:
        github_get(token=TOKEN)("https://api.github.com/x")
    assert TOKEN not in str(err.value) and "URLError" in str(err.value)
    with pytest.raises(FetchFailed, match="non-https"):
        github_get(token=TOKEN)("http://api.github.com/x")


# ---------------------------------------------------------------- the CLI, read-only


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "configs").mkdir(parents=True)
    shutil.copy2(ROOT / "configs/corpus.toml", root / "configs/corpus.toml")
    shutil.copytree(ROOT / "data/manifest", root / "data/manifest")
    cache = root / "data/cache/github_releases"
    cache.mkdir(parents=True)
    (cache / "v1.2.1.md").write_text("cached body", encoding="utf-8")  # must stay untouched
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.parametrize(
    ("scenario", "code", "result"),
    [
        ("match", 0, "RESULT: NO DRIFT (5 checks)"),
        ("drift", 1, "RESULT: DRIFT"),
        ("incomplete", 2, "RESULT: INCOMPLETE"),
    ],
)
def test_cli_exit_codes_and_the_fixture_root_stays_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    scenario: str,
    code: int,
    result: str,
) -> None:
    root = _fixture_root(tmp_path)
    cfg, manifest = _cfg(), _matching_manifest()
    fetch = FakeFetch(cfg, manifest)
    if scenario == "drift":
        rec = _first(manifest)
        fetch.responses[_release_url(cfg, rec.release_id)] = (200, _release_json(rec, body="x"))
    if scenario == "incomplete":
        fetch.responses[_commit_url(cfg)] = (503, b"")
    monkeypatch.setattr(upstream, "default_fetch", lambda: fetch)
    monkeypatch.setattr(commands, "load_manifest", lambda path: (manifest, []))
    before = _snapshot(root)
    assert main(["corpus", "check-upstream", "--root", str(root)]) == code
    out = capsys.readouterr().out
    assert result in out and MARKER not in out
    assert _snapshot(root) == before  # read-only, caches included


def test_cli_with_the_real_client_never_prints_the_token_or_release_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = _fixture_root(tmp_path)
    cfg, manifest = _cfg(), _matching_manifest()
    fake = FakeFetch(cfg, manifest)
    rec = _first(manifest)
    fake.responses[_release_url(cfg, rec.release_id)] = (
        200,
        _release_json(rec, body=f"{MARKER} changed upstream"),
    )

    def outcome(url: str) -> _Resp:
        status, body = fake(url)
        return _Resp(status, body)

    seen = _stub_urlopen(monkeypatch, outcome)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(commands, "load_manifest", lambda path: (manifest, []))
    before = _snapshot(root)
    assert main(["corpus", "check-upstream", "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert all(h["authorization"] == f"Bearer {TOKEN}" for h in seen) and len(seen) == 5
    for stream in (captured.out, captured.err):
        assert TOKEN not in stream and MARKER not in stream and "changed upstream" not in stream
    assert _snapshot(root) == before


def test_cli_unreadable_configuration_or_manifest_is_incomplete(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["corpus", "check-upstream", "--root", str(tmp_path)]) == 2
    assert "RESULT: INCOMPLETE (configuration unreadable)" in capsys.readouterr().out
    root = _fixture_root(tmp_path)
    shutil.rmtree(root / "data/manifest")
    assert main(["corpus", "check-upstream", "--root", str(root)]) == 2
    assert "RESULT: INCOMPLETE (corpus manifest unreadable)" in capsys.readouterr().out


def test_cli_against_the_committed_manifest_reports_drift_for_fixture_bodies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Without patching load_manifest: the CLI compares with the committed manifest (D3), so
    fixture bodies (which differ from the real ones) are reported by hash, never by text."""
    root = _fixture_root(tmp_path)
    cfg = _cfg()
    fetch = FakeFetch(cfg, _matching_manifest())
    monkeypatch.setattr(upstream, "default_fetch", lambda: fetch)
    before = _snapshot(root)
    assert main(["corpus", "check-upstream", "--root", str(root)]) == 1
    out = capsys.readouterr().out
    committed = _committed_manifest().release_notes.included
    assert all(f"expected {rec.body_sha256}" in out for rec in committed)
    assert MARKER not in out
    assert _snapshot(root) == before


# ---------------------------------------------------------------- unexpected exceptions

# Exception text that must never reach any output: it carries the release-text marker and the
# fixture token, as an unexpected exception message from a fetcher or library might.
SECRET = f"{MARKER} {TOKEN} exception payload"
DEEP_JSON = b"[" * 100_000 + b"]" * 100_000


def _assert_no_leak(*streams: str) -> None:
    for stream in streams:
        assert MARKER not in stream and TOKEN not in stream and "payload" not in stream
        assert "Traceback" not in stream


def test_an_unexpected_fetcher_exception_is_incomplete_and_other_checks_continue() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = RuntimeError(SECRET)

    report, text, fetch = _run(edit)
    rec = _first(_matching_manifest())
    assert report.drift == [] and report.exit_code == 2
    assert len(fetch.calls) == 5 and len(report.checks) == 5  # the other checks still ran
    assert report.findings == [
        upstream.Finding(
            "incomplete", f"release {rec.tag}", "fetch failed unexpectedly (RuntimeError)"
        )
    ]
    assert text.endswith("RESULT: INCOMPLETE (1 checks incomplete)")
    _assert_no_leak(text)


def test_deeply_nested_json_is_incomplete_not_a_crash() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = (200, DEEP_JSON)

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    assert report.drift == [] and report.exit_code == 2
    assert f"INCOMPLETE [release {rec.tag}] response is not JSON" in text


def test_a_body_with_an_escaped_lone_surrogate_is_incomplete_and_never_shown() -> None:
    body = f"{MARKER} text " + chr(0xD800)  # json.dumps writes it as an escaped \ud800

    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        rec = _first(manifest)
        payload = _release_json(rec, body=body)
        assert b"\\ud800" in payload  # the escaped form, as an API response would carry it
        fetch.responses[_release_url(cfg, rec.release_id)] = (200, payload)

    report, text, _ = _run(edit)
    rec = _first(_matching_manifest())
    assert report.drift == [] and report.exit_code == 2
    assert report.findings == [
        upstream.Finding("incomplete", f"release {rec.tag}", "body is not valid UTF-8")
    ]
    _assert_no_leak(text)


class _ReadFails(_Resp):
    def __init__(self, exc: Exception) -> None:
        super().__init__(200, b"")
        self._exc = exc

    def read(self) -> bytes:
        raise self._exc


@pytest.mark.parametrize(
    ("where", "exc"),
    [
        ("read", http.client.IncompleteRead(SECRET.encode())),
        ("open", http.client.BadStatusLine(SECRET)),
        ("open", ValueError(SECRET)),
    ],
    ids=["IncompleteRead", "BadStatusLine", "ValueError"],
)
def test_client_turns_http_library_errors_into_fetch_failures(
    monkeypatch: pytest.MonkeyPatch, where: str, exc: Exception
) -> None:
    def outcome(url: str) -> _Resp:
        if where == "open":
            raise exc
        return _ReadFails(exc)

    _stub_urlopen(monkeypatch, outcome)
    name = type(exc).__name__
    with pytest.raises(FetchFailed) as err:
        github_get(token=TOKEN)("https://api.github.com/x")
    assert str(err.value) == f"invalid HTTP response ({name})"
    # Through the checker with the real client: every check is incomplete, nothing leaks.
    report = check_upstream(_cfg(), _matching_manifest(), github_get(token=TOKEN))
    text = "\n".join(report.lines())
    assert report.drift == [] and report.exit_code == 2 and len(report.incomplete_checks) == 5
    assert f"no response ({name})" in text
    _assert_no_leak(text)


def test_drift_wins_over_an_unexpected_exception_in_another_check() -> None:
    def edit(fetch: FakeFetch, cfg: CorpusConfig, manifest: CorpusManifest) -> None:
        fetch.responses[_commit_url(cfg)] = (404, b"")
        fetch.responses[_release_url(cfg, _first(manifest).release_id)] = RuntimeError(SECRET)

    report, text, _ = _run(edit)
    assert report.exit_code == 1  # S6b-D1
    assert "DRIFT      [commit d01484462710] pinned commit not found (HTTP 404)" in text
    assert "fetch failed unexpectedly (RuntimeError)" in text
    assert text.endswith("RESULT: DRIFT (1 findings, 1 checks incomplete)")
    _assert_no_leak(text)


def test_an_exception_inside_one_check_leaves_the_others_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, manifest = _cfg(), _matching_manifest()
    broken = _first(manifest).tag
    original = upstream.check_release

    def check_release(cfg: CorpusConfig, rec: Any, fetch: Any, report: Any) -> None:
        if rec.tag == broken:
            raise KeyError(SECRET)
        original(cfg, rec, fetch, report)

    monkeypatch.setattr(upstream, "check_release", check_release)
    fetch = FakeFetch(cfg, manifest)
    report = check_upstream(cfg, manifest, fetch)
    text = "\n".join(report.lines())
    assert report.exit_code == 2 and report.drift == []
    assert sorted(report.checks) == sorted(
        ["commit d01484462710", *(f"release {r.tag}" for r in manifest.release_notes.included)]
    )
    assert f"INCOMPLETE [release {broken}] check failed unexpectedly (KeyError)" in text
    assert len(fetch.calls) == 4  # the commit and the three other releases
    _assert_no_leak(text)


# ---------------------------------------------------------------- CLI: unexpected exceptions


def _boom(*args: object, **kwargs: object) -> Any:
    raise RuntimeError(SECRET)


@pytest.mark.parametrize(
    ("scenario", "code", "result"),
    [
        ("fetcher raises", 2, "RESULT: INCOMPLETE (1 checks incomplete)"),
        ("drift and fetcher raises", 1, "RESULT: DRIFT (1 findings, 1 checks incomplete)"),
        (
            "check_upstream raises",
            2,
            "RESULT: INCOMPLETE (check failed unexpectedly: RuntimeError)",
        ),
        ("default_fetch raises", 2, "RESULT: INCOMPLETE (check failed unexpectedly: RuntimeError)"),
        ("load_manifest raises", 2, "RESULT: INCOMPLETE (corpus manifest unreadable)"),
        ("load_config raises", 2, "RESULT: INCOMPLETE (configuration unreadable)"),
    ],
)
def test_cli_unexpected_exceptions_give_the_documented_exit_code_and_leak_nothing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    scenario: str,
    code: int,
    result: str,
) -> None:
    root = _fixture_root(tmp_path)
    cfg, manifest = _cfg(), _matching_manifest()
    fetch = FakeFetch(cfg, manifest)
    release_url = _release_url(cfg, _first(manifest).release_id)
    monkeypatch.setattr(upstream, "default_fetch", lambda: fetch)
    monkeypatch.setattr(commands, "load_manifest", lambda path: (manifest, []))
    if scenario == "fetcher raises":
        fetch.responses[release_url] = RuntimeError(SECRET)
    elif scenario == "drift and fetcher raises":
        fetch.responses[_commit_url(cfg)] = (422, b"")
        fetch.responses[release_url] = RuntimeError(SECRET)
    elif scenario == "check_upstream raises":
        monkeypatch.setattr(upstream, "check_upstream", _boom)
    elif scenario == "default_fetch raises":
        monkeypatch.setattr(upstream, "default_fetch", _boom)
    elif scenario == "load_manifest raises":
        monkeypatch.setattr(commands, "load_manifest", _boom)
    else:
        monkeypatch.setattr(commands, "load_config", _boom)
    before = _snapshot(root)
    assert main(["corpus", "check-upstream", "--root", str(root)]) == code
    captured = capsys.readouterr()
    assert result in captured.out
    _assert_no_leak(captured.out, captured.err)
    assert _snapshot(root) == before


@pytest.mark.parametrize("failure", ["part file unreadable", "directory iteration fails"])
def test_cli_unreadable_manifest_part_or_directory_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    failure: str,
) -> None:
    """The real load_manifest (not patched) on a fixture copy of the committed manifest, with
    reading one part file, or listing the parts directory, failing as on a permission error."""
    root = _fixture_root(tmp_path)
    files_dir = root / "data/manifest/files"
    part = sorted(files_dir.iterdir())[0]
    fetch = FakeFetch(_cfg(), _matching_manifest())
    monkeypatch.setattr(upstream, "default_fetch", lambda: fetch)
    before = _snapshot(root)
    with monkeypatch.context() as patch:
        if failure == "part file unreadable":
            read_bytes = Path.read_bytes

            def guarded_read(self: Path) -> bytes:
                if self == part:
                    raise PermissionError(SECRET)
                return read_bytes(self)

            patch.setattr(Path, "read_bytes", guarded_read)
        else:
            iterdir = Path.iterdir

            def guarded_iterdir(self: Path) -> Any:
                if self == files_dir:
                    raise PermissionError(SECRET)
                return iterdir(self)

            patch.setattr(Path, "iterdir", guarded_iterdir)
        assert main(["corpus", "check-upstream", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    assert "RESULT: INCOMPLETE (corpus manifest unreadable)" in captured.out
    assert fetch.calls == []  # no upstream request without a usable manifest
    _assert_no_leak(captured.out, captured.err)
    assert _snapshot(root) == before


# ---------------------------------------------------------------- FetchFailed messages


@pytest.mark.parametrize(
    ("cause", "detail"),
    [(None, "no response (FetchFailed)"), (RuntimeError(SECRET), "no response (RuntimeError)")],
    ids=["no-cause", "with-cause"],
)
def test_a_fetch_failed_message_from_an_injected_fetcher_is_never_shown(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    cause: Exception | None,
    detail: str,
) -> None:
    """An injected fetcher may put any text in a FetchFailed message (here the release-text
    marker and the fixture token): only type names reach the finding and the CLI output."""
    failure = FetchFailed(SECRET)
    failure.__cause__ = cause
    cfg, manifest = _cfg(), _matching_manifest()
    rec = _first(manifest)
    fetch = FakeFetch(cfg, manifest)
    fetch.responses[_release_url(cfg, rec.release_id)] = failure
    report = check_upstream(cfg, manifest, fetch)
    assert report.findings == [upstream.Finding("incomplete", f"release {rec.tag}", detail)]
    assert report.exit_code == 2
    _assert_no_leak("\n".join(report.lines()))
    # Through the CLI: exit 2, and neither secret on stdout or stderr; nothing written.
    root = _fixture_root(tmp_path)
    monkeypatch.setattr(upstream, "default_fetch", lambda: fetch)
    monkeypatch.setattr(commands, "load_manifest", lambda path: (manifest, []))
    before = _snapshot(root)
    assert main(["corpus", "check-upstream", "--root", str(root)]) == 2
    captured = capsys.readouterr()
    assert f"INCOMPLETE [release {rec.tag}] {detail}" in captured.out
    assert "RESULT: INCOMPLETE (1 checks incomplete)" in captured.out
    _assert_no_leak(captured.out, captured.err)
    assert _snapshot(root) == before
