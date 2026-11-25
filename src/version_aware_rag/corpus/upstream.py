"""Read-only upstream drift check (RAG-M3 S6b, docs/design/RAG-M3_S6_ci.md §4).

``vrag corpus check-upstream`` asks the GitHub REST API whether the pinned docs commit is still
reachable and whether the four approved release bodies still match the committed corpus manifest
(sha256, byte count, tag, publish time, draft and prerelease flags). It writes nothing: no
manifest, cache or other file. Release-body text is hashed and measured in memory only; it is
never stored, printed or logged. ``GITHUB_TOKEN`` is used as a bearer token when set and never
appears in any output. It is a local or manual check, never part of PR CI (S6-D5).

Exit codes: 0 no drift; 1 definitive drift (even if another check was incomplete, S6b-D1);
2 no drift found but at least one check could not be completed (network or HTTP error, rate
limit, malformed or unexpected response, unreadable configuration or manifest).

Limitation: a commit visible through the API is very likely, but not guaranteed, to be fetchable
by git; the CI fresh fetch remains the authoritative reachability test.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from version_aware_rag.corpus.config import CorpusConfig
from version_aware_rag.corpus.manifest import CorpusManifest, ReleaseRecord
from version_aware_rag.corpus.releases import GITHUB_API_VERSION, USER_AGENT

Response = tuple[int, bytes]  # HTTP status and body
Fetch = Callable[[str], Response]
DRIFT_STATUSES = (404, 422)  # the object is gone: definitive drift
Kind = Literal["drift", "incomplete"]


class FetchFailed(Exception):
    """No HTTP response at all (network error, timeout, refused URL)."""


def github_get(token: str | None = None, timeout: float = 30.0) -> Fetch:
    """A read-only GET for the GitHub REST API returning (status, body). An HTTP error status is
    returned, not raised (its body is not read), so that drift (404, 422) and incomplete checks
    (403, 429, 5xx) can be told apart. ``token`` is sent only as a header and never echoed."""

    def get(url: str) -> Response:
        if not url.startswith("https://"):
            raise FetchFailed("refusing a non-https URL")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as resp:  # noqa: S310 (https checked above)
                return int(resp.status), bytes(resp.read())
        except HTTPError as exc:
            return int(exc.code), b""
        except TimeoutError as exc:
            raise FetchFailed(f"timed out after {timeout}s") from exc
        except (URLError, OSError) as exc:
            raise FetchFailed(f"network error ({type(exc).__name__})") from exc
        except (http.client.HTTPException, ValueError) as exc:  # truncated or malformed response
            raise FetchFailed(f"invalid HTTP response ({type(exc).__name__})") from exc

    return get


def default_fetch() -> Fetch:
    return github_get(token=os.environ.get("GITHUB_TOKEN") or None)


@dataclass(frozen=True)
class Finding:
    kind: Kind
    check: str
    detail: str


@dataclass
class UpstreamReport:
    checks: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def add(self, kind: Kind, check: str, detail: str) -> None:
        self.findings.append(Finding(kind, check, detail))

    @property
    def drift(self) -> list[Finding]:
        return [f for f in self.findings if f.kind == "drift"]

    @property
    def incomplete_checks(self) -> list[str]:
        return sorted({f.check for f in self.findings if f.kind == "incomplete"})

    @property
    def exit_code(self) -> int:
        if self.drift:
            return 1
        return 2 if self.incomplete_checks else 0

    def lines(self) -> list[str]:
        out = [f"checked {c}" for c in self.checks]
        for f in self.findings:
            label = "DRIFT" if f.kind == "drift" else "INCOMPLETE"
            out.append(f"{label:<10} [{f.check}] {f.detail}")
        incomplete = len(self.incomplete_checks)
        if self.drift:
            out.append(
                f"RESULT: DRIFT ({len(self.drift)} findings, {incomplete} checks incomplete)"
            )
        elif incomplete:
            out.append(f"RESULT: INCOMPLETE ({incomplete} checks incomplete)")
        else:
            out.append(f"RESULT: NO DRIFT ({len(self.checks)} checks)")
        return out


def _get_json(
    fetch: Fetch, url: str, check: str, report: UpstreamReport
) -> tuple[int, dict[str, object] | None]:
    """(status, JSON object) for a 200 response; (status, None) otherwise. Any failure to obtain
    or read a response is recorded as an incomplete check (never as drift)."""
    try:
        status, body = fetch(url)
    except FetchFailed as exc:  # type names only: an injected fetcher may put any text in it
        reason = exc.__cause__ if exc.__cause__ is not None else exc
        report.add("incomplete", check, f"no response ({type(reason).__name__})")
        return 0, None
    except Exception as exc:  # any other fetcher failure: type name only, never its message
        report.add("incomplete", check, f"fetch failed unexpectedly ({type(exc).__name__})")
        return 0, None
    if status in DRIFT_STATUSES:
        return status, None
    if status != 200:
        hint = " (rate limit or access denied)" if status in (401, 403, 429) else ""
        report.add("incomplete", check, f"HTTP {status}{hint}")
        return status, None
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        report.add("incomplete", check, "response is not JSON")
        return status, None
    if not isinstance(data, dict):
        report.add("incomplete", check, "unexpected response shape")
        return status, None
    return status, data


def _commit_check(cfg: CorpusConfig) -> str:
    return f"commit {cfg.docs_source.commit[:12]}"


def _release_check(rec: ReleaseRecord) -> str:
    return f"release {rec.tag}"


def check_commit(cfg: CorpusConfig, fetch: Fetch, report: UpstreamReport) -> None:
    commit = cfg.docs_source.commit
    check = _commit_check(cfg)
    report.checks.append(check)
    url = f"{cfg.release_notes.api_base}/repos/{cfg.docs_source.repo}/commits/{commit}"
    status, data = _get_json(fetch, url, check, report)
    if status in DRIFT_STATUSES:
        report.add("drift", check, f"pinned commit not found (HTTP {status})")
    elif data is not None:
        sha = data.get("sha")
        if not isinstance(sha, str):
            report.add("incomplete", check, "unexpected response shape")
        elif sha != commit:
            report.add("drift", check, f"sha is {sha!r}, expected {commit!r}")


def check_release(
    cfg: CorpusConfig, rec: ReleaseRecord, fetch: Fetch, report: UpstreamReport
) -> None:
    check = _release_check(rec)
    report.checks.append(check)
    notes = cfg.release_notes
    url = f"{notes.api_base}/repos/{notes.repo}/releases/{rec.release_id}"
    status, data = _get_json(fetch, url, check, report)
    if status in DRIFT_STATUSES:
        report.add("drift", check, f"release id {rec.release_id} not found (HTTP {status})")
        return
    if data is None:
        return
    body = data.get("body")
    if "body" not in data or not (body is None or isinstance(body, str)):
        report.add("incomplete", check, "unexpected response shape")
        return
    expected: dict[str, object] = {
        "id": rec.release_id,
        "tag_name": rec.tag,
        "published_at": rec.published_at,
        "draft": False,
        "prerelease": False,
    }
    for key, want in expected.items():  # GitHub metadata: shown as expected and observed (D2)
        got = data.get(key)
        if got != want:
            report.add("drift", check, f"{key} is {got!r}, expected {want!r}")
    # The body: hashed and measured in memory only; its text is never kept or shown (D2).
    try:
        encoded = (body or "").encode("utf-8")
    except UnicodeEncodeError:  # for example an escaped lone surrogate; no payload is reported
        report.add("incomplete", check, "body is not valid UTF-8")
        return
    digest, size = hashlib.sha256(encoded).hexdigest(), len(encoded)
    del body, encoded
    if digest != rec.body_sha256:
        report.add("drift", check, f"body sha256 is {digest}, expected {rec.body_sha256}")
    if size != rec.body_bytes:
        report.add("drift", check, f"body byte count is {size}, expected {rec.body_bytes}")


def check_upstream(cfg: CorpusConfig, manifest: CorpusManifest, fetch: Fetch) -> UpstreamReport:
    """The pinned docs commit and every approved release, against the committed manifest."""
    report = UpstreamReport()
    _guarded(report, _commit_check(cfg), lambda: check_commit(cfg, fetch, report))
    for rec in manifest.release_notes.included:
        _guarded(report, _release_check(rec), partial(check_release, cfg, rec, fetch, report))
    return report


def _guarded(report: UpstreamReport, check: str, run: Callable[[], None]) -> None:
    """Run one check; an unexpected exception makes only that check incomplete (type name only,
    never its message), and the remaining checks still run."""
    try:
        run()
    except Exception as exc:
        if check not in report.checks:
            report.checks.append(check)
        report.add("incomplete", check, f"check failed unexpectedly ({type(exc).__name__})")
