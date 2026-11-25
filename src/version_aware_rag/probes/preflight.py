"""``vrag probes preflight`` (RAG-M4): metadata-only readiness check for the canonical probe run.

It runs no probe and writes nothing. It reports the Git state (the canonical run refuses a dirty
tree because observations record ``runner_commit``), the evidence destination, the platform tag,
and for each pinned package: the hash-pinned requirements file, the venv, the DuckDB and Python
versions it reports, and whether the hash-verified install is satisfiable offline (``uv pip
install --dry-run --offline``). Exit codes: 0 ready, 1 not ready (blockers listed), 2 configuration
error.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from version_aware_rag.probes.pins import DEFAULT_PATH, load_pins
from version_aware_rag.probes.runner import REQUIREMENTS, HarnessError, platform_tag
from version_aware_rag.settings import Settings


@dataclass
class PackageStatus:
    version: str
    requirements_pinned: bool
    venv_present: bool
    reported_duckdb: str | None
    reported_python: str | None
    offline_install_ok: bool


@dataclass
class Preflight:
    head: str
    dirty_entries: int
    evidence_dir_in_repo: bool
    platform: str
    python_version: str
    packages: list[PackageStatus] = field(default_factory=list)

    def blockers(self) -> list[str]:
        out = []
        if self.dirty_entries:
            out.append(
                f"working tree has {self.dirty_entries} uncommitted entries (a commit is required)"
            )
        for p in self.packages:
            if not p.requirements_pinned:
                out.append(f"{p.version}: requirements file does not pin the {self.platform} wheel")
            if not p.offline_install_ok:
                out.append(f"{p.version}: hash-verified install not satisfiable offline")
            if p.venv_present and p.reported_duckdb not in (None, p.version):
                out.append(f"{p.version}: venv reports duckdb {p.reported_duckdb}")
            if p.venv_present and p.reported_python not in (None, self.python_version):
                out.append(f"{p.version}: venv reports Python {p.reported_python}")
        return out


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False
    )


def gather(
    root: Path, config: Path, evidence_dir: Path, envs_dir: Path, uv: list[str]
) -> Preflight:
    pins = load_pins(root / config)
    tag = platform_tag()
    head = _run(["git", "rev-parse", "--short", "HEAD"], root).stdout.strip()
    dirty = len(
        [
            ln
            for ln in _run(["git", "status", "--porcelain"], root).stdout.splitlines()
            if ln.strip()
        ]
    )
    pf = Preflight(
        head,
        dirty,
        (root / evidence_dir).resolve().is_relative_to(root.resolve()),
        tag,
        pins.python_version,
    )
    for pin in pins.packages:
        req = root / REQUIREMENTS / f"duckdb-{pin.version}.txt"
        wheel = getattr(pin.wheel_sha256, tag)
        text = req.read_text(encoding="utf-8") if req.is_file() else ""
        pinned = text.startswith(f"duckdb=={pin.version} ") and f"--hash=sha256:{wheel}" in text
        env = root / envs_dir / f"duckdb-{pin.version}"
        python = env / ("Scripts/python.exe" if tag == "windows" else "bin/python")
        duck = py = None
        if python.is_file():
            out = _run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "import duckdb, platform; print(duckdb.__version__, platform.python_version())",
                ],
                root,
            )
            if out.returncode == 0 and len(out.stdout.split()) == 2:
                duck, py = out.stdout.split()
        dry = (
            _run(
                [
                    *uv,
                    "pip",
                    "install",
                    "-q",
                    "--dry-run",
                    "--offline",
                    "--require-hashes",
                    "--python",
                    str(python),
                    "-r",
                    str(req),
                ],
                root,
            )
            if python.is_file() and pinned
            else None
        )
        pf.packages.append(
            PackageStatus(
                pin.version, pinned, python.is_file(), duck, py, bool(dry and dry.returncode == 0)
            )
        )
    return pf


def add_probe_preflight_command(
    probes_sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    pre = probes_sub.add_parser(
        "preflight",
        help="Metadata-only readiness check for the canonical probe run "
        "(runs nothing; exit 0 ready, 1 not ready).",
    )
    pre.add_argument("--root", type=Path, default=Path(), help="Repository root.")
    pre.add_argument("--config", type=Path, default=DEFAULT_PATH, help="Relative to --root.")
    pre.add_argument(
        "--evidence-dir", type=Path, default=Path("data/evidence"), help="Relative to --root."
    )
    pre.add_argument(
        "--envs-dir", type=Path, default=Path(".probe-envs"), help="Relative to --root."
    )
    pre.set_defaults(handler=_cmd_preflight)


def _cmd_preflight(args: argparse.Namespace, settings: Settings) -> int:
    from version_aware_rag.probes.commands import _uv  # the run command's uv resolution

    try:
        pf = gather(args.root.resolve(), args.config, args.evidence_dir, args.envs_dir, _uv())
    except (OSError, ValueError, HarnessError) as exc:
        print(f"RESULT: INCOMPLETE (configuration cannot be loaded: {type(exc).__name__})")
        return 2
    print(
        f"git HEAD {pf.head}; uncommitted entries {pf.dirty_entries}; "
        f"evidence dir inside repo {pf.evidence_dir_in_repo}"
    )
    print(f"platform {pf.platform}; pinned Python {pf.python_version}")
    for p in pf.packages:
        print(
            f"  duckdb {p.version}: requirements pinned {p.requirements_pinned}; "
            f"venv {p.venv_present}; reports duckdb {p.reported_duckdb}, "
            f"Python {p.reported_python}; offline install ok {p.offline_install_ok}"
        )
    blockers = pf.blockers()
    for b in blockers:
        print(f"BLOCKER {b}")
    print("RESULT: READY" if not blockers else f"RESULT: NOT READY ({len(blockers)} blockers)")
    return 0 if not blockers else 1
