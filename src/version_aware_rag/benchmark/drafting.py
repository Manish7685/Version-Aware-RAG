"""Gemini-assisted drafting of pilot candidates (RAG-M4 S3; design §3.4 step 3 and §3.5 step 3;
protocol v0 §10).

Pipeline: plan -> passages -> payload -> privacy audit -> (cache or Gemini) -> validation -> drafts.

- **Eligibility.** Only ``pilot_candidate`` inventory records. ``unresolved`` (VARIANT, MERGE)
  and ``excluded`` records are never used.
- **Permitted evidence only** (owner decision, S2 review). Passages are MIT documentation:
  the record's cited ``docs_page`` and ``migration_guide`` ranges, and corpus chunks of those
  kinds that contain the change's identifiers. Restricted sources (release posts, release notes)
  are never put in a payload; drafts keep only their path and line references.
- **Fail-closed audit.** Every payload is checked against every restricted line and every
  10-word sequence of the release bodies, the release posts and the local D1 sidecar, and for
  key-like strings. A passage that fails is dropped; a payload that still fails is never sent.
  If a restricted source cannot be loaded, nothing is sent.
- **Prompts** are the versioned templates of ``benchmark/protocol/prompts``, verified against
  the sha256 values in ``protocol_v0.toml`` before any request.
- **Just before each request** the exact prompt is audited again; a failure skips it.
- **Cache** (``benchmark/drafts/cache.jsonl``, git-ignored, local only). Every response is
  stored with the model id, generation config, prompt hashes, request hash and timestamp; a
  cached request is never sent again, so runs resume after rate limits or the daily budget.
- **Drafts** (``benchmark/drafts/pilot_drafts_v0.jsonl``, git-ignored) are V0 candidates: never
  human-verified, never gold. Docs answers only (``gold_basis = docs``); runtime observations
  are not merged in.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from version_aware_rag.benchmark.gemini import (
    FatalError,
    MalformedResponse,
    RateLimited,
    TransientError,
)
from version_aware_rag.benchmark.inventory import ChangeRecord
from version_aware_rag.benchmark.quota import (
    DailyLimitReached,
    QuotaError,
    QuotaLedger,
    estimate_input_tokens,
)
from version_aware_rag.evidence.models import RESTRICTED_SOURCE_KINDS
from version_aware_rag.evidence.verify import ClaimSource

PERMITTED_KINDS = frozenset({"docs_page", "migration_guide"})
CORPUS_VERSIONS = ("1.2", "1.3", "1.4", "1.5", "2.0")
TYPES = ("T1", "T2", "T3", "T4", "T5")
PROMPT_DIR = Path("benchmark/protocol/prompts")
QUOTA_LEDGER_PATH = Path("benchmark/drafts/quota_ledger.json")  # git-ignored, persistent
QUOTA_LOCK_PATH = Path("benchmark/drafts/run.lock")
PROTOCOL_TOML = Path("benchmark/protocol/protocol_v0.toml")
CACHE_PATH = Path("benchmark/drafts/cache.jsonl")
DRAFTS_PATH = Path("benchmark/drafts/pilot_drafts_v0.jsonl")
REVIEW_SHEET_PATH = Path("benchmark/drafts/review_sheet_v0.md")  # git-ignored, for the owner
TYPE_TEMPLATE = {t: f"draft_{t}.md" for t in TYPES}  # protocol v0 (hash-recorded, unchanged)
# Prompt v1 (owner, 2026-11-23): only T2 changes; v1 files are recorded in prompts_v1.toml.
PROMPTS_V1_TOML = Path("benchmark/protocol/prompts_v1.toml")
V1_TEMPLATES = ("v1/draft_T2.md", "v1/draft_common.md", "v1/draft_T5.md")
COMMON_V1 = "v1/draft_common.md"
ACTIVE_TEMPLATE = {**TYPE_TEMPLATE, "T2": "v1/draft_T2.md"}
PROMPT_VERSION = {t: ("v1" if ACTIVE_TEMPLATE[t].startswith("v1/") else "v0") for t in TYPES}
COMMON_TEMPLATE = "draft_common.md"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_KEY_LIKE = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
SHINGLE = 10


class DraftingError(RuntimeError):
    """A drafting step that cannot proceed; nothing is sent. Messages never quote source text."""


# ---------------------------------------------------------------------------------------------
# configuration


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(_Strict):
    id: str
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(gt=0)


class Limits(_Strict):
    """Application limits, enforced across runs by ``quota.QuotaLedger`` (retries included)."""

    requests_per_minute: int = Field(gt=0)
    input_tokens_per_minute: int = Field(gt=0)
    requests_per_day: int = Field(gt=0)  # rolling 24 hours, persisted
    max_requests_per_run: int = Field(gt=0)
    min_seconds_between_requests: float = Field(ge=0.0)
    max_retries: int = Field(ge=0)
    backoff_base_seconds: float = Field(gt=0.0)
    backoff_jitter: float = Field(ge=0.0, le=1.0)  # extra delay, a random fraction of the backoff
    max_backoff_seconds: float = Field(gt=0.0)


class AccountQuota(_Strict):
    """The owner's Google AI Studio quota for the model; application limits stay below it."""

    requests_per_minute: int = Field(gt=0)
    input_tokens_per_minute: int = Field(gt=0)
    requests_per_day: int = Field(gt=0)


class PlanSettings(_Strict):
    T1: int = Field(ge=0)
    T2: int = Field(ge=0)
    T3: int = Field(ge=0)
    T4: int = Field(ge=0)
    T5: int = Field(ge=0)
    t2_three_version_sets: int = Field(ge=0)
    # Owner-approved T2 changes (fixed; never substituted) and sets held for manual review.
    t2_changes: tuple[str, ...] = ()
    t2_held: tuple[str, ...] = ()
    t2_passages_per_version: int = Field(default=2, gt=0)
    # Fixed T3 priority: keeps the T3 order stable when other types' counts change.
    t3_priority: tuple[str, ...] = ()
    # Extra T4 items (owner-approved), each with passages from distinct pages.
    t4_extra: tuple[str, ...] = ()
    # Items up to this ordinal per type keep the v0 shared prompt (their responses are cached);
    # later items use the v1 shared prompt (protocol v1 §2.4). Empty: every item uses v0.
    common_v0_through: dict[str, int] = {}
    # Items redrafted with the v1 prompts after failing V1 (design §3.4 step 7: new drafts of the
    # same topic; the superseded responses stay cached).
    redraft_v1: tuple[str, ...] = ()
    max_passage_chars: int = Field(gt=0)
    max_keyword_passages_per_version: int = Field(gt=0)


class ChangeHints(_Strict):
    keywords: tuple[str, ...] = Field(min_length=1)
    t5: Literal["", "before", "after"]
    # Owner exclusions: question types this change must not be drafted as (reason required).
    exclude_types: tuple[Literal["T1", "T2", "T3", "T4", "T5"], ...] = ()
    exclude_reason: str = ""

    @model_validator(mode="after")
    def _reasoned(self) -> Self:
        if self.exclude_types and not self.exclude_reason.strip():
            raise ValueError("exclude_types needs an exclude_reason")
        return self


class DraftingConfig(_Strict):
    model: ModelSettings
    account_quota: AccountQuota
    limits: Limits
    plan: PlanSettings
    changes: dict[str, ChangeHints]

    @model_validator(mode="after")
    def _within_quota(self) -> Self:
        q, lim = self.account_quota, self.limits
        for name in ("requests_per_minute", "input_tokens_per_minute", "requests_per_day"):
            if getattr(lim, name) > getattr(q, name):
                raise ValueError(f"limits.{name} exceeds the account quota")
        if lim.max_requests_per_run > lim.requests_per_day:
            raise ValueError("limits.max_requests_per_run exceeds limits.requests_per_day")
        return self


def load_drafting_config(path: Path) -> DraftingConfig:
    return DraftingConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------------------------
# prompts


def verify_prompts(root: Path) -> dict[str, str]:
    """The prompt texts, after checking every v0 file against protocol_v0.toml and every v1 file
    against prompts_v1.toml; raises otherwise."""
    texts: dict[str, str] = {}
    for toml_path, names in (
        (PROTOCOL_TOML, (COMMON_TEMPLATE, *TYPE_TEMPLATE.values())),
        (PROMPTS_V1_TOML, V1_TEMPLATES),
    ):
        recorded = tomllib.loads((root / toml_path).read_text(encoding="utf-8"))["prompts"]
        for name in names:
            data = (root / PROMPT_DIR / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != recorded.get(name):
                raise DraftingError(f"prompt {name} does not match its sha256 in {toml_path.name}")
            texts[name] = data.decode("utf-8")
    return texts


def type_template(spec: ItemSpec) -> str:
    """The type prompt file: ACTIVE_TEMPLATE, or the v1 file for a redraft when one exists."""
    v1 = f"v1/draft_{spec.type}.md"
    return v1 if spec.type_prompt == "v1" and v1 in V1_TEMPLATES else ACTIVE_TEMPLATE[spec.type]


def common_template(spec: ItemSpec) -> str:
    """The shared prompt file for an item: v0 for items drafted before protocol v1, v1 after."""
    return COMMON_V1 if spec.common_prompt == "v1" else COMMON_TEMPLATE


def prompt_sha256(texts: dict[str, str], name: str) -> str:
    return hashlib.sha256(texts[name].encode("utf-8")).hexdigest()


def render(template: str, values: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in values:
            raise DraftingError(f"no value for placeholder {{{{{key}}}}}")
        return values[key]

    return _PLACEHOLDER.sub(sub, template)


# ---------------------------------------------------------------------------------------------
# passages (permitted evidence only)


@dataclass(frozen=True)
class Passage:
    passage_id: str
    version: str
    source_kind: str
    source_path: str
    text: str


@dataclass
class PassagePool:
    """Permitted MIT passages: cited inventory ranges and identifier-matching corpus chunks."""

    chunks: dict[str, list[dict[str, Any]]]  # version -> permitted chunks, sorted by chunk_id
    index: dict[tuple[str, str], ClaimSource]
    max_chars: int
    per_version: int

    def cited(self, record: ChangeRecord) -> list[Passage]:
        out = []
        for ref in record.sources:
            if ref.source_kind not in PERMITTED_KINDS:
                continue  # restricted: reference only, never text
            src = self.index.get((ref.source_kind, ref.source_path))
            data = src.read_bytes() if src is not None else None
            if src is None or data is None:
                raise DraftingError(
                    f"{record.change_id}: cited source {ref.source_path} not available"
                )
            lines = data.decode("utf-8").splitlines()[ref.line_start - 1 : ref.line_end]
            out.append(
                Passage(
                    f"src:{ref.source_path}:{ref.line_start}-{ref.line_end}",
                    src.doc_version,
                    ref.source_kind,
                    ref.source_path,
                    "\n".join(lines),
                )
            )
        return out

    def keyword(self, version: str, keywords: Sequence[str]) -> list[Passage]:
        found = [
            c
            for c in self.chunks.get(version, [])
            if len(c["content"]) <= self.max_chars and any(k in c["content"] for k in keywords)
        ]
        return [_chunk_passage(c) for c in found[: self.per_version]]

    def stable(self, keywords: Sequence[str]) -> list[str]:
        """Contents of identifier-matching chunks that are identical in all five versions."""
        sets = [
            {
                c["content"]
                for c in self.chunks.get(v, [])
                if len(c["content"]) <= self.max_chars and any(k in c["content"] for k in keywords)
            }
            for v in CORPUS_VERSIONS
        ]
        return sorted(set.intersection(*sets)) if all(sets) else []

    def chunk_with_content(self, version: str, content: str) -> Passage:
        chunk = next(c for c in self.chunks[version] if c["content"] == content)
        return _chunk_passage(chunk)


def _chunk_passage(c: dict[str, Any]) -> Passage:
    return Passage(
        f"chunk:{c['chunk_id']}", c["version"], c["source_kind"], c["source_path"], c["content"]
    )


def load_permitted_chunks(chunk_dir: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for version in CORPUS_VERSIONS:
        path = chunk_dir / f"{version}.jsonl"
        try:
            rows = [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line
            ]
        except OSError as exc:
            raise DraftingError(
                f"chunk file {path.name} not available ({type(exc).__name__})"
            ) from None
        out[version] = sorted(
            (r for r in rows if r["source_kind"] in PERMITTED_KINDS), key=lambda r: r["chunk_id"]
        )
    return out


# ---------------------------------------------------------------------------------------------
# plan


class ItemSpec(_Strict):
    item_id: str
    type: Literal["T1", "T2", "T3", "T4", "T5"]
    target_version: str
    version_set: tuple[str, ...]
    change_id: str
    boundary: str
    passage_ids: tuple[str, ...]
    restricted_refs: tuple[str, ...]  # "kind:path:start-end", never text
    probe_ids: tuple[str, ...]
    review_hold: str = ""  # non-empty: kept for manual review, never counted toward a target
    common_prompt: Literal["v0", "v1"] = "v0"  # shared prompt version used for this item
    type_prompt: Literal["v0", "v1"] = "v0"  # v1 only for redrafts (T2 always uses v1)


@dataclass
class PlannedItem:
    spec: ItemSpec
    passages: list[Passage]
    record: ChangeRecord


def _neighbour(version: str, previous: str) -> str | None:
    i = CORPUS_VERSIONS.index(version)
    if i + 1 < len(CORPUS_VERSIONS):
        return CORPUS_VERSIONS[i + 1]
    j = CORPUS_VERSIONS.index(previous) if previous in CORPUS_VERSIONS else -1
    return CORPUS_VERSIONS[j - 1] if j > 0 else None


def _restricted_refs(record: ChangeRecord) -> tuple[str, ...]:
    return tuple(
        f"{r.source_kind}:{r.source_path}:{r.line_start}-{r.line_end}"
        for r in record.sources
        if r.source_kind in RESTRICTED_SOURCE_KINDS
    )


def _dedupe(passages: list[Passage]) -> list[Passage]:
    seen: set[str] = set()
    return [p for p in passages if not (p.passage_id in seen or seen.add(p.passage_id))]  # type: ignore[func-returns-value]


_BEHAVIOUR_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "is",
        "are",
        "in",
        "from",
        "up",
        "to",
        "with",
        "and",
        "of",
        "it",
        "on",
        "for",
        "as",
        "returns",
        "return",
        "error",
        "parser",
        "works",
        "lists",
        "no",
        "longer",
        "exists",
        "raise",
        "raises",
        "both",
        "now",
        "when",
        "be",
        "that",
        "this",
        "by",
        "or",
        "into",
    ]
)


def behaviour_terms(record: ChangeRecord, keywords: Sequence[str]) -> tuple[set[str], set[str]]:
    """Terms specific to the record's own before/after descriptions (never runtime data)."""
    ident = {t.lower() for k in keywords for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", k)}

    def terms(text: str) -> set[str]:
        toks = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|\d+(?:\.\d+)?", text)}
        return {
            t
            for t in toks
            if t not in _BEHAVIOUR_STOP
            and t not in ident
            and not re.fullmatch(r"\d+\.\d+\.\d+|\d", t)
        }

    before, after = terms(record.before), terms(record.after)
    return before - after, after - before


def _shingles(text: str, n: int = 5) -> set[str]:
    w = text.lower().split()
    return {" ".join(w[i : i + n]) for i in range(max(len(w) - n + 1, 1))} if w else set()


def t2_passages(
    record: ChangeRecord,
    versions: Sequence[str],
    keywords: Sequence[str],
    pool: PassagePool,
    per_version: int,
) -> list[Passage]:
    """Documentation-diff passage selection for one T2 set: pages that mention the identifier in
    every version of the set, ranked by changed identifier lines that carry before/after-specific
    terms; for each version, the chunks of that version's own page that contain those changed
    lines. The cited migration guide is added for its own version only. Never runtime evidence."""
    before_t, after_t = behaviour_terms(record, keywords)
    page_lines: dict[str, dict[str, list[str]]] = {}
    for (kind, path), src in pool.index.items():
        if kind != "docs_page" or not any(path.startswith(f"docs/{v}/") for v in versions):
            continue
        data = src.read_bytes()
        if data is None:
            continue
        v, rel = path.split("/")[1], path.split("/", 2)[2]
        lines = [
            " ".join(x.split())
            for x in data.decode("utf-8", "replace").splitlines()
            if any(k in x for k in keywords)
        ]
        if lines:
            page_lines.setdefault(rel, {})[v] = lines
    ranked = []
    for rel, by_v in page_lines.items():
        if set(by_v) != set(versions):
            continue  # the page must document the identifier in every version of the set
        changed = {
            v: [x for x in by_v[v] if not all(x in by_v[o] for o in versions)] for v in versions
        }
        score = sum(
            1
            for v in versions
            for x in changed[v]
            if any(t in x.lower() for t in before_t | after_t)
        )
        if score:
            ranked.append((-score, rel, changed))
    ranked.sort()
    out: list[Passage] = []
    for v in versions:
        picked: list[Passage] = []
        for _, rel, changed in ranked:
            want = set().union(*(_shingles(x) for x in changed[v])) if changed[v] else set()
            for c in pool.chunks.get(v, []):
                if len(picked) >= per_version:
                    break
                if c["source_path"] != f"docs/{v}/{rel}" or len(c["content"]) > pool.max_chars:
                    continue
                if want & _shingles(" ".join(c["content"].split())):
                    picked.append(_chunk_passage(c))
            if len(picked) >= per_version:
                break
        out += picked
    guides = record.model_copy(
        update={"sources": tuple(x for x in record.sources if x.source_kind == "migration_guide")}
    )
    out += [p for p in pool.cited(guides) if p.version in versions]
    return out


def page_diverse_passages(
    record: ChangeRecord, keywords: Sequence[str], pool: PassagePool, pages: int = 3
) -> list[Passage]:
    """The cited permitted passages, then the first identifier passage of each further page of the
    record's own version (by chunk id), up to ``pages`` distinct pages in total."""
    out = _dedupe(pool.cited(record))
    seen = {p.source_path for p in out}
    for c in pool.chunks.get(record.version, []):
        if len(seen) >= pages:
            break
        if c["source_path"] in seen or len(c["content"]) > pool.max_chars:
            continue
        if any(k in c["content"] for k in keywords):
            out.append(_chunk_passage(c))
            seen.add(c["source_path"])
    return out


def build_plan(
    records: Sequence[ChangeRecord], config: DraftingConfig, pool: PassagePool
) -> list[PlannedItem]:
    """The deterministic list of items to draft. Only pilot candidates are used."""
    pilots = sorted(
        (r for r in records if r.status == "pilot_candidate"),
        key=lambda r: (CORPUS_VERSIONS.index(r.version), r.change_id),
    )
    missing = [r.change_id for r in pilots if r.change_id not in config.changes]
    if missing:
        raise DraftingError(f"no drafting hints for {', '.join(missing)}")
    items: list[PlannedItem] = []

    def add(
        kind: str,
        record: ChangeRecord,
        target: str,
        version_set: Sequence[str],
        passages: list[Passage],
        item_id: str | None = None,
        review_hold: str = "",
    ) -> None:
        passages = _dedupe(passages)
        n = sum(1 for i in items if i.spec.type == kind) + 1
        spec = ItemSpec(
            item_id=item_id or f"{kind}-{n:02d}",
            review_hold=review_hold,
            common_prompt=(
                "v1"
                if (kind in plan.common_v0_through and n > plan.common_v0_through[kind])
                or (item_id or f"{kind}-{n:02d}") in plan.redraft_v1
                else "v0"
            ),
            type_prompt="v1" if (item_id or f"{kind}-{n:02d}") in plan.redraft_v1 else "v0",
            type=kind,
            target_version=target,
            version_set=tuple(version_set),
            change_id=record.change_id,
            boundary=f"{record.previous_version}->{record.version}",
            passage_ids=tuple(p.passage_id for p in passages),
            restricted_refs=_restricted_refs(record),
            probe_ids=record.probe_ids,
        )
        items.append(PlannedItem(spec, passages, record))

    hints = config.changes
    plan = config.plan

    def pilots_for(kind: str) -> list[ChangeRecord]:
        """Pilot candidates that may be drafted as ``kind`` (owner exclusions removed)."""
        return [r for r in pilots if kind not in hints[r.change_id].exclude_types]

    # T2: the owner's fixed list of documentation-grounded changes (no automatic substitution).
    # Each set spans the boundary and its neighbour; passages come from documentation-diff
    # selection (t2_passages). Held sets keep their original selection and are only reviewed.
    by_id = {r.change_id: r for r in pilots}
    for cid in (*plan.t2_changes, *plan.t2_held):
        r = by_id.get(cid)
        if (
            r is None
            or "T2" in hints[cid].exclude_types
            or r.previous_version not in CORPUS_VERSIONS
        ):
            raise DraftingError(f"T2 change {cid} is not an eligible pilot candidate")
    for cid in plan.t2_changes:
        r = by_id[cid]
        extra = _neighbour(r.version, r.previous_version)
        versions = sorted(
            {r.previous_version, r.version, *([extra] if extra else [])}, key=CORPUS_VERSIONS.index
        )
        add(
            "T2",
            r,
            r.version,
            versions,
            t2_passages(r, versions, hints[cid].keywords, pool, plan.t2_passages_per_version),
        )
    for n_held, cid in enumerate(plan.t2_held, 1):
        r = by_id[cid]
        versions = [r.previous_version, r.version]
        legacy = [p for v in versions for p in pool.keyword(v, hints[cid].keywords)] + pool.cited(r)
        add(
            "T2",
            r,
            r.version,
            versions,
            legacy,
            item_id=f"T2-H{n_held:02d}",
            review_hold="documentation conflict to resolve (owner, 2026-11-23)",
        )
    # T1: stable controls, distinct chunks identical in every version.
    stable = [(r, pool.stable(hints[r.change_id].keywords)) for r in pilots_for("T1")]
    stable = [(r, s) for r, s in stable if s]
    used: set[str] = set()
    round_ = 0
    while sum(1 for i in items if i.spec.type == "T1") < plan.T1 and stable and round_ < 50:
        for r, contents in stable:
            fresh = [c for c in contents if c not in used]
            if not fresh or sum(1 for i in items if i.spec.type == "T1") >= plan.T1:
                continue
            used.add(fresh[0])
            target = CORPUS_VERSIONS[len(used) % len(CORPUS_VERSIONS)]
            add("T1", r, target, CORPUS_VERSIONS, [pool.chunk_with_content(target, fresh[0])])
        round_ += 1
    # T4: multi-hop, at least two permitted passages from different pages.
    t4 = []
    for r in pilots_for("T4"):
        passages = _dedupe(pool.cited(r) + pool.keyword(r.version, hints[r.change_id].keywords))
        if len({p.source_path for p in passages}) >= 2:
            t4.append((r, passages))
    for i in range(min(plan.T4, len(t4) * 2)):
        r, passages = t4[i % len(t4)]
        add("T4", r, r.version, (r.version,), passages)
    # Owner-approved extra T4 items (appended, so earlier items keep their order): page-diverse
    # passages from the change's own version, one per distinct page.
    for cid in plan.t4_extra:
        r = next((x for x in pilots_for("T4") if x.change_id == cid), None)
        if r is None:
            raise DraftingError(f"T4 extra {cid} is not an eligible pilot candidate")
        passages = page_diverse_passages(r, hints[cid].keywords, pool)
        if len({p.source_path for p in passages}) < 2:
            raise DraftingError(f"T4 extra {cid} has fewer than two distinct pages")
        add("T4", r, r.version, (r.version,), passages)
    # T5: unanswerable, feature absent in the target version.
    # The v0 T5 template says "introduced in {{version}}", so only introductions are drafted;
    # a removal ("after") would need a template change (a protocol change for the owner).
    t5 = [r for r in pilots_for("T5") if hints[r.change_id].t5 == "before"]
    # Depth 1 is the version just before the introduction, depth 2 the one before that.
    targets: list[tuple[ChangeRecord, str]] = []
    for depth in (1, 2):
        for r in t5:
            i = CORPUS_VERSIONS.index(r.version) - depth
            if i >= 0:
                targets.append((r, CORPUS_VERSIONS[i]))
    for r, absent_at in targets[: plan.T5]:
        passages = pool.cited(r) + pool.keyword(r.version, hints[r.change_id].keywords)
        add("T5", r, absent_at, (absent_at,), passages)
    # T3: change-introduced, allocated last so that it covers every pilot candidate the other types
    # left out (first, in plan order), then the rest in plan order; the T3 target is unchanged.
    t3 = [
        r
        for r in pilots_for("T3")
        if pool.cited(r) or pool.keyword(r.version, hints[r.change_id].keywords)
    ]
    # Coverage from T1/T4/T5 only, so that a change to the T2 list never reorders T3.
    if plan.t3_priority:  # explicit, stable order (owner): listed changes first, then plan order
        first = [r for cid in plan.t3_priority for r in t3 if r.change_id == cid]
        t3 = first + [r for r in t3 if r.change_id not in plan.t3_priority]
    else:
        covered = {i.spec.change_id for i in items if i.spec.type != "T2"}
        t3 = [r for r in t3 if r.change_id not in covered] + [
            r for r in t3 if r.change_id in covered
        ]
    for i in range(min(plan.T3, len(t3) * 2)):
        r = t3[i % len(t3)]
        add(
            "T3",
            r,
            r.version,
            (r.version,),
            pool.cited(r) + pool.keyword(r.version, hints[r.change_id].keywords),
        )
    return items


def shortfalls(config: DraftingConfig, plan: Sequence[PlannedItem]) -> dict[str, tuple[int, int]]:
    """Types whose planned draft slots fall below the target: ``{type: (planned, target)}``.
    Reported, never padded: a shortfall is not filled with ineligible or excluded changes."""
    out = {}
    for kind in TYPES:
        slots = sum(
            len(i.spec.version_set) if kind == "T2" else 1
            for i in plan
            if i.spec.type == kind and not i.spec.review_hold
        )
        target = getattr(config.plan, kind)
        if slots < target:
            out[kind] = (slots, target)
    return out


def unplanned(records: Sequence[ChangeRecord], plan: Sequence[PlannedItem]) -> list[str]:
    """Pilot candidates that no planned item uses (reported, with owner exclusions shown apart)."""
    used = {i.spec.change_id for i in plan}
    return sorted(
        r.change_id for r in records if r.status == "pilot_candidate" and r.change_id not in used
    )


# ---------------------------------------------------------------------------------------------
# privacy audit (fail closed)


def _normalise(text: str) -> list[str]:
    return re.sub(r"\s+", " ", text.lower()).strip().split(" ")


@dataclass
class RestrictedCorpus:
    lines: frozenset[str]
    shingles: frozenset[str]

    @classmethod
    def from_texts(cls, texts: Sequence[str]) -> Self:
        lines: set[str] = set()
        shingles: set[str] = set()
        for text in texts:
            lines.update(ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 30)
            words = _normalise(text)
            shingles.update(
                " ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)
            )
        return cls(frozenset(lines), frozenset(shingles))

    def violations(self, text: str, api_key: str | None = None) -> list[str]:
        """Reasons the text may not be sent; never the matching text itself."""
        reasons = []
        if any(ln in text for ln in self.lines):
            reasons.append("contains a restricted line")
        words = _normalise(text)
        if any(
            " ".join(words[i : i + SHINGLE]) in self.shingles
            for i in range(len(words) - SHINGLE + 1)
        ):
            reasons.append("contains a restricted 10-word sequence")
        if (api_key and api_key in text) or _KEY_LIKE.search(text):
            reasons.append("contains a key-like string")
        return reasons


def load_restricted_corpus(
    index: dict[tuple[str, str], ClaimSource], chunk_dir: Path, sidecar: Path
) -> RestrictedCorpus:
    """Every restricted text available locally: the raw release posts and release-note bodies of
    the manifest, their processed chunks, and the D1 sidecar spans. Raises if a restricted source
    in the manifest cannot be loaded (fail closed: no audit, no request)."""
    texts: list[str] = []
    restricted = sorted(k for k in index if k[0] in RESTRICTED_SOURCE_KINDS)
    if not restricted:
        raise DraftingError("the manifest lists no restricted source: cannot audit payloads")
    for key in restricted:
        data = index[key].read_bytes()
        if data is None:
            raise DraftingError(
                f"restricted {key[0]} {key[1]} is not cached locally: cannot audit payloads"
            )
        texts.append(data.decode("utf-8", "replace"))
    for name in (*(f"{v}.jsonl" for v in CORPUS_VERSIONS), "github_releases.jsonl"):
        try:
            lines = (chunk_dir / name).read_text(encoding="utf-8").splitlines()
        except OSError:
            raise DraftingError(f"chunk file {name} not available: cannot audit") from None
        for line in lines:
            if line:
                row = json.loads(line)
                if row["source_kind"] not in PERMITTED_KINDS:
                    texts.append(row["content"])
    if sidecar.is_file():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            if line.strip():
                texts.append(json.loads(line)["span_text"])
    return RestrictedCorpus.from_texts(texts)


@dataclass
class Payload:
    item: PlannedItem
    prompt: str
    passages: list[Passage]
    dropped_passages: int
    blocked: list[str] = field(default_factory=list)  # audit reasons; non-empty means never sent


def build_payload(
    item: PlannedItem, prompts: dict[str, str], corpus: RestrictedCorpus, api_key: str | None
) -> Payload:
    kept, dropped = [], 0
    for p in item.passages:
        if p.source_kind not in PERMITTED_KINDS or corpus.violations(p.text, api_key):
            dropped += 1
            continue
        kept.append(p)
    r = item.record
    values = {
        "target_version": item.spec.target_version,
        "passages": "\n\n".join(
            f"[{p.passage_id}] (DuckDB docs {p.version}, {p.source_path})\n{p.text}" for p in kept
        )
        or "(no passages)",
        "change_feature": r.feature,
        "change_before": r.before,
        "change_after": r.after,
        "version_set": ", ".join(item.spec.version_set),
        "previous_version": r.previous_version,
        "version": r.version,
    }
    prompt = (
        render(prompts[common_template(item.spec)], values)
        + "\n\n---\n\n"
        + render(prompts[type_template(item.spec)], values)
    )
    payload = Payload(item, prompt, kept, dropped)
    payload.blocked = corpus.violations(prompt, api_key)
    if not kept and item.spec.type != "T5":
        payload.blocked.append("no permitted passage survived the audit")
    return payload


# ---------------------------------------------------------------------------------------------
# cache and run


def request_sha256(model: dict[str, Any], prompt: str) -> str:
    return hashlib.sha256(
        json.dumps({"config": model, "prompt": prompt}, sort_keys=True).encode()
    ).hexdigest()


def load_cache(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def append_cache(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


@dataclass
class RunResult:
    sent: int = 0
    cached: int = 0
    blocked: int = 0
    errors: int = 0
    stopped: str | None = None  # why the run stopped early, if it did


class Generator:
    """Anything with ``generate(prompt) -> str`` (the Gemini client, or a fake in tests)."""

    def generate(self, prompt: str) -> str:  # pragma: no cover - protocol
        raise NotImplementedError


def run_drafting(
    payloads: Sequence[Payload],
    client: Any,
    generation: dict[str, Any],
    prompts: dict[str, str],
    cache_path: Path,
    limits: Limits,
    corpus: RestrictedCorpus,
    api_key: str | None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC).replace(microsecond=0),
    ledger: QuotaLedger | None = None,
    rng: Callable[[], float] = random.random,
) -> RunResult:
    result = RunResult()
    done = {e["request_sha256"] for e in load_cache(cache_path) if e.get("status") == "ok"}
    first = True
    for payload in payloads:
        spec = payload.item.spec
        if payload.blocked:
            result.blocked += 1
            continue
        rh = request_sha256(generation, payload.prompt)
        if rh in done:
            result.cached += 1
            continue
        if result.sent >= limits.max_requests_per_run:
            result.stopped = (
                f"request budget of {limits.max_requests_per_run} reached; run again to resume"
            )
            break
        # Fail closed: re-audit the exact text about to be sent, immediately before sending.
        if corpus.violations(payload.prompt, api_key) or any(
            p.source_kind not in PERMITTED_KINDS for p in payload.passages
        ):
            result.blocked += 1
            continue
        if not first:
            sleep(limits.min_seconds_between_requests)
        first = False
        entry: dict[str, Any] = {
            "item_id": spec.item_id,
            "request_sha256": rh,
            "generation": generation,
            "prompt_sha256": {
                "common": prompt_sha256(prompts, common_template(spec)),
                "type": prompt_sha256(prompts, type_template(spec)),
            },
            "prompt_version": PROMPT_VERSION[spec.type],
            "common_prompt_version": spec.common_prompt,
            "payload_sha256": hashlib.sha256(payload.prompt.encode("utf-8")).hexdigest(),
            "payload": payload.prompt,
            "spec": spec.model_dump(mode="json"),
        }
        try:
            text = _generate_with_retries(client, payload.prompt, limits, sleep, ledger, rng)
        except DailyLimitReached as exc:
            result.stopped = str(exc)  # resumable: nothing failed
            break
        except QuotaError as exc:
            result.stopped = f"stopped: {exc}"
            break
        except FatalError as exc:
            result.stopped = f"stopped: {exc}"
            break
        except (MalformedResponse, RateLimited, TransientError) as exc:
            result.sent += 1
            result.errors += 1
            append_cache(
                cache_path,
                {
                    **entry,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "created_at": now().isoformat(),
                },
            )
            if isinstance(exc, RateLimited | TransientError):
                result.stopped = f"stopped after retries: {exc}"
                break
            continue
        result.sent += 1
        if corpus.violations(text, api_key):
            result.errors += 1  # never store a response that contains restricted text
            append_cache(
                cache_path,
                {
                    **entry,
                    "status": "error",
                    "error_type": "restricted_text_in_response",
                    "created_at": now().isoformat(),
                },
            )
            continue
        append_cache(
            cache_path,
            {**entry, "status": "ok", "response_text": text, "created_at": now().isoformat()},
        )
        done.add(rh)
    return result


def _generate_with_retries(
    client: Any,
    prompt: str,
    limits: Limits,
    sleep: Callable[[float], None],
    ledger: QuotaLedger | None = None,
    rng: Callable[[], float] = random.random,
) -> str:
    """At most ``max_retries`` retries, each counted by the ledger like a first attempt.
    Backoff: base * 2**attempt plus up to ``backoff_jitter`` of it at random, never shorter than
    the server's retry delay, capped at ``max_backoff_seconds``."""
    tokens = estimate_input_tokens(prompt)
    attempt = 0
    while True:
        if ledger is not None:
            ledger.acquire(tokens)
        try:
            text = str(client.generate(prompt))
        except (RateLimited, TransientError) as exc:
            if attempt >= limits.max_retries:
                raise
            backoff = min(limits.backoff_base_seconds * 2**attempt, limits.max_backoff_seconds)
            backoff *= 1 + limits.backoff_jitter * rng()
            server = exc.retry_after if isinstance(exc, RateLimited) and exc.retry_after else 0.0
            sleep(min(max(backoff, server), limits.max_backoff_seconds))
            attempt += 1
            continue
        if ledger is not None:
            ledger.settle(getattr(client, "last_prompt_tokens", None))
        return text


# ---------------------------------------------------------------------------------------------
# validation of responses into V0 drafts


class Span(_Strict):
    passage_id: str
    version: str
    source_kind: str
    source_path: str
    text: str


class Draft(_Strict):
    draft_id: str
    item_id: str
    status: Literal["draft", "rejected"]
    rejection_reasons: tuple[str, ...]
    type: Literal["T1", "T2", "T3", "T4", "T5"]
    target_version: str
    version_set: tuple[str, ...]
    change_id: str
    boundary: str
    question: str | None
    gold_short_answer: str | None
    answer_type: str | None
    accepted_aliases: tuple[str, ...]
    other_version_answers: dict[str, str]
    evidence_spans: tuple[Span, ...]
    answerable: bool | None
    abstain_reason: str | None
    restricted_refs: tuple[str, ...]
    probe_ids: tuple[str, ...]
    runtime_observations: tuple[str, ...]
    gold_basis: Literal["docs"]
    verification_level: Literal["V0"]
    human_verified: Literal[False]
    gold: Literal[False]
    generation: dict[str, Any]
    review_hold: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "rejected" and not self.rejection_reasons:
            raise ValueError("a rejected draft needs a reason")
        if self.status == "draft" and (self.rejection_reasons or not self.question):
            raise ValueError("a draft has a question and no rejection reasons")
        if self.runtime_observations:
            raise ValueError("drafts do not merge runtime observations (docs answers only)")
        return self


REQUIRED_KEYS = (
    "question",
    "type",
    "target_version",
    "gold_short_answer",
    "gold_answer_type",
    "accepted_aliases",
    "evidence_spans",
    "answerable",
    "abstain_reason",
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


# Labels a model may use for the requested type (the v0 templates name the type in their headings
# but do not prescribe the value of the "type" key). Only labels that name the requested type are
# accepted; anything else ("factual", "value", "single_hop", ...) stays a type mismatch.
TYPE_LABELS = {
    "T1": {"t1", "stable", "control", "t1 stable", "t1 stable (control)", "stable (control)"},
    "T2": {"t2", "version-specific", "version_specific", "t2 version-specific"},
    "T3": {"t3", "change-introduced", "change_introduced", "t3 change-introduced"},
    "T4": {"t4", "multi-hop", "multi_hop", "multihop", "t4 multi-hop"},
    "T5": {"t5", "unanswerable", "t5 unanswerable"},
}


def type_label_matches(label: object, wanted: str) -> bool:
    return isinstance(label, str) and " ".join(label.lower().split()) in TYPE_LABELS[wanted]


def _exact_in(text: str, passage: Passage) -> str | None:
    """The passage's own text for ``text``: an exact substring, or the substring that differs from
    it only in whitespace. The stored span is always verbatim passage text (protocol v0 §5.1)."""
    if text in passage.text:
        return text
    tokens = text.split()
    if not tokens:
        return None
    m = re.search(r"\s+".join(re.escape(t) for t in tokens), passage.text)
    return m.group(0) if m else None


def resolve_span(
    pid: object, text: object, passages: dict[str, Passage]
) -> tuple[Passage, str] | None:
    """The cited passage and the verbatim span, or None. The passage id may be written as given,
    without its ``chunk:``/``src:`` prefix, in brackets, or as the passage's source path (with or
    without a line range); if it names none of them, the span is accepted only when exactly one
    given passage contains the text."""
    if not isinstance(text, str) or not text.strip():
        return None
    candidates: list[Passage] = []
    if isinstance(pid, str):
        key = pid.strip().strip("[]").strip()
        for cand in (key, f"chunk:{key}", f"src:{key}"):
            if cand in passages:
                candidates = [passages[cand]]
                break
        if not candidates:
            path = re.sub(r":\d+(-\d+)?$", "", key)
            candidates = [p for p in passages.values() if p.source_path == path]
    for passage in candidates:
        exact = _exact_in(text, passage)
        if exact is not None:
            return passage, exact
    if candidates:
        return None  # the cited passage does not contain the span
    hits = [(p, e) for p in passages.values() if (e := _exact_in(text, p)) is not None]
    return hits[0] if len(hits) == 1 else None


def t2_version_checks(
    data: dict[str, Any], spec: dict[str, Any], passages: dict[str, Passage], gold: str
) -> tuple[dict[str, str], list[dict[str, str]], list[str]]:
    """Prompt v1 T2 rules: ``version_answers`` has exactly one well-formed entry per version in
    the set; every version is documented, with an answer and at least one verbatim span from a
    passage of that same version; the target answer is the gold answer; ``behaviour`` is
    "changed" and the answers really differ; ``other_version_answers`` agrees. Returns the
    documented answers, their spans and the rejection reasons."""
    vset = list(spec["version_set"])
    va = data.get("version_answers")
    if (
        not isinstance(va, dict)
        or set(va) != set(vset)
        or not all(isinstance(va[v], dict) for v in vset)
    ):
        return {}, [], ["t2_incomplete_version_answers"]
    reasons: list[str] = []
    answers: dict[str, str] = {}
    spans: list[dict[str, str]] = []
    for v in vset:
        entry = va[v]
        status, answer, evidence = (
            entry.get("status"),
            entry.get("answer"),
            entry.get("evidence_spans"),
        )
        if status not in ("documented", "not_documented") or not isinstance(evidence, list):
            reasons.append("t2_incomplete_version_answers")
            continue
        if status == "not_documented":
            reasons.append("t2_version_not_documented")
            if answer not in (None, "") or evidence:
                reasons.append("t2_inconsistent_version_status")
            continue
        if not isinstance(answer, str) or not answer.strip():
            reasons.append("t2_incomplete_version_answers")
            continue
        if re.search(r"\bor\b|;", answer):
            reasons.append("multiple_answers")
        same_version = {k: p for k, p in passages.items() if p.version == v}
        own: list[dict[str, str]] = []
        for s in evidence:
            pid = s.get("passage_id") if isinstance(s, dict) else None
            text = s.get("exact_text", s.get("text")) if isinstance(s, dict) else None
            found = resolve_span(pid, text, same_version)
            if found is None:
                elsewhere = resolve_span(pid, text, passages)
                reasons.append("t2_span_wrong_version" if elsewhere else "span_not_in_passages")
                continue
            passage, exact = found
            own.append(
                {
                    "passage_id": passage.passage_id,
                    "version": passage.version,
                    "source_kind": passage.source_kind,
                    "source_path": passage.source_path,
                    "text": exact,
                }
            )
        if not own:
            reasons.append("t2_missing_version_evidence")
        answers[v] = answer
        spans += own
    target = spec["target_version"]
    if target not in answers:
        reasons.append("t2_target_not_documented")
    elif answers[target] != gold:
        reasons.append("t2_gold_mismatch")
    if data.get("behaviour") != "changed" or len(set(answers.values())) < 2:
        reasons.append("t2_behaviour_unchanged")
    others = data.get("other_version_answers")
    derived = {v: a for v, a in answers.items() if v != target}
    if not isinstance(others, dict) or any(others.get(v) != a for v, a in derived.items()):
        reasons.append("t2_inconsistent_answers")
    return answers, spans, reasons


def validate_response(
    entry: dict[str, Any], passages: dict[str, Passage], answer_types: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    """The parsed fields and the reasons to reject them (empty when the draft is acceptable)."""
    spec = entry["spec"]
    try:
        data = json.loads(entry["response_text"])
    except (ValueError, KeyError):
        return {}, ["malformed_json"]
    if not isinstance(data, dict):
        return {}, ["not_a_json_object"]
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        return {}, ["missing_keys"]
    reasons: list[str] = []
    question = data["question"] if isinstance(data["question"], str) else ""
    gold = data["gold_short_answer"] if isinstance(data["gold_short_answer"], str) else ""
    answer_type = data["gold_answer_type"]
    if not question.strip():
        reasons.append("empty_question")
    if not type_label_matches(data["type"], spec["type"]):
        reasons.append("type_mismatch")
    if data["target_version"] != spec["target_version"]:
        reasons.append("target_version_mismatch")
    if answer_type not in answer_types:
        reasons.append("invalid_answer_type")
    t5 = spec["type"] == "T5"
    if t5 and (
        answer_type != "none" or data["answerable"] is not False or not data["abstain_reason"]
    ):
        reasons.append("t5_must_abstain")
    if not t5 and (answer_type == "none" or data["answerable"] is not True or not gold.strip()):
        reasons.append("answerable_item_needs_an_answer")
    if not t5 and re.search(r"\bor\b|;", gold):
        reasons.append("multiple_answers")
    if (
        gold
        and answer_type in ("identifier", "value")
        and len(gold) >= 4
        and gold.lower() in question.lower()
    ):
        reasons.append("answer_in_question")
    spans: list[dict[str, str]] = []
    raw_spans = data["evidence_spans"] if isinstance(data["evidence_spans"], list) else []
    for s in raw_spans:
        pid = s.get("passage_id") if isinstance(s, dict) else None
        text = s.get("text", s.get("exact_text")) if isinstance(s, dict) else None
        found = resolve_span(pid, text, passages)
        if found is None:
            reasons.append("span_not_in_passages")
            break
        passage, exact = found
        spans.append(
            {
                "passage_id": passage.passage_id,
                "version": passage.version,
                "source_kind": passage.source_kind,
                "source_path": passage.source_path,
                "text": exact,
            }
        )
    if not t5 and not spans:
        reasons.append("no_evidence_span")
    if t5 and raw_spans:
        reasons.append("t5_has_gold_span")  # protocol v0 §5.6: T5 has no gold span
    if spec["type"] == "T4" and len({s["source_path"] for s in spans}) < 2:
        reasons.append("t4_needs_two_pages")
    others = data.get("other_version_answers") or {}
    if spec["type"] == "T2":
        _answers, v_spans, v_reasons = t2_version_checks(data, spec, passages, gold)
        reasons += v_reasons
        known = {(sp["passage_id"], sp["text"]) for sp in spans}
        spans += [sp for sp in v_spans if (sp["passage_id"], sp["text"]) not in known]
        wanted = [v for v in spec["version_set"] if v != spec["target_version"]]
        if (
            not isinstance(others, dict)
            or set(others) != set(wanted)
            or all(others.get(v) == gold for v in wanted)
        ):
            reasons.append("t2_needs_differing_other_version_answers")
    aliases = data["accepted_aliases"] if isinstance(data["accepted_aliases"], list) else []
    fields = {
        "question": question or None,
        "gold_short_answer": gold if not t5 else None,
        "answer_type": answer_type if isinstance(answer_type, str) else None,
        "accepted_aliases": tuple(a for a in aliases if isinstance(a, str)),
        "other_version_answers": {k: v for k, v in others.items() if isinstance(v, str)}
        if isinstance(others, dict)
        else {},
        "evidence_spans": tuple(spans),
        "answerable": data["answerable"] if isinstance(data["answerable"], bool) else None,
        "abstain_reason": data["abstain_reason"]
        if isinstance(data["abstain_reason"], str)
        else None,
    }
    return fields, sorted(set(reasons))


_EMPTY: dict[str, Any] = {
    "question": None,
    "gold_short_answer": None,
    "answer_type": None,
    "accepted_aliases": (),
    "other_version_answers": {},
    "evidence_spans": (),
    "answerable": None,
    "abstain_reason": None,
}


def _members(
    spec: ItemSpec, fields: dict[str, Any], reasons: list[str]
) -> list[tuple[str, str, dict[str, Any], list[str]]]:
    """(item_id, target_version, fields, reasons) per draft. A T2 set becomes one draft per
    version in the set: the same question, that version's gold answer and only that version's
    spans; every other item is one draft."""
    if spec.type != "T2" or not fields:
        return [(spec.item_id, spec.target_version, fields, reasons)]
    answers = {**fields["other_version_answers"], spec.target_version: fields["gold_short_answer"]}
    out = []
    for v in spec.version_set:
        spans = tuple(sp for sp in fields["evidence_spans"] if sp["version"] == v)
        own = list(reasons)
        if not answers.get(v):
            own.append("t2_needs_differing_other_version_answers")
        if not spans:
            own.append("no_evidence_span")
        member = {
            **fields,
            "gold_short_answer": answers.get(v),
            "other_version_answers": {k: a for k, a in answers.items() if k != v and a},
            "evidence_spans": spans,
        }
        out.append((f"{spec.item_id}@{v}", v, member, sorted(set(own))))
    return out


def build_drafts(
    planned: Sequence[PlannedItem],
    cache: Sequence[dict[str, Any]],
    answer_types: Sequence[str],
    generation: dict[str, Any],
    prompts: dict[str, str],
    corpus: RestrictedCorpus,
) -> list[Draft]:
    """Drafts for every planned item that has a cached response to its current request. Each
    draft is V0: status ``draft`` when every automatic S3 check passed, otherwise ``rejected``
    with the reason codes. Neither is human-verified or gold."""
    by_hash = {e["request_sha256"]: e for e in cache if e.get("status") == "ok"}
    drafts: list[Draft] = []
    accepted: list[tuple[str, set[str]]] = []  # (target version, question tokens)
    for item in planned:
        payload = build_payload(item, prompts, corpus, None)
        rh = request_sha256(generation, payload.prompt)
        entry = by_hash.get(rh)
        if entry is None or payload.blocked:
            continue
        fields, reasons = validate_response(
            entry, {p.passage_id: p for p in payload.passages}, answer_types
        )
        for item_id, target, member, own in _members(item.spec, fields, reasons):
            if not own and member.get("question"):
                toks = _tokens(member["question"])
                if any(
                    t == target and toks and len(toks & other) / len(toks | other) >= 0.8
                    for t, other in accepted
                ):
                    own = ["duplicate"]
            status: Literal["draft", "rejected"] = "rejected" if own else "draft"
            if status == "draft":
                accepted.append((target, _tokens(member["question"])))
            suffix = "" if item_id == item.spec.item_id else f"-{target}"
            base = {
                "draft_id": f"dq-{rh[:12]}{suffix}",
                "item_id": item_id,
                "status": status,
                "rejection_reasons": tuple(own),
                "type": item.spec.type,
                "target_version": target,
                "version_set": item.spec.version_set,
                "change_id": item.spec.change_id,
                "boundary": item.spec.boundary,
                "restricted_refs": item.spec.restricted_refs,
                "probe_ids": item.spec.probe_ids,
                "review_hold": item.spec.review_hold,
                "runtime_observations": (),
                "gold_basis": "docs",
                "verification_level": "V0",
                "human_verified": False,
                "gold": False,
                "generation": {
                    **entry["generation"],
                    "prompt_sha256": entry["prompt_sha256"],
                    "request_sha256": rh,
                    "payload_sha256": entry["payload_sha256"],
                    "created_at": entry["created_at"],
                },
            }
            try:
                drafts.append(Draft.model_validate({**base, **(member or _EMPTY)}))
            except ValidationError:
                drafts.append(
                    Draft.model_validate(
                        {
                            **base,
                            **_EMPTY,
                            "status": "rejected",
                            "rejection_reasons": tuple(sorted({*own, "schema_invalid"})),
                        }
                    )
                )
    return drafts


def write_drafts(path: Path, drafts: Sequence[Draft]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(d.model_dump(mode="json"), sort_keys=True, ensure_ascii=False) + "\n"
        for d in sorted(drafts, key=lambda d: d.item_id)
    ]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes("".join(lines).encode("utf-8"))
    tmp.replace(path)


def render_review_sheet(drafts: Sequence[Draft]) -> str:
    """The owner's manual-verification sheet (protocol v0 §8): every V0 draft that passed the
    automatic S3 checks, with its evidence and empty review fields. Nothing here is gold or
    human-verified; selecting the 50-item pilot follows the V1 checks (S4)."""
    accepted = sorted(
        (x for x in drafts if x.status == "draft" and not x.review_hold),
        key=lambda x: (x.type, x.item_id),
    )
    held = sorted(
        (x for x in drafts if x.status == "draft" and x.review_hold), key=lambda x: x.item_id
    )
    rejected = [x for x in drafts if x.status == "rejected"]
    out = [
        "# Pilot drafts v0: review sheet (V0, not gold, not human-verified)",
        "",
        f"{len(accepted)} drafts passed the automatic S3 checks; {len(rejected)} were rejected "
        "(see `pilot_drafts_v0.jsonl`). Fill the review fields of protocol v0 §8 for each item. "
        "Reason codes R1-R9 are defined in protocol v0 §7.",
    ]
    groups = [
        (f"{k} ({sum(1 for x in accepted if x.type == k)})", [x for x in accepted if x.type == k])
        for k in TYPES
    ]
    groups.append((f"Held for manual review, not counted toward any target ({len(held)})", held))
    for title, items in groups:
        out += ["", f"## {title}"]
        for x in items:
            out += [
                "",
                f"### {x.item_id} ({x.draft_id})",
                "",
                f"- change: `{x.change_id}` (boundary {x.boundary}); "
                f"target version {x.target_version}"
                + (f"; version set {', '.join(x.version_set)}" if x.type == "T2" else ""),
                f"- question: {x.question}",
                f"- gold_short_answer ({x.answer_type}): {x.gold_short_answer}"
                if x.answerable
                else f"- unanswerable; abstain_reason: {x.abstain_reason}",
            ]
            if x.accepted_aliases:
                out.append(f"- accepted_aliases: {', '.join(x.accepted_aliases)}")
            if x.other_version_answers:
                others = "; ".join(f"{v}: {a}" for v, a in sorted(x.other_version_answers.items()))
                out.append(f"- other_version_answers: {others}")
            for sp in x.evidence_spans:
                out.append(f"- evidence ({sp.version}, `{sp.source_path}`): {sp.text}")
            if x.restricted_refs:
                out.append(f"- restricted sources (reference only): {', '.join(x.restricted_refs)}")
            if x.probe_ids:
                out.append(f"- probes: {', '.join(x.probe_ids)}")
            if x.review_hold:
                out.append(f"- HELD: {x.review_hold}")
            out += [
                "- decision (accept | accept_minor_edit | accept_major_edit | reject): ",
                "- reason_codes: ",
                "- comment: ",
                "- edits: ",
                "- time_spent_seconds: ",
                "- reviewer: ",
                "- review_batch: ",
            ]
    return "\n".join(out) + "\n"
