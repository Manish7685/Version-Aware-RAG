"""GitHub release-note snapshots (RAG-M3 stage S2).

Owner rules this module enforces:

- Only the four approved releases (Q4), fetched **by their recorded release id**.
- The raw body is written **unchanged** (the API's ``body`` string, UTF-8 encoded, no newline
  translation) to the git-ignored cache, and **only after** every check has passed.
- Every fetch is checked against the recorded evidence: release id, tag, publish time,
  non-draft, non-prerelease and the body's sha256. Any difference stops the fetch: nothing is
  overwritten, the new text is saved beside the cache for inspection, and a unified diff against
  the previously verified local copy is produced when one exists (U2).
- An unavailable source fails clearly with the reason (HTTP status or network error).
- Licence status stays ``unverified`` and records are never redistributable (Q8).

Retrieval-text cleanup (Q7) is not done here: it belongs to parsing (S3) and never touches the
raw cache.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from version_aware_rag.corpus.config import IncludedRelease, ReleaseNotesConfig

GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "version-aware-rag-corpus"  # generic: no personal or account details

Fetcher = Callable[[str], bytes]


class ReleaseError(RuntimeError):
    """Base class for release-note acquisition problems."""


class ReleaseUnavailable(ReleaseError):
    """The source could not be retrieved (HTTP error, network failure, invalid response)."""


class ReleaseMismatch(ReleaseError):
    """The retrieved release differs from the recorded evidence."""

    def __init__(self, tag: str, problems: list[str], saved: Path | None, diff: str | None):
        self.tag, self.problems, self.saved, self.diff = tag, problems, saved, diff
        super().__init__(f"{tag}: " + "; ".join(problems))


def github_fetcher(timeout: float = 30.0, token: str | None = None) -> Fetcher:
    """Return a fetcher for the GitHub REST API.

    ``token`` is optional and only used when given (for example ``GITHUB_TOKEN`` in GitHub
    Actions, to avoid the shared unauthenticated rate limit). It is never logged.
    """

    def fetch(url: str) -> bytes:
        if not url.startswith("https://"):
            raise ReleaseUnavailable(f"refusing non-https URL {url}")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as resp:  # noqa: S310 (https checked above)
                data: bytes = resp.read()
                return data
        except HTTPError as exc:
            hint = " (GitHub rate limit?)" if exc.code in (403, 429) else ""
            raise ReleaseUnavailable(f"{url}: HTTP {exc.code} {exc.reason}{hint}") from exc
        except URLError as exc:
            raise ReleaseUnavailable(f"{url}: network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ReleaseUnavailable(f"{url}: timed out after {timeout}s") from exc

    return fetch


def default_fetcher() -> Fetcher:
    return github_fetcher(token=os.environ.get("GITHUB_TOKEN") or None)


# ------------------------------------------------------------------ paths and URLs


def api_url(notes: ReleaseNotesConfig, rel: IncludedRelease) -> str:
    return f"{notes.api_base}/repos/{notes.repo}/releases/{rel.release_id}"


def html_url(notes: ReleaseNotesConfig, tag: str) -> str:
    return f"https://github.com/{notes.repo}/releases/tag/{tag}"


def body_path(cache_dir: Path, tag: str) -> Path:
    return cache_dir / f"{tag}.md"


def fetch_record_path(cache_dir: Path, tag: str) -> Path:
    return cache_dir / f"{tag}.fetch.json"


# ------------------------------------------------------------------ fetching


@dataclass(frozen=True)
class FetchResult:
    tag: str
    release_id: int
    body_sha256: str
    body_bytes: int
    retrieved_at: str
    path: Path


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_release(
    notes: ReleaseNotesConfig,
    rel: IncludedRelease,
    cache_dir: Path,
    fetcher: Fetcher,
    now: Callable[[], str] = _now,
) -> FetchResult:
    """Fetch one release by id, verify it against the evidence, then write it to the cache."""
    url = api_url(notes, rel)
    raw = fetcher(url)
    retrieved_at = now()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ReleaseUnavailable(f"{rel.tag}: response from {url} is not JSON") from exc
    if not isinstance(data, dict):
        raise ReleaseUnavailable(f"{rel.tag}: unexpected response shape from {url}")

    problems: list[str] = []
    expected_fields = {
        "id": rel.release_id,
        "tag_name": rel.tag,
        "published_at": rel.published_at,
        "draft": False,
        "prerelease": False,
    }
    for key, expected in expected_fields.items():
        if data.get(key) != expected:
            problems.append(f"{key} is {data.get(key)!r}, expected {expected!r}")
    body = data.get("body")
    if not isinstance(body, str) or not body:
        problems.append("release body is missing or empty")
        body_bytes = b""
    else:
        body_bytes = body.encode("utf-8")
    digest = hashlib.sha256(body_bytes).hexdigest()
    if body_bytes and digest != rel.body_sha256:
        problems.append(f"body sha256 is {digest}, expected {rel.body_sha256}")

    cache_dir.mkdir(parents=True, exist_ok=True)
    if problems:
        saved = None
        diff = None
        if body_bytes:
            saved = cache_dir / f"{rel.tag}.mismatch-{digest[:12]}.md"
            saved.write_bytes(body_bytes)
            previous = body_path(cache_dir, rel.tag)
            if previous.is_file():
                diff = "".join(
                    difflib.unified_diff(
                        previous.read_bytes().decode("utf-8", "replace").splitlines(keepends=True),
                        body_bytes.decode("utf-8", "replace").splitlines(keepends=True),
                        fromfile=f"{rel.tag} (verified local copy)",
                        tofile=f"{rel.tag} (fetched {retrieved_at})",
                    )
                )
        raise ReleaseMismatch(rel.tag, problems, saved, diff)

    target = body_path(cache_dir, rel.tag)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_bytes(body_bytes)
    tmp.replace(target)
    record = {
        "tag": rel.tag,
        "release_id": rel.release_id,
        "api_url": url,
        "source_url": html_url(notes, rel.tag),
        "published_at": data["published_at"],
        "retrieved_at": retrieved_at,
        "body_sha256": digest,
        "body_bytes": len(body_bytes),
    }
    fetch_record_path(cache_dir, rel.tag).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return FetchResult(rel.tag, rel.release_id, digest, len(body_bytes), retrieved_at, target)


def fetch_all(notes: ReleaseNotesConfig, cache_dir: Path, fetcher: Fetcher) -> list[FetchResult]:
    """Fetch every approved release; stops at the first failure (nothing later is fetched)."""
    return [fetch_release(notes, rel, cache_dir, fetcher) for rel in notes.included]


# ------------------------------------------------------------------ offline verification


@dataclass(frozen=True)
class ReleaseCheck:
    tag: str
    status: str  # "verified" | "missing" | "changed"
    expected_sha256: str
    actual_sha256: str | None
    last_retrieved_at: str | None


@dataclass
class ReleaseVerifyReport:
    checks: list[ReleaseCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.status == "verified" for c in self.checks)


def verify_releases(notes: ReleaseNotesConfig, cache_dir: Path) -> ReleaseVerifyReport:
    """Check the cached raw bodies against the recorded hashes (no network)."""
    report = ReleaseVerifyReport()
    for rel in notes.included:
        path = body_path(cache_dir, rel.tag)
        record_path = fetch_record_path(cache_dir, rel.tag)
        retrieved = None
        if record_path.is_file():
            retrieved = json.loads(record_path.read_text(encoding="utf-8")).get("retrieved_at")
        if not path.is_file():
            report.checks.append(ReleaseCheck(rel.tag, "missing", rel.body_sha256, None, retrieved))
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        status = (
            "verified" if digest == rel.body_sha256 and len(data) == rel.body_bytes else "changed"
        )
        report.checks.append(ReleaseCheck(rel.tag, status, rel.body_sha256, digest, retrieved))
    return report
