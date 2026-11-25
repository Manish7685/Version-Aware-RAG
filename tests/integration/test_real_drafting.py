"""The S3 drafting plan against the real corpus (RAG-M4 S3): every planned payload contains only
permitted MIT passages and passes the fail-closed privacy audit. Offline: nothing is sent.
Skipped unless the caches have been fetched and the corpus built; the CI corpus-rebuild job does
both.

Restricted text must never reach test output: assertions use precomputed booleans and counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from version_aware_rag.benchmark import drafting as d
from version_aware_rag.benchmark.draft_commands import load_context
from version_aware_rag.corpus.config import load_config

pytestmark = pytest.mark.corpus


def test_every_real_payload_is_permitted_and_passes_the_audit(repo_root: Path) -> None:
    cfg, _ = load_config(repo_root / "configs/corpus.toml")
    if (
        not (cfg.cache_path(repo_root) / ".git").is_dir()
        or not (repo_root / "data/build/chunks/2.0.jsonl").is_file()
    ):
        pytest.skip("corpus cache not fetched or corpus not built")
    ctx = load_context(repo_root, Path("configs/drafting.toml"))
    payloads = [d.build_payload(i, ctx.prompts, ctx.corpus, None) for i in ctx.plan]
    pilots_only = all(p.item.record.status == "pilot_candidate" for p in payloads)
    permitted_only = all(x.source_kind in d.PERMITTED_KINDS for p in payloads for x in p.passages)
    blocked = sum(1 for p in payloads if p.blocked)
    clean = sum(1 for p in payloads if not ctx.corpus.violations(p.prompt))
    boundaries = {p.item.spec.boundary for p in payloads}
    excluded = {c for c, h in ctx.config.changes.items() if h.exclude_types}
    planned_types = {(i.spec.change_id, i.spec.type) for i in ctx.plan}
    exclusions_respected = all(
        (c, t) not in planned_types for c, h in ctx.config.changes.items() for t in h.exclude_types
    )
    assert len(payloads) == len(ctx.plan) and exclusions_respected
    assert set(d.unplanned(ctx.records, ctx.plan)) <= excluded  # only owner exclusions unplanned
    assert d.shortfalls(ctx.config, ctx.plan) == {}
    assert pilots_only and permitted_only
    assert blocked == 0 and clean == len(payloads) > 0
    assert {"1.2->1.3", "1.3->1.4", "1.4->1.5", "1.5->2.0"} <= boundaries
