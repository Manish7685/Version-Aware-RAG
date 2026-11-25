"""``vrag benchmark pilot check | select | report`` (RAG-M4, protocol v1).

- ``check``: V1 checks on every accepted, counted draft; writes
  ``benchmark/drafts/v1_results.jsonl``.
- ``select``: the protocol v1 pilot (50) from drafts that pass V1, review sheets in batches of 25
  and the review-record template (``benchmark/review/``; recorded decisions are never overwritten).
- ``report``: the pilot report from complete review records only.

Exit codes: 0 done; 1 a check failed or the selection falls short; 2 inputs missing or the review is
incomplete. Output: ids, counts and check names only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from version_aware_rag.benchmark import drafting as d
from version_aware_rag.benchmark import pilot as p
from version_aware_rag.benchmark import split as sp
from version_aware_rag.benchmark.draft_commands import load_context
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.verify import build_source_index
from version_aware_rag.probes.pins import DEFAULT_PATH as PROBES_PATH
from version_aware_rag.probes.pins import load_pins
from version_aware_rag.settings import Settings

V1_RESULTS = Path("benchmark/drafts/v1_results.jsonl")
# Held drafts the owner released into the review (decision D2, 2026-11-24). They join the pilot
# review set as candidates only: no review record, not V2, never counted until reviewed.
RELEASED_HOLDS = Path("benchmark/review/released_holds.json")


def released_hold_ids(root: Path, drafts: dict[str, dict[str, Any]]) -> list[str]:
    """Released held drafts, validated: each must exist, be a ``draft`` and carry a review hold."""
    path = root / RELEASED_HOLDS
    if not path.is_file():
        return []
    ids = [str(e["draft_id"]) for e in json.loads(path.read_text("utf-8"))["released"]]
    for i in ids:
        x = drafts.get(i)
        if x is None or x.get("status") != "draft" or not x.get("review_hold"):
            raise ValueError(f"released hold {i} is not a held draft")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate released hold")
    return ids


REPORT_PATH = Path("docs/benchmark/pilot_report.md")
SCREEN_REPORT = Path("benchmark/review/proposed/screen_report.md")


def _cmd_screen(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.benchmark import screen as sc
    from version_aware_rag.retrieval.commands import corpus_from

    try:
        pilot = _pilot_drafts(args.root)
        corpus = corpus_from(args.root)
    except (OSError, ValueError) as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    results = [sc.screen_item(x, corpus) for x in pilot if x["type"] in ("T4", "T5")]
    out = args.root / SCREEN_REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    # LF only, so the committed report is stable under the line-ending hooks on any OS.
    out.write_text(sc.render(results, "pilot review set"), encoding="utf-8", newline="\n")
    flagged = [r for r in results if r.flags]
    for r in flagged:
        print(f"  FLAG {r.item_id}: {'; '.join(r.flags)}")
    print(f"RESULT: DONE ({len(flagged)} of {len(results)} T4/T5 items flagged; advisory)")
    return 0


def add_pilot_commands(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    pilot = actions.add_parser(
        "pilot", help="Pilot V1 checks, selection, review and report (RAG-M4)."
    )
    sub = pilot.add_subparsers(dest="pilot_command", required=True, metavar="ACTION")
    for name, handler, text in (
        ("check", _cmd_check, "Run the V1 checks on the accepted drafts."),
        ("select", _cmd_select, "Select the protocol v1 pilot and write the review batches."),
        ("report", _cmd_report, "Write the pilot report from complete review records."),
        (
            "export-review",
            _cmd_export,
            "Write the review sheet (CSV; recorded decisions filled in).",
        ),
        (
            "import-review",
            _cmd_import,
            "Validate a filled review sheet and store the review records.",
        ),
        (
            "validation-sample",
            _cmd_sample,
            "List the rejected topics to redraft (complete review only).",
        ),
        ("split-plan", _cmd_split_plan, "Dry run: dev/test split by change and leakage checks."),
        (
            "freeze",
            _cmd_freeze,
            "Freeze the dev/test split and test hashes (complete review only).",
        ),
        ("datasheet", _cmd_datasheet, "Write the benchmark datasheet from the stored data."),
        (
            "screen",
            _cmd_screen,
            "Advisory T4/T5 drafting-check screen (protocol v1, D3); decides nothing.",
        ),
    ):
        c = sub.add_parser(name, help=text)
        c.add_argument("--root", type=Path, default=Path(), help="Repository root.")
        if name == "import-review":
            c.add_argument("--sheet", type=Path, default=p.REVIEW_SHEET, help="Relative to --root.")
        c.set_defaults(handler=handler)


def _run_v1(root: Path) -> tuple[list[dict[str, Any]], dict[str, p.V1Result]]:
    ctx = load_context(root, Path("configs/drafting.toml"))
    drafts = [
        json.loads(ln)
        for ln in (root / d.DRAFTS_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    ]
    cfg, _ = load_config(root / "configs/corpus.toml")
    ch, _ = load_chunking_config(root / "configs/chunking.toml")
    man, _ = load_manifest(root / cfg.paths.manifest)
    if man is None:
        raise d.DraftingError("corpus manifest cannot be loaded")
    pins = load_pins(root / PROBES_PATH)
    lookup = p.CorpusLookup(
        p.load_chunks(root / "data/build/chunks", d.CORPUS_VERSIONS),
        build_source_index(root, cfg, ch, man),
        {pkg.applies_to_doc_version: pkg.version for pkg in pins.packages},
    )
    observed: set[str] = set()
    obs = root / "data/evidence/runtime_observations.jsonl"
    if obs.is_file():
        observed = {
            json.loads(ln).get("probe_id", "")
            for ln in obs.read_text("utf-8").splitlines()
            if ln.strip()
        }
    by_id = {x["draft_id"]: x for x in drafts}
    v1 = p.v1_check(
        drafts,
        lookup,
        {r.change_id: r for r in ctx.records},
        observed,
        frozenset(released_hold_ids(root, by_id)),
    )
    out = root / V1_RESULTS
    out.write_bytes(
        "".join(
            json.dumps(r.as_dict(), sort_keys=True) + "\n" for _, r in sorted(v1.items())
        ).encode()
    )
    return drafts, v1


def _summary(v1: dict[str, p.V1Result]) -> None:
    status = Counter(r.status for r in v1.values())
    print("V1 results: " + ", ".join(f"{k} {v}" for k, v in sorted(status.items())))
    for check in p.CHECKS:
        c = Counter(r.checks.get(check, "-") for r in v1.values())
        print(f"  {check}: " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    reasons = Counter(x for r in v1.values() for x in set(r.reasons))
    print(
        "failure reasons: " + (", ".join(f"{k} {v}" for k, v in sorted(reasons.items())) or "none")
    )


def _cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    try:
        _, v1 = _run_v1(args.root)
    except (OSError, d.DraftingError, ValueError) as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    _summary(v1)
    failed = sum(r.status == "fail" for r in v1.values())
    print(
        f"RESULT: {'FAILED' if failed else 'PASSED'} ({len(v1)} drafts checked; {failed} fail V1)"
    )
    return 1 if failed else 0


def _cmd_select(args: argparse.Namespace, settings: Settings) -> int:
    try:
        drafts, v1 = _run_v1(args.root)
    except (OSError, d.DraftingError, ValueError) as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    _summary(v1)
    pilot, gaps = p.select_pilot(drafts, v1)
    (args.root / p.PILOT_PATH).write_bytes(
        "".join(
            json.dumps(
                {
                    "draft_id": x["draft_id"],
                    "item_id": x["item_id"],
                    "type": x["type"],
                    "target_version": x["target_version"],
                    "boundary": x["boundary"],
                    "change_id": x["change_id"],
                    "v1": v1[x["draft_id"]].status,
                },
                sort_keys=True,
            )
            + "\n"
            for x in pilot
        ).encode()
    )
    written = p.write_review_batches(pilot, v1, args.root)
    print(
        "pilot composition: "
        + ", ".join(
            f"{t} {sum(1 for x in pilot if x['type'] == t)}" for t in p.PROTOCOL_V1_COMPOSITION
        )
    )
    print(
        "pilot per boundary: "
        + ", ".join(f"{k} {v}" for k, v in sorted(Counter(x["boundary"] for x in pilot).items()))
    )
    print(
        "pilot per target version: "
        + ", ".join(
            f"{k} {v}" for k, v in sorted(Counter(x["target_version"] for x in pilot).items())
        )
    )
    for path in written:
        print(f"wrote {path.relative_to(args.root).as_posix()}")
    if gaps:
        print(
            "RESULT: FAILED (shortfall: "
            + "; ".join(f"{k} {a} of {b}" for k, (a, b) in gaps.items())
            + ")"
        )
        return 1
    print(f"RESULT: DONE ({len(pilot)} pilot items; V0 drafts awaiting owner review, none gold)")
    return 0


def _cmd_report(args: argparse.Namespace, settings: Settings) -> int:
    path = args.root / p.REVIEW_RECORDS
    if not path.is_file():
        print("RESULT: INCOMPLETE (no review records; run pilot select)")
        return 2
    records = [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    problems = p.check_review_records(records)
    if problems:
        decided = sum(1 for r in records if r.get("decision") in p.DECISIONS)
        print(f"review records: {decided} of {len(records)} decided; {len(problems)} problems")
        print("RESULT: INCOMPLETE (the pilot report needs complete owner review records)")
        return 2
    pilot = [
        json.loads(ln)
        for ln in (args.root / d.DRAFTS_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    ]
    v1 = {
        json.loads(ln)["draft_id"]: json.loads(ln)
        for ln in (args.root / V1_RESULTS).read_text("utf-8").splitlines()
        if ln.strip()
    }
    out = args.root / REPORT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(p.render_pilot_report(records, v1, pilot).encode("utf-8"))
    print(f"RESULT: DONE (wrote {REPORT_PATH.as_posix()} from {len(records)} review records)")
    return 0


SPLIT_DIR = Path("benchmark/splits")
DATASHEET = Path("docs/benchmark/datasheet.md")


def _pilot_drafts(root: Path) -> list[dict[str, Any]]:
    ids = [
        json.loads(ln)["draft_id"]
        for ln in (root / p.PILOT_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    ]
    drafts = {
        json.loads(ln)["draft_id"]: json.loads(ln)
        for ln in (root / d.DRAFTS_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    }
    released = [i for i in released_hold_ids(root, drafts) if i not in ids]
    return [drafts[i] for i in ids] + [drafts[i] for i in released]


def _records(root: Path) -> list[dict[str, Any]]:
    path = root / p.REVIEW_RECORDS
    return (
        [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]
        if path.is_file()
        else []
    )


def _v1_results(root: Path) -> dict[str, p.V1Result]:
    out = {}
    for ln in (root / V1_RESULTS).read_text("utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            out[r["draft_id"]] = p.V1Result(r["draft_id"], r["checks"], r["reasons"])
    return out


def _cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    try:
        pilot, v1 = _pilot_drafts(args.root), _v1_results(args.root)
    except OSError as exc:
        print(f"RESULT: INCOMPLETE (run pilot select first: {type(exc).__name__})")
        return 2
    n = p.export_review_sheet(pilot, v1, _records(args.root), args.root / p.REVIEW_SHEET)
    print(f"RESULT: DONE (wrote {p.REVIEW_SHEET.as_posix()} with {n} items)")
    return 0


def _cmd_import(args: argparse.Namespace, settings: Settings) -> int:
    try:
        pilot = _pilot_drafts(args.root)
        ctx = load_context(args.root, Path("configs/drafting.toml"))
        cfg, _ = load_config(args.root / "configs/corpus.toml")
        ch, _ = load_chunking_config(args.root / "configs/chunking.toml")
        man, _ = load_manifest(args.root / cfg.paths.manifest)
        pins = load_pins(args.root / PROBES_PATH)
        lookup = p.CorpusLookup(
            p.load_chunks(args.root / "data/build/chunks", d.CORPUS_VERSIONS),
            build_source_index(args.root, cfg, ch, man),  # type: ignore[arg-type]
            {pkg.applies_to_doc_version: pkg.version for pkg in pins.packages},
        )
    except (OSError, d.DraftingError, ValueError) as exc:
        print(f"RESULT: INCOMPLETE (inputs cannot be loaded: {type(exc).__name__})")
        return 2
    records, problems = p.import_review_sheet(
        args.root / args.sheet, pilot, lookup, ctx.answer_types
    )
    if problems:
        for prob in problems[:40]:
            print(f"INVALID {prob}")
        print(f"RESULT: FAILED ({len(problems)} problems; nothing written)")
        return 1
    (args.root / p.REVIEW_RECORDS).write_bytes(
        "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records).encode(
            "utf-8"
        )
    )
    decided = Counter(r["decision"] or "undecided" for r in records)
    print("decisions: " + ", ".join(f"{k} {v}" for k, v in sorted(decided.items())))
    print(f"RESULT: DONE (stored {len(records)} review records in {p.REVIEW_RECORDS.as_posix()})")
    return 0


def _cmd_sample(args: argparse.Namespace, settings: Settings) -> int:
    try:
        topics = p.validation_sample(_records(args.root), _pilot_drafts(args.root))
    except (ValueError, OSError):
        print("RESULT: INCOMPLETE (the validation sample needs complete owner review records)")
        return 2
    print(
        "topics to redraft with the final v1 prompts: "
        + (", ".join(topics) or "none (no rejections)")
    )
    print(f"RESULT: DONE ({len(topics)} topics)")
    return 0


def _split_items(root: Path) -> tuple[list[dict[str, Any]], bool]:
    pilot, records = _pilot_drafts(root), {r["draft_id"]: r for r in _records(root)}
    reviewed = bool(records) and not p.check_review_records(list(records.values()))
    if not reviewed:
        return pilot, False
    items = [sp.apply_edits(x, records[x["draft_id"]]) for x in pilot]
    return [x for x in items if x["review"]["decision"] != "reject"], True


def _print_split(split: dict[str, list[dict[str, Any]]], leak: sp.LeakageReport) -> None:
    for name in ("dev", "test"):
        xs = split[name]
        print(
            f"{name}: {len(xs)} items, {len({x['change_id'] for x in xs})} changes; "
            + ", ".join(
                f"{t} {sum(1 for x in xs if x['type'] == t)}" for t in p.PROTOCOL_V1_COMPOSITION
            )
        )
    print(
        f"leakage: shared changes {leak.shared_changes}, near-duplicate pairs "
        f"{leak.near_duplicate_pairs}, "
        f"shared evidence texts {leak.shared_evidence_texts}; shared passages (informational) "
        f"{leak.shared_passages}"
    )


def _cmd_split_plan(args: argparse.Namespace, settings: Settings) -> int:
    try:
        items, reviewed = _split_items(args.root)
    except OSError as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    split = sp.split_by_change(items)
    leak = sp.leakage_checks(split["dev"], split["test"])
    _print_split(split, leak)
    plan = {name: [x["draft_id"] for x in xs] for name, xs in split.items()}
    (args.root / "benchmark/drafts/split_plan.json").write_text(
        json.dumps(plan, indent=1) + "\n", "utf-8"
    )
    kind = "reviewed (rejections removed, edits applied)" if reviewed else "UNREVIEWED V0 drafts"
    print(f"items: {kind}")
    print(f"RESULT: {'PASSED' if leak.passed else 'FAILED'} (dry run; nothing frozen)")
    return 0 if leak.passed else 1


def _cmd_freeze(args: argparse.Namespace, settings: Settings) -> int:
    try:
        pilot = _pilot_drafts(args.root)
    except OSError as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    problems = sp.freeze_preconditions(_records(args.root), {x["draft_id"] for x in pilot})
    if problems:
        print(
            f"RESULT: NOT FROZEN (review incomplete: {len(problems)} problems; run pilot "
            "import-review)"
        )
        return 2
    items, _ = _split_items(args.root)
    split = sp.split_by_change(items)
    leak = sp.leakage_checks(split["dev"], split["test"])
    _print_split(split, leak)
    if not leak.passed:
        print("RESULT: NOT FROZEN (leakage checks failed)")
        return 1
    out = args.root / SPLIT_DIR
    out.mkdir(parents=True, exist_ok=True)
    for name, xs in split.items():
        (out / f"{name}.jsonl").write_bytes(
            "".join(
                json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n"
                for x in sorted(xs, key=lambda x: x["draft_id"])
            ).encode("utf-8")
        )
    manifest = sp.test_manifest(split["test"])
    sp.write_test_manifest(out / "test_manifest.json", manifest)
    print(f"RESULT: FROZEN (test {manifest['items']} items, sha256 {manifest['test_set_sha256']})")
    return 0


def _cmd_datasheet(args: argparse.Namespace, settings: Settings) -> int:
    import hashlib
    import tomllib

    try:
        pilot, v1 = _pilot_drafts(args.root), _v1_results(args.root)
        ctx = load_context(args.root, Path("configs/drafting.toml"))
    except (OSError, d.DraftingError) as exc:
        print(f"RESULT: INCOMPLETE ({type(exc).__name__})")
        return 2
    records = _records(args.root)
    reviewed = bool(records) and not p.check_review_records(records)
    frozen = (args.root / SPLIT_DIR / "test_manifest.json").is_file()
    levels: Counter[str] = Counter()
    rec_by = {r["draft_id"]: r for r in records}
    for x in pilot:
        dec = rec_by.get(x["draft_id"], {}).get("decision")
        levels[
            "V2 (owner accepted)"
            if dec in ("accept", "accept_minor_edit", "accept_major_edit")
            else "rejected in review"
            if dec == "reject"
            else f"V0, V1 {v1[x['draft_id']].status}"
            if x["draft_id"] in v1
            else "V0"
        ] += 1
    v1toml = tomllib.loads((args.root / "benchmark/protocol/protocol_v1.toml").read_text("utf-8"))
    selected = sum(
        1 for ln in (args.root / p.PILOT_PATH).read_text("utf-8").splitlines() if ln.strip()
    )
    splits = (
        {
            name: [
                json.loads(ln)
                for ln in (args.root / SPLIT_DIR / f"{name}.jsonl").read_text("utf-8").splitlines()
                if ln.strip()
            ]
            for name in ("dev", "test")
        }
        if frozen
        else {}
    )
    facts = {
        "status": sp.datasheet_status(
            selected,
            len(pilot) - selected,
            levels["V2 (owner accepted)"],
            reviewed,
            {k: len(v) for k, v in splits.items()} or None,
        ),
        "splits_by_type": {k: dict(Counter(x["type"] for x in v)) for k, v in splits.items()},
        "by_type": dict(Counter(x["type"] for x in pilot)),
        "by_boundary": dict(Counter(x["boundary"] for x in pilot)),
        "by_target": dict(Counter(x["target_version"] for x in pilot)),
        "by_level": dict(levels),
        "model": ctx.config.model.id,
        "temperature": ctx.config.model.temperature,
        "max_output_tokens": ctx.config.model.max_output_tokens,
        "prompts": v1toml["prompts"],
        "cached_requests": sum(
            1 for e in d.load_cache(args.root / d.CACHE_PATH) if e.get("status") == "ok"
        ),
        "corpus_manifest_sha256": hashlib.sha256(
            (args.root / "data/manifest/corpus_manifest.json").read_bytes()
        ).hexdigest(),
        "limitations": [
            "Version-specific (T2) items are scarce: most version changes are not documented in "
            "each version's own pages (protocol v1 §1).",
            "Documentation corrections (earlier docs disagreeing with that version's behaviour) "
            "are excluded from counted items.",
            "Canonical observations were recorded and validated for all probes referenced by the "
            "benchmark items. Automatic per-item comparison against runtime observations is not "
            "implemented. Items are verified against documentation and owner review; probe "
            "consistency remains pending.",
            "Two T2 drafts held for a suspected documentation conflict were released into the "
            "review by owner decision D2 and reviewed with the pilot.",
            "Drafted with a Gemini free-tier model; only owner-accepted (V2) records are gold.",
        ],
        "commands": [
            "python -m uv run vrag benchmark draft plan",
            "python -m uv run vrag benchmark pilot check",
            "python -m uv run vrag benchmark pilot select",
            "python -m uv run vrag benchmark pilot export-review",
            "python -m uv run vrag benchmark pilot import-review",
            "python -m uv run vrag benchmark pilot report",
            "python -m uv run vrag benchmark pilot split-plan",
            "python -m uv run vrag benchmark pilot freeze",
            "python -m uv run vrag benchmark pilot datasheet",
        ],
    }
    out = args.root / DATASHEET
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(sp.render_datasheet(facts).encode("utf-8"))
    print(f"RESULT: DONE (wrote {DATASHEET.as_posix()}; status: {facts['status']})")
    return 0
