"""S3 drafting: privacy audit, prompt hashes, plan traceability, response validation, duplicates,
retries, cache and resume. Synthetic fixtures and a fake client only: nothing is sent."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from version_aware_rag.benchmark import drafting as d
from version_aware_rag.benchmark.gemini import (
    FatalError,
    MalformedResponse,
    RateLimited,
    TransientError,
)
from version_aware_rag.benchmark.inventory import ChangeRecord
from version_aware_rag.benchmark.quota import QuotaLedger, QuotaLimits
from version_aware_rag.evidence.verify import ClaimSource

ROOT = Path(__file__).resolve().parents[3]
RESTRICTED = "The release post says this exact restricted sentence about frobnication in DuckDB."
KEY = "AIza" + "FAKE" + "0" * 28  # dummy key-shaped value for the key-like guard; not a credential
DOC = "frob_count(x) returns the number of frobs in x. It is available in this version."
GEN = {
    "model": "test-model",
    "temperature": 0.2,
    "max_output_tokens": 2048,
    "response_mime_type": "application/json",
}
LIMITS = d.Limits(
    requests_per_minute=10,
    input_tokens_per_minute=200_000,
    requests_per_day=400,
    max_requests_per_run=10,
    min_seconds_between_requests=0.0,
    max_retries=2,
    backoff_base_seconds=2.0,
    backoff_jitter=0.0,
    max_backoff_seconds=5.0,
)
QUOTA = {"requests_per_minute": 15, "input_tokens_per_minute": 250_000, "requests_per_day": 500}


def _record(cid: str = "chg-1.5-01", status: str = "pilot_candidate", **kw: Any) -> ChangeRecord:
    version = cid.split("-")[1]
    prev = {"1.3": "1.2", "1.4": "1.3", "1.5": "1.4", "2.0": "1.5"}[version]
    data: dict[str, Any] = {
        "change_id": cid,
        "version": version,
        "previous_version": prev,
        "feature": "frob_count function",
        "before": "absent",
        "after": "present",
        "sources": [
            {
                "source_kind": "docs_page",
                "source_path": f"docs/{version}/frob.md",
                "line_start": 1,
                "line_end": 1,
                "note": "docs",
            },
            {
                "source_kind": "release_post",
                "source_path": "_posts/post.md",
                "line_start": 3,
                "line_end": 4,
                "note": "release post",
            },
        ],
        "probe_ids": ["P01"],
        "runtime_evidence": "scratch_only",
        "canonical_observations": "pending",
        "status": status,
        "status_reason": "S2 scratch run (not canonical evidence)",
        "owner_review": "pending",
    }
    data.update(kw)
    return ChangeRecord.model_validate(data)


def _index(readable: bool = True) -> dict[tuple[str, str], ClaimSource]:
    def src(kind: str, path: str, version: str, text: str | None) -> ClaimSource:
        return ClaimSource(
            kind,
            path,
            "repo",
            None,
            "0" * 64,
            version,
            lambda: None if text is None else text.encode(),
        )

    out = {
        ("docs_page", f"docs/{v}/frob.md"): src("docs_page", f"docs/{v}/frob.md", v, DOC)
        for v in d.CORPUS_VERSIONS
    }
    out[("release_post", "_posts/post.md")] = src(
        "release_post", "_posts/post.md", "1.5", f"x\ny\n{RESTRICTED}\nz\n" if readable else None
    )
    return out


def _chunk(cid: str, version: str, content: str, kind: str = "docs_page") -> dict[str, Any]:
    return {
        "chunk_id": cid,
        "version": version,
        "source_kind": kind,
        "source_path": f"docs/{version}/x.md",
        "content": content,
    }


def _pool(chunks: dict[str, list[dict[str, Any]]] | None = None) -> d.PassagePool:
    default = {
        v: [_chunk(f"c-{v}", v, f"frob_count in {v}: see the docs. {DOC}")]
        for v in d.CORPUS_VERSIONS
    }
    return d.PassagePool(chunks or default, _index(), 2500, 2)


def _corpus() -> d.RestrictedCorpus:
    return d.RestrictedCorpus.from_texts([RESTRICTED])


def _prompts() -> dict[str, str]:
    return d.verify_prompts(ROOT)


def _item(
    kind: str = "T3",
    passages: list[d.Passage] | None = None,
    record: ChangeRecord | None = None,
    target: str = "1.5",
    version_set: tuple[str, ...] = ("1.5",),
) -> d.PlannedItem:
    record = record or _record()
    passages = (
        passages
        if passages is not None
        else [d.Passage("p1", "1.5", "docs_page", "docs/1.5/frob.md", DOC)]
    )
    spec = d.ItemSpec(
        item_id=f"{kind}-01",
        type=kind,
        target_version=target,
        version_set=version_set,
        change_id=record.change_id,
        boundary=f"{record.previous_version}->{record.version}",
        passage_ids=tuple(p.passage_id for p in passages),
        restricted_refs=("release_post:_posts/post.md:3-4",),
        probe_ids=record.probe_ids,
    )
    return d.PlannedItem(spec, passages, record)


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.responses, self.prompts = list(responses), []  # type: list[Any], list[str]

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return str(r)


def _answer(**kw: Any) -> str:
    base = {
        "question": "Which function counts the frobs in a value in DuckDB 1.5?",
        "type": "T3",
        "target_version": "1.5",
        "gold_short_answer": "frob_count",
        "gold_answer_type": "identifier",
        "accepted_aliases": [],
        "evidence_spans": [
            {"passage_id": "p1", "text": "frob_count(x) returns the number of frobs in x."}
        ],
        "answerable": True,
        "abstain_reason": None,
    }
    base.update(kw)
    return json.dumps(base)


def _entry(item: d.PlannedItem, text: str) -> dict[str, Any]:
    return {"spec": item.spec.model_dump(mode="json"), "response_text": text}


def _reasons(item: d.PlannedItem, text: str) -> list[str]:
    return d.validate_response(
        _entry(item, text), {p.passage_id: p for p in item.passages}, d_types()
    )[1]


def d_types() -> list[str]:
    return ["value", "type_name", "identifier", "version", "yes_no", "sql_expression", "none"]


def _run(
    payloads: list[d.Payload], client: FakeClient, cache: Path, sleeps: list[float] | None = None
) -> d.RunResult:
    sleep: Callable[[float], None] = sleeps.append if sleeps is not None else (lambda s: None)
    return d.run_drafting(
        payloads, client, GEN, _prompts(), cache, LIMITS, _corpus(), KEY, sleep=sleep
    )


# --- privacy ---------------------------------------------------------------------------------


def test_restricted_passages_are_dropped_and_never_reach_a_payload() -> None:
    passages = [
        d.Passage("ok", "1.5", "docs_page", "docs/1.5/frob.md", DOC),
        d.Passage("quote", "1.5", "docs_page", "docs/1.5/a.md", f"Intro. {RESTRICTED} More."),
        d.Passage("kind", "1.5", "release_post", "_posts/post.md", "harmless looking text"),
        d.Passage(
            "shingle",
            "1.5",
            "docs_page",
            "docs/1.5/b.md",
            "release post says this exact restricted sentence about frobnication in duckdb ok",
        ),
    ]
    payload = d.build_payload(_item(passages=passages), _prompts(), _corpus(), KEY)
    assert [p.passage_id for p in payload.passages] == ["ok"]
    assert payload.dropped_passages == 3 and payload.blocked == []
    sent = payload.prompt
    assert RESTRICTED not in sent and "harmless looking text" not in sent
    assert _corpus().violations(sent, KEY) == []


def test_restricted_text_in_the_inventory_description_blocks_the_payload() -> None:
    item = _item(record=_record(feature=RESTRICTED))
    payload = d.build_payload(item, _prompts(), _corpus(), KEY)
    assert payload.blocked and "restricted" in payload.blocked[0]
    client = FakeClient([])
    result = _run([payload], client, Path("unused-never-written.jsonl"))
    assert client.prompts == [] and result.blocked == 1 and result.sent == 0


def test_key_like_strings_block_the_payload() -> None:
    payload = d.build_payload(
        _item(record=_record(feature=f"uses {KEY}")), _prompts(), _corpus(), KEY
    )
    assert "contains a key-like string" in payload.blocked


def test_every_sent_prompt_passes_the_audit(tmp_path: Path) -> None:
    items = [_item(), _item(record=_record(feature=RESTRICTED))]
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in items]
    client = FakeClient([_answer()])
    _run(payloads, client, tmp_path / "cache.jsonl")
    assert len(client.prompts) == 1
    assert all(_corpus().violations(p, KEY) == [] for p in client.prompts)
    cache_text = (tmp_path / "cache.jsonl").read_text("utf-8")
    assert KEY not in cache_text and RESTRICTED not in cache_text


def test_restricted_text_in_a_response_is_not_stored(tmp_path: Path) -> None:
    payload = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    result = _run([payload], FakeClient([_answer(question=RESTRICTED)]), tmp_path / "c.jsonl")
    entries = d.load_cache(tmp_path / "c.jsonl")
    assert result.errors == 1 and entries[0]["status"] == "error"
    assert (
        entries[0]["error_type"] == "restricted_text_in_response"
        and "response_text" not in entries[0]
    )


def test_audit_fails_closed_when_a_restricted_source_is_missing(tmp_path: Path) -> None:
    with pytest.raises(d.DraftingError, match="not cached locally"):
        d.load_restricted_corpus(_index(readable=False), tmp_path, tmp_path / "sidecar.jsonl")
    no_restricted = {k: v for k, v in _index().items() if k[0] == "docs_page"}
    with pytest.raises(d.DraftingError, match="no restricted source"):
        d.load_restricted_corpus(no_restricted, tmp_path, tmp_path / "sidecar.jsonl")


def test_restricted_corpus_includes_processed_chunks_and_the_sidecar(tmp_path: Path) -> None:
    processed = "A processed release post chunk that differs from the raw markdown text entirely."
    sidecar_text = "A sidecar span of restricted release note text kept only in the local file."
    for v in d.CORPUS_VERSIONS:
        rows = [_chunk("d", v, DOC), _chunk("r", v, processed, kind="release_post")]
        (tmp_path / f"{v}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    (tmp_path / "github_releases.jsonl").write_text("", "utf-8")
    (tmp_path / "side.jsonl").write_text(
        json.dumps({"claim_id": "c", "span_text": sidecar_text}) + "\n", "utf-8"
    )
    corpus = d.load_restricted_corpus(_index(), tmp_path, tmp_path / "side.jsonl")
    assert (
        corpus.violations(processed)
        and corpus.violations(sidecar_text)
        and corpus.violations(RESTRICTED)
    )
    assert corpus.violations(DOC) == []


def test_permitted_chunks_exclude_restricted_kinds(tmp_path: Path) -> None:
    for v in d.CORPUS_VERSIONS:
        rows = [_chunk("b", v, DOC), _chunk("a", v, "post text", kind="release_post")]
        (tmp_path / f"{v}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    loaded = d.load_permitted_chunks(tmp_path)
    assert all([c["source_kind"] for c in cs] == ["docs_page"] for cs in loaded.values())


# --- prompts ---------------------------------------------------------------------------------


def test_prompts_match_their_recorded_hashes() -> None:
    texts = _prompts()
    assert set(texts) == {d.COMMON_TEMPLATE, *d.TYPE_TEMPLATE.values(), *d.V1_TEMPLATES}
    assert d.ACTIVE_TEMPLATE["T2"] == "v1/draft_T2.md" and d.PROMPT_VERSION["T2"] == "v1"
    assert all(d.ACTIVE_TEMPLATE[t] == d.TYPE_TEMPLATE[t] for t in ("T1", "T3", "T4", "T5"))


def test_a_changed_prompt_is_refused(tmp_path: Path) -> None:
    for rel in (
        d.PROTOCOL_TOML,
        d.PROMPTS_V1_TOML,
        *(
            d.PROMPT_DIR / n
            for n in (d.COMMON_TEMPLATE, *d.TYPE_TEMPLATE.values(), *d.V1_TEMPLATES)
        ),
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((ROOT / rel).read_bytes())
    d.verify_prompts(tmp_path)
    path = tmp_path / d.PROMPT_DIR / "draft_T3.md"
    path.write_bytes(path.read_bytes() + b"\nAlso use outside knowledge.\n")
    with pytest.raises(d.DraftingError, match=r"draft_T3\.md does not match its sha256"):
        d.verify_prompts(tmp_path)
    path.write_bytes((ROOT / d.PROMPT_DIR / "draft_T3.md").read_bytes())
    v1 = tmp_path / d.PROMPT_DIR / "v1/draft_T2.md"
    v1.write_bytes(v1.read_bytes() + b"\nAnswers may be guessed.\n")
    with pytest.raises(
        d.DraftingError, match=r"v1/draft_T2\.md does not match its sha256 in prompts_v1"
    ):
        d.verify_prompts(tmp_path)


def test_every_template_placeholder_is_filled() -> None:
    for kind in d.TYPES:
        prompt = d.build_payload(_item(kind=kind), _prompts(), _corpus(), None).prompt
        assert "{{" not in prompt
    with pytest.raises(d.DraftingError, match=r"no value for placeholder"):
        d.render("{{unknown}}", {})


# --- plan and traceability -------------------------------------------------------------------


def _config(**plan: int) -> d.DraftingConfig:
    base = {
        "T1": 2,
        "T2": 4,
        "T3": 3,
        "T4": 0,
        "T5": 2,
        "t2_three_version_sets": 1,
        "max_passage_chars": 2500,
        "max_keyword_passages_per_version": 2,
    }
    base.update(plan)
    hints = {
        c: {"keywords": ["frob_count"], "t5": t5}
        for c, t5 in (
            ("chg-1.5-01", ""),
            ("chg-2.0-01", "before"),
            ("chg-1.4-01", ""),
            ("chg-1.4-02", ""),
        )
    }
    return d.DraftingConfig.model_validate(
        {
            "model": {"id": "", "temperature": 0.2, "max_output_tokens": 2048},
            "account_quota": QUOTA,
            "limits": LIMITS.model_dump(),
            "plan": base,
            "changes": hints,
        }
    )


def test_plan_uses_only_pilot_candidates_and_traces_every_item() -> None:
    records = [
        _record(),
        _record("chg-2.0-01"),
        _record("chg-1.4-01", status="unresolved"),
        _record("chg-1.4-02", status="excluded", runtime_evidence="none", probe_ids=[]),
    ]
    plan = d.build_plan(records, _config(), _pool())
    assert plan and {i.spec.change_id for i in plan} <= {"chg-1.5-01", "chg-2.0-01"}
    for item in plan:
        assert item.spec.passage_ids == tuple(p.passage_id for p in item.passages)
        assert all(p.source_kind in d.PERMITTED_KINDS for p in item.passages)
        assert item.spec.restricted_refs == ("release_post:_posts/post.md:3-4",)
        assert item.spec.probe_ids == ("P01",)
        assert item.spec.boundary == f"{item.record.previous_version}->{item.record.version}"
    t5 = [i for i in plan if i.spec.type == "T5"]
    assert [i.spec.target_version for i in t5] == ["1.5", "1.4"]  # before the 2.0 introduction
    assert all(i.record.change_id == "chg-2.0-01" for i in t5)


def _t2_config(**plan: Any) -> d.DraftingConfig:
    base = _config(T1=0, T2=3, T3=0, T4=0, T5=0)
    return base.model_copy(update={"plan": base.plan.model_copy(update=plan)})


def test_t2_sets_come_only_from_the_fixed_list() -> None:
    records = [_record(), _record("chg-2.0-01")]
    assert [i for i in d.build_plan(records, _t2_config(), _pool()) if i.spec.type == "T2"] == []
    plan = d.build_plan(records, _t2_config(t2_changes=("chg-1.5-01",)), _pool())
    sets = [i for i in plan if i.spec.type == "T2"]
    assert [
        (i.spec.item_id, i.spec.change_id, i.spec.version_set, i.spec.target_version) for i in sets
    ] == [("T2-01", "chg-1.5-01", ("1.4", "1.5", "2.0"), "1.5")]


def test_t2_list_entries_are_never_substituted() -> None:
    records = [_record(), _record("chg-2.0-01")]
    with pytest.raises(d.DraftingError, match=r"chg-1\.3-01 is not an eligible pilot candidate"):
        d.build_plan(records, _t2_config(t2_changes=("chg-1.3-01",)), _pool())
    excluded = _excluding(_t2_config(t2_changes=("chg-1.5-01",)), "chg-1.5-01", "T2")
    with pytest.raises(d.DraftingError, match=r"chg-1\.5-01 is not an eligible pilot candidate"):
        d.build_plan(records, excluded, _pool())


def test_held_t2_sets_are_flagged_and_never_counted() -> None:
    records = [_record(), _record("chg-2.0-01")]
    config = _t2_config(t2_changes=("chg-1.5-01",), t2_held=("chg-2.0-01",))
    plan = d.build_plan(records, config, _pool())
    held = [i for i in plan if i.spec.review_hold]
    assert [(i.spec.item_id, i.spec.change_id, i.spec.version_set) for i in held] == [
        ("T2-H01", "chg-2.0-01", ("1.5", "2.0"))
    ]
    assert (
        d.shortfalls(config, plan) == {}
    )  # 3 counted slots from chg-1.5-01; the held 2 do not count
    short = config.model_copy(update={"plan": config.plan.model_copy(update={"T2": 5})})
    assert d.shortfalls(short, plan) == {"T2": (3, 5)}


def test_the_t2_list_does_not_reorder_t3() -> None:
    records = [_record(), _record("chg-2.0-01")]
    without = d.build_plan(records, _config(T1=0, T2=3, T3=2, T4=0, T5=0), _pool())
    with_t2 = d.build_plan(
        records,
        _excluding(_config(T1=0, T2=3, T3=2, T4=0, T5=0), "chg-1.4-01", "T1").model_copy(
            update={
                "plan": _config(T2=3, T3=2, T4=0, T5=0, T1=0).plan.model_copy(
                    update={"t2_changes": ("chg-1.5-01",)}
                )
            }
        ),
        _pool(),
    )
    assert [i.spec for i in without if i.spec.type == "T3"] == [
        i.spec for i in with_t2 if i.spec.type == "T3"
    ]


def _reader(text: str) -> Callable[[], bytes | None]:
    return lambda: text.encode()


def _diff_pool() -> d.PassagePool:
    """Pages in three versions: 'changed.md' documents the behaviour differently in each version,
    'same.md' identically, 'new.md' only in 2.0. Chunks mirror the page lines."""
    texts = {
        "changed.md": {
            "1.4": "frob_count(x) returns an INTEGER count.",
            "1.5": "frob_count(x) returns a BIGINT count.",
            "2.0": "frob_count(x) returns a BIGINT count and raises on overflow.",
        },
        "same.md": {v: "frob_count(x) is documented here." for v in ("1.4", "1.5", "2.0")},
        "new.md": {"2.0": "frob_count(x) has a new INTEGER page."},
    }
    index: dict[tuple[str, str], ClaimSource] = {}
    chunks: dict[str, list[dict[str, Any]]] = {v: [] for v in d.CORPUS_VERSIONS}
    for rel, by_v in texts.items():
        for v, text in by_v.items():
            path = f"docs/{v}/{rel}"
            index[("docs_page", path)] = ClaimSource(
                "docs_page", path, "repo", None, "0" * 64, v, _reader(text)
            )
            chunks[v].append(
                {
                    "chunk_id": f"c-{rel}-{v}",
                    "version": v,
                    "source_kind": "docs_page",
                    "source_path": path,
                    "content": f"Intro words. {text}",
                }
            )
    return d.PassagePool(chunks, index, 2500, 2)


def test_documentation_diff_selection_uses_each_versions_own_changed_page() -> None:
    record = _record(
        before="Up to 1.4.5 it returns an INTEGER.", after="From 1.5.6 it returns a BIGINT."
    )
    passages = d.t2_passages(record, ("1.4", "1.5", "2.0"), ("frob_count",), _diff_pool(), 2)
    assert [(p.version, p.source_path) for p in passages] == [
        ("1.4", "docs/1.4/changed.md"),
        ("1.5", "docs/1.5/changed.md"),
        ("2.0", "docs/2.0/changed.md"),
    ]
    # unchanged pages and pages missing in a version are never selected
    assert not any("same.md" in p.source_path or "new.md" in p.source_path for p in passages)


def test_documentation_diff_selection_finds_nothing_without_a_behaviour_difference() -> None:
    record = _record(before="Up to 1.4.5 it frobnicates.", after="From 1.5.6 it zorbles.")
    assert d.t2_passages(record, ("1.4", "1.5", "2.0"), ("frob_count",), _diff_pool(), 2) == []


def test_plan_is_deterministic() -> None:
    records = [_record(), _record("chg-2.0-01")]
    a = [i.spec for i in d.build_plan(records, _config(), _pool())]
    b = [i.spec for i in d.build_plan(list(reversed(records)), _config(), _pool())]
    assert a == b


def test_plan_requires_hints_for_every_pilot_candidate() -> None:
    with pytest.raises(d.DraftingError, match=r"no drafting hints for chg-1\.3-01"):
        d.build_plan([_record("chg-1.3-01")], _config(), _pool())


def test_repository_config_covers_exactly_the_pilot_candidates() -> None:
    config = d.load_drafting_config(ROOT / "configs/drafting.toml")
    pilots = {
        json.loads(ln)["change_id"]
        for ln in (ROOT / "benchmark/inventory/pilot_change_inventory.jsonl")
        .read_text("utf-8")
        .splitlines()
        if json.loads(ln)["status"] == "pilot_candidate"
    }
    assert set(config.changes) == pilots
    assert config.model.id == "gemini-3.5-flash-lite"  # owner choice, verified 2026-11-23


# --- response validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "malformed_json"),
        ("[1, 2]", "not_a_json_object"),
        (json.dumps({"question": "q"}), "missing_keys"),
        (_answer(question=""), "empty_question"),
        (_answer(type="T1"), "type_mismatch"),
        (_answer(target_version="1.4"), "target_version_mismatch"),
        (_answer(gold_answer_type="prose"), "invalid_answer_type"),
        (_answer(gold_short_answer="frob_count or frobs"), "multiple_answers"),
        (
            _answer(question="Is frob_count the function that counts frobs in 1.5?"),
            "answer_in_question",
        ),
        (
            _answer(evidence_spans=[{"passage_id": "p1", "text": "invented text"}]),
            "span_not_in_passages",
        ),
        (
            _answer(evidence_spans=[{"passage_id": "nope", "text": "text found nowhere"}]),
            "span_not_in_passages",
        ),
        (_answer(evidence_spans=[]), "no_evidence_span"),
        (_answer(answerable=False), "answerable_item_needs_an_answer"),
    ],
)
def test_malformed_or_unsupported_responses_are_rejected(text: str, reason: str) -> None:
    assert reason in _reasons(_item(), text)


def test_a_complete_response_passes() -> None:
    assert _reasons(_item(), _answer()) == []


def test_t5_must_abstain_and_t4_needs_two_pages() -> None:
    t5 = _item(kind="T5", target="1.4", version_set=("1.4",))
    ok = _answer(
        type="T5",
        target_version="1.4",
        gold_short_answer="",
        gold_answer_type="none",
        evidence_spans=[],
        answerable=False,
        abstain_reason="Not in 1.4.",
    )
    assert _reasons(t5, ok) == []
    assert "t5_must_abstain" in _reasons(t5, _answer(type="T5", target_version="1.4"))
    t4 = _item(kind="T4")
    assert "t4_needs_two_pages" in _reasons(t4, _answer(type="T4"))


T2_PASSAGES = [
    d.Passage("a", "1.4", "docs_page", "docs/1.4/frob.md", "In 1.4 it returns 1."),
    d.Passage("b", "1.5", "docs_page", "docs/1.5/frob.md", "In 1.5 it returns 2."),
]


def _t2_item() -> d.PlannedItem:
    return _item(kind="T2", passages=list(T2_PASSAGES), version_set=("1.4", "1.5"))


def _t2_answer(**kw: Any) -> str:
    """A complete prompt-v1 T2 response for _t2_item(); keyword arguments override fields."""
    base = {
        "question": "What does frobbing a list return?",
        "type": "T2",
        "target_version": "1.5",
        "gold_short_answer": "2",
        "gold_answer_type": "value",
        "accepted_aliases": [],
        "evidence_spans": [{"passage_id": "b", "exact_text": "it returns 2"}],
        "answerable": True,
        "abstain_reason": None,
        "behaviour": "changed",
        "other_version_answers": {"1.4": "1"},
        "version_answers": {
            "1.4": {
                "status": "documented",
                "answer": "1",
                "evidence_spans": [{"passage_id": "a", "exact_text": "it returns 1"}],
            },
            "1.5": {
                "status": "documented",
                "answer": "2",
                "evidence_spans": [{"passage_id": "b", "exact_text": "it returns 2"}],
            },
        },
    }
    base.update(kw)
    return json.dumps(base)


def _va(**entries: Any) -> dict[str, Any]:
    full = json.loads(_t2_answer())["version_answers"]
    full.update(entries)
    return {k: v for k, v in full.items() if v is not None}


def test_t2_needs_differing_answers_for_the_other_versions() -> None:
    assert _reasons(_t2_item(), _t2_answer()) == []
    same = _t2_answer(other_version_answers={"1.4": "2"})
    assert "t2_needs_differing_other_version_answers" in _reasons(_t2_item(), same)
    assert "t2_needs_differing_other_version_answers" in _reasons(
        _t2_item(), _t2_answer(other_version_answers={})
    )


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"version_answers": None}, "t2_incomplete_version_answers"),
        (
            {
                "version_answers": {
                    "1.5": {"status": "documented", "answer": "2", "evidence_spans": []}
                }
            },
            "t2_incomplete_version_answers",
        ),  # a version of the set is missing
        ({"version_answers": "1.4: 1"}, "t2_incomplete_version_answers"),
        (
            {
                "version_answers": _va(
                    **{"1.4": {"status": "documented", "answer": "", "evidence_spans": []}}
                )
            },
            "t2_incomplete_version_answers",
        ),
        (
            {
                "version_answers": _va(
                    **{"1.4": {"status": "maybe", "answer": "1", "evidence_spans": []}}
                )
            },
            "t2_incomplete_version_answers",
        ),
        (
            {
                "version_answers": _va(
                    **{"1.4": {"status": "not_documented", "answer": None, "evidence_spans": []}}
                )
            },
            "t2_version_not_documented",
        ),
        (
            {
                "version_answers": _va(
                    **{"1.4": {"status": "not_documented", "answer": "1", "evidence_spans": []}}
                )
            },
            "t2_inconsistent_version_status",
        ),
        (
            {
                "version_answers": _va(
                    **{"1.4": {"status": "documented", "answer": "1", "evidence_spans": []}}
                )
            },
            "t2_missing_version_evidence",
        ),
        (
            {
                "version_answers": _va(
                    **{
                        "1.4": {
                            "status": "documented",
                            "answer": "1",
                            "evidence_spans": [{"passage_id": "b", "exact_text": "it returns 2"}],
                        }
                    }
                )
            },
            "t2_span_wrong_version",
        ),
        (
            {
                "version_answers": _va(
                    **{
                        "1.4": {
                            "status": "documented",
                            "answer": "1",
                            "evidence_spans": [{"passage_id": "a", "exact_text": "it returns 7"}],
                        }
                    }
                )
            },
            "span_not_in_passages",
        ),
        ({"gold_short_answer": "3"}, "t2_gold_mismatch"),
        ({"behaviour": "unchanged"}, "t2_behaviour_unchanged"),
        (
            {
                "version_answers": _va(
                    **{
                        "1.4": {
                            "status": "documented",
                            "answer": "2",
                            "evidence_spans": [{"passage_id": "a", "exact_text": "it returns 1"}],
                        }
                    }
                ),
                "other_version_answers": {"1.4": "2"},
            },
            "t2_behaviour_unchanged",
        ),  # identical answers are not a version-specific item
        ({"other_version_answers": {"1.4": "0"}}, "t2_inconsistent_answers"),
        (
            {
                "version_answers": _va(
                    **{
                        "1.4": {
                            "status": "documented",
                            "answer": "1 or 0",
                            "evidence_spans": [{"passage_id": "a", "exact_text": "it returns 1"}],
                        }
                    }
                ),
                "other_version_answers": {"1.4": "1 or 0"},
            },
            "multiple_answers",
        ),
    ],
)
def test_incomplete_or_inconsistent_cross_version_answers_are_rejected(
    change: dict[str, Any], reason: str
) -> None:
    data = json.loads(_t2_answer())
    for key, value in change.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    assert reason in _reasons(_t2_item(), json.dumps(data))


def test_a_t2_set_with_a_missing_version_produces_no_accepted_draft(tmp_path: Path) -> None:
    incomplete = _t2_answer(
        version_answers=_va(
            **{"1.4": {"status": "not_documented", "answer": None, "evidence_spans": []}}
        )
    )
    drafts = _drafts([_t2_item()], [incomplete], tmp_path)
    assert drafts and all(x.status == "rejected" for x in drafts)


def test_t2_payloads_use_the_v1_template_and_others_stay_v0() -> None:
    t2 = d.build_payload(_t2_item(), _prompts(), _corpus(), KEY).prompt
    t3 = d.build_payload(_item(), _prompts(), _corpus(), KEY).prompt
    assert "Drafting prompt v1: T2" in t2 and '"version_answers"' in t2
    assert "Drafting prompt v0: T3" in t3 and "version_answers" not in t3


def _drafts(items: list[d.PlannedItem], responses: list[str], tmp: Path) -> list[d.Draft]:
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in items]
    _run(payloads, FakeClient(responses), tmp / "cache.jsonl")
    return d.build_drafts(
        items, d.load_cache(tmp / "cache.jsonl"), d_types(), GEN, _prompts(), _corpus()
    )


def test_drafts_are_v0_and_never_verified_or_gold(tmp_path: Path) -> None:
    [draft] = _drafts([_item()], [_answer()], tmp_path)
    assert draft.status == "draft" and draft.verification_level == "V0"
    assert draft.human_verified is False and draft.gold is False and draft.gold_basis == "docs"
    assert draft.runtime_observations == () and draft.change_id == "chg-1.5-01"
    assert draft.evidence_spans[0].source_path == "docs/1.5/frob.md"
    gen = draft.generation
    assert gen["model"] == "test-model" and set(gen["prompt_sha256"]) == {"common", "type"}
    assert {"request_sha256", "payload_sha256", "created_at", "temperature"} <= set(gen)
    data = draft.model_dump(mode="json")
    for field, value in (
        ("human_verified", True),
        ("gold", True),
        ("verification_level", "V2"),
        ("gold_basis", "docs+runtime"),
        ("runtime_observations", ["obs-1"]),
    ):
        with pytest.raises(ValidationError):
            d.Draft.model_validate({**data, field: value})


def test_near_duplicate_questions_are_rejected(tmp_path: Path) -> None:
    first, second = (
        _item(),
        _item(passages=[d.Passage("p1", "1.5", "docs_page", "docs/1.5/frob.md", DOC + " ")]),
    )
    second.spec = second.spec.model_copy(update={"item_id": "T3-02"})
    reworded = _answer(question="Which function counts the frobs in a value in DuckDB 1.5 ?")
    drafts = _drafts([first, second], [_answer(), reworded], tmp_path)
    assert [x.status for x in drafts] == ["draft", "rejected"]
    assert drafts[1].rejection_reasons == ("duplicate",)


def test_t2_set_becomes_one_draft_per_version(tmp_path: Path) -> None:
    drafts = _drafts([_t2_item()], [_t2_answer()], tmp_path)
    assert [(x.item_id, x.target_version, x.gold_short_answer, x.status) for x in drafts] == [
        ("T2-01@1.4", "1.4", "1", "draft"),
        ("T2-01@1.5", "1.5", "2", "draft"),
    ]
    assert drafts[0].question == drafts[1].question
    assert [s.version for s in drafts[0].evidence_spans] == ["1.4"]
    assert [s.version for s in drafts[1].evidence_spans] == ["1.5"]
    assert drafts[0].other_version_answers == {"1.5": "2"}
    assert drafts[0].generation["prompt_sha256"]["type"] == d.prompt_sha256(
        _prompts(), "v1/draft_T2.md"
    )


def test_drafts_file_is_canonical_and_rebuildable(tmp_path: Path) -> None:
    drafts = _drafts([_item()], [_answer()], tmp_path)
    d.write_drafts(tmp_path / "drafts.jsonl", drafts)
    first = (tmp_path / "drafts.jsonl").read_bytes()
    rebuilt = d.build_drafts(
        [_item()], d.load_cache(tmp_path / "cache.jsonl"), d_types(), GEN, _prompts(), _corpus()
    )
    d.write_drafts(tmp_path / "drafts.jsonl", rebuilt)
    assert (tmp_path / "drafts.jsonl").read_bytes() == first and b"\r" not in first


# --- retries, budget, cache and resume -------------------------------------------------------


def test_cache_entries_record_model_prompt_hashes_and_payload(tmp_path: Path) -> None:
    payload = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    _run([payload], FakeClient([_answer()]), tmp_path / "c.jsonl")
    [entry] = d.load_cache(tmp_path / "c.jsonl")
    assert entry["status"] == "ok" and entry["generation"] == GEN
    assert entry["request_sha256"] == d.request_sha256(GEN, payload.prompt)
    assert entry["payload"] == payload.prompt and entry["spec"]["change_id"] == "chg-1.5-01"
    assert entry["prompt_sha256"]["type"] == d.prompt_sha256(_prompts(), "draft_T3.md")


def test_resume_never_repeats_a_cached_request(tmp_path: Path) -> None:
    payloads = [d.build_payload(_item(), _prompts(), _corpus(), KEY)]
    _run(payloads, FakeClient([_answer()]), tmp_path / "c.jsonl")
    again = FakeClient([])
    result = _run(payloads, again, tmp_path / "c.jsonl")
    assert again.prompts == [] and result.cached == 1 and result.sent == 0


def test_a_changed_model_config_is_a_new_request(tmp_path: Path) -> None:
    payload = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    assert d.request_sha256(GEN, payload.prompt) != d.request_sha256(
        {**GEN, "temperature": 0.0}, payload.prompt
    )


def test_rate_limits_are_retried_with_the_server_delay(tmp_path: Path) -> None:
    sleeps: list[float] = []
    client = FakeClient([RateLimited("429", 3.0), TransientError("503"), _answer()])
    result = _run(
        [d.build_payload(_item(), _prompts(), _corpus(), KEY)], client, tmp_path / "c.jsonl", sleeps
    )
    assert result.sent == 1 and result.errors == 0 and sleeps == [3.0, 4.0]


def test_exhausted_retries_stop_the_run_and_record_the_error(tmp_path: Path) -> None:
    client = FakeClient([RateLimited("429", None)] * 3)
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in (_item(), _item(kind="T1"))]
    result = _run(payloads, client, tmp_path / "c.jsonl")
    assert result.stopped and "retries" in result.stopped and len(client.prompts) == 3
    [entry] = d.load_cache(tmp_path / "c.jsonl")
    assert entry["status"] == "error" and entry["error_type"] == "RateLimited"


def test_a_fatal_error_stops_without_caching(tmp_path: Path) -> None:
    client = FakeClient(
        [FatalError("quota limit is 0 for this model: the free tier is not available")]
    )
    result = _run(
        [d.build_payload(_item(), _prompts(), _corpus(), KEY)], client, tmp_path / "c.jsonl"
    )
    assert result.stopped and "free tier" in result.stopped and not (tmp_path / "c.jsonl").exists()


def test_a_malformed_response_is_recorded_and_the_run_continues(tmp_path: Path) -> None:
    client = FakeClient([MalformedResponse("incomplete candidate"), _answer(type="T1")])
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in (_item(), _item(kind="T1"))]
    result = _run(payloads, client, tmp_path / "c.jsonl")
    assert result.sent == 2 and result.errors == 1 and result.stopped is None
    assert [e["status"] for e in d.load_cache(tmp_path / "c.jsonl")] == ["error", "ok"]


def test_the_request_budget_stops_the_run_for_resumption(tmp_path: Path) -> None:
    limits = LIMITS.model_copy(update={"max_requests_per_run": 1})
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in (_item(), _item(kind="T1"))]
    client = FakeClient([_answer(), _answer(type="T1")])
    result = d.run_drafting(
        payloads,
        client,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        limits,
        _corpus(),
        KEY,
        sleep=lambda s: None,
    )
    assert result.sent == 1 and result.stopped and "budget" in result.stopped
    resumed = d.run_drafting(
        payloads,
        client,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        limits,
        _corpus(),
        KEY,
        sleep=lambda s: None,
    )
    assert resumed.cached == 1 and resumed.sent == 1 and resumed.stopped is None


# --- S3 review: allocation, pre-send audit, local-only outputs --------------------------------


def test_t3_covers_candidates_the_other_types_left_out_first() -> None:
    records = [_record(), _record("chg-2.0-01")]
    plan = d.build_plan(records, _config(T1=0, T2=0, T3=1, T4=0, T5=1), _pool())
    assert [(i.spec.type, i.spec.change_id) for i in plan] == [
        ("T5", "chg-2.0-01"),
        ("T3", "chg-1.5-01"),
    ]
    assert d.unplanned(records, plan) == []


def test_unplanned_reports_pilot_candidates_without_an_item() -> None:
    records = [_record(), _record("chg-2.0-01"), _record("chg-1.4-01", status="unresolved")]
    plan = d.build_plan(records, _config(T1=0, T2=0, T3=1, T4=0, T5=0), _pool())
    assert d.unplanned(records, plan) == ["chg-2.0-01"]


def test_the_exact_prompt_is_audited_again_just_before_sending(tmp_path: Path) -> None:
    clean = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    tampered = d.Payload(clean.item, clean.prompt + "\n" + RESTRICTED, clean.passages, 0)
    restricted_kind = d.Payload(
        clean.item, clean.prompt, [d.Passage("x", "1.5", "release_post", "_posts/post.md", "x")], 0
    )
    assert (
        tampered.blocked == [] and restricted_kind.blocked == []
    )  # as if the plan audit missed them
    client = FakeClient([])
    result = _run([tampered, restricted_kind], client, tmp_path / "c.jsonl")
    assert client.prompts == [] and result.blocked == 2 and not (tmp_path / "c.jsonl").exists()


def test_drafting_outputs_are_git_ignored_and_never_ci_artifacts() -> None:
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    for rel in (d.CACHE_PATH, d.DRAFTS_PATH):
        check = subprocess.run([git, "check-ignore", "-q", rel.as_posix()], cwd=ROOT, check=False)
        assert check.returncode == 0, f"{rel} is not git-ignored"
    workflow = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    assert "benchmark/drafts" not in workflow and "drafting" not in workflow


def _excluding(config: d.DraftingConfig, cid: str, *types: str) -> d.DraftingConfig:
    hints = dict(config.changes)
    hints[cid] = hints[cid].model_copy(update={"exclude_types": types, "exclude_reason": "owner"})
    return config.model_copy(update={"changes": hints})


def test_owner_exclusions_are_respected_and_another_candidate_is_used() -> None:
    records = [_record(), _record("chg-2.0-01")]
    config = _excluding(_config(T1=0, T2=0, T3=2, T4=0, T5=0), "chg-2.0-01", "T3")
    plan = d.build_plan(records, config, _pool())
    assert [(i.spec.type, i.spec.change_id) for i in plan] == [
        ("T3", "chg-1.5-01"),
        ("T3", "chg-1.5-01"),
    ]
    assert d.unplanned(records, plan) == ["chg-2.0-01"] and d.shortfalls(config, plan) == {}


def test_a_shortfall_is_reported_not_padded() -> None:
    records = [_record(), _record("chg-2.0-01")]
    config = _excluding(
        _excluding(_config(T1=0, T2=0, T3=3, T4=0, T5=0), "chg-2.0-01", "T3"), "chg-1.5-01", "T3"
    )
    plan = d.build_plan(records, config, _pool())
    assert [i for i in plan if i.spec.type == "T3"] == []
    assert d.shortfalls(config, plan) == {"T3": (0, 3)}


def test_an_exclusion_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="exclude_reason"):
        d.ChangeHints(keywords=("x",), t5="", exclude_types=("T3",))


def test_repository_keeps_chg_2_0_09_out_of_t3_and_t4() -> None:
    config = d.load_drafting_config(ROOT / "configs/drafting.toml")
    excluded = {c: h.exclude_types for c, h in config.changes.items() if h.exclude_types}
    assert excluded == {"chg-2.0-09": ("T3", "T4")}


# --- quota: application limits, retries through the ledger, resume ---------------------------


class FakeTime:
    """A clock whose sleep advances it, so waits are observable and instant."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now, self.slept = start, []  # type: float, list[float]

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _ledger(
    tmp: Path, ft: FakeTime, rpm: int = 10, tpm: int = 200_000, rpd: int = 400
) -> QuotaLedger:
    return QuotaLedger(
        tmp / "ledger.json", QuotaLimits(rpm, tpm, rpd), clock=ft.clock, sleep=ft.sleep
    )


def test_retries_are_counted_by_the_ledger_and_capped_at_two(tmp_path: Path) -> None:
    ft = FakeTime()
    ledger = _ledger(tmp_path, ft)
    client = FakeClient([TransientError("503")] * 3)
    payload = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    result = d.run_drafting(
        [payload],
        client,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        LIMITS,
        _corpus(),
        KEY,
        sleep=ft.sleep,
        ledger=ledger,
    )
    assert len(client.prompts) == 3  # first attempt + max_retries=2
    assert ledger.usage()["requests_last_24h"] == 3  # every attempt is counted
    assert result.stopped and "retries" in result.stopped


def test_backoff_is_exponential_with_jitter_and_honours_the_server_delay(tmp_path: Path) -> None:
    limits = LIMITS.model_copy(update={"backoff_jitter": 0.5, "max_backoff_seconds": 60.0})
    sleeps: list[float] = []
    client = FakeClient([TransientError("503"), TransientError("503"), _answer()])
    d._generate_with_retries(client, "p", limits, sleeps.append, None, rng=lambda: 1.0)
    assert sleeps == [3.0, 6.0]  # 2 * 2**n, plus 50 % jitter
    sleeps.clear()
    client = FakeClient([RateLimited("429", 30.0), _answer()])
    d._generate_with_retries(client, "p", limits, sleeps.append, None, rng=lambda: 0.0)
    assert sleeps == [30.0]  # never shorter than the server's retry delay


def test_the_daily_limit_stops_the_run_resumably_and_cached_drafts_are_never_resent(
    tmp_path: Path,
) -> None:
    ft = FakeTime()
    items = [_item(), _item(kind="T1"), _item(kind="T4")]
    payloads = [d.build_payload(i, _prompts(), _corpus(), KEY) for i in items]
    ledger = _ledger(tmp_path, ft, rpd=2)
    first = FakeClient([_answer(), _answer(type="T1")])
    r1 = d.run_drafting(
        payloads,
        first,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        LIMITS,
        _corpus(),
        KEY,
        sleep=ft.sleep,
        ledger=ledger,
    )
    assert r1.sent == 2 and r1.stopped and r1.stopped.startswith("daily request limit")
    # "Restart": a new ledger object on the same file still sees today's usage.
    restarted = _ledger(tmp_path, ft, rpd=2)
    blocked = FakeClient([])
    r2 = d.run_drafting(
        payloads,
        blocked,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        LIMITS,
        _corpus(),
        KEY,
        sleep=ft.sleep,
        ledger=restarted,
    )
    assert blocked.prompts == [] and r2.cached == 2 and r2.stopped
    # A day later only the missing draft is requested; the two cached ones are not sent again.
    ft.now += 24 * 3600 + 1
    later = FakeClient([_answer(type="T4")])
    r3 = d.run_drafting(
        payloads,
        later,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        LIMITS,
        _corpus(),
        KEY,
        sleep=ft.sleep,
        ledger=_ledger(tmp_path, ft, rpd=2),
    )
    assert len(later.prompts) == 1 and r3.cached == 2 and r3.sent == 1 and r3.stopped is None


def test_the_api_token_count_is_recorded_when_higher_than_the_estimate(tmp_path: Path) -> None:
    ft = FakeTime()
    ledger = _ledger(tmp_path, ft)
    client = FakeClient([_answer()])
    client.last_prompt_tokens = 150_000  # type: ignore[attr-defined]
    payload = d.build_payload(_item(), _prompts(), _corpus(), KEY)
    d.run_drafting(
        [payload],
        client,
        GEN,
        _prompts(),
        tmp_path / "c.jsonl",
        LIMITS,
        _corpus(),
        KEY,
        sleep=ft.sleep,
        ledger=ledger,
    )
    assert ledger.usage()["input_tokens_last_minute"] == 150_000


def test_configured_limits_may_not_exceed_the_account_quota() -> None:
    data = _config().model_dump()
    for field, value in (
        ("requests_per_minute", 16),
        ("input_tokens_per_minute", 250_001),
        ("requests_per_day", 501),
    ):
        bad = {**data, "limits": {**data["limits"], field: value}}
        with pytest.raises(ValidationError, match="exceeds the account quota"):
            d.DraftingConfig.model_validate(bad)


def test_repository_limits_match_the_owner_settings() -> None:
    config = d.load_drafting_config(ROOT / "configs/drafting.toml")
    lim, q = config.limits, config.account_quota
    assert (q.requests_per_minute, q.input_tokens_per_minute, q.requests_per_day) == (
        15,
        250_000,
        500,
    )
    assert (lim.requests_per_minute, lim.input_tokens_per_minute, lim.requests_per_day) == (
        10,
        200_000,
        400,
    )
    assert lim.max_retries == 2 and lim.backoff_jitter > 0


def test_the_review_sheet_lists_accepted_v0_drafts_with_empty_review_fields(tmp_path: Path) -> None:
    drafts = _drafts([_item()], [_answer()], tmp_path)
    sheet = d.render_review_sheet(drafts)
    assert "V0, not gold, not human-verified" in sheet and "### T3-01" in sheet
    assert "- decision (accept | accept_minor_edit | accept_major_edit | reject): " in sheet
    assert "evidence (1.5, `docs/1.5/frob.md`)" in sheet
    assert RESTRICTED not in sheet and KEY not in sheet
    assert d.REVIEW_SHEET_PATH.parent == d.CACHE_PATH.parent  # git-ignored folder


def test_type_labels_naming_the_requested_type_are_accepted() -> None:
    assert d.type_label_matches("T2", "T2") and d.type_label_matches("version_specific", "T2")
    assert d.type_label_matches("T4 Multi-hop", "T4") and d.type_label_matches("unanswerable", "T5")
    assert not d.type_label_matches("factual", "T1") and not d.type_label_matches(
        "single_hop", "T4"
    )
    assert not d.type_label_matches("T3", "T4") and not d.type_label_matches(None, "T1")


def test_span_ids_and_text_keys_are_resolved_but_stay_verbatim() -> None:
    p = {
        "chunk:c-1": d.Passage(
            "chunk:c-1", "1.5", "docs_page", "docs/1.5/a.md", "Alpha beta\ngamma delta."
        ),
        "src:docs/1.5/b.md:3-4": d.Passage(
            "src:docs/1.5/b.md:3-4", "1.5", "migration_guide", "docs/1.5/b.md", "Zeta eta."
        ),
    }
    assert d.resolve_span("c-1", "Alpha beta", p) == (p["chunk:c-1"], "Alpha beta")
    assert d.resolve_span("[chunk:c-1]", "beta gamma", p) == (
        p["chunk:c-1"],
        "beta\ngamma",
    )  # verbatim
    assert d.resolve_span("docs/1.5/b.md:3", "Zeta eta.", p) == (
        p["src:docs/1.5/b.md:3-4"],
        "Zeta eta.",
    )
    assert d.resolve_span("unknown", "Zeta eta.", p) == (p["src:docs/1.5/b.md:3-4"], "Zeta eta.")
    assert d.resolve_span("c-1", "Zeta eta.", p) is None  # the cited passage lacks the text
    assert d.resolve_span("c-1", "invented words", p) is None


def test_exact_text_key_is_accepted() -> None:
    answer = _answer(
        evidence_spans=[
            {"passage_id": "p1", "exact_text": "frob_count(x) returns the number of frobs in x."}
        ]
    )
    assert _reasons(_item(), answer) == []


def test_the_v1_prompt_states_the_validator_rule_for_undocumented_versions() -> None:
    """Prompt and validator agree: every version must be documented; not_documented is reported
    honestly and the item is rejected, never repaired."""
    text = _prompts()["v1/draft_T2.md"]
    assert 'accepted only when every version is "documented"' in text
    assert 'as "not_documented": the item is then rejected, not repaired' in text
    undocumented = _t2_answer(
        version_answers=_va(
            **{"1.4": {"status": "not_documented", "answer": None, "evidence_spans": []}}
        )
    )
    assert "t2_version_not_documented" in _reasons(_t2_item(), undocumented)


def test_an_explicit_t3_priority_keeps_the_order_independent_of_other_counts() -> None:
    records = [_record(), _record("chg-2.0-01")]
    base = _config(T1=0, T2=0, T3=2, T4=0, T5=0)
    pinned = base.model_copy(
        update={"plan": base.plan.model_copy(update={"t3_priority": ("chg-2.0-01",)})}
    )
    order = [
        i.spec.change_id for i in d.build_plan(records, pinned, _pool()) if i.spec.type == "T3"
    ]
    assert order == ["chg-2.0-01", "chg-1.5-01"]
    more = pinned.model_copy(update={"plan": pinned.plan.model_copy(update={"T5": 1})})
    assert [
        i.spec.change_id for i in d.build_plan(records, more, _pool()) if i.spec.type == "T3"
    ] == order


def test_t4_extra_items_are_appended_with_distinct_pages() -> None:
    records = [_record(), _record("chg-2.0-01")]
    chunks = {
        v: [
            _chunk(f"c-{v}-a", v, f"frob_count page A {v}"),
            _chunk(f"c-{v}-b", v, f"frob_count page B {v}"),
        ]
        for v in d.CORPUS_VERSIONS
    }
    for v in d.CORPUS_VERSIONS:
        chunks[v][1]["source_path"] = f"docs/{v}/other.md"
    pool = d.PassagePool(chunks, _index(), 2500, 2)
    base = _config(T1=0, T2=0, T3=0, T4=0, T5=0)
    extra = base.model_copy(
        update={"plan": base.plan.model_copy(update={"t4_extra": ("chg-1.5-01",)})}
    )
    items = [i for i in d.build_plan(records, extra, pool) if i.spec.type == "T4"]
    assert [i.spec.change_id for i in items] == ["chg-1.5-01"]
    assert len({p.source_path for p in items[0].passages}) >= 2
    one_page = d.PassagePool(
        {v: [_chunk(f"c-{v}", v, "nothing relevant")] for v in d.CORPUS_VERSIONS}, _index(), 2500, 2
    )
    with pytest.raises(d.DraftingError, match="fewer than two distinct pages"):
        d.build_plan(records, extra, one_page)


def test_items_after_the_v0_cutoff_use_the_v1_shared_prompt() -> None:
    records = [_record(), _record("chg-2.0-01")]
    base = _config(T1=0, T2=0, T3=3, T4=0, T5=0)
    cfg = base.model_copy(
        update={"plan": base.plan.model_copy(update={"common_v0_through": {"T3": 1}})}
    )
    t3 = [i for i in d.build_plan(records, cfg, _pool()) if i.spec.type == "T3"]
    assert [i.spec.common_prompt for i in t3] == ["v0", "v1", "v1"]
    v0 = d.build_payload(t3[0], _prompts(), _corpus(), KEY).prompt
    v1 = d.build_payload(t3[1], _prompts(), _corpus(), KEY).prompt
    assert "Drafting prompt v0: common" in v0 and "Drafting prompt v1: common" in v1
    assert 'written as one of "T1", "T2", "T3", "T4", "T5"' in v1


def test_a_t5_response_with_a_gold_span_is_rejected() -> None:
    t5 = _item(kind="T5", target="1.4", version_set=("1.4",))
    spans = [{"passage_id": "p1", "exact_text": "frob_count(x) returns the number of frobs in x."}]
    answer = _answer(
        type="T5",
        target_version="1.4",
        gold_short_answer="",
        gold_answer_type="none",
        evidence_spans=spans,
        answerable=False,
        abstain_reason="Not in 1.4.",
    )
    assert "t5_has_gold_span" in _reasons(t5, answer)


def test_redraft_items_use_the_v1_prompts() -> None:
    records = [_record(), _record("chg-2.0-01")]
    base = _config(T1=0, T2=0, T3=0, T4=0, T5=2)
    cfg = base.model_copy(update={"plan": base.plan.model_copy(update={"redraft_v1": ("T5-02",)})})
    t5 = [i for i in d.build_plan(records, cfg, _pool()) if i.spec.type == "T5"]
    assert [(i.spec.item_id, i.spec.common_prompt, i.spec.type_prompt) for i in t5] == [
        ("T5-01", "v0", "v0"),
        ("T5-02", "v1", "v1"),
    ]
    prompt = d.build_payload(t5[1], _prompts(), _corpus(), KEY).prompt
    assert "Drafting prompt v1: T5" in prompt and '"evidence_spans" must be an empty list' in prompt
