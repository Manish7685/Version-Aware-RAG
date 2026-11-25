"""Error analysis (RAG-M8, design §5.4): every observed failure from the stored raw outputs,
classified automatically from ids, qrels and tags only (no text is read or written).

A **retrieval failure** is a (method, filter, item) run whose top 10 misses at least one grade-2
chunk. Categories (design §5.4 names, decided in this order):

- ``wrong_version``: a version-confuser chunk (same page and section, incompatible version) is in
  the top 10;
- ``missing_hop``: a T4 item with some but not all of its grade-2 chunks in the top 10;
- ``chunk_split``: another type with some but not all of its grade-2 chunks in the top 10 (the
  evidence is spread over several chunks);
- ``wrong_section``: no grade-2 chunk in the top 10 but a grade-1 chunk (same page) is;
- ``lexical_miss``: neither the evidence nor its page is in the top 10.

``near_miss`` marks a failure whose missing grade-2 chunk is ranked 11-20.

An **answer failure** is a scored answer that is wrong: ``wrong_version`` (a T2 answer matching
another version's gold), ``retrieval_miss`` (no required evidence was retrieved),
``over_abstention`` (abstained on an answerable item), ``failed_abstention`` (answered a T5 item),
``possible_matcher_gap`` (the answer shares most tokens with the gold; owner to confirm) or
``wrong_answer``. ``unresolved`` answers are reported separately (owner-graded, design §5.3).

The categories are an automatic first pass. Confirming or re-labelling them is the owner's review;
nothing here is presented as human-verified.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from version_aware_rag.settings import Settings

RETRIEVAL = Path("results/retrieval")
ANSWERS = Path("results/answers")
OUT = Path("results/errors")
DESIGN_MINIMUM = 30
_WORD = re.compile(r"[a-z0-9_]+")


def classify_retrieval(
    run: dict[str, Any], qrels: dict[str, int], confusers: set[str]
) -> dict[str, Any] | None:
    relevant = {c for c, g in qrels.items() if g >= 2}
    top10, top20 = run["ranked"][:10], run["ranked"][:20]
    found = relevant & set(top10)
    if found == relevant:
        return None
    if confusers & set(top10):
        category = "wrong_version"
    elif found:
        category = "missing_hop" if run["type"] == "T4" else "chunk_split"
    elif any(qrels.get(c) == 1 for c in top10):
        category = "wrong_section"
    else:
        category = "lexical_miss"
    return {
        "kind": "retrieval",
        "method": run["method"],
        "filtered": run["filtered"],
        "draft_id": run["draft_id"],
        "item_id": run["item_id"],
        "type": run["type"],
        "category": category,
        "near_miss": bool((relevant - found) & set(top20[10:])),
        "recall@10": len(found) / len(relevant),
    }


def _tokens(text: str | None) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def classify_answer(
    score: dict[str, Any], answer: dict[str, Any] | None, gold: str | None
) -> dict[str, Any] | None:
    if score["correct"] is True:
        return None
    base = {"kind": "answer", "draft_id": score["draft_id"], "type": score["type"]}
    if score["correct"] is None:
        return {**base, "category": "unresolved"}
    if score["type"] == "T5":
        return {**base, "category": "failed_abstention"}
    if score["abstain"]:
        return {**base, "category": "over_abstention"}
    if score.get("version_outcome") == "wrong_version":
        return {**base, "category": "wrong_version"}
    if score.get("evidence_recall") == 0.0 and not score.get("citation_precision"):
        return {**base, "category": "retrieval_miss"}
    a, g = _tokens((answer or {}).get("short_answer")), _tokens(gold)
    if a and g and len(a & g) / len(a | g) >= 0.5:
        return {**base, "category": "possible_matcher_gap"}
    return {**base, "category": "wrong_answer"}


def analyse(
    runs: list[dict[str, Any]],
    info: dict[str, Any],
    scores: list[dict[str, Any]],
    answers: dict[str, dict[str, Any]],
    golds: dict[str, str | None],
) -> dict[str, Any]:
    qrels = info["qrels"]
    tags = info["hard_negatives"]
    failures: list[dict[str, Any]] = []
    for r in runs:
        f = classify_retrieval(
            r, qrels[r["draft_id"]], set(tags.get(r["draft_id"], {}).get("version_confusers", []))
        )
        if f:
            failures.append(f)
    for s in scores:
        f = classify_answer(s, answers.get(s["draft_id"]), golds.get(s["draft_id"]))
        if f:
            failures.append(f)
    counted = [f for f in failures if f["category"] != "unresolved"]

    def key(f: dict[str, Any]) -> str:
        if f["kind"] == "answer":
            return "answer"
        return f"{f['method']} ({'version' if f['filtered'] else 'none'})"

    return {
        "failures": failures,
        "total_failures": len(counted),
        "distinct_items": len({f["draft_id"] for f in counted}),
        "design_minimum": DESIGN_MINIMUM,
        "by_category": dict(sorted(Counter(f["category"] for f in counted).items())),
        "by_run": {
            k: dict(sorted(Counter(f["category"] for f in counted if key(f) == k).items()))
            for k in sorted({key(f) for f in counted})
        },
        "near_misses": sum(1 for f in counted if f.get("near_miss")),
    }


def render(result: dict[str, Any], label: str) -> str:
    lines = [
        f"# Error analysis ({label})",
        "",
        f"Failures: {result['total_failures']} over {result['distinct_items']} distinct items "
        f"(design minimum {result['design_minimum']}). Automatic classification from ids, qrels "
        "and hard-negative tags; the owner's confirmation is pending.",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in result["by_category"].items()]
    lines += [
        "",
        f"Near misses (a missing grade-2 chunk at ranks 11-20): {result['near_misses']}.",
        "",
    ]
    lines += ["| Run | " + "Categories |", "|---|---|"]
    lines += [
        f"| {k} | " + ", ".join(f"{c} {n}" for c, n in v.items()) + " |"
        for k, v in result["by_run"].items()
    ]
    lines += [
        "",
        "## Failures",
        "",
        "| Kind | Run | Item | Type | Category |",
        "|---|---|---|---|---|",
    ]
    for f in result["failures"]:
        run = (
            f"{f['method']} ({'version' if f['filtered'] else 'none'})"
            if f["kind"] == "retrieval"
            else "answer (hybrid)"
        )
        lines.append(
            f"| {f['kind']} | {run} | {f.get('item_id', f['draft_id'])} | {f['type']} "
            f"| {f['category']}{' (near miss)' if f.get('near_miss') else ''} |"
        )
    return "\n".join(lines) + "\n"


def add_error_commands(eval_actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    e = eval_actions.add_parser("errors", help="Error analysis from the stored raw outputs (M8).")
    e.add_argument("--root", type=Path, default=Path())
    e.add_argument("--answer-method", default="hybrid")
    e.set_defaults(handler=_cmd_errors)


def _cmd_errors(args: argparse.Namespace, settings: Settings) -> int:
    root = args.root
    try:
        summary = json.loads((root / RETRIEVAL / "summary.json").read_text("utf-8"))
        runs = [
            json.loads(ln)
            for ln in (root / RETRIEVAL / "runs.jsonl").read_text("utf-8").splitlines()
            if ln.strip()
        ]
    except OSError:
        print("RESULT: INCOMPLETE (run vrag eval retrieval first; runs.jsonl is needed)")
        return 2
    scores: list[dict[str, Any]] = []
    answers: dict[str, dict[str, Any]] = {}
    golds: dict[str, str | None] = {}
    apath = root / ANSWERS / f"summary_{args.answer_method}.json"
    if apath.is_file():
        scores = json.loads(apath.read_text("utf-8"))["scores"]
        ans_path = root / ANSWERS / f"answers_{args.answer_method}.jsonl"
        if ans_path.is_file():
            for ln in ans_path.read_text("utf-8").splitlines():
                if ln.strip():
                    a = json.loads(ln)
                    answers[a["draft_id"]] = a
        from version_aware_rag.retrieval.commands import eval_items

        items, _ = eval_items(root)
        golds = {x["draft_id"]: x.get("gold_short_answer") for x in items}
    result = analyse(runs, summary["info"], scores, answers, golds)
    out = root / OUT
    out.mkdir(parents=True, exist_ok=True)
    label = summary.get("label", "")
    (out / "error_analysis.json").write_text(
        json.dumps({"label": label, **result}, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    (out / "error_analysis.md").write_text(render(result, label), "utf-8")
    print(f"failures {result['total_failures']} over {result['distinct_items']} items ({label})")
    for k, v in result["by_category"].items():
        print(f"  {k}: {v}")
    print(f"RESULT: DONE (wrote {OUT.as_posix()}/error_analysis.json and .md)")
    return 0
