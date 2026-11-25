"""``vrag eval answers`` (RAG-M7): answer the evaluation items with the version-aware pipeline and
compute the Tier H answer metrics. Gemini only through the existing controls: key from
``VRAG_GEMINI_API_KEY``, the shared quota ledger (every attempt counted), retries with backoff,
the fail-closed restricted-text audit, and a response cache (``data/build/answer_cache.jsonl``,
git-ignored). Results: ``results/answers/`` (labelled provisional while review is pending)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from version_aware_rag.answer.pipeline import AnswerPipeline, aggregate, score_item
from version_aware_rag.benchmark.drafting import QUOTA_LOCK_PATH
from version_aware_rag.benchmark.quota import ProcessLock
from version_aware_rag.retrieval import harness
from version_aware_rag.retrieval.commands import corpus_from, eval_items, load_retrievers
from version_aware_rag.settings import Settings

ANSWER_CACHE = Path("data/build/answer_cache.jsonl")
RESULTS = Path("results/answers")


class LedgerGenerator:
    """Gemini behind the generator interface, through the quota ledger and retry policy."""

    def __init__(self, root: Path, api_key: str) -> None:
        from version_aware_rag.benchmark import drafting as d
        from version_aware_rag.benchmark.gemini import GeminiClient, GenerationConfig
        from version_aware_rag.benchmark.quota import QuotaLimits

        cfg = d.load_drafting_config(root / "configs/drafting.toml")
        self._d, self._limits = d, cfg.limits
        self._client = GeminiClient(
            api_key, GenerationConfig(cfg.model.id, 0.0, cfg.model.max_output_tokens)
        )
        lim = cfg.limits
        self._quota = QuotaLimits(
            lim.requests_per_minute, lim.input_tokens_per_minute, lim.requests_per_day
        )
        self._ledger_path = root / d.QUOTA_LEDGER_PATH
        self._ledger: Any = None
        self.model_id = cfg.model.id
        self.requests = 0
        self._last = 0.0

    def bind(self, heartbeat: Any) -> None:
        """Open the shared ledger under the caller's run lock (one Gemini run at a time)."""
        from version_aware_rag.benchmark.quota import QuotaLedger

        self._ledger = QuotaLedger(self._ledger_path, self._quota, heartbeat=heartbeat)

    def generate(self, prompt: str) -> str:
        if self._ledger is None:
            raise RuntimeError("generator used outside the run lock")
        if self.requests >= self._limits.max_requests_per_run:
            raise self._d.DraftingError("max_requests_per_run reached")
        wait = self._limits.min_seconds_between_requests - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self.requests += 1
        try:
            return self._d._generate_with_retries(
                self._client, prompt, self._limits, time.sleep, self._ledger
            )
        finally:
            self._last = time.monotonic()


def restricted_audit(root: Path) -> Any:
    """The fail-closed restricted-text audit (raises if a restricted source cannot be loaded)."""
    from version_aware_rag.benchmark import drafting as d
    from version_aware_rag.corpus.config import load_chunking_config, load_config
    from version_aware_rag.corpus.manifest import load_manifest
    from version_aware_rag.evidence.store import SIDECAR_FILE
    from version_aware_rag.evidence.verify import build_source_index

    cfg, _ = load_config(root / "configs/corpus.toml")
    ch, _ = load_chunking_config(root / "configs/chunking.toml")
    man, problems = load_manifest(root / cfg.paths.manifest)
    if man is None or problems:
        raise d.DraftingError(f"corpus manifest cannot be loaded ({len(problems)} problems)")
    corpus = d.load_restricted_corpus(
        build_source_index(root, cfg, ch, man),
        root / "data/build/chunks",
        root / "data/evidence" / SIDECAR_FILE,
    )
    return corpus.violations


def add_answer_commands(eval_actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    a = eval_actions.add_parser(
        "answers", help="Answer the evaluation items and score them (Tier H)."
    )
    a.add_argument("--root", type=Path, default=Path())
    a.add_argument(
        "--method",
        default="hybrid",
        choices=("bm25", "dense", "finetuned", "hybrid", "rerank", "late"),
    )
    a.add_argument("--limit", type=int, default=0, help="Answer only the first N items (0: all).")
    a.add_argument(
        "--offline", action="store_true", help="Use cached responses only; never call Gemini."
    )
    a.set_defaults(handler=_cmd_answers)


def _cmd_answers(args: argparse.Namespace, settings: Settings) -> int:
    root = args.root
    corpus = corpus_from(root)
    retriever = load_retrievers(root, corpus, [args.method])[0]
    items, label = eval_items(root)
    if args.limit:
        items = items[: args.limit]
    key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    generator = None if args.offline or not key else LedgerGenerator(root, key)
    try:
        violations = restricted_audit(root)
    except Exception as exc:  # fail closed: no audit, no request
        print(
            "RESULT: INCOMPLETE (restricted-text audit unavailable: "
            f"{type(exc).__name__}; nothing sent)"
        )
        return 2

    def audit(prompt: str) -> list[str]:
        return list(violations(prompt, api_key=key or None))  # also refuses key-like strings

    pipe = AnswerPipeline(retriever, corpus, generator, audit, root / ANSWER_CACHE)
    scores: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    stopped: list[str] = []

    def run_all() -> None:
        for x in items:
            try:
                ans = pipe.answer(x["question"], x["target_version"])
            except Exception as exc:  # fatal API or quota error: stop sending, keep results
                stopped.append(type(exc).__name__)
                pipe.generator = None
                ans = pipe.answer(x["question"], x["target_version"])  # cache or no_generator
            answers.append({"draft_id": x["draft_id"], **ans.as_dict()})
            scores.append(score_item(x, ans, harness.qrels_for(x, corpus)))

    if generator is None:
        run_all()
    else:
        with ProcessLock(root / QUOTA_LOCK_PATH) as lock:
            generator.bind(lock.refresh)
            run_all()
    agg = aggregate(scores)
    out = root / RESULTS
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": label,
        "method": args.method,
        "version_filtered": True,
        "aggregate": agg,
        "scores": scores,
    }
    (out / f"summary_{args.method}.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", "utf-8"
    )
    (out / f"answers_{args.method}.jsonl").write_text(
        "".join(json.dumps(a, sort_keys=True) + "\n" for a in answers), "utf-8"
    )
    print(f"items: {len(items)} ({label}); method {args.method}, version-filtered")
    for k, v in agg.items():
        print(f"  {k}: {v if not isinstance(v, float) else round(v, 3)}")
    no_gen = agg["flags"].get("no_generator", 0)
    status = "INCOMPLETE" if no_gen else "DONE"
    if generator is not None:
        print(f"  gemini requests this run: {generator.requests} (model {generator.model_id})")
    if stopped:
        print(f"  generation stopped early: {stopped[0]}")
    print(f"RESULT: {status} ({no_gen} items without a generator response)")
    return 2 if no_gen else 0
