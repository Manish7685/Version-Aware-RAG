"""``vrag benchmark draft plan | run | build`` (RAG-M4 S3).

- ``plan``: offline. Builds the drafting plan and every payload and runs the privacy audit;
  sends and writes nothing.
- ``run``: sends the audited payloads that are not yet cached to Gemini, within the request
  budget, appending each response to the cache, then rebuilds the drafts. Needs
  ``[model] id`` in ``configs/drafting.toml`` and ``VRAG_GEMINI_API_KEY``.
- ``build``: offline. Rebuilds the drafts file from the cache.

Exit codes: **0** done; **1** a check failed (a prompt hash, a blocked payload, a fatal API
error such as no free tier); **2** an input is missing (configuration, corpus, key or model) or
the run stopped at its budget or a rate limit (run again to resume). Output names items by id
and counts only, never passage, prompt or response text, and never the key.
"""

from __future__ import annotations

import argparse
import logging
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from version_aware_rag.benchmark import drafting as d
from version_aware_rag.benchmark.gemini import FatalError, GeminiClient, GenerationConfig
from version_aware_rag.benchmark.inventory import (
    DEFAULT_PATH,
    ChangeRecord,
    InventoryReport,
    load_inventory,
)
from version_aware_rag.benchmark.quota import (
    ProcessLock,
    QuotaError,
    QuotaLedger,
    QuotaLimits,
    estimate_input_tokens,
)
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.store import SIDECAR_FILE
from version_aware_rag.evidence.verify import build_source_index
from version_aware_rag.settings import Settings

log = logging.getLogger(__name__)


def add_draft_commands(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    draft = actions.add_parser("draft", help="Gemini-assisted drafting of pilot candidates (S3).")
    sub = draft.add_subparsers(dest="draft_command", required=True, metavar="ACTION")
    for name, handler, text in (
        ("plan", _cmd_plan, "Plan and audit every payload (offline; sends nothing)."),
        ("run", _cmd_run, "Send uncached audited payloads to Gemini, then rebuild the drafts."),
        ("build", _cmd_build, "Rebuild the drafts from the cache (offline)."),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("--root", type=Path, default=Path(), help="Repository root.")
        p.add_argument("--config", type=Path, default=Path("configs/drafting.toml"))
        p.add_argument("--cache", type=Path, default=d.CACHE_PATH, help="Relative to --root.")
        p.add_argument("--drafts", type=Path, default=d.DRAFTS_PATH, help="Relative to --root.")
        p.set_defaults(handler=handler)


@dataclass
class _Context:
    config: d.DraftingConfig
    records: list[ChangeRecord]
    plan: list[d.PlannedItem]
    prompts: dict[str, str]
    corpus: d.RestrictedCorpus
    answer_types: list[str]

    def generation(self) -> dict[str, object]:
        m = self.config.model
        return GenerationConfig(m.id, m.temperature, m.max_output_tokens).as_dict()


def load_context(root: Path, config_path: Path) -> _Context:
    cfg, _ = load_config(root / "configs/corpus.toml")
    chunking, _ = load_chunking_config(root / "configs/chunking.toml")
    manifest, problems = load_manifest(root / cfg.paths.manifest)
    if manifest is None or problems:
        raise d.DraftingError(f"corpus manifest cannot be loaded ({len(problems)} problems)")
    index = build_source_index(root, cfg, chunking, manifest)
    report = InventoryReport()
    load_inventory(root / DEFAULT_PATH, report)
    if report.errors or report.incomplete:
        raise d.DraftingError("the change inventory does not load cleanly (run inventory validate)")
    config = d.load_drafting_config(root / config_path)
    chunk_dir = root / "data/build/chunks"
    pool = d.PassagePool(
        d.load_permitted_chunks(chunk_dir),
        index,
        config.plan.max_passage_chars,
        config.plan.max_keyword_passages_per_version,
    )
    protocol = tomllib.loads((root / d.PROTOCOL_TOML).read_text(encoding="utf-8"))
    return _Context(
        config=config,
        records=report.records,
        plan=d.build_plan(report.records, config, pool),
        prompts=d.verify_prompts(root),
        corpus=d.load_restricted_corpus(index, chunk_dir, root / "data/evidence" / SIDECAR_FILE),
        answer_types=list(protocol["answer_types"]),
    )


def _load_or_report(args: argparse.Namespace) -> _Context | int:
    try:
        return load_context(args.root, args.config)
    except d.DraftingError as exc:
        print(f"RESULT: FAILED ({exc})")
        return 1 if "sha256" in str(exc) else 2
    except Exception as exc:
        log.error("draft_load_failed", extra={"error": type(exc).__name__})
        print(f"RESULT: INCOMPLETE (inputs cannot be loaded: {type(exc).__name__})")
        return 2


def _payloads(ctx: _Context, api_key: str | None) -> list[d.Payload]:
    return [d.build_payload(item, ctx.prompts, ctx.corpus, api_key) for item in ctx.plan]


def _print_plan(ctx: _Context, payloads: list[d.Payload]) -> int:
    requests: Counter[str] = Counter(p.item.spec.type for p in payloads)
    slots: Counter[str] = Counter()
    boundaries: Counter[str] = Counter()
    for p in payloads:
        n = len(p.item.spec.version_set) if p.item.spec.type == "T2" else 1
        slots[p.item.spec.type] += n
        boundaries[p.item.spec.boundary] += n
    blocked = [p for p in payloads if p.blocked]
    eligible = sum(1 for r in ctx.records if r.status == "pilot_candidate")
    used = len({p.item.spec.change_id for p in payloads})
    print(f"eligible changes (pilot candidates): {eligible}; used: {used}")
    for cid, h in sorted(ctx.config.changes.items()):
        if h.exclude_types:
            print(
                f"owner exclusion: {cid} not drafted as {', '.join(h.exclude_types)}"
                f" ({h.exclude_reason})"
            )
    print(
        "eligible changes not planned: " + (", ".join(d.unplanned(ctx.records, ctx.plan)) or "none")
    )
    gaps = d.shortfalls(ctx.config, ctx.plan)
    print(
        "type targets: "
        + ("; ".join(f"SHORTFALL {k} {p} of {t}" for k, (p, t) in gaps.items()) or "all met")
    )
    print("requests: " + ", ".join(f"{t} {requests[t]}" for t in d.TYPES))
    print("draft slots: " + ", ".join(f"{t} {slots[t]}" for t in d.TYPES))
    print(
        "draft slots per boundary: " + ", ".join(f"{k} {v}" for k, v in sorted(boundaries.items()))
    )
    print(f"passages dropped by the privacy audit: {sum(p.dropped_passages for p in payloads)}")
    for p in blocked:
        print(f"BLOCKED {p.item.spec.item_id}: {'; '.join(p.blocked)}")
    return len(blocked)


def _print_quota(args: argparse.Namespace, ctx: _Context) -> None:
    lim, m = ctx.config.limits, ctx.config.model
    print(
        f"model: {m.id or '(not set)'} | limits: {lim.requests_per_minute} RPM, "
        f"{lim.input_tokens_per_minute} input TPM, {lim.requests_per_day} requests per rolling 24 h"
    )
    quota = QuotaLimits(lim.requests_per_minute, lim.input_tokens_per_minute, lim.requests_per_day)
    try:
        used = QuotaLedger(args.root / d.QUOTA_LEDGER_PATH, quota).usage()
    except QuotaError as exc:
        print(f"quota usage: unavailable ({exc})")
        return
    print(f"quota usage: {used['requests_last_24h']} of {lim.requests_per_day} in the last 24 h")
    # Requests the next run would send: those without a successful cached response.
    done = {
        e["request_sha256"] for e in d.load_cache(args.root / args.cache) if e.get("status") == "ok"
    }
    pending = [
        p
        for p in _payloads(ctx, None)
        if not p.blocked and d.request_sha256(ctx.generation(), p.prompt) not in done
    ]
    tokens = [estimate_input_tokens(p.prompt) for p in pending]
    by_type = Counter(p.item.spec.type for p in pending)
    print(
        f"cached: {len(ctx.plan) - len(pending)}; to send: {len(pending)} "
        f"({', '.join(f'{k} {v}' for k, v in sorted(by_type.items())) or 'none'})"
    )
    if pending:
        print(
            f"estimated input tokens to send: {sum(tokens)} (max {max(tokens)} per request; "
            f"limit {lim.input_tokens_per_minute} per minute)"
        )
        after = used["requests_last_24h"] + len(pending)
        print(
            f"quota after the run: {after} of {lim.requests_per_day} (rolling 24 h); at most "
            f"{lim.requests_per_minute} per minute, plus up to {lim.max_retries} retries "
            "per request"
        )
    for kind, name in sorted(d.ACTIVE_TEMPLATE.items()):
        print(
            f"prompt {kind}: {name} ({d.PROMPT_VERSION[kind]}) "
            f"sha256 {d.prompt_sha256(ctx.prompts, name)}"
        )
    print(
        f"prompt common v1: {d.COMMON_V1} sha256 {d.prompt_sha256(ctx.prompts, d.COMMON_V1)}; "
        f"common v0: {d.COMMON_TEMPLATE} "
        f"sha256 {d.prompt_sha256(ctx.prompts, d.COMMON_TEMPLATE)}"
    )


def _cmd_plan(args: argparse.Namespace, settings: Settings) -> int:
    ctx = _load_or_report(args)
    if isinstance(ctx, int):
        return ctx
    blocked = _print_plan(ctx, _payloads(ctx, None))
    _print_quota(args, ctx)
    if blocked:
        print(f"RESULT: FAILED ({blocked} payloads blocked by the privacy audit)")
        return 1
    print(f"RESULT: PASSED ({len(ctx.plan)} payloads audited; nothing sent)")
    return 0


def _build(args: argparse.Namespace, ctx: _Context) -> list[d.Draft]:
    drafts = d.build_drafts(
        ctx.plan,
        d.load_cache(args.root / args.cache),
        ctx.answer_types,
        ctx.generation(),
        ctx.prompts,
        ctx.corpus,
    )
    d.write_drafts(args.root / args.drafts, drafts)
    sheet = args.root / d.REVIEW_SHEET_PATH
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_bytes(d.render_review_sheet(drafts).encode("utf-8"))
    status = Counter(x.status for x in drafts)
    accepted = [x for x in drafts if x.status == "draft" and not x.review_hold]
    held = [x for x in drafts if x.status == "draft" and x.review_hold]
    reasons = Counter(r for x in drafts for r in x.rejection_reasons)
    print(f"drafts: {len(drafts)} (draft {status['draft']}, rejected {status['rejected']})")
    print(f"held for manual review (not counted): {len(held)}")
    print(
        "accepted per type: "
        + ", ".join(f"{t} {sum(1 for x in accepted if x.type == t)}" for t in d.TYPES)
    )
    print(
        "accepted per boundary: "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(x.boundary for x in accepted).items()))
    )
    print(
        "rejection reasons: "
        + (", ".join(f"{k} {v}" for k, v in sorted(reasons.items())) or "none")
    )
    return drafts


def _cmd_build(args: argparse.Namespace, settings: Settings) -> int:
    ctx = _load_or_report(args)
    if isinstance(ctx, int):
        return ctx
    _build(args, ctx)
    print("RESULT: DONE (drafts rebuilt from the cache; V0, none human-verified or gold)")
    return 0


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    ctx = _load_or_report(args)
    if isinstance(ctx, int):
        return ctx
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    if not ctx.config.model.id:
        print("RESULT: INCOMPLETE (no model: set [model] id in configs/drafting.toml)")
        return 2
    if not key:
        print("RESULT: INCOMPLETE (no key: set VRAG_GEMINI_API_KEY)")
        return 2
    payloads = _payloads(ctx, key)
    if _print_plan(ctx, payloads):
        print("RESULT: FAILED (payloads blocked by the privacy audit; nothing sent)")
        return 1
    m = ctx.config.model
    try:
        client = GeminiClient(key, GenerationConfig(m.id, m.temperature, m.max_output_tokens))
    except FatalError as exc:
        print(f"RESULT: INCOMPLETE ({exc})")
        return 2
    lim = ctx.config.limits
    quota = QuotaLimits(lim.requests_per_minute, lim.input_tokens_per_minute, lim.requests_per_day)
    try:
        with ProcessLock(args.root / d.QUOTA_LOCK_PATH) as lock:
            ledger = QuotaLedger(args.root / d.QUOTA_LEDGER_PATH, quota, heartbeat=lock.refresh)
            result = d.run_drafting(
                payloads,
                client,
                ctx.generation(),
                ctx.prompts,
                args.root / args.cache,
                lim,
                ctx.corpus,
                key,
                ledger=ledger,
            )
    except QuotaError as exc:  # lock held by another run
        print(f"RESULT: FAILED ({exc})")
        return 1
    print(
        f"requests sent {result.sent}, cached {result.cached}, "
        f"errors {result.errors}, blocked {result.blocked}"
    )
    _build(args, ctx)
    if result.stopped and result.stopped.startswith("stopped:"):
        print(f"RESULT: FAILED ({result.stopped})")
        return 1
    if result.stopped:
        print(f"RESULT: INCOMPLETE ({result.stopped})")
        return 2
    print("RESULT: DONE (every planned request is cached)")
    return 0
