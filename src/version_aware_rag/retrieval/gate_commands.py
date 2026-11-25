"""``vrag eval gate baseline`` and ``vrag eval gate check`` (RAG-M9; design §6).

The enforced gate runs on the frozen V2 ci-gate split (``benchmark/splits/ci_gate.jsonl``). Until
that split exists the gate reports ERROR (exit 2), never PASS. ``--provisional`` runs the same
machinery on the V0 pilot drafts for a dry run; its baseline is labelled provisional and can never
be compared with a ci-gate run (the item hash differs, which is ERROR).

``baseline.json`` stores per-item scores (Recall@10 and VCR@10 as exact fractions) plus the item,
corpus and configuration hashes. An item or corpus hash mismatch is a stale baseline (ERROR); a
configuration difference is what a pull request changes, so it is reported but gated normally
(design §6.1). The enforced baseline must match the owner-approved sha256 in ``configs/gate.toml``
(design §6.2 "Baseline protection"); otherwise ERROR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tomllib
from fractions import Fraction
from pathlib import Path
from typing import Any

from version_aware_rag.retrieval import gate, harness
from version_aware_rag.retrieval import metrics as m
from version_aware_rag.retrieval.commands import corpus_from, eval_items, load_retrievers
from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.retrieval.methods import (
    DENSE_MODEL,
    LATE_MODEL,
    RERANK_MODEL,
    RRF_K,
    Retriever,
)
from version_aware_rag.settings import Settings

CI_GATE_SPLIT = Path("benchmark/splits/ci_gate.jsonl")
BASELINE = Path("benchmark/gate/baseline.json")
PROVISIONAL_BASELINE = Path("results/gate/baseline_provisional.json")
RESULTS = Path("results/gate")
GATE_CONFIG = Path("configs/gate.toml")
STALE_KEYS = ("items_sha256", "corpus_sha256")  # design §6.1: these make a baseline stale


def approved_baseline_problem(root: Path, baseline_bytes: bytes) -> str | None:
    """None when the baseline matches the owner-approved sha256, otherwise the ERROR reason."""
    try:
        cfg = tomllib.loads((root / GATE_CONFIG).read_text("utf-8"))
        recorded = str(cfg["gate"]["baseline_sha256"]).strip().lower()
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        return f"gate config unreadable ({type(exc).__name__})"
    if not recorded:
        return f"no owner-approved baseline sha256 in {GATE_CONFIG.as_posix()}"
    actual = hashlib.sha256(baseline_bytes).hexdigest()
    if actual != recorded:
        return f"baseline sha256 {actual[:12]} does not match the approved {recorded[:12]}"
    return None


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode("utf-8")).hexdigest()


def per_item(
    items: list[dict[str, Any]], corpus: RetrievalCorpus, retriever: Retriever
) -> dict[str, dict[str, Any]]:
    """Version-filtered top-10 scores per item. Recall@10 and VCR@10 are exact fractions."""
    out: dict[str, dict[str, Any]] = {}
    for x in items:
        q = harness.qrels_for(x, corpus)
        relevant = {c for c, g in q.items() if g >= 2}
        if not relevant:
            continue  # not retrieval-eligible (T5 or no grade-2 chunk)
        ranked = [c for c, _ in retriever.retrieve(x["question"], x["target_version"], 10)]
        mask = corpus.mask(x["target_version"])
        wrong = sum(1 for c in ranked if not mask[corpus.by_id[c]])
        out[str(x["item_id"])] = {
            "recall@10": str(Fraction(len(relevant & set(ranked)), len(relevant))),
            "ndcg@10": m.ndcg_at_k(ranked, q, 10),
            "vcr@10": str(Fraction(wrong, len(ranked))) if ranked else None,  # None -> ERROR
            "type": x["type"],
            "cluster": harness.cluster_of(x),
        }
    return out


def _context(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str, Path] | str:
    root = args.root
    if args.provisional:
        items, label = eval_items(root)
        return items, f"PROVISIONAL dry run, not an enforced gate ({label})", PROVISIONAL_BASELINE
    path = root / CI_GATE_SPLIT
    if not path.is_file():
        return (
            "the frozen V2 ci-gate split does not exist yet "
            f"({CI_GATE_SPLIT.as_posix()}; it needs the owner-reviewed benchmark)"
        )
    items = [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]
    return items, "ci-gate split (V2)", BASELINE


def _hashes(items: list[dict[str, Any]], corpus: RetrievalCorpus, method: str) -> dict[str, str]:
    return {
        "items_sha256": _sha(
            sorted([str(x["item_id"]), x["question"], x["target_version"]] for x in items)
        ),
        "corpus_sha256": _sha([c.chunk_id for c in corpus.chunks]),
        "config_sha256": _sha(
            {
                "method": method,
                "dense": DENSE_MODEL,
                "rerank": RERANK_MODEL,
                "late": LATE_MODEL,
                "rrf_k": RRF_K,
            }
        ),
    }


def add_gate_commands(eval_actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    g = eval_actions.add_parser("gate", help="CI regression gate (RAG-M9, design §6).")
    ga = g.add_subparsers(dest="gate_command", required=True, metavar="ACTION")
    for name, handler, text in (
        ("baseline", _cmd_baseline, "Write the baseline per-item scores."),
        ("check", _cmd_check, "Compare the candidate with the baseline (exit 0/1/2)."),
    ):
        p = ga.add_parser(name, help=text)
        p.add_argument("--root", type=Path, default=Path())
        p.add_argument(
            "--method",
            default="hybrid",
            choices=("bm25", "dense", "finetuned", "hybrid", "rerank", "late"),
        )
        p.add_argument("--provisional", action="store_true", help="Dry run on the V0 pilot drafts.")
        p.add_argument("--resamples", type=int, default=gate.RESAMPLES)
        p.set_defaults(handler=handler)
    c = ga.add_parser(
        "calibrate",
        help="Neutral, benign and injected-regression scenarios through the gate (design 6.4).",
    )
    c.add_argument("--root", type=Path, default=Path())
    c.add_argument("--provisional", action="store_true", help="Dry run on the V0 pilot drafts.")
    c.add_argument("--resamples", type=int, default=gate.RESAMPLES)
    c.set_defaults(handler=_cmd_calibrate)


def _cmd_calibrate(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.retrieval import gate_calibration as gc

    ctx = _context(args)
    if isinstance(ctx, str):
        print(f"RESULT: ERROR ({ctx})")
        return 2
    items, label, _ = ctx
    corpus = corpus_from(args.root)
    hybrid = load_retrievers(args.root, corpus, ["hybrid"])[0]
    baseline = per_item(items, corpus, hybrid)
    gran = gc.granularity(baseline)
    rows = gc.run(
        baseline,
        gc.scenarios(hybrid, corpus),  # type: ignore[arg-type]
        lambda r: per_item(items, corpus, r),
        args.resamples,
    )
    out = args.root / RESULTS
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(
        json.dumps(
            {"label": label, "granularity": gran, "scenarios": rows}, indent=1, sort_keys=True
        )
        + "\n",
        "utf-8",
    )
    (out / "calibration.md").write_text(gc.render(label, gran, rows), "utf-8")
    for r in rows:
        print(f"  {r['outcome']:<11} expected {r['expected']:<12} {r['scenario']}")
    bad = [r["scenario"] for r in rows if not r["as_expected"]]
    status = "DONE" if not bad else "DONE WITH SURPRISES"
    print(f"RESULT: {status} ({label}; {len(bad)} unexpected)")
    return 0


def _cmd_baseline(args: argparse.Namespace, settings: Settings) -> int:
    ctx = _context(args)
    if isinstance(ctx, str):
        print(f"RESULT: ERROR ({ctx})")
        return 2
    items, label, path = ctx
    corpus = corpus_from(args.root)
    retriever = load_retrievers(args.root, corpus, [args.method])[0]
    scores = per_item(items, corpus, retriever)
    payload = {
        "label": label,
        "method": args.method,
        **_hashes(items, corpus, args.method),
        "scores": scores,
    }
    out = args.root / path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", "utf-8")
    print(f"{label}: {len(scores)} retrieval-eligible items, method {args.method}")
    if not args.provisional:
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        print(
            f"baseline sha256 {digest}: the gate enforces it only after the owner approves it "
            f"and records it as baseline_sha256 in {GATE_CONFIG.as_posix()}"
        )
    print(f"RESULT: DONE (wrote {path.as_posix()})")
    return 0


def _cmd_check(args: argparse.Namespace, settings: Settings) -> int:
    ctx = _context(args)
    if isinstance(ctx, str):
        print(f"RESULT: ERROR ({ctx})")
        return 2
    items, label, path = ctx
    bpath = args.root / path
    if not bpath.is_file():
        print(f"RESULT: ERROR (no baseline at {path.as_posix()}; run gate baseline)")
        return 2
    raw = bpath.read_bytes()
    baseline_sha = hashlib.sha256(raw).hexdigest()
    if not args.provisional:
        problem = approved_baseline_problem(args.root, raw)
        if problem:
            print(f"RESULT: ERROR ({problem})")
            return 2
    base = json.loads(raw.decode("utf-8"))
    corpus = corpus_from(args.root)
    hashes = _hashes(items, corpus, args.method)
    stale = [k for k in STALE_KEYS if base.get(k) != hashes[k]]
    if stale:
        print(f"RESULT: ERROR (stale baseline: {', '.join(stale)} differ)")
        return 2
    config_changed = base.get("config_sha256") != hashes["config_sha256"]
    retriever = load_retrievers(args.root, corpus, [args.method])[0]
    cand = per_item(items, corpus, retriever)
    bs = base["scores"]
    overall, results, errors = gate.decide(
        bs,
        cand,
        {i: s["cluster"] for i, s in bs.items()},
        {i: s["type"] for i, s in bs.items()},
        args.resamples,
    )
    lines = [
        f"# Gate report ({label})",
        "",
        f"Method: {args.method} (baseline: {base.get('method')}). Overall: **{overall}**.",
        f"Baseline sha256: `{baseline_sha}`. Configuration "
        + (
            "differs from the baseline (the change under test)." if config_changed else "unchanged."
        ),
        "",
    ]
    lines += [
        "| Metric | R-bar | L (0.625th pct) | Band | Significant | Outcome |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.metric} | {r.r_bar:.4f} | {r.lower:.4f} | {r.band} "
            f"| {r.significant} | {r.outcome} |"
        )
        if r.outcome in ("WARN", "BLOCK") and r.worst_items:
            lines.append(f"|  | worst items: {', '.join(r.worst_items)} | | | | |")
    lines += [f"- ERROR: {e}" for e in errors]
    out = args.root / RESULTS
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text("\n".join(lines) + "\n", "utf-8")
    # Every outcome is logged with the baseline hash it was measured against (design §6.2).
    with (out / "gate_history.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            json.dumps(
                {
                    "label": label,
                    "method": args.method,
                    "baseline_sha256": baseline_sha,
                    "outcome": overall,
                    "metrics": {r.metric: r.outcome for r in results},
                },
                sort_keys=True,
            )
            + "\n"
        )
    for r in results:
        print(f"  {r.metric:<13} R {r.r_bar:+.4f}  L {r.lower:+.4f}  {r.band:<10} {r.outcome}")
    for e in errors:
        print(f"  ERROR: {e}")
    print(f"RESULT: {overall} ({label})")
    return gate.EXIT[overall]
