"""S5a deterministic corpus build (design docs/design/RAG-M3_S5_build.md), on fixture corpora.

Failure cases only ever modify fixture caches under ``tmp_path``; the real cache is never touched.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from version_aware_rag.corpus import build as build_module
from version_aware_rag.corpus import validate
from version_aware_rag.corpus.acquire import fetch_pinned, open_cache
from version_aware_rag.corpus.build import (
    ALL_OUTPUTS,
    BACKUP_DIR,
    BUILD_MANIFEST,
    BUILD_REPORT,
    JOURNAL,
    S4C_OUTPUTS,
    S4D_OUTPUTS,
    STAGING_DIR,
    UNRESOLVED_FILE,
    BuildEnvironmentError,
    BuildError,
    BuildInputs,
    recover_interrupted,
    run_build,
)
from version_aware_rag.corpus.chunk import chunk_documents
from version_aware_rag.corpus.chunk_output import (
    BuildContext,
    render_chunk_files,
    render_chunk_manifest,
)
from version_aware_rag.corpus.config import CorpusConfig, ParsingConfig, load_chunking_config
from version_aware_rag.corpus.inspect import inspect_corpus
from version_aware_rag.corpus.manifest import build_manifest, read_manifest, write_manifest
from version_aware_rag.corpus.parse import PARSER_VERSION
from version_aware_rag.corpus.releases import body_path, fetch_record_path

from .conftest import Upstream, git, make_config, write_release_cache

ROOT = Path(__file__).resolve().parents[3]
CHUNKING, CHUNKING_SHA = load_chunking_config(ROOT / "configs/chunking.toml")
PARSING = ParsingConfig(site_base="https://example.invalid", excluded_pages=(), samples=())
CFG_SHA = "c" * 64


_TEMPLATE: dict[str, Path] = {}


@pytest.fixture(autouse=True)
def _template(upstream: Upstream, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Prepare one fixture corpus (cache, release cache, manifest) per session; tests copy it."""
    if "root" not in _TEMPLATE:
        base = tmp_path_factory.mktemp("build-template")
        _inputs(upstream, base, copy=False)
        _TEMPLATE["root"] = base


def _inputs(
    up: Upstream, root: Path, cfg: CorpusConfig | None = None, *, copy: bool = True
) -> BuildInputs:
    cfg = cfg or make_config(up, up.commit1)
    cache = cfg.cache_path(root)
    if copy and not (cache / ".git").is_dir():
        shutil.copytree(_TEMPLATE["root"], root, dirs_exist_ok=True)
    if not (cache / ".git").is_dir():
        fetch_pinned(cfg, cache)
    rel = root / cfg.release_notes.cache_dir
    if not rel.is_dir():
        write_release_cache(rel)
        for r in cfg.release_notes.included:
            fetch_record_path(rel, r.tag).write_text(
                json.dumps(
                    {
                        "release_id": r.release_id,
                        "body_sha256": r.body_sha256,
                        "retrieved_at": "2026-11-20T18:46:33Z",
                    }
                ),
                encoding="utf-8",
            )
    manifest_path = root / cfg.paths.manifest
    if not manifest_path.is_file():
        write_manifest(build_manifest(cfg, open_cache(cfg, cache), CFG_SHA, rel), manifest_path)
    return BuildInputs(
        root=root,
        cfg=cfg,
        cfg_sha256=CFG_SHA,
        cache=cache,
        release_cache=rel,
        manifest_path=manifest_path,
        parsing=PARSING,
        parsing_sha256="p" * 64,
        chunking=CHUNKING,
        chunking_sha256=CHUNKING_SHA,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file outside the caches, so 'byte-identical' covers outputs and work folders."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "data/cache" not in p.relative_to(root).as_posix()
    }


def _no_work_files(root: Path) -> bool:
    return not any((root / p).exists() for p in (STAGING_DIR, BACKUP_DIR, JOURNAL))


# ---------------------------------------------------------------- success and determinism


def test_fresh_build_writes_every_output_and_a_rebuild_changes_nothing(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    first = run_build(inp, check=False)
    assert first.written == list(ALL_OUTPUTS) and first.unchanged == []
    assert _no_work_files(tmp_path)
    second = run_build(inp, check=False)
    assert (second.written, second.unchanged) == ([], list(ALL_OUTPUTS))
    assert run_build(inp, check=True).problems == []
    manifest = json.loads((tmp_path / BUILD_MANIFEST).read_bytes())
    assert manifest["logical_corpus"][-1] == "data/build/chunks/github_releases.jsonl"
    listed = {e["path"]: e["sha256"] for e in manifest["outputs"]}
    assert set(listed) == set(ALL_OUTPUTS) - {BUILD_MANIFEST}
    report = json.loads((tmp_path / BUILD_REPORT).read_bytes())
    assert {v["result"] for v in report["verification"]} == {"passed"}


def test_two_independent_builds_are_byte_identical(upstream: Upstream, tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    run_build(_inputs(upstream, a), check=False)
    run_build(_inputs(upstream, b), check=False)
    assert {p: (a / p).read_bytes() for p in ALL_OUTPUTS} == {
        p: (b / p).read_bytes() for p in ALL_OUTPUTS
    }


def test_s4_outputs_equal_the_s4_functions(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    outputs = run_build(inp, check=False).outputs
    manifest = read_manifest(inp.manifest_path)
    insp = inspect_corpus(manifest, inp.cache, PARSING, inp.parsing_sha256)
    pages = chunk_documents([p.doc for p in insp.pages], manifest, CHUNKING, CHUNKING_SHA)
    files = render_chunk_files(pages)

    ctx = BuildContext(
        manifest.source.commit,
        PARSER_VERSION,
        CHUNKING.chunker_version,
        CHUNKING_SHA,
        hashlib.sha256(inp.manifest_path.read_bytes()).hexdigest(),
    )
    for name, data in files.items():
        assert outputs[f"data/build/chunks/{name}"] == data
    assert outputs["data/manifest/chunk_manifest.json"] == render_chunk_manifest(pages, files, ctx)
    assert set(S4C_OUTPUTS) | set(S4D_OUTPUTS) <= set(outputs)


# ---------------------------------------------------------------- failures leave outputs alone


def _built_then(upstream: Upstream, root: Path) -> tuple[BuildInputs, dict[str, bytes]]:
    inp = _inputs(upstream, root)
    run_build(inp, check=False)
    return inp, _snapshot(root)


def test_tampered_corpus_file_stops_the_build(upstream: Upstream, tmp_path: Path) -> None:
    inp, before = _built_then(upstream, tmp_path)
    page = inp.cache / "docs/1.4/sql/functions/map.md"
    page.write_bytes(page.read_bytes() + b"tampered\n")
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    assert err.value.stage == "verify inputs" and err.value.exit_code == 1
    assert any("changed cache file: docs/1.4/sql/functions/map.md" in p for p in err.value.problems)
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_tampered_corpus_file_on_a_fresh_root_writes_nothing(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    page = inp.cache / "docs/1.2/sql/functions/map.md"
    page.write_bytes(b"different\n")
    before = _snapshot(tmp_path)
    with pytest.raises(BuildError):
        run_build(inp, check=False)
    assert _snapshot(tmp_path) == before
    assert not any((tmp_path / p).exists() for p in ALL_OUTPUTS)


def test_m1_content_hash_mismatch_stops_the_build(upstream: Upstream, tmp_path: Path) -> None:
    inp, before = _built_then(upstream, tmp_path)
    bad_cfg = make_config(upstream, upstream.commit1, **{"1.4": {"content_hash": "0" * 64}})
    with pytest.raises(BuildError) as err:
        run_build(dataclasses.replace(inp, cfg=bad_cfg), check=False)
    assert any(p.startswith("RAG-M1 evidence (IR-6)") for p in err.value.problems)
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


@pytest.mark.parametrize("fault", ["changed", "missing"])
def test_changed_or_missing_release_body_stops_the_build(
    upstream: Upstream, tmp_path: Path, fault: str
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    body = body_path(inp.release_cache, "v1.3.1")
    if fault == "changed":
        body.write_bytes(body.read_bytes() + b" ")
    else:
        body.unlink()
    before = _snapshot(tmp_path)
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    assert any(f"v1.3.1 {fault}" in p for p in err.value.problems)
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_missing_cache_is_an_environment_error(upstream: Upstream, tmp_path: Path) -> None:
    inp = _inputs(upstream, tmp_path)
    missing = dataclasses.replace(inp, cache=tmp_path / "nowhere")
    with pytest.raises(BuildEnvironmentError) as err:
        run_build(missing, check=False)
    assert err.value.exit_code == 2
    assert not any((tmp_path / p).exists() for p in ALL_OUTPUTS)


def test_corrupt_existing_output_stops_the_build(upstream: Upstream, tmp_path: Path) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    target = tmp_path / "data/build/chunks/1.2.jsonl"
    target.write_bytes(target.read_bytes() + b'{"not": "a chunk"}\n')
    before = _snapshot(tmp_path)
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    assert any(p.startswith("existing S4c output") for p in err.value.problems)
    assert _snapshot(tmp_path) == before


def test_in_memory_validation_failure_writes_nothing(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp = _inputs(upstream, tmp_path)
    monkeypatch.setattr(build_module, "chunk_file_problems", lambda *_: ["injected problem"])
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    assert err.value.stage == "validate in memory"
    assert not any((tmp_path / p).exists() for p in ALL_OUTPUTS)


def test_failed_replacement_rolls_every_file_back(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    changed = dataclasses.replace(inp, chunking_sha256="f" * 64)  # changes most outputs
    real = build_module._REPLACE
    calls = {"n": 0}

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 6:  # after the journal and a few files have been swapped
            raise OSError("injected replacement failure")
        real(src, dst)

    monkeypatch.setattr(build_module, "_REPLACE", flaky)
    with pytest.raises(BuildError) as err:
        run_build(changed, check=False)
    assert err.value.stage == "write outputs" and "rolled back" in err.value.problems[0]
    assert calls["n"] == 6
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_interrupted_build_is_detected_and_restored(upstream: Upstream, tmp_path: Path) -> None:
    inp, before = _built_then(upstream, tmp_path)
    # Simulate a crash mid-replacement: the old build report moved to the backup folder, a new
    # one in place, and a newly created file, all recorded in the journal.
    (tmp_path / BACKUP_DIR / "data/build").mkdir(parents=True)
    (tmp_path / BUILD_REPORT).replace(tmp_path / BACKUP_DIR / BUILD_REPORT)
    (tmp_path / BUILD_REPORT).write_bytes(b"half-written")
    (tmp_path / "data/build/new.json").write_bytes(b"created")
    (tmp_path / JOURNAL).write_text(
        json.dumps(
            {"replace": [BUILD_REPORT, "data/build/new.json"], "created": ["data/build/new.json"]}
        )
    )
    check = run_build(inp, check=True)
    assert any("interrupted build" in p for p in check.problems)
    assert (tmp_path / BUILD_REPORT).read_bytes() == b"half-written"  # check writes nothing
    assert recover_interrupted(tmp_path) == [
        f"restored {BUILD_REPORT}",
        "removed data/build/new.json (created by the interrupted build)",
    ]
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_check_reports_stale_outputs_without_touching_them(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    (tmp_path / BUILD_REPORT).write_bytes(b"stale")
    (tmp_path / UNRESOLVED_FILE).unlink()
    before = _snapshot(tmp_path)
    result = run_build(inp, check=True)
    assert f"{BUILD_REPORT}: differs from a fresh build" in result.problems
    assert f"{UNRESOLVED_FILE}: missing" in result.problems
    assert result.written == [] and _snapshot(tmp_path) == before and _no_work_files(tmp_path)


# ---------------------------------------------------------------- unresolved sources (S5-D2)


def test_unresolved_sources_are_recorded_and_never_chunked(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp = _inputs(upstream, tmp_path)
    (inp.cache / "_posts/2026-12-01-announcing-duckdb-999.md").write_bytes(b"# not mapped\n")
    (inp.release_cache / "v9.9.9.md").write_bytes(b"unapproved body\n")
    result = run_build(inp, check=False)
    records = [json.loads(x) for x in (tmp_path / UNRESOLVED_FILE).read_text().splitlines()]
    assert records == [
        {
            "detecting_step": "build.scan_cache_posts",
            "kind": "release_post",
            "path": "_posts/2026-12-01-announcing-duckdb-999.md",
            "reason": "unmapped_release_post",
            "sha256": hashlib.sha256(b"# not mapped\n").hexdigest(),
        },
        {
            "detecting_step": "build.scan_release_cache",
            "kind": "github_release",
            "path": "releases/tag/v9.9.9",
            "reason": "unapproved_release_body",
            "sha256": hashlib.sha256(b"unapproved body\n").hexdigest(),
        },
    ]
    chunk_paths = {
        json.loads(line)["source_path"]
        for p in ALL_OUTPUTS
        if p.endswith(".jsonl") and p != UNRESOLVED_FILE and "empty_sections" not in p
        for line in (tmp_path / p).read_text(encoding="utf-8").splitlines()
    }
    assert not chunk_paths & {r["path"] for r in records}
    assert result.counts["unresolved"] == 2


# ---------------------------------------------------------------- S5a corrections (audit)


class _Stop(BaseException):
    """Simulates the process stopping at a named point (not caught by the build)."""


def _changed(inp: BuildInputs) -> BuildInputs:
    return dataclasses.replace(inp, chunking_sha256="f" * 64)  # changes most outputs


def _stop_at(monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    def checkpoint(name: str) -> None:
        if name == point:
            raise _Stop(name)

    monkeypatch.setattr(build_module, "_CHECKPOINT", checkpoint)


def _outputs_of(root: Path) -> dict[str, bytes]:
    return {p: (root / p).read_bytes() for p in ALL_OUTPUTS if (root / p).is_file()}


def test_interruption_before_journal_removal_restores_the_complete_old_set(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    _stop_at(monkeypatch, "replacements_done")
    with pytest.raises(_Stop):
        run_build(_changed(inp), check=False)
    assert (tmp_path / JOURNAL).is_file() and (tmp_path / BACKUP_DIR).is_dir()
    assert _snapshot(tmp_path) != before  # new files are in place, not yet committed
    monkeypatch.setattr(build_module, "_CHECKPOINT", build_module._no_checkpoint)
    result = run_build(inp, check=False)  # recovers first, then rebuilds the old inputs
    assert result.recovered and result.written == []
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_interruption_immediately_after_journal_removal_keeps_the_new_set(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    _stop_at(monkeypatch, "journal_removed")
    with pytest.raises(_Stop):
        run_build(_changed(inp), check=False)
    assert not (tmp_path / JOURNAL).exists() and (tmp_path / BACKUP_DIR).is_dir()
    committed = _outputs_of(tmp_path)
    monkeypatch.setattr(build_module, "_CHECKPOINT", build_module._no_checkpoint)
    assert run_build(_changed(inp), check=True).problems == []  # the new set is complete
    result = run_build(_changed(inp), check=False)
    assert result.written == [] and _outputs_of(tmp_path) == committed
    assert any("stale transaction data" in m for m in result.recovered)
    assert _no_work_files(tmp_path)


def test_interruption_during_backup_cleanup_keeps_the_new_set(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    backups = tmp_path / BACKUP_DIR

    def partial_rmtree(path: Path) -> None:
        if path == backups and path.is_dir():
            next(p for p in path.rglob("*") if p.is_file()).unlink()  # one backup gone
            raise _Stop("during backup cleanup")
        shutil.rmtree(path, ignore_errors=True)

    monkeypatch.setattr(build_module, "_RMTREE", partial_rmtree)
    with pytest.raises(_Stop):
        run_build(_changed(inp), check=False)
    assert not (tmp_path / JOURNAL).exists()  # committed before the cleanup started
    assert (tmp_path / BACKUP_DIR).is_dir()  # partly deleted backups left behind
    committed = _outputs_of(tmp_path)
    monkeypatch.setattr(build_module, "_RMTREE", build_module._rmtree_quiet)
    assert run_build(_changed(inp), check=True).problems == []
    assert run_build(_changed(inp), check=False).written == []
    assert _outputs_of(tmp_path) == committed and _no_work_files(tmp_path)


def test_rollback_failure_keeps_journal_and_backups_then_recovers(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    real = build_module._REPLACE
    calls = {"n": 0}
    blocked: list[Path] = []

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 6:
            # Make restoring an already-replaced file impossible: a directory now sits there.
            first = tmp_path / ALL_OUTPUTS[0]
            first.unlink()
            first.mkdir()
            blocked.append(first)
            raise OSError("injected replacement failure")
        real(src, dst)

    monkeypatch.setattr(build_module, "_REPLACE", flaky)
    with pytest.raises(BuildError) as err:
        run_build(_changed(inp), check=False)
    assert err.value.outputs_unchanged is False
    assert any(p.startswith("rollback incomplete") for p in err.value.problems)
    assert any("journal" in p and "kept" in p for p in err.value.problems)
    assert (tmp_path / JOURNAL).is_file()
    assert (tmp_path / BACKUP_DIR / ALL_OUTPUTS[0]).is_file()  # its backup survived
    monkeypatch.setattr(build_module, "_REPLACE", real)
    blocked[0].rmdir()  # the operator clears the obstacle
    result = run_build(inp, check=False)
    assert f"restored {ALL_OUTPUTS[0]}" in result.recovered
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_keyboard_interrupt_during_replacement_rolls_back_and_reraises(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    real = build_module._REPLACE
    calls = {"n": 0}

    def interrupted(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 6:
            raise KeyboardInterrupt
        real(src, dst)

    monkeypatch.setattr(build_module, "_REPLACE", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_build(_changed(inp), check=False)
    assert calls["n"] == 6
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_write_outputs_refuses_while_a_journal_exists(upstream: Upstream, tmp_path: Path) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    outputs = run_build(_changed(inp), check=True).outputs
    (tmp_path / JOURNAL).write_text('{"replace": [], "created": []}')
    (tmp_path / BACKUP_DIR).mkdir(parents=True)
    (tmp_path / BACKUP_DIR / "recovery-data").write_bytes(b"keep me")
    before = _snapshot(tmp_path)
    with pytest.raises(BuildError) as err:
        build_module.write_outputs(tmp_path, outputs)
    assert "active transaction journal" in err.value.problems[0]
    assert _snapshot(tmp_path) == before  # journal, backups and outputs untouched


def _simulate_interrupted(root: Path) -> None:
    (root / BACKUP_DIR / "data/build").mkdir(parents=True)
    (root / BUILD_REPORT).replace(root / BACKUP_DIR / BUILD_REPORT)
    (root / BUILD_REPORT).write_bytes(b"half-written")
    (root / "data/build/new.json").write_bytes(b"created")
    record = {"replace": [BUILD_REPORT, "data/build/new.json"], "created": ["data/build/new.json"]}
    (root / JOURNAL).write_text(json.dumps(record))


def test_recovery_then_failed_verification_leaves_the_previous_set(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    _simulate_interrupted(tmp_path)
    page = inp.cache / "docs/1.4/sql/functions/map.md"
    page.write_bytes(page.read_bytes() + b"tampered\n")
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    assert err.value.stage == "verify inputs" and err.value.outputs_unchanged
    assert f"restored {BUILD_REPORT}" in err.value.recovered
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_check_mode_with_a_journal_neither_recovers_nor_cleans(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    _simulate_interrupted(tmp_path)
    (tmp_path / STAGING_DIR).mkdir(parents=True)
    (tmp_path / STAGING_DIR / "stale").write_bytes(b"x")
    before = _snapshot(tmp_path)
    result = run_build(inp, check=True)
    assert any("interrupted build" in p for p in result.problems)
    assert result.recovered == [] and _snapshot(tmp_path) == before


def _tree_digest(path: Path) -> dict[str, bytes]:
    return {
        p.relative_to(path).as_posix(): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def test_check_mode_never_lazy_fetches_missing_git_objects(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    cache = inp.cache  # an isolated fixture partial clone; never the project's repository
    for pack in (cache / ".git/objects/pack").iterdir():
        pack.chmod(stat.S_IREAD | stat.S_IWRITE)  # git packs are read-only (Windows refuses)
        pack.unlink()
    git(cache, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", upstream.commit1)
    blob = git(cache, "rev-parse", f"{upstream.commit1}:docs/1.4/sql/functions/map.md")
    git_before = _tree_digest(cache / ".git")
    assert "GIT_NO_LAZY_FETCH" not in os.environ
    with pytest.raises(BuildEnvironmentError) as err:
        run_build(inp, check=True)
    assert err.value.exit_code == 2 and err.value.stage == "git"
    assert any("missing" in p for p in err.value.problems)
    assert _tree_digest(cache / ".git") == git_before  # no object fetched, nothing written
    assert "GIT_NO_LAZY_FETCH" not in os.environ  # restored after check mode
    assert _snapshot(tmp_path) == before
    # Control: without the guard git does fetch the missing blob, so the test is not vacuous.
    open_cache(inp.cfg, cache).read_blobs([blob])
    assert _tree_digest(cache / ".git") != git_before


def test_existing_output_mismatch_is_diagnosed_and_nothing_is_overwritten(
    upstream: Upstream, tmp_path: Path
) -> None:
    inp, _ = _built_then(upstream, tmp_path)
    for name in ("1.2.jsonl", "2.0.jsonl"):
        target = tmp_path / "data/build/chunks" / name
        target.write_bytes(target.read_bytes() + b'{"stale": true}\n')
    before = _snapshot(tmp_path)
    with pytest.raises(BuildError) as err:
        run_build(inp, check=False)
    text = "\n".join(err.value.problems)
    expected = (
        "stale or inconsistent generated outputs: data/build/chunks/1.2.jsonl, "
        "data/build/chunks/2.0.jsonl"
    )
    assert expected in text
    assert "do not match their committed manifests" in text
    assert "safe manual recovery" in text and "Nothing is deleted or overwritten" in text
    assert _snapshot(tmp_path) == before


def test_repeated_recovery_is_idempotent(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, before = _built_then(upstream, tmp_path)
    _simulate_interrupted(tmp_path)
    real = build_module._REPLACE

    def failing(src: Path, dst: Path) -> None:
        raise OSError("injected recovery failure")

    monkeypatch.setattr(build_module, "_REPLACE", failing)
    with pytest.raises(BuildError) as err:
        recover_interrupted(tmp_path)
    assert err.value.stage == "recover" and not err.value.outputs_unchanged
    assert (tmp_path / JOURNAL).is_file()  # kept for a retry
    monkeypatch.setattr(build_module, "_REPLACE", real)
    first = recover_interrupted(tmp_path)
    assert first == [
        f"restored {BUILD_REPORT}",
        "removed data/build/new.json (created by the interrupted build)",
    ]
    assert recover_interrupted(tmp_path) == []  # nothing left to do
    assert recover_interrupted(tmp_path) == []
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


def test_journal_removal_failure_is_not_a_commit_and_keeps_recovery_data(
    upstream: Upstream, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inp, before = _built_then(upstream, tmp_path)
    removals: list[Path] = []
    real_rmtree = build_module._RMTREE

    def spy_rmtree(path: Path) -> None:
        removals.append(path)
        real_rmtree(path)

    def failing_removal(path: Path) -> None:
        raise OSError("injected journal removal failure")

    monkeypatch.setattr(build_module, "_RMTREE", spy_rmtree)
    monkeypatch.setattr(build_module, "_REMOVE_JOURNAL", failing_removal)
    with pytest.raises(BuildError) as err:
        run_build(_changed(inp), check=False)

    # 1. Reported correctly: not committed, cause kept, recovery explained, outputs flagged.
    error = err.value
    assert error.stage == "commit" and error.outputs_unchanged is False
    assert error.problems[0].startswith("journal removal failed (injected journal removal failure)")
    assert "could not be committed" in error.problems[0]
    assert "next normal `vrag corpus build` will restore" in error.problems[1]
    assert not any("is committed" in p or "were committed" in p for p in error.problems)
    assert isinstance(error.__cause__, OSError)

    # 2. The journal remains. 3. Every backup remains.
    journal = json.loads((tmp_path / JOURNAL).read_text())
    replaced_existing = [r for r in journal["replace"] if r not in journal["created"]]
    assert replaced_existing  # the rebuild replaced existing outputs
    for rel in replaced_existing:
        assert (tmp_path / BACKUP_DIR / rel).is_file(), rel
        assert (tmp_path / BACKUP_DIR / rel).read_bytes() == before[rel]  # the old version
    assert (tmp_path / STAGING_DIR).is_dir()  # staging data kept as well

    # 5. No cleanup after the failed removal: only the two pre-transaction clears ran.
    assert removals == [tmp_path / STAGING_DIR, tmp_path / BACKUP_DIR]

    # 4. The next normal build restores the previous consistent set.
    monkeypatch.setattr(build_module, "_REMOVE_JOURNAL", build_module._remove_journal)
    monkeypatch.setattr(build_module, "_RMTREE", real_rmtree)
    result = run_build(inp, check=False)
    assert sorted(result.recovered) == sorted(f"restored {r}" for r in replaced_existing)
    assert result.written == []
    assert _snapshot(tmp_path) == before and _no_work_files(tmp_path)


# ---------------------------------------------------------------- S5c extended build report


def test_s5c_report_sections_schema_and_unchanged_manifest_schema(
    upstream: Upstream, tmp_path: Path
) -> None:
    run_build(_inputs(upstream, tmp_path), check=False)
    report = json.loads((tmp_path / BUILD_REPORT).read_bytes())
    manifest = json.loads((tmp_path / BUILD_MANIFEST).read_bytes())
    assert report["schema_version"] == 2 and manifest["schema_version"] == 1  # S5c-D6
    assert report["generator"] == manifest["generator"] == "vrag corpus build (RAG-M3 S5a)"
    assert {"warnings", "duplicates", "sizes", "parse_warnings", "includes"} <= set(report)
    assert set(report["warnings"]) == {
        "oversize",
        "near_empty_pages",
        "section_path_fallback",
        "cross_version_duplicates",
    }
    # The manifest's summary counts are unchanged by S5c.
    assert set(manifest["counts"]) == {
        "by_version",
        "empty_sections",
        "logical_corpus_chunks",
        "pages",
        "release_note_chunks",
        "unresolved",
        "version_file_chunks",
    }
    hist = report["sizes"]["token_histogram"]
    total = sum(h["chunks"] for h in hist["logical_corpus"])
    assert total == report["counts"]["logical_corpus_chunks"]
    assert [sum(h["chunks"] for h in hist[k]) for k in ("version_files", "release_notes")] == [
        report["counts"]["corpus"]["chunks"],
        report["counts"]["release_notes"]["chunks"],
    ]


def test_s5c_report_counts_equal_s5b_validation_on_the_built_corpus(
    upstream: Upstream, tmp_path: Path
) -> None:
    run_build(_inputs(upstream, tmp_path), check=False)
    report = json.loads((tmp_path / BUILD_REPORT).read_bytes())
    checked = validate.ValidationReport()
    validate.add_warnings(validate.load_chunks(tmp_path), checked)
    warnings = report["warnings"]
    assert warnings["oversize"]["by_reason"] == checked.counts["oversize"]
    assert warnings["near_empty_pages"]["count"] == checked.counts["near_empty_pages"]
    assert warnings["section_path_fallback"]["count"] == checked.counts["section_path_fallback"]
    assert report["duplicates"]["count"] == checked.counts["cross_version_duplicates"]
    assert warnings["cross_version_duplicates"]["count"] == report["duplicates"]["count"]


def test_s5c_report_contains_no_corpus_text(upstream: Upstream, tmp_path: Path) -> None:
    run_build(_inputs(upstream, tmp_path), check=False)
    text = (tmp_path / BUILD_REPORT).read_text(encoding="utf-8")
    lines = {
        s
        for lc in validate.load_chunks(tmp_path)
        for ln in lc.chunk.content.splitlines()
        if len(s := ln.strip()) > 25
    }
    assert lines and not [s for s in lines if s in text]


# ---------------------------------------------------------------- S6a fresh checkout


def test_fresh_checkout_with_committed_manifests_but_no_chunk_files(
    upstream: Upstream, tmp_path: Path
) -> None:
    """A fresh clone has the committed manifests and reports but none of the git-ignored
    data/build files: the build must proceed, recreate data/build and leave every committed
    output byte-identical (RAG-M3 S6a, docs/design/RAG-M3_S6_ci.md §2)."""
    inp = _inputs(upstream, tmp_path)
    run_build(inp, check=False)
    committed = [p for p in ALL_OUTPUTS if not p.startswith("data/build/")]
    ignored = [p for p in ALL_OUTPUTS if p.startswith("data/build/")]
    assert committed and ignored
    before = {p: (tmp_path / p).read_bytes() for p in ALL_OUTPUTS}
    shutil.rmtree(tmp_path / "data/build")  # what a fresh clone lacks (git-ignored)
    assert all((tmp_path / p).is_file() for p in committed)
    assert build_module.existing_output_problems(tmp_path) == []
    assert run_build(inp, check=True).problems == [f"{p}: missing" for p in ignored]
    result = run_build(inp, check=False)
    assert sorted(result.written) == sorted(ignored)
    assert sorted(result.unchanged) == sorted(committed)
    assert {p: (tmp_path / p).read_bytes() for p in ALL_OUTPUTS} == before
    assert _no_work_files(tmp_path)
    assert run_build(inp, check=True).problems == []
