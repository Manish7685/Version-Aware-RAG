"""Dev/test split by change, leakage checks, test-set freezing and the benchmark datasheet
(RAG-M4; design §3.5 step 7 and plan acceptance "test split frozen").

- **Split by change, never by question.** Items are grouped by ``change_id`` (a T2 set and every
  question about one change stay together) and the groups are ordered by a salted sha256, then
  assigned to test until it reaches its share; the result is deterministic.
- **Leakage checks** between dev and test: no shared change, no near-duplicate questions, no shared
  gold evidence text; shared corpus passages are reported for information.
- **Freezing** writes the test items (owner edits applied) and their sha256 hashes. It refuses
  until every item has a complete owner review decision; rejected items never enter a split.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SPLIT_SALT = "vrag-rag-m4-split-v1"
TEST_FRACTION = 0.6  # design: dev 200 / test 300


def _group_key(change_id: str) -> str:
    return hashlib.sha256(f"{SPLIT_SALT}:{change_id}".encode()).hexdigest()


def split_by_change(
    items: Sequence[dict[str, Any]], test_fraction: float = TEST_FRACTION
) -> dict[str, list[dict[str, Any]]]:
    """Change-grouped, type-stratified, deterministic: groups (one per ``change_id``) are taken in
    salted-hash order and each goes to the split with the larger remaining per-type deficit for
    the group's type mix (ties go to test)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for x in items:
        groups.setdefault(x["change_id"], []).append(x)
    totals = Counter(x["type"] for x in items)
    share = {"test": test_fraction, "dev": 1.0 - test_fraction}
    out: dict[str, list[dict[str, Any]]] = {"dev": [], "test": []}
    counts: dict[str, Counter[str]] = {"dev": Counter(), "test": Counter()}
    for cid in sorted(groups, key=_group_key):
        mix = Counter(x["type"] for x in groups[cid])
        deficit = {
            name: sum(n * (share[name] * totals[t] - counts[name][t]) for t, n in mix.items())
            for name in ("test", "dev")
        }
        name = "test" if deficit["test"] >= deficit["dev"] else "dev"
        out[name].extend(groups[cid])
        counts[name].update(mix)
    return out


def _tokens(text: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", (text or "").lower()))


@dataclass
class LeakageReport:
    shared_changes: int
    near_duplicate_pairs: int
    shared_evidence_texts: int
    shared_passages: int  # informational: one passage may support questions about different changes

    @property
    def passed(self) -> bool:
        return not (self.shared_changes or self.near_duplicate_pairs or self.shared_evidence_texts)


def leakage_checks(
    dev: Sequence[dict[str, Any]], test: Sequence[dict[str, Any]], threshold: float = 0.8
) -> LeakageReport:
    shared_changes = len({x["change_id"] for x in dev} & {x["change_id"] for x in test})
    pairs = 0
    for a in dev:
        ta = _tokens(a.get("question"))
        for b in test:
            tb = _tokens(b.get("question"))
            if ta and tb and len(ta & tb) / len(ta | tb) >= threshold:
                pairs += 1
    return LeakageReport(
        shared_changes,
        pairs,
        len(_span_values(dev, "text") & _span_values(test, "text")),
        len(_span_values(dev, "passage_id") & _span_values(test, "passage_id")),
    )


def _span_values(items: Sequence[dict[str, Any]], key: str) -> set[str]:
    return {str(sp[key]) for x in items for sp in x.get("evidence_spans", [])}


def apply_edits(draft: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """The reviewed item: the original draft with the owner's recorded edits applied (the draft
    itself is never modified) and its verification level from the review decision."""
    item: dict[str, Any] = dict(json.loads(json.dumps(draft)))
    for e in record.get("edits", []):
        item[e["field"]] = e["to"]
    accepted = record.get("decision") in ("accept", "accept_minor_edit", "accept_major_edit")
    item["verification_level"] = "V2" if accepted else "V0"
    item["human_verified"] = accepted
    item["review"] = {
        k: record.get(k) for k in ("decision", "reason_codes", "reviewer", "review_batch")
    }
    return item


def item_sha256(item: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def freeze_preconditions(records: Sequence[dict[str, Any]], expected_ids: set[str]) -> list[str]:
    """Reasons the test set may not be frozen yet (empty when it may)."""
    from version_aware_rag.benchmark.pilot import check_review_records

    problems = check_review_records(records)
    covered = {r["draft_id"] for r in records}
    if expected_ids - covered:
        problems.append(f"{len(expected_ids - covered)} selected items have no review record")
    return problems


def test_manifest(test_items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    hashes = {
        x["draft_id"]: item_sha256(x) for x in sorted(test_items, key=lambda x: x["draft_id"])
    }
    overall = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in hashes.items()).encode()).hexdigest()
    return {
        "salt": SPLIT_SALT,
        "items": len(hashes),
        "item_sha256": hashes,
        "test_set_sha256": overall,
    }


def write_test_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the frozen test manifest with LF line endings on every platform."""
    path.write_bytes((json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def datasheet_status(
    selected: int,
    released: int,
    retained: int,
    reviewed: bool,
    splits: dict[str, int] | None,
) -> str:
    """The datasheet status line, computed from the stored data: the protocol v1 selection, the
    released holds reviewed with it, the records retained after review and the frozen splits."""
    status = (
        f"pilot (protocol v1): {selected + released} reviewed records "
        f"({selected} selected + {released} released from hold); "
    )
    status += (
        f"owner review complete, {retained} retained (V2)"
        if reviewed
        else "owner review pending; nothing is human-verified"
    )
    if splits:
        sizes = ", ".join(f"{k} {v}" for k, v in sorted(splits.items()))
        status += f"; test split frozen ({sizes})"
    else:
        status += "; test split not frozen"
    return status


def render_datasheet(facts: dict[str, Any]) -> str:
    """The benchmark datasheet, filled only from computed facts (no estimates)."""

    def table(title: str, counts: dict[str, int]) -> list[str]:
        return [
            f"| {title} | count |",
            "|---|---|",
            *[f"| {k} | {v} |" for k, v in sorted(counts.items())],
            "",
        ]

    out = [
        "# Benchmark datasheet (RAG-M4)",
        "",
        f"Status: **{facts['status']}**. Generated by `vrag benchmark datasheet` from the stored "
        "drafts, V1 results and "
        "review records; every number below is computed, none is estimated.",
        "",
        "## Composition",
        "",
        "Counts below cover all reviewed records, including those rejected in review; the frozen "
        "splits contain only the retained (V2) records.",
        "",
        *[
            line
            for name, counts in sorted((facts.get("splits_by_type") or {}).items())
            for line in table(f"Frozen {name} split, type", counts)
        ],
        *table("Reviewed records, type", facts["by_type"]),
        *table("Reviewed records, boundary", facts["by_boundary"]),
        *table("Reviewed records, target version", facts["by_target"]),
        *table("Reviewed records, verification level", facts["by_level"]),
        "## Provenance",
        "",
        f"- Generation model: `{facts['model']}` (temperature {facts['temperature']}, max output "
        f"tokens {facts['max_output_tokens']}); "
        "drafts are V0 until the owner's review.",
        "- Prompts (sha256): "
        + "; ".join(f"`{k}` {v[:12]}" for k, v in sorted(facts["prompts"].items())),
        f"- Gemini requests cached: {facts['cached_requests']} (successful requests are never "
        "sent again).",
        "- Source corpus: DuckDB documentation (MIT), corpus manifest sha256 "
        f"`{facts['corpus_manifest_sha256'][:16]}`.",
        "",
        "## Evidence and privacy",
        "",
        "- Gold answers are documentation answers; runtime observations confirm or contradict "
        "them and are stored separately.",
        "- Documentation-conflict items are a diagnostic slice, excluded from headline metrics.",
        "- Release-post and release-note text (restricted) is never stored in the benchmark or "
        "sent to any external model; "
        "items cite restricted sources by path and line only.",
        "",
        "## Known limitations",
        "",
        *[f"- {x}" for x in facts["limitations"]],
        "",
        "## Reproduce",
        "",
        "```",
        *facts["commands"],
        "```",
        "",
    ]
    return "\n".join(out)
