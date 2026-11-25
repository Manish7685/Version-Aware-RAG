"""``vrag evidence validate`` (RAG-M4 S1a): read-only validation of the IR-2 evidence records.

Exit codes:
- **0** every check passed (warnings allowed; absent evidence files count as empty);
- **1** at least one record is invalid (load, schema, source, pin or reference error);
- **2** the check could not be completed: a configuration, the corpus manifest or the wheel pins
  cannot be loaded, or a claim's cached source is not available, so it cannot be verified.

Nothing is written. Output names records by ID and never prints span text or observed values.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.store import load_evidence
from version_aware_rag.evidence.verify import build_source_index, validate_evidence
from version_aware_rag.evidence.workflow_commands import add_workflow_commands
from version_aware_rag.probes.pins import DEFAULT_PATH as PINS_PATH
from version_aware_rag.probes.pins import load_pins
from version_aware_rag.settings import Settings

EXIT_USAGE = 2
log = logging.getLogger(__name__)


def add_evidence_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    evidence = sub.add_parser("evidence", help="Evidence records: claims, observations, conflicts.")
    actions = evidence.add_subparsers(dest="evidence_command", required=True, metavar="ACTION")
    validate = actions.add_parser(
        "validate",
        help="Validate the evidence records (read-only; exit 0 valid, 1 invalid, 2 incomplete).",
    )
    validate.add_argument("--root", type=Path, default=Path(), help="Repository root.")
    validate.add_argument(
        "--config", type=Path, default=Path("configs/corpus.toml"), help="Relative to --root."
    )
    validate.add_argument(
        "--chunking-config",
        type=Path,
        default=Path("configs/chunking.toml"),
        help="Relative to --root.",
    )
    validate.add_argument("--pins", type=Path, default=PINS_PATH, help="Relative to --root.")
    validate.add_argument(
        "--evidence-dir", type=Path, default=Path("data/evidence"), help="Relative to --root."
    )
    validate.set_defaults(handler=_cmd_validate)
    add_workflow_commands(actions)  # RAG-M4 S1c


def _incomplete(what: str, exc: Exception) -> int:
    log.error("evidence_validate_failed", extra={"input": what, "error": type(exc).__name__})
    print(f"RESULT: INCOMPLETE ({what} cannot be loaded)")
    return EXIT_USAGE


def _cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    root: Path = args.root
    try:
        cfg, _ = load_config(root / args.config)
        chunking, _ = load_chunking_config(root / args.chunking_config)
    except Exception as exc:
        return _incomplete("configuration", exc)
    try:
        pins = load_pins(root / args.pins)
    except Exception as exc:
        return _incomplete("wheel pins", exc)
    loaded = load_evidence(root / args.evidence_dir)
    index = None
    if loaded.evidence.claims:
        manifest, problems = load_manifest(root / cfg.paths.manifest)
        if manifest is None or problems:
            print(f"corpus manifest unreadable or inconsistent ({len(problems)} problems)")
            print("RESULT: INCOMPLETE (corpus manifest cannot be loaded)")
            return EXIT_USAGE
        index = build_source_index(root, cfg, chunking, manifest)
    report = validate_evidence(loaded.evidence, loaded.problems, index, pins)
    for name in loaded.absent:
        print(f"note: {name} does not exist yet (no records)")
    for f in report.findings:
        print(f"{f.severity.upper():<7} [{f.code}] {f.message}")
    for item in report.incomplete:
        print(f"INCOMPLETE {item}")
    print("counts: " + ", ".join(f"{k}={v}" for k, v in report.counts.items()))
    if report.errors:
        print(f"RESULT: FAILED ({len(report.errors)} errors, {len(report.warnings)} warnings)")
    elif report.incomplete:
        print(f"RESULT: INCOMPLETE ({len(report.incomplete)} checks could not be run)")
    else:
        print(f"RESULT: PASSED ({len(report.warnings)} warnings)")
    return report.exit_code
