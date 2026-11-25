"""Protocol v1 T4/T5 drafting checks (owner decision D3): the advisory screen flags the patterns
found in the pilot review and passes clean items."""

from __future__ import annotations

from version_aware_rag.benchmark import screen as sc
from version_aware_rag.retrieval.corpus import Chunk, RetrievalCorpus

PKG = {"1.3": "1.3.2", "1.4": "1.4.5"}


def _corpus() -> RetrievalCorpus:
    def ch(cid: str, v: str, text: str) -> Chunk:
        return Chunk(cid, v, "docs_page", "documentation", None, f"docs/{v}/{cid}.md", ("X",), text)

    return RetrievalCorpus(
        [
            ch(
                "e13",
                "1.3",
                "| `epoch_ms(ms)` | Converts milliseconds since the epoch to a timestamp. |",
            ),
            ch(
                "m14",
                "1.4",
                "| `make_timestamp_ms(ms)` | Converts milliseconds since the epoch "
                "to a timestamp. |",
            ),
            ch(
                "s14",
                "1.4",
                "| `struct_contains(s, e)` | Checks whether a struct contains the entry. |",
            ),
            ch("a13", "1.3", "Other unrelated text about lists."),
        ],
        PKG,
    )


def _t5(question: str, reason: str, v: str = "1.3") -> dict[str, object]:
    return {
        "item_id": "T5-x",
        "type": "T5",
        "target_version": v,
        "question": question,
        "abstain_reason": reason,
    }


def test_identifiers_keep_the_longest_names() -> None:
    item = _t5("How do I use the MERGE INTO statement?", "`merge into` was introduced in 1.4")
    assert sc.feature_identifiers(item) == {"merge into"}


def test_documented_alternative_is_flagged() -> None:
    item = _t5(
        "Which function converts milliseconds since the epoch to a timestamp in DuckDB 1.3?",
        "The make_timestamp_ms function was introduced in DuckDB 1.4.",
    )
    r = sc.screen_item(item, _corpus())
    assert any(f.startswith("T5-S2") for f in r.flags)
    assert r.facts["present_in_target"] == []  # the named feature itself is absent


def test_feature_present_in_the_target_version_is_flagged() -> None:
    item = _t5("How do I call struct_contains in DuckDB 1.4?", "struct_contains is absent", "1.4")
    assert any(f.startswith("T5-S1") for f in sc.screen_item(item, _corpus()).flags)


def test_a_clean_t5_item_passes() -> None:
    item = _t5("What is the alias of struct_contains in DuckDB 1.3?", "struct_contains came in 1.4")
    assert sc.screen_item(item, _corpus()).flags == []


def test_t4_single_span_that_answers_alone_is_flagged() -> None:
    item = {
        "item_id": "T4-x",
        "type": "T4",
        "target_version": "1.4",
        "question": "Which function converts milliseconds since the epoch to a timestamp?",
        "gold_short_answer": "make_timestamp_ms",
        "evidence_spans": [{"text": "Converts milliseconds since the epoch to a timestamp."}],
    }
    assert any(f.startswith("T4-S1") for f in sc.screen_item(item, _corpus()).flags)
    item["evidence_spans"] = [{"text": "Other unrelated text"}]
    assert sc.screen_item(item, _corpus()).flags == []


def test_short_words_inside_longer_identifiers_are_dropped() -> None:
    uuid = _t5(
        "Which function extracts the UUID version with uuid_extract_version in DuckDB 1.2?",
        "The function `uuid_extract_version` was introduced in DuckDB 1.3.",
    )
    assert sc.feature_identifiers(uuid) == {"uuid_extract_version"}
    merge = _t5(
        "How can I use the MERGE INTO statement to perform upserts in DuckDB 1.3?",
        "The MERGE statement was introduced in version 1.4.",
    )
    assert sc.feature_identifiers(merge) == {"merge into"}


def test_a_generic_word_documented_in_the_target_does_not_flag_the_item() -> None:
    # "UUID" is documented in 1.3 (generic type name) but uuid_extract_version is not: no T5-S1.
    corpus = RetrievalCorpus(
        [
            Chunk(
                "u13",
                "1.3",
                "docs_page",
                "documentation",
                None,
                "docs/1.3/u.md",
                ("U",),
                "The UUID type stores universally unique identifiers.",
            ),
            Chunk(
                "u14",
                "1.4",
                "docs_page",
                "documentation",
                None,
                "docs/1.4/u.md",
                ("U",),
                "uuid_extract_version(uuid) returns the version of a UUID.",
            ),
        ],
        PKG,
    )
    item = _t5(
        "Which function extracts the UUID version with uuid_extract_version in DuckDB 1.3?",
        "The function `uuid_extract_version` was introduced in DuckDB 1.4.",
    )
    r = sc.screen_item(item, corpus)
    assert r.facts["identifiers"] == ["uuid_extract_version"]
    assert r.facts["present_in_target"] == [] and r.facts["present_elsewhere"] == [
        "uuid_extract_version"
    ]
    assert not any(f.startswith("T5-S1") for f in r.flags)
