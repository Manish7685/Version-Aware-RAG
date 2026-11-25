"""The answer pipeline (design §5.2): retrieve -> grounded prompt -> structured answer.

- Context is permitted chunks only (the retrieval corpus never contains restricted text); the
  fail-closed privacy audit still runs on every prompt before it is sent.
- The generator is any object with ``generate(prompt) -> str`` (Gemini in production, a fake in
  tests); responses are cached by prompt hash and every attempt goes through the quota ledger.
- Citations must be chunk ids that were retrieved for this question; others are dropped and
  flagged. No compatible evidence -> abstain without calling the model.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from version_aware_rag.retrieval.corpus import RetrievalCorpus
from version_aware_rag.retrieval.methods import Retriever

PROMPT_VERSION = "answer-v1"
PROMPT = """You answer questions about the DuckDB documentation, version {version}.
Use only the numbered context passages below. Each passage is labelled with its chunk id
and version.
Rules:
1. Answer only for DuckDB {version}. If the passages do not state the answer for {version},
   or the feature does not exist in {version}, abstain.
2. "short_answer" is the shortest complete answer (a value, type name, identifier, version,
   yes/no or SQL expression), with no surrounding prose; empty when abstaining.
3. "citations" lists the chunk ids of the passages that support the answer; cite at least
   one when answering.
4. "explanation" is one to three sentences, each supported by the cited passages.
Return one JSON object:
{{"abstain": true|false, "short_answer": "...", "explanation": "...", "citations": ["chunk id"]}}

Question: {question}

Context:
{context}
"""


class Generator(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass
class Answer:
    question: str
    version: str | None
    abstain: bool
    short_answer: str
    explanation: str
    citations: list[str]
    retrieved: list[str]
    flags: list[str] = field(
        default_factory=list
    )  # e.g. invalid_citation, malformed_response, no_evidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "version": self.version,
            "abstain": self.abstain,
            "short_answer": self.short_answer,
            "explanation": self.explanation,
            "citations": self.citations,
            "retrieved": self.retrieved,
            "flags": self.flags,
        }


@dataclass
class AnswerPipeline:
    retriever: Retriever
    corpus: RetrievalCorpus
    generator: Generator | None
    audit: Callable[[str], list[str]]  # restricted-text audit; non-empty -> never sent
    cache_path: Path | None = None
    k: int = 8
    max_chars: int = 1500
    _cache: dict[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.cache_path is not None and self.cache_path.is_file():
            for line in self.cache_path.read_text("utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    self._cache[e["prompt_sha256"]] = e["response"]

    def build_prompt(self, question: str, version: str | None, hits: list[str]) -> str:
        parts = []
        for cid in hits:
            c = self.corpus.chunks[self.corpus.by_id[cid]]
            parts.append(
                f"[{cid}] (DuckDB {c.version}, {c.source_path})\n{c.content[: self.max_chars]}"
            )
        return PROMPT.format(
            version=version or "(any version)", question=question, context="\n\n".join(parts)
        )

    def answer(self, question: str, version: str | None) -> Answer:
        hits = [c for c, _ in self.retriever.retrieve(question, version, self.k)]
        if not hits:
            return Answer(
                question,
                version,
                True,
                "",
                "No compatible documentation was found.",
                [],
                [],
                ["no_evidence"],
            )
        prompt = self.build_prompt(question, version, hits)
        pre_flags: list[str] = []
        if self.audit(prompt):
            # Drop passages that individually trip the audit, then audit the whole prompt again:
            # only a prompt that passes the full audit is ever sent.
            kept = [h for h in hits if not self.audit(self.build_prompt(question, version, [h]))]
            prompt = self.build_prompt(question, version, kept) if kept else ""
            if kept and not self.audit(prompt):
                pre_flags.append("context_filtered")
                hits = kept
        if not prompt or self.audit(prompt):
            return Answer(
                question,
                version,
                True,
                "",
                "Blocked by the privacy audit.",
                [],
                hits,
                ["privacy_blocked"],
            )
        key = hashlib.sha256(f"{PROMPT_VERSION}\n{prompt}".encode()).hexdigest()
        raw = self._cache.get(key)
        if raw is None:
            if self.generator is None:
                return Answer(
                    question,
                    version,
                    True,
                    "",
                    "No generator configured.",
                    [],
                    hits,
                    ["no_generator"],
                )
            raw = self.generator.generate(prompt)
            self._cache[key] = raw
            if self.cache_path is not None:
                record = {"prompt_sha256": key, "prompt_version": PROMPT_VERSION, "response": raw}
                try:
                    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.cache_path.open("a", encoding="utf-8", newline="\n") as fh:
                        fh.write(json.dumps(record) + "\n")
                except OSError:
                    pre_flags.append("cache_unwritable")  # answer still returned, not cached
        ans = parse_answer(raw, question, version, hits)
        ans.flags = pre_flags + ans.flags
        return ans


def parse_answer(raw: str, question: str, version: str | None, hits: list[str]) -> Answer:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError:
        return Answer(
            question,
            version,
            True,
            "",
            "The model response could not be parsed.",
            [],
            hits,
            ["malformed_response"],
        )
    flags: list[str] = []
    cited = [c for c in data.get("citations") or [] if isinstance(c, str)]
    valid = [c for c in cited if c in hits]
    if len(valid) != len(cited):
        flags.append("invalid_citation")
    abstain = bool(data.get("abstain"))
    short = str(data.get("short_answer") or "").strip()
    if not abstain and (not short or not valid):
        flags.append("ungrounded_answer")
        abstain = True  # an answer without a valid citation is not returned as an answer
    return Answer(
        question,
        version,
        abstain,
        "" if abstain else short,
        str(data.get("explanation") or ""),
        [] if abstain else valid,
        hits,
        flags,
    )


# ---------------------------------------------------------------------------------------------
# Tier H answer metrics (design §5.3)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().strip("`'\"").lower())


def matches(
    answer: str, gold: str | None, aliases: list[str], answer_type: str | None
) -> bool | None:
    """True/False when the deterministic matcher can decide, None for the unresolved bucket."""
    if gold is None:
        return None
    cands = [gold, *aliases]
    if answer_type == "sql_expression":
        # An identical expression is equivalent; anything else needs execution equivalence and
        # stays unresolved for owner grading (never LLM-graded).
        return True if any(_norm(answer) == _norm(c) for c in cands) else None
    if answer_type == "yes_no":
        polarity = {"yes": True, "true": True, "no": False, "false": False}
        a, g = polarity.get(_norm(answer).rstrip(".")), polarity.get(_norm(gold).rstrip("."))
        return None if g is None else (a is not None and a == g)  # an unclear answer is wrong
    return any(
        _norm(answer) == _norm(c) or _norm(answer).replace(" ", "") == _norm(c).replace(" ", "")
        for c in cands
    )


def score_item(item: dict[str, Any], ans: Answer, qrels: dict[str, int]) -> dict[str, Any]:
    t5 = item["type"] == "T5"
    out: dict[str, Any] = {
        "draft_id": item["draft_id"],
        "type": item["type"],
        "abstain": ans.abstain,
        "flags": ans.flags,
    }
    if t5:
        out["correct"] = ans.abstain
    elif ans.abstain:
        out["correct"] = False
    else:
        out["correct"] = matches(
            ans.short_answer,
            item.get("gold_short_answer"),
            item.get("accepted_aliases") or [],
            item.get("answer_type"),
        )
    if item["type"] == "T2":
        if not ans.abstain and out["correct"] is False:
            others = item.get("other_version_answers") or {}
            wrong = any(
                matches(ans.short_answer, g, [], item.get("answer_type"))
                and _norm(g) != _norm(item.get("gold_short_answer"))
                for g in others.values()
            )
            out["version_outcome"] = "wrong_version" if wrong else "other_error"
        elif ans.abstain:
            out["version_outcome"] = "abstained"
        elif out["correct"] is None:
            out["version_outcome"] = "unresolved"
        else:
            out["version_outcome"] = "correct"
    if ans.citations:
        out["citation_precision"] = sum(1 for c in ans.citations if qrels.get(c, 0) >= 1) / len(
            ans.citations
        )
    relevant = [c for c, g in qrels.items() if g >= 2]
    if relevant and not t5:
        out["evidence_recall"] = (
            len(set(relevant) & set(ans.citations)) / len(relevant) if ans.citations else 0.0
        )
    return out


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [s for s in scores if s["type"] != "T5"]
    resolved = [s for s in answerable if s["correct"] is not None]
    t5 = [s for s in scores if s["type"] == "T5"]
    abst = [s for s in scores if s["abstain"]]
    t2 = [s for s in scores if s["type"] == "T2"]
    tp = sum(1 for s in t5 if s["abstain"])
    prec = tp / len(abst) if abst else float("nan")
    rec = tp / len(t5) if t5 else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if abst and t5 and (prec + rec) else float("nan")
    cp = [s["citation_precision"] for s in scores if "citation_precision" in s]
    er = [s["evidence_recall"] for s in scores if "evidence_recall" in s]
    return {
        "items": len(scores),
        "answer_accuracy_resolved": sum(1 for s in resolved if s["correct"]) / len(resolved)
        if resolved
        else float("nan"),
        "unresolved_rate": (len(answerable) - len(resolved)) / len(answerable)
        if answerable
        else float("nan"),
        "t2_outcomes": {
            o: sum(1 for s in t2 if s.get("version_outcome") == o)
            for o in ("correct", "wrong_version", "other_error", "abstained", "unresolved")
        },
        "t2_version_accuracy": sum(1 for s in t2 if s.get("version_outcome") == "correct") / len(t2)
        if t2
        else float("nan"),
        "t2_wrong_version_rate": sum(1 for s in t2 if s.get("version_outcome") == "wrong_version")
        / len(t2)
        if t2
        else float("nan"),
        "abstention_precision": prec,
        "abstention_recall": rec,
        "abstention_f1": f1,
        "over_abstention_rate": sum(1 for s in answerable if s["abstain"]) / len(answerable)
        if answerable
        else float("nan"),
        "citation_precision": sum(cp) / len(cp) if cp else float("nan"),
        "evidence_recall": sum(er) / len(er) if er else float("nan"),
        "flags": {
            f: sum(1 for s in scores if f in s["flags"])
            for f in sorted({f for s in scores for f in s["flags"]})
        },
    }
