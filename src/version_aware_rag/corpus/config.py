"""Typed corpus configuration loaded from ``configs/corpus.toml``.

The approved version set is fixed here (owner decision Q1, 2026-11-20). A config that names any
other version, or maps a version to a different behaviour package, is rejected.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Owner-approved corpus versions -> behaviour package (assumption A1). Changing this mapping
# changes an approved decision and needs owner approval.
APPROVED_VERSIONS: dict[str, str] = {
    "1.2": "1.2.2",
    "1.3": "1.3.2",
    "1.4": "1.4.5",
    "1.5": "1.5.6",
    "2.0": "2.0.1",
}

DocsVersion = Literal["1.2", "1.3", "1.4", "1.5", "2.0"]

_SHA1 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"{what} must be lowercase hex of the right length, got {value!r}")
    return value


class ExpectedSnapshot(_Strict):
    """Approved evidence for one docs version (RAG-M1 manifest §1.1)."""

    git_tree: str
    md_files: int = Field(gt=0)
    words: int = Field(gt=0)
    content_hash: str

    @field_validator("git_tree")
    @classmethod
    def _tree(cls, v: str) -> str:
        return _check(_SHA1, v, "git_tree")

    @field_validator("content_hash")
    @classmethod
    def _hash(cls, v: str) -> str:
        return _check(_SHA256, v, "content_hash")


class VersionSpec(_Strict):
    version: DocsVersion
    docs_dir: str
    behaviour_package: str
    expected: ExpectedSnapshot

    @model_validator(mode="after")
    def _consistent(self) -> VersionSpec:
        if self.docs_dir != f"docs/{self.version}":
            raise ValueError(f"docs_dir for {self.version} must be docs/{self.version}")
        if APPROVED_VERSIONS[self.version] != self.behaviour_package:
            raise ValueError(
                f"behaviour package for {self.version} must be "
                f"{APPROVED_VERSIONS[self.version]} (approved), got {self.behaviour_package}"
            )
        return self


class DocsSource(_Strict):
    source_id: str
    repo: str
    repo_url: str
    commit: str
    url_base: str
    licence: str
    licence_status: Literal["verified"]
    copyright: str
    licence_file: str
    licence_file_git_blob: str
    licence_file_sha256: str

    @field_validator("commit", "licence_file_git_blob")
    @classmethod
    def _sha1(cls, v: str) -> str:
        return _check(_SHA1, v, "git object id")

    @field_validator("licence_file_sha256")
    @classmethod
    def _sha256(cls, v: str) -> str:
        return _check(_SHA256, v, "licence_file_sha256")


class Paths(_Strict):
    cache_dir: Path
    manifest: Path


# Owner decision Q4 (2026-11-20): only these GitHub releases enter the retrieval corpus.
APPROVED_RELEASE_TAGS: tuple[str, ...] = ("v1.2.1", "v1.2.2", "v1.3.1", "v1.3.2")

_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _check_timestamp(v: str) -> str:
    if not _UTC_TIMESTAMP.fullmatch(v):
        raise ValueError(f"timestamp must look like 2025-03-05T18:03:02Z, got {v!r}")
    return v


class InvestigatedRelease(_Strict):
    """A release that was examined but deliberately not included (owner Q4)."""

    tag: str
    release_id: int = Field(gt=0)
    published_at: str
    body_sha256: str
    body_bytes: int = Field(gt=0)

    @field_validator("published_at")
    @classmethod
    def _ts(cls, v: str) -> str:
        return _check_timestamp(v)

    @field_validator("body_sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        return _check(_SHA256, v, "body_sha256")


class IncludedRelease(InvestigatedRelease):
    """An approved release note, with the evidence its fetched text must match."""

    version: DocsVersion
    introduced_in: str
    tag_commit: str

    @field_validator("tag_commit")
    @classmethod
    def _commit(cls, v: str) -> str:
        return _check(_SHA1, v, "tag_commit")

    @model_validator(mode="after")
    def _version_from_tag(self) -> IncludedRelease:
        # Explicit and checked, never inferred: v1.2.1 -> introduced_in 1.2.1 -> series 1.2.
        if self.tag != f"v{self.introduced_in}":
            raise ValueError(f"tag {self.tag} does not match introduced_in {self.introduced_in}")
        if not self.introduced_in.startswith(f"{self.version}."):
            raise ValueError(f"introduced_in {self.introduced_in} is not in series {self.version}")
        return self


class ReleaseNotesConfig(_Strict):
    source_id: str
    repo: str
    api_base: str
    cache_dir: Path
    evidence_retrieved_at: str
    licence_status: Literal["unverified"]  # owner Q8: never inferred from the repo licence
    licence_note: str
    attribution_template: str
    retrieval_cleanup: str
    investigated_not_included: tuple[InvestigatedRelease, ...]
    included: tuple[IncludedRelease, ...]

    @field_validator("api_base")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("api_base must be an https URL")
        return v.rstrip("/")

    @field_validator("evidence_retrieved_at")
    @classmethod
    def _ts(cls, v: str) -> str:
        return _check_timestamp(v)

    @field_validator("attribution_template")
    @classmethod
    def _template(cls, v: str) -> str:
        if "{tag}" not in v:
            raise ValueError("attribution_template must contain {tag}")
        return v

    @model_validator(mode="after")
    def _approved(self) -> ReleaseNotesConfig:
        tags = tuple(r.tag for r in self.included)
        if tags != APPROVED_RELEASE_TAGS:
            raise ValueError(
                f"included releases must be exactly {list(APPROVED_RELEASE_TAGS)} (owner Q4), "
                f"got {list(tags)}"
            )
        others = [r.tag for r in self.investigated_not_included]
        if len(set(others)) != len(others) or set(others) & set(tags):
            raise ValueError("investigated_not_included must be unique and disjoint from included")
        ids = [r.release_id for r in (*self.included, *self.investigated_not_included)]
        if len(set(ids)) != len(ids):
            raise ValueError("release ids must be unique")
        return self


# Owner decision L2 (2026-11-21): these duckdb-web release posts, at the pinned commit.
APPROVED_RELEASE_POSTS: tuple[str, ...] = tuple(
    f"_posts/{name}.md"
    for name in (
        "2025-02-05-announcing-duckdb-120",
        "2025-05-21-announcing-duckdb-130",
        "2025-09-16-announcing-duckdb-140",
        "2025-10-07-announcing-duckdb-141",
        "2025-11-12-announcing-duckdb-142",
        "2025-12-09-announcing-duckdb-143",
        "2026-01-26-announcing-duckdb-144",
        "2026-03-09-announcing-duckdb-150",
        "2026-03-23-announcing-duckdb-151",
        "2026-04-13-announcing-duckdb-152",
        "2026-05-20-announcing-duckdb-153",
        "2026-06-17-announcing-duckdb-145",
        "2026-06-17-announcing-duckdb-154",
        "2026-07-22-announcing-duckdb-155",
        "2026-09-28-announcing-duckdb-156",
        "2026-10-21-announcing-duckdb-200",
        "2026-11-20-announcing-duckdb-201",
    )
)
_POST_PATH = re.compile(r"_posts/(\d{4})-(\d{2})-(\d{2})-([a-z0-9-]+)\.md")
_RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _post_parts(path: str) -> re.Match[str]:
    m = _POST_PATH.fullmatch(path)
    if m is None:
        raise ValueError(f"not a release-post path: {path}")
    return m


class ReleasePost(_Strict):
    path: str
    release: str
    git_blob: str
    sha256: str

    @field_validator("git_blob")
    @classmethod
    def _blob(cls, v: str) -> str:
        return _check(_SHA1, v, "git_blob")

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        return _check(_SHA256, v, "sha256")

    @model_validator(mode="after")
    def _shape(self) -> ReleasePost:
        if not _POST_PATH.fullmatch(self.path):
            raise ValueError(f"not a release-post path: {self.path}")
        if not _RELEASE.fullmatch(self.release):
            raise ValueError(f"release must look like 1.4.5, got {self.release!r}")
        return self

    @property
    def version(self) -> str:
        """Docs series the post belongs to, for example 1.4 for release 1.4.5."""
        major, minor, _ = self.release.split(".")
        return f"{major}.{minor}"

    @property
    def published_date(self) -> str:
        m = _post_parts(self.path)
        return f"{m[1]}-{m[2]}-{m[3]}"

    def canonical_url(self, site: str, pattern: str) -> str:
        m = _post_parts(self.path)
        return pattern.format(site=site.rstrip("/"), yyyy=m[1], mm=m[2], dd=m[3], slug=m[4])


class ReleasePostsConfig(_Strict):
    repository_licence: str
    repository_licence_file: str
    repository_copyright: str
    repository_copyright_end_year: int
    licence_applicability: Literal["uncertain", "confirmed"]
    licence_status: Literal["unverified", "verified"]
    licence_basis: str = Field(min_length=20)
    evidence_inspected: tuple[str, ...] = Field(min_length=1)
    resolution_evidence: tuple[str, ...]
    url_pattern: str
    posts: tuple[ReleasePost, ...]

    @model_validator(mode="after")
    def _approved(self) -> ReleasePostsConfig:
        paths = tuple(p.path for p in self.posts)
        if paths != APPROVED_RELEASE_POSTS:
            raise ValueError(
                "release posts must be exactly the 17 approved posts in path order (owner L2)"
            )
        for field_name in ("{site}", "{yyyy}", "{mm}", "{dd}", "{slug}"):
            if field_name not in self.url_pattern:
                raise ValueError(f"url_pattern must contain {field_name}")
        if any(p.version not in APPROVED_VERSIONS for p in self.posts):
            raise ValueError("every release post must belong to an approved docs version")
        if self.licence_applicability == "uncertain":
            if self.licence_status != "unverified":
                raise ValueError(
                    "uncertain licence applicability requires licence_status unverified"
                )
            if not self.resolution_evidence:
                raise ValueError("uncertain applicability must say what evidence would resolve it")
        return self

    @property
    def redistributable(self) -> bool:
        return self.licence_applicability == "confirmed"


class CorpusConfig(_Strict):
    library: Literal["duckdb"]
    docs_source: DocsSource
    paths: Paths
    release_notes: ReleaseNotesConfig
    release_posts: ReleasePostsConfig
    versions: tuple[VersionSpec, ...]

    @model_validator(mode="after")
    def _approved_set(self) -> CorpusConfig:
        names = [v.version for v in self.versions]
        if names != list(APPROVED_VERSIONS):
            raise ValueError(
                f"versions must be exactly {list(APPROVED_VERSIONS)} in that order "
                f"(owner-approved set), got {names}"
            )
        return self

    def cache_path(self, root: Path) -> Path:
        """Commit-keyed cache directory, so a cache can't be reused across commits."""
        base = root / self.paths.cache_dir
        return base.with_name(f"{base.name}-{self.docs_source.commit[:12]}")


class ExcludedPage(_Strict):
    """A page kept in parse output but excluded from the retrieval corpus (reviewed in S3)."""

    path: str
    kind: Literal["landing"]
    reason: str = Field(min_length=10)


class SamplePage(_Strict):
    path: str
    label: str
    lines: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _range(self) -> SamplePage:
        if self.lines is not None and not 1 <= self.lines[0] <= self.lines[1]:
            raise ValueError(f"invalid line range {self.lines} for {self.path}")
        return self


class ParsingConfig(_Strict):
    site_base: str
    excluded_pages: tuple[ExcludedPage, ...]
    samples: tuple[SamplePage, ...] = ()

    @field_validator("site_base")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("site_base must be an https URL")
        return v.rstrip("/")

    @model_validator(mode="after")
    def _unique(self) -> ParsingConfig:
        paths = [e.path for e in self.excluded_pages]
        if len(set(paths)) != len(paths):
            raise ValueError("excluded_pages must be unique")
        return self

    def exclusions(self) -> dict[str, ExcludedPage]:
        return {e.path: e for e in self.excluded_pages}


def load_parsing_config(path: Path) -> tuple[ParsingConfig, str]:
    """Return the validated parser config and the sha256 of the exact file bytes."""
    raw = path.read_bytes()
    return ParsingConfig.model_validate(tomllib.loads(raw.decode("utf-8"))), hashlib.sha256(
        raw
    ).hexdigest()


def load_config(path: Path) -> tuple[CorpusConfig, str]:
    """Return the validated config and the sha256 of the exact file bytes."""
    raw = path.read_bytes()
    cfg = CorpusConfig.model_validate(tomllib.loads(raw.decode("utf-8")))
    return cfg, hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------- RAG-M3 S4 chunking (S4-D6)

APPROVED_MIGRATION_GUIDES = (
    "docs/2.0/clients/python/migration.md",
    "docs/2.0/operations_manual/migration.md",
)


class ApiDetection(_Strict):
    function_table_first_cells: tuple[str, ...] = Field(min_length=1)
    function_table_other_cells: tuple[str, ...] = Field(min_length=1)


class OversizeRules(_Strict):
    """Owner Q3 and S4-D3. Each value is fixed: the rule is recorded, not tunable away."""

    table: Literal["split_between_rows_repeat_header"]
    code: Literal["keep_whole_flag"]
    list: Literal["split_between_top_level_items"]
    api_entry_block: Literal["keep_whole_flag"]


class MigrationGuide(_Strict):
    path: str
    introduced_in: str

    @model_validator(mode="after")
    def _consistent(self) -> MigrationGuide:
        m = _RELEASE.fullmatch(self.introduced_in)
        if not m:
            raise ValueError(f"introduced_in must be X.Y.Z: {self.introduced_in!r}")
        series = f"{m.group(1)}.{m.group(2)}"
        if not self.path.startswith(f"docs/{series}/") or not self.path.endswith(".md"):
            raise ValueError(f"migration guide {self.path} is not a docs/{series}/ page")
        return self


class ChunkingConfig(_Strict):
    chunker_version: str = Field(pattern=r"^s4-\d+$")
    target_tokens: int = Field(gt=0)
    hard_limit_tokens: int = Field(gt=0)
    merge_below_tokens: int = Field(gt=0)
    overlap_ratio: float = Field(gt=0, lt=1)
    boundary_levels: tuple[int, ...]
    skip_h1_equal_to_title: bool
    id_hex_chars: Literal[20]  # S4-D1: 80 bits
    api_detection: ApiDetection
    oversize: OversizeRules
    migration_guides: tuple[MigrationGuide, ...]

    @model_validator(mode="after")
    def _approved(self) -> ChunkingConfig:
        if not self.merge_below_tokens < self.target_tokens < self.hard_limit_tokens:
            raise ValueError("sizes must satisfy merge_below < target < hard_limit")
        if self.boundary_levels != (1, 2, 3, 4, 5, 6):
            raise ValueError("boundary_levels must be exactly 1-6 (R1, S4-D6)")
        if tuple(sorted(g.path for g in self.migration_guides)) != APPROVED_MIGRATION_GUIDES:
            raise ValueError("migration_guides must be exactly the two approved guides (plan §4.6)")
        return self


def load_chunking_config(path: Path) -> tuple[ChunkingConfig, str]:
    """Return the validated chunking config and the sha256 of the exact file bytes."""
    raw = path.read_bytes()
    cfg = ChunkingConfig.model_validate(tomllib.loads(raw.decode("utf-8")))
    return cfg, hashlib.sha256(raw).hexdigest()
