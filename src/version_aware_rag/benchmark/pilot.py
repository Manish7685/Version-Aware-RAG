"""Pilot workflow (RAG-M4, protocol v1; design §3.3-§3.4): V1 checks, pilot selection, review
batches, review records and the pilot report.

- **V1 checks** (automatic, never a substitute for review): schema and required fields; every
  evidence span verbatim in the corpus file it cites (re-read from the corpus, not the prompt);
  version compatibility (``corpus.compat``); near-duplicates; inventory and source consistency;
  probe consistency, which stays ``pending`` until canonical runtime observations exist.
- **Selection** follows the protocol v1 composition from accepted, counted V0 drafts that pass V1
  (a pending probe check is allowed and recorded). Held drafts and failures are never selected.
- **Review** records follow protocol v0 §8. Nothing is gold or V2 until the owner's decision is
  recorded; the pilot report is generated only from complete review records.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from version_aware_rag.benchmark.inventory import ChangeRecord
from version_aware_rag.corpus.compat import is_compatible
from version_aware_rag.evidence.models import RESTRICTED_SOURCE_KINDS
from version_aware_rag.evidence.verify import ClaimSource

PROTOCOL_V1_COMPOSITION = {"T1": 13, "T2": 6, "T3": 12, "T4": 7, "T5": 12}
PILOT_PATH = Path("benchmark/drafts/pilot_v1.jsonl")
REVIEW_DIR = Path("benchmark/review")
REVIEW_RECORDS = REVIEW_DIR / "pilot_review_records.jsonl"
BATCH_SIZE = 25
DECISIONS = ("accept", "accept_minor_edit", "accept_major_edit", "reject")
REASON_CODES = tuple(f"R{i}" for i in range(1, 10))
CHECKS = (
    "schema",
    "spans_verbatim",
    "version_compatible",
    "near_duplicate",
    "inventory",
    "probe_consistency",
)


@dataclass
class CorpusLookup:
    chunks: dict[
        str, dict[str, Any]
    ]  # chunk_id -> chunk row (version, source_type, introduced_in, content)
    index: dict[tuple[str, str], ClaimSource]
    packages: dict[str, str]  # docs series -> behaviour package

    def span_source(self, passage_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """(source text, chunk row or None) for a passage id, read from the corpus itself."""
        if passage_id.startswith("chunk:"):
            row = self.chunks.get(passage_id.removeprefix("chunk:"))
            return (row["content"], row) if row else (None, None)
        if passage_id.startswith("src:"):
            m = re.fullmatch(r"src:(.+):(\d+)-(\d+)", passage_id)
            if not m:
                return None, None
            for kind in ("docs_page", "migration_guide"):
                src = self.index.get((kind, m.group(1)))
                if src is not None and (data := src.read_bytes()) is not None:
                    lines = data.decode("utf-8").splitlines()[int(m.group(2)) - 1 : int(m.group(3))]
                    return "\n".join(lines), None
        return None, None


def load_chunks(chunk_dir: Path, versions: Sequence[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for v in versions:
        for line in (chunk_dir / f"{v}.jsonl").read_text(encoding="utf-8").splitlines():
            if line:
                row = json.loads(line)
                if row["source_kind"] not in RESTRICTED_SOURCE_KINDS:
                    out[row["chunk_id"]] = {
                        k: row[k] for k in ("version", "source_type", "introduced_in", "content")
                    }
    return out


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _set_id(draft: dict[str, Any]) -> str:
    return str(draft["item_id"]).split("@")[0]


@dataclass
class V1Result:
    draft_id: str
    checks: dict[str, str] = field(
        default_factory=dict
    )  # check -> pass | fail | pending | not_applicable
    reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if "fail" in self.checks.values():
            return "fail"
        return "pass_pending_probe" if self.checks.get("probe_consistency") == "pending" else "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "status": self.status,
            "checks": self.checks,
            "reasons": sorted(set(self.reasons)),
        }


def v1_check(
    drafts: Sequence[dict[str, Any]],
    lookup: CorpusLookup,
    inventory: dict[str, ChangeRecord],
    canonical_probe_ids: set[str],
    released_holds: frozenset[str] = frozenset(),
) -> dict[str, V1Result]:
    """V1 checks for accepted, counted drafts (status ``draft``, no review hold), plus held drafts
    the owner released into the review (``released_holds``)."""
    from version_aware_rag.benchmark.drafting import Draft  # local: avoid an import cycle

    pool = [
        x
        for x in drafts
        if x["status"] == "draft" and (not x.get("review_hold") or x["draft_id"] in released_holds)
    ]
    out: dict[str, V1Result] = {}
    for x in pool:
        res = V1Result(x["draft_id"])
        try:
            Draft.model_validate(x)
            ok = bool(x.get("question")) and bool(x.get("answer_type"))
            if x["type"] == "T5":
                ok = (
                    ok
                    and x.get("answerable") is False
                    and bool(x.get("abstain_reason"))
                    and not x["evidence_spans"]
                )
            else:
                ok = ok and bool(x.get("gold_short_answer")) and bool(x["evidence_spans"])
            res.checks["schema"] = "pass" if ok else "fail"
            if not ok:
                res.reasons.append("required_field_missing")
        except Exception:  # pydantic ValidationError; reported by type only
            res.checks["schema"] = "fail"
            res.reasons.append("schema_invalid")
        verbatim = compatible = True
        for sp in x["evidence_spans"]:
            text, row = lookup.span_source(sp["passage_id"])
            if text is None or sp["text"] not in text:
                verbatim = False
                res.reasons.append("span_not_verbatim_in_corpus")
            if row is not None:
                ok_v = is_compatible(
                    row["version"],
                    row["source_type"],
                    row["introduced_in"],
                    x["target_version"],
                    lookup.packages,
                )
            else:
                ok_v = sp["version"] == x["target_version"]
            if not ok_v:
                compatible = False
                res.reasons.append("span_version_incompatible")
        res.checks["spans_verbatim"] = (
            ("pass" if verbatim else "fail") if x["evidence_spans"] else "not_applicable"
        )
        res.checks["version_compatible"] = (
            ("pass" if compatible else "fail") if x["evidence_spans"] else "not_applicable"
        )
        rec = inventory.get(x["change_id"])
        inv_ok = (
            rec is not None
            and rec.status == "pilot_candidate"
            and tuple(rec.probe_ids) == tuple(x["probe_ids"])
            and x["boundary"] == f"{rec.previous_version}->{rec.version}"
            and set(x["restricted_refs"])
            <= {f"{s.source_kind}:{s.source_path}:{s.line_start}-{s.line_end}" for s in rec.sources}
        )
        res.checks["inventory"] = "pass" if inv_ok else "fail"
        if not inv_ok:
            res.reasons.append("inventory_mismatch")
        # Probe consistency needs canonical observations for the item's probes (design §3.3); until
        # they are ingested the check is pending, never a pass.
        res.checks["probe_consistency"] = "not_applicable" if not x["probe_ids"] else "pending"
        out[x["draft_id"]] = res
    for x in pool:  # near-duplicates: same target version, different item set
        toks = _tokens(x["question"] or "")
        dup = any(
            y["draft_id"] != x["draft_id"]
            and _set_id(y) != _set_id(x)
            and y["target_version"] == x["target_version"]
            and toks
            and len(toks & _tokens(y["question"] or "")) / len(toks | _tokens(y["question"] or ""))
            >= 0.8
            for y in pool
        )
        out[x["draft_id"]].checks["near_duplicate"] = "fail" if dup else "pass"
        if dup:
            out[x["draft_id"]].reasons.append("near_duplicate")
    return out


def select_pilot(
    drafts: Sequence[dict[str, Any]],
    v1: dict[str, V1Result],
    composition: dict[str, int] = PROTOCOL_V1_COMPOSITION,
) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int]]]:
    """Deterministic selection by type (item order); returns the pilot and any shortfalls."""
    eligible = [  # held drafts never enter the selection; the owner releases them explicitly
        x
        for x in drafts
        if x["draft_id"] in v1 and v1[x["draft_id"]].status != "fail" and not x.get("review_hold")
    ]
    pilot: list[dict[str, Any]] = []
    gaps: dict[str, tuple[int, int]] = {}
    for kind, n in composition.items():
        items = sorted(
            (x for x in eligible if x["type"] == kind), key=lambda x: (_set_id(x), x["item_id"])
        )
        if kind == "T2":  # keep sets whole: select complete sets only
            sets: dict[str, list[dict[str, Any]]] = {}
            for x in items:
                sets.setdefault(_set_id(x), []).append(x)
            chosen: list[dict[str, Any]] = []
            for members in sets.values():
                if len(chosen) + len(members) <= n:
                    chosen += members
            items = chosen
        picked = items[:n]
        pilot += picked
        if len(picked) < n:
            gaps[kind] = (len(picked), n)
    return pilot, gaps


def _review_entry(x: dict[str, Any], v1: V1Result, batch: int) -> list[str]:
    lines = [
        f"### {x['item_id']} ({x['draft_id']})",
        "",
        f"- type {x['type']}; target version {x['target_version']}; "
        f"change `{x['change_id']}` (boundary {x['boundary']})",
        f"- question: {x['question']}",
        f"- gold_short_answer ({x['answer_type']}): {x['gold_short_answer']}"
        if x.get("answerable")
        else f"- unanswerable; abstain_reason: {x['abstain_reason']}",
    ]
    if x.get("accepted_aliases"):
        lines.append(f"- accepted_aliases: {', '.join(x['accepted_aliases'])}")
    if x.get("other_version_answers"):
        lines.append(
            "- other_version_answers: "
            + "; ".join(f"{k}: {v}" for k, v in sorted(x["other_version_answers"].items()))
        )
    for sp in x["evidence_spans"]:
        lines.append(
            f"- evidence ({sp['version']}, `{sp['source_path']}`, {sp['passage_id']}): {sp['text']}"
        )
    if x.get("restricted_refs"):
        lines.append(f"- restricted sources (reference only): {', '.join(x['restricted_refs'])}")
    lines.append(f"- V1: {v1.status} ({', '.join(f'{k} {v}' for k, v in v1.checks.items())})")
    lines += [f"- review batch {batch}: record the decision in `{REVIEW_RECORDS.as_posix()}`", ""]
    return lines


def write_review_batches(
    pilot: Sequence[dict[str, Any]], v1: dict[str, V1Result], root: Path
) -> list[Path]:
    """Review sheets in batches of 25 and the review-record template (decisions left empty)."""
    out_dir = root / REVIEW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    order = sorted(pilot, key=lambda x: (x["type"], _set_id(x), x["item_id"]))
    for b in range(0, len(order), BATCH_SIZE):
        batch = order[b : b + BATCH_SIZE]
        n = b // BATCH_SIZE + 1
        text = [
            f"# Pilot review batch {n} of {-(-len(order) // BATCH_SIZE)} (protocol v1)",
            "",
            "V0 drafts: not gold, not human-verified. Decisions (accept | accept_minor_edit | "
            "accept_major_edit | reject), reason codes R1-R9 (protocol v0 §7), comment, edits and "
            "time spent go in the review records.",
            "",
        ]
        for x in batch:
            text += _review_entry(x, v1[x["draft_id"]], n)
        path = out_dir / f"pilot_review_batch_{n:02d}.md"
        path.write_bytes("\n".join(text).encode("utf-8"))
        paths.append(path)
    records = root / REVIEW_RECORDS
    existing = {}
    if records.is_file():
        existing = {
            json.loads(ln)["draft_id"]: json.loads(ln)
            for ln in records.read_text("utf-8").splitlines()
            if ln.strip()
        }
    lines = []
    for i, x in enumerate(order):
        rec = existing.get(x["draft_id"]) or {
            "draft_id": x["draft_id"],
            "item_id": x["item_id"],
            "type": x["type"],
            "review_batch": i // BATCH_SIZE + 1,
            "decision": None,
            "reason_codes": [],
            "comment": "",
            "edits": [],
            "time_spent_seconds": None,
            "reviewer": None,
        }
        lines.append(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    records.write_bytes("".join(lines).encode("utf-8"))  # never overwrites recorded decisions
    return [*paths, records]


def check_review_records(records: Sequence[dict[str, Any]]) -> list[str]:
    """Problems that make the review records incomplete or invalid (empty when complete)."""
    problems = []
    for r in records:
        rid = r.get("item_id", "?")
        if r.get("decision") not in DECISIONS:
            problems.append(f"{rid}: no decision")
            continue
        codes = r.get("reason_codes") or []
        if any(c not in REASON_CODES for c in codes):
            problems.append(f"{rid}: unknown reason code")
        if r["decision"] == "reject" and not codes:
            problems.append(f"{rid}: a rejection needs a reason code")
        if "R9" in codes and not str(r.get("comment", "")).strip():
            problems.append(f"{rid}: R9 needs a comment")
        if not r.get("reviewer"):
            problems.append(f"{rid}: no reviewer")
    return problems


def render_pilot_report(
    records: Sequence[dict[str, Any]],
    v1: dict[str, dict[str, Any]],
    pilot: Sequence[dict[str, Any]],
) -> str:
    """The pilot report (design §3.4 step 5), computed only from the review records."""
    by_id = {x["draft_id"]: x for x in pilot}
    dec = Counter(r["decision"] for r in records)
    per_type: dict[str, Counter[str]] = {}
    for r in records:
        per_type.setdefault(r["type"], Counter())[r["decision"]] += 1
    codes = Counter(c for r in records for c in r.get("reason_codes", []))
    false_pass = [
        r
        for r in records
        if r["decision"] == "reject" and v1.get(r["draft_id"], {}).get("status") != "fail"
    ]
    times = [
        r["time_spent_seconds"]
        for r in records
        if isinstance(r.get("time_spent_seconds"), (int, float))
    ]
    out = [
        "# Pilot report (protocol v1)",
        "",
        "Every number below is computed from `benchmark/review/pilot_review_records.jsonl`.",
        "",
        f"Reviewed items: {len(records)}; decisions: "
        + ", ".join(f"{k} {dec[k]}" for k in DECISIONS),
        "",
        "| Type | accept | minor edit | major edit | reject |",
        "|---|---|---|---|---|",
    ]
    for t in sorted(per_type):
        c = per_type[t]
        out.append(
            f"| {t} | {c['accept']} | {c['accept_minor_edit']} | "
            f"{c['accept_major_edit']} | {c['reject']} |"
        )
    out += [
        "",
        "Reason codes: " + (", ".join(f"{k} {codes[k]}" for k in sorted(codes)) or "none"),
        f"V1 false passes (passed V1, rejected in review): {len(false_pass)}",
        f"Median review time: {sorted(times)[len(times) // 2] if times else 'not recorded'} s",
        "",
        "## Rejected items",
        "",
    ]
    for r in records:
        if r["decision"] == "reject":
            x = by_id.get(r["draft_id"], {})
            out.append(
                f"- {r['item_id']} ({x.get('type')}, target {x.get('target_version')}): codes "
                f"{', '.join(r['reason_codes'])}; question: {x.get('question')}; "
                f"comment: {r.get('comment', '')}"
            )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------
# Review sheet (CSV, opens in Excel): export and validated import


REVIEW_SHEET = REVIEW_DIR / "pilot_review_sheet.csv"
READ_ONLY = (
    "draft_id",
    "item_id",
    "review_batch",
    "type",
    "target_version",
    "change_id",
    "boundary",
    "question",
    "gold_short_answer",
    "answer_type",
    "accepted_aliases",
    "other_version_answers",
    "abstain_reason",
    "evidence",
    "v1_status",
)
EDITABLE = (
    "decision",
    "reason_codes",
    "comment",
    "time_spent_seconds",
    "reviewer",
    "edit_question",
    "edit_gold_short_answer",
    "edit_answer_type",
    "edit_accepted_aliases",
    "edit_evidence",
)
EVIDENCE_SEP = " || "


def _evidence_cell(spans: Sequence[dict[str, Any]]) -> str:
    return EVIDENCE_SEP.join(f"{sp['passage_id']}::{sp['text']}" for sp in spans)


def _v1_cell(result: V1Result | None) -> str:
    if result is None:
        return "not checked"
    return f"{result.status} ({'; '.join(result.reasons) or 'no failures'})"


def export_review_sheet(
    pilot: Sequence[dict[str, Any]],
    v1: dict[str, V1Result],
    records: Sequence[dict[str, Any]],
    path: Path,
) -> int:
    """Write the review sheet; decisions already recorded are filled in. Returns the row count."""
    import csv

    by_id = {r["draft_id"]: r for r in records}
    order = sorted(pilot, key=lambda x: (x["type"], _set_id(x), x["item_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:  # BOM: Excel opens UTF-8 correctly
        w = csv.DictWriter(fh, fieldnames=[*READ_ONLY, *EDITABLE])
        w.writeheader()
        for i, x in enumerate(order):
            rec = by_id.get(x["draft_id"], {})
            edits = {e["field"]: e["to"] for e in rec.get("edits", [])}
            w.writerow(
                {
                    "draft_id": x["draft_id"],
                    "item_id": x["item_id"],
                    "review_batch": i // BATCH_SIZE + 1,
                    "type": x["type"],
                    "target_version": x["target_version"],
                    "change_id": x["change_id"],
                    "boundary": x["boundary"],
                    "question": x["question"],
                    "gold_short_answer": x.get("gold_short_answer") or "",
                    "answer_type": x.get("answer_type") or "",
                    "accepted_aliases": "; ".join(x.get("accepted_aliases") or []),
                    "other_version_answers": "; ".join(
                        f"{k}: {v}"
                        for k, v in sorted((x.get("other_version_answers") or {}).items())
                    ),
                    "abstain_reason": x.get("abstain_reason") or "",
                    "evidence": _evidence_cell(x["evidence_spans"]),
                    "v1_status": _v1_cell(v1.get(x["draft_id"])),
                    "decision": rec.get("decision") or "",
                    "reason_codes": " ".join(rec.get("reason_codes") or []),
                    "comment": rec.get("comment") or "",
                    "time_spent_seconds": rec.get("time_spent_seconds") or "",
                    "reviewer": rec.get("reviewer") or "",
                    "edit_question": edits.get("question", ""),
                    "edit_gold_short_answer": edits.get("gold_short_answer", ""),
                    "edit_answer_type": edits.get("answer_type", ""),
                    "edit_accepted_aliases": "; ".join(edits.get("accepted_aliases", []) or []),
                    "edit_evidence": _evidence_cell(edits.get("evidence_spans", []) or []),
                }
            )
    return len(order)


def _parse_evidence(
    cell: str, x: dict[str, Any], lookup: CorpusLookup
) -> tuple[list[dict[str, Any]], list[str]]:
    spans, problems = [], []
    for part in [c for c in cell.split(EVIDENCE_SEP) if c.strip()]:
        if "::" not in part:
            problems.append("edit_evidence entries must be passage_id::exact text")
            continue
        pid, text = part.split("::", 1)
        pid, text = pid.strip(), text.strip()
        source, row = lookup.span_source(pid)
        if source is None or not text or text not in source:
            problems.append("edited evidence is not verbatim in its cited corpus passage")
            continue
        version = (
            row["version"] if row else pid.split("/")[1] if pid.startswith("src:docs/") else ""
        )
        ok = (
            is_compatible(
                row["version"],
                row["source_type"],
                row["introduced_in"],
                x["target_version"],
                lookup.packages,
            )
            if row
            else version == x["target_version"]
        )
        if not ok:
            problems.append("edited evidence is from an incompatible version")
            continue
        spans.append({"passage_id": pid, "version": version, "text": text})
    return spans, problems


def import_review_sheet(
    path: Path, pilot: Sequence[dict[str, Any]], lookup: CorpusLookup, answer_types: Sequence[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validated review records from a filled sheet (partial progress allowed); problems are
    reported by item id and rule, and any problem means nothing is written."""
    import csv

    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    by_id = {x["draft_id"]: x for x in pilot}
    problems: list[str] = []
    seen: set[str] = set()
    records = []
    for row in rows:
        did = (row.get("draft_id") or "").strip()
        x = by_id.get(did)
        if x is None or did in seen:
            problems.append(f"row {did or '?'}: unknown or duplicate draft_id")
            continue
        seen.add(did)
        rid = x["item_id"]
        if (row.get("item_id") or "").strip() != rid:
            problems.append(f"{rid}: item_id changed")
        decision = (row.get("decision") or "").strip() or None
        codes = (row.get("reason_codes") or "").replace(",", " ").split()
        comment = (row.get("comment") or "").strip()
        reviewer = (row.get("reviewer") or "").strip() or None
        t = (row.get("time_spent_seconds") or "").strip()
        time_spent = int(float(t)) if re.fullmatch(r"\d+(\.\d+)?", t) else None
        if t and time_spent is None:
            problems.append(f"{rid}: time_spent_seconds must be a number")
        edits: list[dict[str, Any]] = []
        for column, field_name in (
            ("edit_question", "question"),
            ("edit_gold_short_answer", "gold_short_answer"),
            ("edit_answer_type", "answer_type"),
        ):
            value = (row.get(column) or "").strip()
            if value and value != (x.get(field_name) or ""):
                edits.append({"field": field_name, "from": x.get(field_name), "to": value})
        aliases = [
            a.strip() for a in (row.get("edit_accepted_aliases") or "").split(";") if a.strip()
        ]
        if aliases and aliases != list(x.get("accepted_aliases") or []):
            edits.append(
                {
                    "field": "accepted_aliases",
                    "from": list(x.get("accepted_aliases") or []),
                    "to": aliases,
                }
            )
        if (row.get("edit_evidence") or "").strip():
            spans, ev_problems = _parse_evidence(row["edit_evidence"], x, lookup)
            problems += [f"{rid}: {p}" for p in ev_problems]
            if spans:
                edits.append({"field": "evidence_spans", "from": x["evidence_spans"], "to": spans})
        for e in edits:
            if e["field"] == "question" and not e["to"]:
                problems.append(f"{rid}: edited question is empty")
            if e["field"] == "answer_type" and e["to"] not in answer_types:
                problems.append(f"{rid}: edited answer_type is not a protocol answer type")
            if e["field"] == "gold_short_answer" and (
                x["type"] == "T5" or re.search(r"\bor\b|;", e["to"])
            ):
                problems.append(
                    f"{rid}: edited gold answer is invalid (T5 has none; one answer only)"
                )
        if decision is None:
            if edits or codes:
                problems.append(f"{rid}: edits or reason codes without a decision")
        else:
            if decision not in DECISIONS:
                problems.append(f"{rid}: decision must be one of {', '.join(DECISIONS)}")
            if decision == "accept" and edits:
                problems.append(
                    f"{rid}: 'accept' with edits must be accept_minor_edit or accept_major_edit"
                )
            if decision in ("accept_minor_edit", "accept_major_edit") and not edits:
                problems.append(f"{rid}: an edit decision needs at least one edit column")
            if decision == "reject" and edits:
                problems.append(f"{rid}: a rejected item takes no edits")
            if not reviewer:
                problems.append(f"{rid}: reviewer is required with a decision")
        records.append(
            {
                "draft_id": did,
                "item_id": rid,
                "type": x["type"],
                "review_batch": int(row.get("review_batch") or 0) or None,
                "decision": decision,
                "reason_codes": codes,
                "comment": comment,
                "edits": edits,
                "time_spent_seconds": time_spent,
                "reviewer": reviewer,
            }
        )
    missing = set(by_id) - seen
    if missing:
        problems.append(f"{len(missing)} pilot items are missing from the sheet")
    problems += [p for p in check_review_records([r for r in records if r["decision"]])]
    return records, problems


def validation_sample(
    records: Sequence[dict[str, Any]], pilot: Sequence[dict[str, Any]], size: int = 10
) -> list[str]:
    """Design §3.4 step 7: the change ids of rejected pilot items (topics to redraft with the final
    v1 prompts), in item order, at most ``size``. Requires complete review records."""
    if check_review_records(records):
        raise ValueError("review records are incomplete")
    by_id = {x["draft_id"]: x for x in pilot}
    topics: list[str] = []
    for r in sorted(records, key=lambda r: r["item_id"]):
        if r["decision"] == "reject":
            cid = by_id[r["draft_id"]]["change_id"]
            if cid not in topics:
                topics.append(cid)
    return topics[:size]
