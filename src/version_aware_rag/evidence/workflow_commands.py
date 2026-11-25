"""Evidence workflow commands (RAG-M4 S1c), registered under ``vrag evidence``:

- ``claim add``: a documentation claim cut from the verified cached source at given offsets
  (offsets, never text, so restricted text never appears in a command line);
- ``observations ingest FILE``: recorded ``RuntimeObservation`` JSONL (for example from
  ``vrag probes run`` on another platform);
- ``conflict suggest``: compare a claim's ``claimed_value`` with observations; a disagreement
  becomes a **suspected** conflict (``detected_by = script``);
- ``conflict add``: the owner's own **suspected** conflict;
- ``conflict review``: the owner confirms or dismisses a suspected conflict.

**Owner identity is a workflow convention, not authentication.** ``--reviewer`` must be the
identity label ``owner`` (``models.OWNER``); the CLI cannot verify who runs it. What is enforced:
no automated path (``suggest``, ``add``, a script or a Gemini draft) can produce ``confirmed`` or
``dismissed``; a review needs an explicit ``conflict review`` with reviewer ``owner``; and every
review is recorded in ``reviewed_by`` and ``reviewed_at``. The audit trail is the Git history of
the committed evidence files (D14: "Only the owner can set confirmed or dismissed").

Exit codes: **0** done (or, for ``suggest``, no disagreement and nothing written); **1** the step
is refused (invalid or unverifiable result, not the owner, duplicate); **2** an input cannot be
loaded (configuration, corpus manifest, wheel pins, input file). Nothing is written unless the
exit code is 0. Output names records by ID; it never prints span text or observed values.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from version_aware_rag.corpus.acquire import GitError, GitRepo
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.models import RuntimeObservation
from version_aware_rag.evidence.store import EvidenceSet, load_evidence
from version_aware_rag.evidence.verify import build_source_index
from version_aware_rag.evidence.workflow import (
    Index,
    WorkflowError,
    add_claim,
    add_conflict,
    apply_review,
    create_doc_claim,
    ingest_observations,
    manual_conflict,
    suggest_conflict,
    update_evidence,
)
from version_aware_rag.probes.pins import DEFAULT_PATH as PINS_PATH
from version_aware_rag.probes.pins import ProbePins, load_pins
from version_aware_rag.settings import Settings

EXIT_REFUSED, EXIT_INPUT = 1, 2
log = logging.getLogger(__name__)


class InputUnavailable(Exception):
    """A required input cannot be loaded."""


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=Path(), help="Repository root.")
    p.add_argument(
        "--config", type=Path, default=Path("configs/corpus.toml"), help="Relative to --root."
    )
    p.add_argument(
        "--chunking-config",
        type=Path,
        default=Path("configs/chunking.toml"),
        help="Relative to --root.",
    )
    p.add_argument("--pins", type=Path, default=PINS_PATH, help="Relative to --root.")
    p.add_argument(
        "--evidence-dir", type=Path, default=Path("data/evidence"), help="Relative to --root."
    )


def add_workflow_commands(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    claim = actions.add_parser("claim", help="Documentation claims.")
    claim_actions = claim.add_subparsers(dest="claim_command", required=True, metavar="ACTION")
    add = claim_actions.add_parser("add", help="Add a claim cut from the verified cached source.")
    _common(add)
    add.add_argument(
        "--source-kind",
        required=True,
        choices=["docs_page", "release_post", "migration_guide", "github_release"],
    )
    add.add_argument("--source-path", required=True, help="Repository path, or release tag.")
    add.add_argument("--start", type=int, required=True, help="span_char_start (code points).")
    add.add_argument("--end", type=int, required=True, help="span_char_end (exclusive).")
    add.add_argument("--summary", required=True, help="claim_summary: one normalised sentence.")
    add.add_argument("--url", required=True, help="source_url (https).")
    add.add_argument("--heading", action="append", default=[], help="heading_path, in order.")
    add.add_argument("--claimed-value", default=None)
    add.add_argument("--introduced-in", default=None, help="Change sources only: X.Y.Z.")
    add.add_argument("--created-by", default="owner", choices=["owner", "script", "gemini_draft"])
    add.set_defaults(handler=_cmd_claim_add)

    obs = actions.add_parser("observations", help="Runtime observations.")
    obs_actions = obs.add_subparsers(dest="observations_command", required=True, metavar="ACTION")
    ingest = obs_actions.add_parser("ingest", help="Ingest recorded RuntimeObservation JSONL.")
    _common(ingest)
    ingest.add_argument("file", type=Path, help="JSONL of RuntimeObservation records.")
    ingest.set_defaults(handler=_cmd_ingest)

    conflict = actions.add_parser("conflict", help="Evidence conflicts (always suspected first).")
    c_actions = conflict.add_subparsers(dest="conflict_command", required=True, metavar="ACTION")
    suggest = c_actions.add_parser("suggest", help="Compare claimed_value with observations.")
    _common(suggest)
    suggest.add_argument("--claim", required=True)
    suggest.add_argument("--obs", action="append", required=True, help="An obs_id (repeatable).")
    suggest.set_defaults(handler=_cmd_suggest)
    manual = c_actions.add_parser("add", help="The owner's own suspected conflict.")
    _common(manual)
    manual.add_argument("--claim", required=True)
    manual.add_argument("--obs", action="append", required=True, help="An obs_id (repeatable).")
    manual.add_argument("--description", required=True, help="What disagrees (no quoted source).")
    manual.set_defaults(handler=_cmd_conflict_add)
    review = c_actions.add_parser("review", help="Confirm or dismiss a suspected conflict.")
    _common(review)
    review.add_argument("conflict_id")
    review.add_argument("--status", required=True, choices=["confirmed", "dismissed"])
    review.add_argument(
        "--reviewer",
        required=True,
        help="Reviewer identity label; must be 'owner'. A workflow convention recorded in "
        "reviewed_by, not authentication: the CLI cannot verify who runs it.",
    )
    review.add_argument("--note", default=None, help="resolution_note (no quoted source).")
    review.set_defaults(handler=_cmd_review)


def _inputs(args: argparse.Namespace) -> tuple[Path, Index, ProbePins]:
    root: Path = args.root
    try:
        cfg, _ = load_config(root / args.config)
        chunking, _ = load_chunking_config(root / args.chunking_config)
        pins = load_pins(root / args.pins)
    except Exception as exc:
        raise InputUnavailable(f"configuration or pins ({type(exc).__name__})") from None
    manifest, problems = load_manifest(root / cfg.paths.manifest)
    if manifest is None or problems:
        raise InputUnavailable(f"corpus manifest ({len(problems)} problems)")
    return root / args.evidence_dir, build_source_index(root, cfg, chunking, manifest), pins


def _run(args: argparse.Namespace, step: Any) -> int:
    try:
        evidence_dir, index, pins = _inputs(args)
        message = step(evidence_dir, index, pins)
    except InputUnavailable as exc:
        print(f"RESULT: INCOMPLETE ({exc} cannot be loaded; nothing written)")
        return EXIT_INPUT
    except WorkflowError as exc:
        log.error("evidence_workflow_refused", extra={"error": str(exc)})
        print(f"RESULT: REFUSED ({exc}; nothing written)")
        return EXIT_REFUSED
    print(f"RESULT: {message}")
    return 0


def _cmd_claim_add(args: argparse.Namespace, settings: Settings) -> int:
    def step(evidence_dir: Path, index: Index, pins: ProbePins) -> str:
        claim = create_doc_claim(
            index,
            source_kind=args.source_kind,
            source_path=args.source_path,
            span_char_start=args.start,
            span_char_end=args.end,
            claim_summary=args.summary,
            source_url=args.url,
            heading_path=args.heading,
            claimed_value=args.claimed_value,
            introduced_in=args.introduced_in,
            created_by=args.created_by,
        )
        update_evidence(evidence_dir, lambda ev: add_claim(ev, claim), index, pins)
        where = (
            "local sidecar"
            if claim.source_kind in ("release_post", "github_release")
            else "committed file"
        )
        return (
            f"CLAIM ADDED {claim.claim_id} ({claim.verification_level}; span text in the {where})"
        )

    return _run(args, step)


def _read_observations(path: Path) -> list[RuntimeObservation]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise InputUnavailable(f"{path.name} ({type(exc).__name__})") from None
    records = []
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(RuntimeObservation.model_validate_json(line))
        except ValidationError as exc:
            types = sorted({e["type"] for e in exc.errors(include_url=False, include_input=False)})
            raise WorkflowError(
                f"{path.name}:{n}: invalid observation ({', '.join(types)})"
            ) from None
    return records


def _cmd_ingest(args: argparse.Namespace, settings: Settings) -> int:
    root: Path = args.root

    def commit_exists(sha: str) -> bool:
        try:
            GitRepo(root).run("cat-file", "-e", f"{sha}^{{commit}}")
            return True
        except GitError:
            return False

    def step(evidence_dir: Path, index: Index, pins: ProbePins) -> str:
        incoming = _read_observations(root / args.file)
        if not incoming:
            raise WorkflowError(f"{args.file.name} has no observations")
        result = update_evidence(
            evidence_dir, lambda ev: ingest_observations(ev, incoming, commit_exists), index, pins
        )
        total = len(result.evidence.observations)
        return f"OBSERVATIONS INGESTED ({len(incoming)} read; {total} in total)"

    return _run(args, step)


def _find(evidence: EvidenceSet, claim_id: str, obs_ids: list[str]) -> tuple[Any, list[Any]]:
    claim = next((c for c in evidence.claims if c.claim_id == claim_id), None)
    if claim is None:
        raise WorkflowError(f"claim {claim_id} does not exist")
    by_id = {o.obs_id: o for o in evidence.observations}
    missing = [o for o in obs_ids if o not in by_id]
    if missing:
        raise WorkflowError(f"observation {missing[0]} does not exist")
    return claim, [by_id[o] for o in obs_ids]


def _cmd_suggest(args: argparse.Namespace, settings: Settings) -> int:
    def step(evidence_dir: Path, index: Index, pins: ProbePins) -> str:
        loaded = load_evidence(evidence_dir)
        if loaded.problems:
            raise WorkflowError(f"the existing evidence has {len(loaded.problems)} problems")
        claim, observations = _find(loaded.evidence, args.claim, args.obs)
        conflict = suggest_conflict(claim, observations)
        if conflict is None:
            return "NO DISAGREEMENT (claimed_value equals every observed value; nothing written)"
        update_evidence(evidence_dir, lambda ev: add_conflict(ev, conflict), index, pins)
        n = len(conflict.obs_ids)
        return f"SUSPECTED CONFLICT {conflict.conflict_id} ({n} observations; owner review needed)"

    return _run(args, step)


def _cmd_conflict_add(args: argparse.Namespace, settings: Settings) -> int:
    def step(evidence_dir: Path, index: Index, pins: ProbePins) -> str:
        loaded = load_evidence(evidence_dir)
        if loaded.problems:
            raise WorkflowError(f"the existing evidence has {len(loaded.problems)} problems")
        _find(loaded.evidence, args.claim, args.obs)
        conflict = manual_conflict(loaded.evidence, args.claim, args.obs, args.description)
        update_evidence(evidence_dir, lambda ev: add_conflict(ev, conflict), index, pins)
        return f"SUSPECTED CONFLICT {conflict.conflict_id} (detected by owner; owner review needed)"

    return _run(args, step)


def _cmd_review(args: argparse.Namespace, settings: Settings) -> int:
    def step(evidence_dir: Path, index: Index, pins: ProbePins) -> str:
        update_evidence(
            evidence_dir,
            lambda ev: apply_review(
                ev,
                args.conflict_id,
                status=args.status,
                reviewer=args.reviewer,
                resolution_note=args.note,
            ),
            index,
            pins,
        )
        return f"CONFLICT {args.status.upper()} {args.conflict_id} (by owner)"

    return _run(args, step)
