"""Command-line entry point: ``vrag`` (or ``python -m version_aware_rag``).

Only commands with a real implementation exist:

- ``vrag info``: package, interpreter and (non-secret) settings.
- ``vrag probes compare``: compare behavioural-probe runs (the Linux check, assumption A2).
- ``vrag corpus fetch | manifest | verify``: pinned docs snapshot and manifest (RAG-M3 S1).

Later stages and milestones add their own subcommands. No placeholder commands exist for work
that hasn't been built.
"""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from version_aware_rag import __version__
from version_aware_rag.api.commands import add_serve_command
from version_aware_rag.benchmark.commands import add_benchmark_commands
from version_aware_rag.corpus.commands import add_corpus_commands
from version_aware_rag.evidence.commands import add_evidence_commands
from version_aware_rag.logging_config import configure_logging
from version_aware_rag.probes.commands import add_probe_run_command
from version_aware_rag.probes.preflight import add_probe_preflight_command
from version_aware_rag.repro.probe_check import (
    DuplicateVersionError,
    compare_runs,
    load_runs,
)
from version_aware_rag.retrieval.commands import add_retrieval_commands
from version_aware_rag.settings import LogFormat, LogLevel, Settings, get_settings

log = logging.getLogger("version_aware_rag.cli")

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2  # argparse also uses 2 for usage errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vrag", description="Version-aware RAG: benchmark, retrieval and evaluation tools."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level", choices=[lv.value for lv in LogLevel], help="Override VRAG_LOG_LEVEL."
    )
    parser.add_argument(
        "--log-format", choices=[f.value for f in LogFormat], help="Override VRAG_LOG_FORMAT."
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    info = sub.add_parser("info", help="Show package, interpreter and settings.")
    info.add_argument("--json", action="store_true", help="Machine-readable output.")
    info.set_defaults(handler=_cmd_info)

    probes = sub.add_parser("probes", help="Behavioural probe utilities.")
    probes_sub = probes.add_subparsers(dest="probes_command", required=True, metavar="ACTION")
    compare = probes_sub.add_parser(
        "compare", help="Compare an actual probe run with the expected (RAG-M1) run."
    )
    compare.add_argument("--expected", type=Path, required=True, help="Expected JSONL.")
    compare.add_argument("--actual", type=Path, required=True, help="Actual JSONL to check.")
    compare.set_defaults(handler=_cmd_probes_compare)
    add_probe_run_command(probes_sub)
    add_probe_preflight_command(probes_sub)

    add_corpus_commands(sub)
    add_evidence_commands(sub)
    add_benchmark_commands(sub)
    add_retrieval_commands(sub)
    add_serve_command(sub)
    return parser


def _cmd_info(args: argparse.Namespace, settings: Settings) -> int:
    info = {
        "package": "version-aware-rag",
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "settings": settings.model_dump(mode="json"),
    }
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        for key, value in info.items():
            print(f"{key}: {value}")
    return EXIT_OK


def _cmd_probes_compare(args: argparse.Namespace, settings: Settings) -> int:
    try:
        expected = load_runs(args.expected)
        actual = load_runs(args.actual)
    except (OSError, ValueError, ValidationError, DuplicateVersionError) as exc:
        # ValueError covers json.JSONDecodeError.
        log.error("probe_input_invalid", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    report = compare_runs(expected, actual)
    print(report.render())
    log.info(
        "probe_comparison_done",
        extra={
            "ok": report.ok,
            "versions": report.compared_versions,
            "differences": len(report.differences),
        },
    )
    return EXIT_OK if report.ok else EXIT_FAILURE


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, configure logging, dispatch. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"invalid configuration:\n{exc}", file=sys.stderr)
        return EXIT_USAGE
    # model_copy does not re-validate, so convert to the enum types explicitly.
    update: dict[str, LogLevel | LogFormat] = {}
    if args.log_level is not None:
        update["log_level"] = LogLevel(args.log_level)
    if args.log_format is not None:
        update["log_format"] = LogFormat(args.log_format)
    if update:
        settings = settings.model_copy(update=update)
    configure_logging(settings.log_level, settings.log_format)
    exit_code: int = args.handler(args, settings)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
