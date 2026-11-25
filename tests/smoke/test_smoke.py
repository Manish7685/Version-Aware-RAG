"""Smoke tests: the installed package and its entry points work in a real subprocess.

These are what "a fresh clone passes the smoke test in CI" (RAG-M2 acceptance) runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib.metadata import version

import pytest

import version_aware_rag

pytestmark = pytest.mark.smoke


def test_package_version_matches_metadata() -> None:
    assert version_aware_rag.__version__ == version("version-aware-rag")


def test_python_dash_m_entry_point() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "version_aware_rag", "info", "--json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["version"] == version_aware_rag.__version__


def test_console_script_is_installed() -> None:
    exe = shutil.which("vrag")
    assert exe is not None, "console script 'vrag' not on PATH; is the venv active / uv run used?"
    proc = subprocess.run(
        [exe, "--version"], capture_output=True, text=True, check=False, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == f"vrag {version_aware_rag.__version__}"


def test_logs_go_to_stderr_as_json() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "version_aware_rag",
            "probes",
            "compare",
            "--expected",
            "missing-a.jsonl",
            "--actual",
            "missing-b.jsonl",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    record = json.loads(proc.stderr.strip().splitlines()[-1])
    assert record["event"] == "probe_input_invalid"
    assert record["level"] == "ERROR"
