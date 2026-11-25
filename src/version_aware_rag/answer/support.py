"""Citation support and faithfulness (Tier M, design §5.3; roadmap P2-R24/R25), with a local NLI
cross-encoder: no API call, nothing leaves the machine.

- **Citation support:** the share of ``explanation`` sentences entailed by at least one of the
  answer's *cited* chunks.
- **Faithfulness:** the share of ``explanation`` sentences entailed by at least one *retrieved*
  chunk. The design's premise is "all retrieved context"; the cross-encoder reads 512 tokens, so
  each retrieved chunk is a separate premise and the best one counts (a documented deviation).

A sentence counts as entailed when P(entailment) >= θ. θ is set on a human-labelled calibration set
and agreement is measured on a separate held-out labelled set (design §5.3). Until the owner has
labelled pairs, θ is the uncalibrated default 0.5 and every output says so.

Only answer records from ``vrag eval answers`` (permitted chunks and Gemini answers to prompts
that passed the restricted-text audit) are read; pair sheets go to the git-ignored ``results/``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.settings import Settings

NLI_MODEL = "cross-encoder/nli-deberta-v3-xsmall"  # Apache-2.0
DEFAULT_THETA = 0.5
RESULTS = Path("results/answers")
PAIRS_CSV = RESULTS / "nli_pairs_to_label.csv"
CALIBRATION = RESULTS / "nli_calibration.json"
PAIR_SCORES = RESULTS / "nli_pairs_model_scores.json"  # kept out of the sheet (no anchoring)
DEV_SYNTHETIC_CSV = RESULTS / "nli_pairs_DEV_SYNTHETIC.csv"
PAIR_FIELDS = (
    "pair_id",
    "set",
    "kind",
    "draft_id",
    "chunk_id",
    "sentence",
    "premise",
    "label",
    "synthetic",
)
MIN_PER_SET = 30  # below this the calibration is refused; the design target is about 100 per set
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`\"'(\[])")

EntailFn = Callable[[Sequence[tuple[str, str]]], list[float]]


def split_sentences(text: str) -> list[str]:
    """Split an explanation into sentences (punctuation followed by a capital or quote)."""
    return [s.strip() for s in _SENT.split(" ".join(text.split())) if len(s.strip()) > 2]


class NLIJudge:
    """P(entailment) for (premise, hypothesis) pairs with a local cross-encoder."""

    def __init__(self, model_name: str = NLI_MODEL) -> None:
        from sentence_transformers import CrossEncoder

        self.model = CrossEncoder(model_name, device="cpu")
        labels = {v.lower(): int(k) for k, v in self.model.model.config.id2label.items()}
        self.entail = labels["entailment"]

    def __call__(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if not pairs:
            return []
        probs = self.model.predict(list(pairs), apply_softmax=True, show_progress_bar=False)
        return [float(p[self.entail]) for p in probs]


def score_answer(
    answer: dict[str, Any], corpus: RetrievalCorpus, entail: EntailFn, theta: float
) -> dict[str, Any] | None:
    """Per-answer citation support and faithfulness; None for abstentions or empty explanations."""
    sentences = split_sentences(answer.get("explanation") or "")
    if answer.get("abstain") or not sentences:
        return None

    def text(cid: str) -> str | None:
        i = corpus.by_id.get(cid)
        return corpus.chunks[i].content if i is not None else None

    def best(chunk_ids: Sequence[str]) -> list[float]:
        premises = [t for t in (text(c) for c in chunk_ids) if t]
        if not premises:
            return [0.0] * len(sentences)
        pairs = [(p, s) for s in sentences for p in premises]
        probs = entail(pairs)
        n = len(premises)
        return [max(probs[j * n : (j + 1) * n]) for j in range(len(sentences))]

    cited = best(answer.get("citations") or [])
    retrieved = best(answer.get("retrieved") or [])
    return {
        "draft_id": answer.get("draft_id"),
        "sentences": len(sentences),
        "citation_support": sum(p >= theta for p in cited) / len(sentences),
        "faithfulness": sum(p >= theta for p in retrieved) / len(sentences),
        "cited_entailment": cited,
        "retrieved_entailment": retrieved,
    }


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else math.nan


def calibrate(rows: Sequence[dict[str, str]], probs: Sequence[float]) -> dict[str, Any]:
    """θ maximising accuracy on the calibration rows (ties: the smallest θ); agreement (accuracy
    and Cohen's kappa) on the separate agreement rows. Labels are 1 (supported) or 0."""
    cal = [
        (p, int(r["label"])) for r, p in zip(rows, probs, strict=True) if r["set"] == "calibration"
    ]
    agr = [
        (p, int(r["label"])) for r, p in zip(rows, probs, strict=True) if r["set"] == "agreement"
    ]
    if not cal or not agr:
        raise ValueError("both a calibration and an agreement set with labels are needed")
    grid = sorted({round(i / 100, 2) for i in range(1, 100)})
    theta = max(grid, key=lambda t: (sum((p >= t) == bool(y) for p, y in cal), -t))

    def agreement(xs: Sequence[tuple[float, int]]) -> tuple[float, float]:
        pred = [int(p >= theta) for p, _ in xs]
        gold = [y for _, y in xs]
        n = len(xs)
        po = sum(a == b for a, b in zip(pred, gold, strict=True)) / n
        pe = (sum(pred) / n) * (sum(gold) / n) + (1 - sum(pred) / n) * (1 - sum(gold) / n)
        kappa = (po - pe) / (1 - pe) if pe < 1 else math.nan
        return po, kappa

    acc, kappa = agreement(agr)
    return {
        "theta": theta,
        "calibration_pairs": len(cal),
        "agreement_pairs": len(agr),
        "agreement_accuracy": acc,
        "agreement_kappa": kappa,
    }


def theta_for(root: Path) -> tuple[float, str]:
    path = root / CALIBRATION
    if path.is_file():
        cal = json.loads(path.read_text("utf-8"))
        return float(cal["theta"]), (
            f"calibrated theta {cal['theta']} on {cal['calibration_pairs']} owner-labelled pairs; "
            f"agreement accuracy {cal['agreement_accuracy']:.3f}, kappa "
            f"{cal['agreement_kappa']:.3f} on {cal['agreement_pairs']} held-out pairs"
        )
    return DEFAULT_THETA, (
        f"UNCALIBRATED theta {DEFAULT_THETA}: no owner-labelled calibration set yet (design §5.3)"
    )


def _answers(root: Path, method: str) -> list[dict[str, Any]]:
    path = root / RESULTS / f"answers_{method}.jsonl"
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln.strip()]


def add_support_commands(eval_actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    s = eval_actions.add_parser(
        "support", help="Citation support and faithfulness with a local NLI model (Tier M)."
    )
    sa = s.add_subparsers(dest="support_command", required=True, metavar="ACTION")
    for name, handler, text in (
        ("score", _cmd_score, "Score the stored answers (results/answers/answers_<method>.jsonl)."),
        ("export-pairs", _cmd_export, "Write a stratified sample of pairs for owner labelling."),
        ("calibrate", _cmd_calibrate, "Set theta from the owner's labels and measure agreement."),
        ("dev-labels", _cmd_dev_labels, "DEVELOPMENT ONLY: synthetic labels to test the pipeline."),
    ):
        p = sa.add_parser(name, help=text)
        p.add_argument("--root", type=Path, default=Path())
        p.add_argument("--method", default="hybrid")
        if name == "export-pairs":
            p.add_argument("--n", type=int, default=200, help="Pairs to sample (design: ~200).")
        p.set_defaults(handler=handler)


def _cmd_score(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.retrieval.commands import corpus_from

    try:
        answers = _answers(args.root, args.method)
    except OSError:
        print(
            f"RESULT: INCOMPLETE (no stored answers; run vrag eval answers --method {args.method})"
        )
        return 2
    corpus = corpus_from(args.root)
    theta, theta_label = theta_for(args.root)
    judge = NLIJudge()
    per = [r for r in (score_answer(a, corpus, judge, theta) for a in answers) if r is not None]
    agg = {
        "answers": len(answers),
        "scored_answers": len(per),
        "sentences": sum(r["sentences"] for r in per),
        "citation_support": _mean([r["citation_support"] for r in per]),
        "faithfulness": _mean([r["faithfulness"] for r in per]),
    }
    out = args.root / RESULTS
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": "M",
        "model": NLI_MODEL,
        "theta": theta,
        "theta_status": theta_label,
        "method": args.method,
        "aggregate": agg,
        "per_answer": per,
    }
    (out / f"support_{args.method}.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    print(f"{theta_label}")
    for k, v in agg.items():
        print(f"  {k}: {round(v, 3) if isinstance(v, float) else v}")
    print(f"RESULT: DONE (wrote {RESULTS.as_posix()}/support_{args.method}.json)")
    return 0


def _cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.retrieval.commands import corpus_from

    try:
        answers = _answers(args.root, args.method)
    except OSError:
        print(
            f"RESULT: INCOMPLETE (no stored answers; run vrag eval answers --method {args.method})"
        )
        return 2
    path = args.root / PAIRS_CSV
    if path.is_file():
        print(f"RESULT: REFUSED ({PAIRS_CSV.as_posix()} exists; it may hold owner labels)")
        return 1
    corpus = corpus_from(args.root)
    cands = candidate_pairs(answers, corpus)
    judge = NLIJudge()
    probs = judge([(c["premise"], c["sentence"]) for c in cands])
    rows = select_pairs(cands, probs, args.n)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PAIR_FIELDS)
        w.writeheader()
        w.writerows(rows)
    # Model scores stay out of the sheet so they cannot anchor the labeller.
    by_id = {c["pair_id"]: p for c, p in zip(cands, probs, strict=True)}
    (args.root / PAIR_SCORES).write_text(
        json.dumps({r["pair_id"]: by_id[r["pair_id"]] for r in rows}, indent=1, sort_keys=True)
        + "\n",
        "utf-8",
    )
    n_cal = sum(r["set"] == "calibration" for r in rows)
    kinds = {k: sum(r["kind"] == k for r in rows) for k in ("cited", "retrieved")}
    print(
        f"candidates {len(cands)}; sampled {len(rows)} (calibration {n_cal}, agreement "
        f"{len(rows) - n_cal}; cited {kinds['cited']}, retrieved {kinds['retrieved']}); "
        "the design asks for about 100 per set"
    )
    print(
        f"RESULT: DONE (wrote {PAIRS_CSV.as_posix()}; guide: docs/benchmark/nli_labelling_guide.md)"
    )
    return 0


def candidate_pairs(
    answers: Sequence[dict[str, Any]], corpus: RetrievalCorpus
) -> list[dict[str, str]]:
    """Every (explanation sentence, chunk) pair of every non-abstained answer: cited chunks
    (citation support) and the other retrieved chunks (faithfulness)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in answers:
        if a.get("abstain"):
            continue
        cited = list(a.get("citations") or [])
        others = [c for c in a.get("retrieved") or [] if c not in cited]
        for sentence in split_sentences(a.get("explanation") or ""):
            for kind, ids in (("cited", cited), ("retrieved", others)):
                for cid in ids:
                    i = corpus.by_id.get(cid)
                    pid = hashlib.sha256(f"{a['draft_id']}|{cid}|{sentence}".encode()).hexdigest()
                    if i is None or pid[:12] in seen:
                        continue
                    seen.add(pid[:12])
                    out.append(
                        {
                            "pair_id": pid[:12],
                            "kind": kind,
                            "draft_id": str(a["draft_id"]),
                            "chunk_id": cid,
                            "sentence": sentence,
                            "premise": corpus.chunks[i].content,
                        }
                    )
    return out


def select_pairs(
    cands: Sequence[dict[str, str]], probs: Sequence[float], n: int, bins: int = 5
) -> list[dict[str, str]]:
    """Deterministic stratified sample of ``n`` pairs: equal shares over P(entailment) bins (so
    the labels cover the threshold region) and over kind, filled in pair-id order; calibration and
    agreement sets alternate inside every stratum so both sets have the same mix."""
    strata: dict[tuple[int, str], list[dict[str, str]]] = {}
    for c, p in zip(cands, probs, strict=True):
        b = min(int(p * bins), bins - 1)
        strata.setdefault((b, c["kind"]), []).append(c)
    for v in strata.values():
        v.sort(key=lambda c: c["pair_id"])
    chosen: list[dict[str, str]] = []
    keys = sorted(strata)
    while len(chosen) < n and any(strata[k] for k in keys):
        for k in keys:  # round-robin over strata
            if strata[k] and len(chosen) < n:
                chosen.append(strata[k].pop(0))
    rows = []
    counter: dict[tuple[int, str], int] = {}
    for c, p in sorted(((c, probs[cands.index(c)]) for c in chosen), key=lambda x: x[0]["pair_id"]):
        key = (min(int(p * bins), bins - 1), c["kind"])
        counter[key] = counter.get(key, 0) + 1
        rows.append(
            {
                **c,
                "set": "calibration" if counter[key] % 2 == 1 else "agreement",
                "label": "",
                "synthetic": "",
            }
        )
    return rows


def _cmd_calibrate(args: argparse.Namespace, settings: Settings) -> int:
    path = args.root / PAIRS_CSV
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        print("RESULT: INCOMPLETE (no pair sheet; run vrag eval support export-pairs)")
        return 2
    problems = label_problems(rows)
    if problems:
        for prob in problems:
            print(f"PROBLEM {prob}")
        print(f"RESULT: INCOMPLETE ({len(problems)} problems; nothing written)")
        return 2
    judge = NLIJudge()
    probs = judge([(r["premise"], r["sentence"]) for r in rows])
    try:
        result = calibrate(rows, probs)
    except ValueError as exc:
        print(f"RESULT: INCOMPLETE ({exc})")
        return 2
    out = args.root / CALIBRATION
    out.write_text(
        json.dumps({"model": NLI_MODEL, **result}, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    print(json.dumps(result, sort_keys=True))
    print(f"RESULT: DONE (wrote {CALIBRATION.as_posix()})")
    return 0


def label_problems(rows: Sequence[dict[str, str]]) -> list[str]:
    """Quality checks before calibration: human labels only (no synthetic rows), every label 0
    or 1, no duplicate pairs, both sets large enough and containing both classes."""
    problems = []
    if any((r.get("synthetic") or "").strip() for r in rows):
        problems.append("synthetic (machine) labels present: calibration needs owner labels only")
    unlabelled = sum(1 for r in rows if (r.get("label") or "").strip() not in ("0", "1"))
    if unlabelled:
        problems.append(f"{unlabelled} of {len(rows)} pairs unlabelled or not 0/1")
    ids = [r.get("pair_id") for r in rows]
    if len(ids) != len(set(ids)):
        problems.append("duplicate pair_id rows")
    for name in ("calibration", "agreement"):
        part = [r for r in rows if r.get("set") == name]
        if len(part) < MIN_PER_SET:
            problems.append(f"{name} set has {len(part)} pairs (< {MIN_PER_SET})")
        labels = {(r.get("label") or "").strip() for r in part}
        if part and not {"0", "1"} <= labels and not unlabelled:
            problems.append(f"{name} set needs both supported (1) and unsupported (0) pairs")
    return problems


def _cmd_dev_labels(args: argparse.Namespace, settings: Settings) -> int:
    """Copies the pair sheet with labels from the NLI model at 0.5, marked synthetic, to a
    separate file. Only for exercising the calibration code; `calibrate` refuses these rows."""
    try:
        with (args.root / PAIRS_CSV).open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        print("RESULT: INCOMPLETE (run vrag eval support export-pairs first)")
        return 2
    probs = NLIJudge()([(r["premise"], r["sentence"]) for r in rows])
    out = args.root / DEV_SYNTHETIC_CSV
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PAIR_FIELDS)
        w.writeheader()
        for r, p in zip(rows, probs, strict=True):
            w.writerow({**r, "label": "1" if p >= 0.5 else "0", "synthetic": "DEV-ONLY"})
    print(f"RESULT: DONE (wrote {DEV_SYNTHETIC_CSV.as_posix()}; synthetic, never for reporting)")
    return 0
