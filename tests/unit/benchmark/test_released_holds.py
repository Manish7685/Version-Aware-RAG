"""Released held drafts (owner decision D2): they join the review set as candidates only, are
validated, never enter automatic selection and carry no review decision."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from version_aware_rag.benchmark import pilot as p
from version_aware_rag.benchmark.drafting import DRAFTS_PATH
from version_aware_rag.benchmark.pilot_commands import (
    RELEASED_HOLDS,
    _pilot_drafts,
    released_hold_ids,
)


def _draft(did: str, hold: str = "", status: str = "draft", typ: str = "T2") -> dict[str, Any]:
    return {"draft_id": did, "item_id": did, "type": typ, "status": status, "review_hold": hold}


def _root(tmp_path: Path, drafts: list[dict[str, Any]], pilot_ids: list[str]) -> Path:
    (tmp_path / DRAFTS_PATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / DRAFTS_PATH).write_text("".join(json.dumps(x) + "\n" for x in drafts), "utf-8")
    (tmp_path / p.PILOT_PATH).write_text(
        "".join(json.dumps({"draft_id": i}) + "\n" for i in pilot_ids), "utf-8"
    )
    return tmp_path


def _release(root: Path, ids: list[str]) -> None:
    (root / RELEASED_HOLDS).parent.mkdir(parents=True, exist_ok=True)
    (root / RELEASED_HOLDS).write_text(
        json.dumps({"released": [{"draft_id": i} for i in ids]}), "utf-8"
    )


def test_without_a_release_file_the_review_set_is_the_pilot(tmp_path: Path) -> None:
    root = _root(tmp_path, [_draft("a"), _draft("h", hold="conflict")], ["a"])
    assert [x["draft_id"] for x in _pilot_drafts(root)] == ["a"]


def test_released_holds_are_appended_after_the_pilot(tmp_path: Path) -> None:
    root = _root(tmp_path, [_draft("a"), _draft("h1", "c"), _draft("h2", "c")], ["a"])
    _release(root, ["h1", "h2"])
    assert [x["draft_id"] for x in _pilot_drafts(root)] == ["a", "h1", "h2"]


@pytest.mark.parametrize(
    ("drafts", "ids"),
    [
        ([_draft("a")], ["missing"]),  # unknown draft
        ([_draft("a")], ["a"]),  # not held
        ([_draft("h", "c", status="rejected")], ["h"]),  # not a draft
        ([_draft("h", "c")], ["h", "h"]),  # duplicate
    ],
)
def test_invalid_releases_are_refused(
    tmp_path: Path, drafts: list[dict[str, Any]], ids: list[str]
) -> None:
    root = _root(tmp_path, drafts, [])
    _release(root, ids)
    with pytest.raises(ValueError):
        released_hold_ids(root, {x["draft_id"]: x for x in drafts})


def test_held_drafts_never_enter_automatic_selection() -> None:
    drafts = [_draft("h", "c", typ="T1")]
    v1 = {"h": p.V1Result("h")}
    pilot, _ = p.select_pilot(drafts, v1, {"T1": 1})
    assert pilot == []


def test_the_repository_release_list_names_only_held_t2_drafts() -> None:
    root = Path(__file__).resolve().parents[3]
    data = json.loads((root / RELEASED_HOLDS).read_text("utf-8"))
    assert [e["item_id"] for e in data["released"]] == ["T2-H01@1.5", "T2-H01@2.0"]
    assert "Not a review decision" in data["decision"]
