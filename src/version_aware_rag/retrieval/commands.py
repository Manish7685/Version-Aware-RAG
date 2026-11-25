"""``vrag index build`` and ``vrag eval retrieval`` (RAG-M5/M6).

The evaluation uses the pilot items. While the owner's review is pending they are V0 drafts, and
every output is labelled provisional (design §5.1: headline results use V2 items only). Results go
to ``results/retrieval/`` (``summary.json`` and ``report.md``); nothing is estimated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from version_aware_rag.probes.pins import DEFAULT_PATH as PROBES_PATH
from version_aware_rag.probes.pins import load_pins
from version_aware_rag.retrieval import harness
from version_aware_rag.retrieval.corpus import RetrievalCorpus, load_corpus
from version_aware_rag.retrieval.methods import (
    INDEX_DIR,
    BM25Retriever,
    DenseRetriever,
    HybridRetriever,
    LateInteractionRetriever,
    RerankRetriever,
    Retriever,
    finetuned_retriever,
)
from version_aware_rag.settings import Settings

RESULTS = Path("results/retrieval")
ALL_METHODS = ("bm25", "dense", "finetuned", "hybrid", "rerank", "late")


def corpus_from(root: Path) -> RetrievalCorpus:
    pins = load_pins(root / PROBES_PATH)
    return load_corpus(
        root / "data/build/chunks", {p.applies_to_doc_version: p.version for p in pins.packages}
    )


def load_retrievers(root: Path, corpus: RetrievalCorpus, names: list[str]) -> list[Retriever]:
    bm25 = BM25Retriever(corpus)
    bm25.load(root / INDEX_DIR / "bm25")
    dense = DenseRetriever(corpus)
    if any(n in names for n in ("dense", "hybrid", "rerank", "late")):
        dense.load(root / INDEX_DIR / "dense")
    hybrid = HybridRetriever(bm25, dense)
    available: dict[str, Retriever] = {
        "bm25": bm25,
        "dense": dense,
        "hybrid": hybrid,
        "rerank": RerankRetriever(hybrid, corpus),
        "late": LateInteractionRetriever(hybrid, corpus),
    }
    if "finetuned" in names:
        ft = finetuned_retriever(root, corpus)
        ft.load(root / INDEX_DIR / "finetuned")
        available["finetuned"] = ft
    return [available[n] for n in names]


def add_retrieval_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    index = sub.add_parser("index", help="Retrieval indexes (RAG-M5).")
    ia = index.add_subparsers(dest="index_command", required=True, metavar="ACTION")
    b = ia.add_parser("build", help="Build the BM25 and dense indexes over the permitted chunks.")
    b.add_argument("--root", type=Path, default=Path())
    b.add_argument(
        "--methods", default="bm25,dense", help="Comma-separated: bm25, dense, finetuned."
    )
    b.set_defaults(handler=_cmd_build)
    f = ia.add_parser(
        "finetune-data", help="Write dev-split training pairs for the Colab embedder fine-tune."
    )
    f.add_argument("--root", type=Path, default=Path())
    f.set_defaults(handler=_cmd_finetune_data)
    cm = ia.add_parser(
        "check-model", help="Validate the fine-tuned model copied back from Colab (RAG-M6)."
    )
    cm.add_argument("--root", type=Path, default=Path())
    cm.add_argument("--no-load", action="store_true", help="Check files and metadata only.")
    cm.set_defaults(handler=_cmd_check_model)
    ev = sub.add_parser("eval", help="Evaluation (RAG-M5+).")
    ea = ev.add_subparsers(dest="eval_command", required=True, metavar="ACTION")
    r = ea.add_parser("retrieval", help="Evaluate retrieval methods with bootstrap CIs.")
    r.add_argument("--root", type=Path, default=Path())
    r.add_argument("--methods", default="bm25,dense,hybrid,rerank")
    r.add_argument("--resamples", type=int, default=10_000)
    r.add_argument(
        "--split",
        choices=("auto", "pilot", "dev", "test"),
        default="auto",
        help="auto: the frozen test split once it exists, otherwise the pilot items.",
    )
    r.set_defaults(handler=_cmd_eval)
    from version_aware_rag.answer.commands import add_answer_commands

    add_answer_commands(ea)
    from version_aware_rag.answer.support import add_support_commands

    add_support_commands(ea)
    from version_aware_rag.retrieval.gate_commands import add_gate_commands

    add_gate_commands(ea)
    from version_aware_rag.retrieval.errors import add_error_commands

    add_error_commands(ea)


def _cmd_build(args: argparse.Namespace, settings: Settings) -> int:
    corpus = corpus_from(args.root)
    out = args.root / INDEX_DIR
    out.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in args.methods.split(",") if n.strip()]
    if "bm25" in names:
        bm25 = BM25Retriever(corpus)
        bm25.build()
        bm25.save(out / "bm25")
        print(f"bm25: indexed {len(corpus.chunks)} permitted chunks")
    if "dense" in names:
        dense = DenseRetriever(corpus)
        dense.build()
        dense.save(out / "dense")
        print(f"dense: embedded {len(corpus.chunks)} permitted chunks with {dense.model_name}")
    if "finetuned" in names:
        try:
            ft = finetuned_retriever(args.root, corpus)
        except FileNotFoundError as exc:
            print(f"RESULT: INCOMPLETE ({exc}; train it with scripts/colab/finetune_embedder.py)")
            return 2
        ft.build()
        ft.save(out / "finetuned")
        print(f"finetuned: embedded {len(corpus.chunks)} permitted chunks")
    print("RESULT: DONE")
    return 0


def _cmd_finetune_data(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.retrieval import finetune as ft

    try:
        dev = ft.load_split(args.root, "dev")
        held = ft.load_split(args.root, "test")
    except FileNotFoundError as exc:
        print(f"RESULT: INCOMPLETE (split not frozen: {exc})")
        return 2
    gate_split = args.root / ft.SPLIT_DIR / "ci_gate.jsonl"
    if gate_split.is_file():
        held += ft.load_split(args.root, "ci_gate")
    corpus = corpus_from(args.root)
    miner = load_retrievers(args.root, corpus, ["bm25"])[0]
    try:
        pairs = ft.build_pairs(dev, held, corpus, miner)
    except ft.LeakageError as exc:
        print(f"RESULT: REFUSED ({exc})")
        return 1
    out = args.root / "data/build/finetune/train_pairs.jsonl"
    ft.write_pairs(pairs, corpus, out)
    manifest = ft.pairs_manifest(pairs, dev, held, out.read_bytes())
    (out.parent / "train_pairs.manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    print(
        f"dev items {len(dev)}, held-out items {len(held)}, pairs {len(pairs)} "
        f"({manifest['pairs_without_negatives']} without a mined negative), "
        f"sha256 {manifest['pairs_sha256'][:12]}"
    )
    print(
        "RESULT: DONE (wrote data/build/finetune/train_pairs.jsonl and train_pairs.manifest.json; "
        "git-ignored)"
    )
    return 0


def _cmd_check_model(args: argparse.Namespace, settings: Settings) -> int:
    """Validate the model copied back from Colab before indexing it (RAG-M6)."""
    from version_aware_rag.retrieval.finetune_check import check_finetuned_model

    problems, facts = check_finetuned_model(args.root, load=not args.no_load)
    for k, v in facts.items():
        print(f"  {k}: {v}")
    for prob in problems:
        print(f"PROBLEM {prob}")
    if problems:
        print(f"RESULT: FAILED ({len(problems)} problems)")
        return 1
    print("RESULT: PASSED (next: vrag index build --methods finetuned)")
    return 0


def eval_items(root: Path, split: str = "auto") -> tuple[list[dict[str, Any]], str]:
    """Items to evaluate. ``auto`` uses the frozen test split once ``pilot freeze`` has written it
    (V2 only), otherwise the pilot items: reviewed (V2, edits applied, rejections removed) once
    the review is complete, else the V0 drafts, labelled provisional."""
    from version_aware_rag.retrieval import finetune as ft

    frozen = (root / ft.SPLIT_DIR / "test_manifest.json").is_file()
    if split in ("dev", "test") or (split == "auto" and frozen):
        name = "test" if split == "auto" else split
        if not frozen:
            raise FileNotFoundError(f"split {name!r} is not frozen (run pilot freeze)")
        return ft.load_split(root, name), f"frozen {name} split (owner-reviewed, V2)"
    from version_aware_rag.benchmark import pilot as p
    from version_aware_rag.benchmark import split as sp
    from version_aware_rag.benchmark.drafting import DRAFTS_PATH

    ids = [
        json.loads(ln)["draft_id"]
        for ln in (root / p.PILOT_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    ]
    drafts = {
        json.loads(ln)["draft_id"]: json.loads(ln)
        for ln in (root / DRAFTS_PATH).read_text("utf-8").splitlines()
        if ln.strip()
    }
    rec_path = root / p.REVIEW_RECORDS
    records = (
        [json.loads(ln) for ln in rec_path.read_text("utf-8").splitlines() if ln.strip()]
        if rec_path.is_file()
        else []
    )
    if records and not p.check_review_records(records):
        by = {r["draft_id"]: r for r in records}
        items = [sp.apply_edits(drafts[i], by[i]) for i in ids]
        return [
            x for x in items if x["review"]["decision"] != "reject"
        ], "pilot, owner-reviewed (V2)"
    return [drafts[i] for i in ids], "PROVISIONAL: 50 pilot V0 drafts, not human-verified"


def _cmd_eval(args: argparse.Namespace, settings: Settings) -> int:
    corpus = corpus_from(args.root)
    names = [n.strip() for n in args.methods.split(",") if n.strip() in ALL_METHODS]
    try:
        retrievers = load_retrievers(args.root, corpus, names)
        items, label = eval_items(args.root, args.split)
    except FileNotFoundError as exc:
        print(f"RESULT: INCOMPLETE ({exc})")
        return 2
    try:
        evaluated, runs, info = harness.evaluate(items, corpus, retrievers)
    except harness.UnresolvedEvidenceError as exc:
        print(f"RESULT: INVALID (unresolved evidence: {exc}; nothing written)")
        return 1
    summary = harness.summarise(evaluated, runs, n_resamples=args.resamples, slices=info["slices"])
    out = args.root / RESULTS
    out.mkdir(parents=True, exist_ok=True)
    payload = {"label": label, "info": info, **summary, "corpus_chunks": len(corpus.chunks)}
    (out / "summary.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", "utf-8")
    (out / "report.md").write_text(harness.render_markdown(summary, info, label), "utf-8")
    # Raw outputs (ids and numbers only): every table regenerates from these.
    (out / "runs.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in harness.raw_runs(evaluated, runs)),
        "utf-8",
    )
    print(f"items: {info['evaluated']} evaluated of {info['items']} ({label})")
    for item_id, why in sorted(info["excluded_reasons"].items()):
        print(f"  excluded {item_id}: {why}")
    for r in summary["runs"]:
        mm = r["metrics"]
        print(
            f"  {r['method']:<9} {'filtered' if r['filtered'] else 'unfiltered':<10} "
            f"recall@10 {mm['recall@10']['mean']:.3f} mrr@10 {mm['mrr@10']['mean']:.3f} "
            f"ndcg@10 {mm['ndcg@10']['mean']:.3f} vcr@10 {mm['vcr@10']['mean']:.3f}"
        )
    print(f"RESULT: DONE (wrote {RESULTS.as_posix()}/summary.json, report.md and runs.jsonl)")
    return 0
