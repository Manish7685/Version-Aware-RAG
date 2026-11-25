"""``vrag probes run`` (RAG-M4 S1b): run the IR-3 suite and record ``RuntimeObservation``s.

Exit codes:
- **0** the run is valid and its observations were written;
- **1** the run is invalid: a control differs across the packages or from its declared expected
  result. **Nothing is written**;
- **2** the run could not be carried out or checked (configuration, Git state, venv, install,
  executor, malformed output, or existing evidence that does not validate). **Nothing is
  written**.

Observations record the commit of the probe runner (``runner_commit``), so a run that writes
repository evidence requires a clean working tree. ``--allow-dirty`` is only for review runs that
write outside the repository.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from version_aware_rag.corpus.acquire import GitError, GitRepo
from version_aware_rag.probes.pins import DEFAULT_PATH, load_pins
from version_aware_rag.probes.runner import (
    HarnessError,
    VenvExecutor,
    build_observations,
    platform_tag,
    record_observations,
    run_suite,
)
from version_aware_rag.probes.suite import load_suite
from version_aware_rag.settings import Settings

EXIT_INVALID, EXIT_INCOMPLETE = 1, 2
log = logging.getLogger(__name__)


def add_probe_run_command(probes_sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run = probes_sub.add_parser(
        "run",
        help="Run the IR-3 probe suite on every pinned package and record RuntimeObservations "
        "(exit 0 recorded, 1 invalid run, 2 incomplete; nothing is written unless 0).",
    )
    run.add_argument("--root", type=Path, default=Path(), help="Repository root.")
    run.add_argument("--config", type=Path, default=DEFAULT_PATH, help="Relative to --root.")
    run.add_argument(
        "--evidence-dir", type=Path, default=Path("data/evidence"), help="Relative to --root."
    )
    run.add_argument(
        "--envs-dir", type=Path, default=Path(".probe-envs"), help="Relative to --root."
    )
    run.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow uncommitted changes; only with an --evidence-dir outside the repository.",
    )
    run.set_defaults(handler=_cmd_run)


def _uv() -> list[str]:
    if os.environ.get("UV"):
        return [os.environ["UV"]]
    found = shutil.which("uv")
    return [found] if found else [sys.executable, "-m", "uv"]


def _git(root: Path, *args: str) -> str:
    try:
        return GitRepo(root).run(*args).decode("utf-8")
    except GitError as exc:
        raise HarnessError(f"git {args[0]} failed ({type(exc).__name__})") from None


def _cmd_run(args: argparse.Namespace, settings: Settings) -> int:
    root: Path = args.root.resolve()
    evidence_dir = (root / args.evidence_dir).resolve()
    try:
        config = root / args.config
        pins, suite = load_pins(config), load_suite(config)
        tag = platform_tag()
        commit = _git(root, "rev-parse", "HEAD").strip()
        dirty = bool(_git(root, "status", "--porcelain").strip())
        if dirty and not args.allow_dirty:
            raise HarnessError(
                "the working tree has uncommitted changes (runner_commit would be wrong)"
            )
        if dirty and evidence_dir.is_relative_to(root):
            raise HarnessError("--allow-dirty runs may not write evidence inside the repository")
        executor = VenvExecutor(root, _uv(), root / args.envs_dir, pins.python_version, tag)
        result = run_suite(pins, suite, executor)
        if not result.valid:
            for failure in result.failures:
                print(f"INVALID [{failure.probe_id}] {failure.reason}")
            print(f"RESULT: INVALID RUN ({len(result.failures)} control failures; nothing written)")
            return EXIT_INVALID
        run_at = datetime.now(UTC).replace(microsecond=0)
        observations = build_observations(pins, suite, result, tag, commit, run_at)
        count = record_observations(evidence_dir, observations, pins, tag)
    except HarnessError as exc:
        log.error("probe_run_failed", extra={"error": str(exc)})
        print(f"RESULT: INCOMPLETE ({exc}; nothing written)")
        return EXIT_INCOMPLETE
    except Exception as exc:  # configuration or unexpected failure: type name only
        log.error("probe_run_failed", extra={"error": type(exc).__name__})
        print(f"RESULT: INCOMPLETE ({type(exc).__name__}; nothing written)")
        return EXIT_INCOMPLETE
    controls = ", ".join(p.id for p in suite.controls)
    print(f"controls passed: {controls} identical in {len(result.runs)} packages")
    state = " (dirty)" if dirty else ""
    print(f"RESULT: RECORDED ({count} observations, platform {tag}, commit {commit[:12]}{state})")
    return 0
