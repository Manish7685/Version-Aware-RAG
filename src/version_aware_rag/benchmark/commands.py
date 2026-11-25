"""``vrag benchmark inventory validate`` (RAG-M4 S2): read-only check of the pilot change inventory.
The S3 drafting commands are in ``draft_commands.py``.

Exit codes: **0** valid; **1** invalid (schema, canonical form, unknown source or probe, a line
range outside the source, a restricted source quoted, a corpus boundary without a pilot
candidate); **2** incomplete (a configuration, the corpus manifest, the probe suite, the inventory
file or a cached source cannot be loaded). Nothing is written. Output names records by id only.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from version_aware_rag.benchmark.draft_commands import add_draft_commands
from version_aware_rag.benchmark.inventory import (
    DEFAULT_PATH,
    InventoryReport,
    SuiteFacts,
    load_inventory,
    validate_inventory,
)
from version_aware_rag.benchmark.pilot_commands import add_pilot_commands
from version_aware_rag.corpus.config import load_chunking_config, load_config
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.evidence.verify import build_source_index
from version_aware_rag.probes.pins import DEFAULT_PATH as PROBES_PATH
from version_aware_rag.probes.pins import load_pins
from version_aware_rag.probes.suite import load_suite
from version_aware_rag.settings import Settings

log = logging.getLogger(__name__)


def add_benchmark_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    benchmark = sub.add_parser("benchmark", help="Benchmark construction (RAG-M4).")
    actions = benchmark.add_subparsers(dest="benchmark_command", required=True, metavar="ACTION")
    inventory = actions.add_parser("inventory", help="The pilot change inventory.")
    inv_actions = inventory.add_subparsers(
        dest="inventory_command", required=True, metavar="ACTION"
    )
    validate = inv_actions.add_parser(
        "validate",
        help="Validate the change inventory (read-only; exit 0 valid, 1 invalid, 2 incomplete).",
    )
    validate.add_argument("--root", type=Path, default=Path(), help="Repository root.")
    validate.add_argument(
        "--inventory", type=Path, default=DEFAULT_PATH, help="Relative to --root."
    )
    validate.add_argument(
        "--config", type=Path, default=Path("configs/corpus.toml"), help="Relative to --root."
    )
    validate.add_argument(
        "--chunking-config",
        type=Path,
        default=Path("configs/chunking.toml"),
        help="Relative to --root.",
    )
    validate.add_argument("--probes", type=Path, default=PROBES_PATH, help="Relative to --root.")
    validate.set_defaults(handler=_cmd_validate)
    add_draft_commands(actions)
    add_pilot_commands(actions)


def _cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    root: Path = args.root
    try:
        cfg, _ = load_config(root / args.config)
        chunking, _ = load_chunking_config(root / args.chunking_config)
        suite = load_suite(root / args.probes)
        pins = load_pins(root / args.probes)
    except Exception as exc:
        log.error("inventory_validate_failed", extra={"error": type(exc).__name__})
        what = type(exc).__name__
        print(f"RESULT: INCOMPLETE (configuration or probe suite cannot be loaded: {what})")
        return 2
    manifest, problems = load_manifest(root / cfg.paths.manifest)
    if manifest is None or problems:
        print(f"RESULT: INCOMPLETE (corpus manifest cannot be loaded: {len(problems)} problems)")
        return 2
    report = InventoryReport()
    load_inventory(root / args.inventory, report)
    if not report.incomplete:  # always: an empty or partial inventory fails the coverage check
        index = build_source_index(root, cfg, chunking, manifest)
        facts = SuiteFacts(
            probe_ids=frozenset(p.id for p in suite.probes),
            rag_m1_probe_ids=frozenset(p.id for p in suite.probes if p.rag_m1_name),
            packages={pkg.applies_to_doc_version: pkg.version for pkg in pins.packages},
        )
        validate_inventory(report, index, facts)
    for error in report.errors:
        print(f"ERROR   {error}")
    for item in report.incomplete:
        print(f"INCOMPLETE {item}")
    by_status: dict[str, int] = {}
    for r in report.records:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    print("records: " + (", ".join(f"{k}={v}" for k, v in sorted(by_status.items())) or "0"))
    print(
        "pilot candidates per boundary: "
        + ", ".join(f"{k} {v}" for k, v in report.coverage().items())
    )
    if report.errors:
        print(f"RESULT: FAILED ({len(report.errors)} errors)")
    elif report.incomplete:
        print(f"RESULT: INCOMPLETE ({len(report.incomplete)} checks could not be run)")
    else:
        print(f"RESULT: PASSED ({len(report.records)} changes)")
    return report.exit_code
