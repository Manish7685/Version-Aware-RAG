"""Safety properties of the `corpus-rebuild` CI job (RAG-M3 S6a, docs/design/RAG-M3_S6_ci.md §3).

Stdlib text checks only (no YAML dependency). The job's embedded JUnit check is extracted from
the workflow and run against sample JUnit files, so the code that gates CI is itself tested.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import get_args

import pytest

from version_aware_rag.cli import main
from version_aware_rag.corpus import commands, validate
from version_aware_rag.corpus.chunk import ChunkSourceKind, SourceType
from version_aware_rag.corpus.manifest import load_manifest
from version_aware_rag.corpus.parse import BlockKind

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github/workflows/ci.yml"
EVIDENCE = ["evidence/outputs.sha256", "evidence/validate.txt", "evidence/corpus-tests.txt"]
ALLOWED_ACTIONS = {"actions/checkout", "astral-sh/setup-uv", "actions/upload-artifact"}


def _text() -> str:
    return CI.read_text(encoding="utf-8")


def _job(name: str) -> str:
    lines = _text().splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.fullmatch(r"  [\w-]+:\s*", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _steps(job: str) -> list[str]:
    parts = re.split(r"\n(?=      - )", job)
    return [p for p in parts[1:] if p.startswith("      - ")]


def test_existing_jobs_and_triggers_are_preserved() -> None:
    text = _text()
    jobs = re.findall(r"^  ([\w-]+):\s*$", text.split("\njobs:\n", 1)[1], flags=re.MULTILINE)
    assert jobs == [
        "checks",
        "pre-commit",
        "linux-probe-check",
        "secret-scan",
        "corpus-rebuild",
        "eval-gate",
    ]
    assert "on:\n  push:\n    branches: [main]\n  pull_request:\n  workflow_dispatch:\n" in text


def test_permissions_stay_contents_read_only() -> None:
    text = _text()
    # The workflow default plus exactly one job-level block: the secret scan, which needs
    # pull-requests: read for gitleaks-action to list a pull request's commits.
    assert re.findall(r"^[ \t]*permissions:.*$", text, flags=re.MULTILINE) == [
        "permissions:",
        "    permissions:",
    ]
    assert "\npermissions:\n  contents: read\n\n" in text
    secret_scan = _job("secret-scan")
    assert (
        "\n    permissions:\n      contents: read\n      pull-requests: read\n    steps:"
        in secret_scan
    )
    for name in ("checks", "pre-commit", "linux-probe-check", "corpus-rebuild", "eval-gate"):
        assert "permissions:" not in _job(name)  # every other job inherits contents: read
    assert not re.search(r":\s*write\b", text)


def test_only_the_builtin_token_and_only_on_release_fetch_steps() -> None:
    text = _text()
    assert set(re.findall(r"secrets\.\w+", text)) == {"secrets.GITHUB_TOKEN"}
    job = _job("corpus-rebuild")
    token_lines = [ln.strip() for ln in job.splitlines() if re.search(r"GITHUB_TOKEN\s*:", ln)]
    assert token_lines == ["GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}"] * 2
    with_token = [s for s in _steps(job) if "secrets.GITHUB_TOKEN" in s]
    assert len(with_token) == 2
    assert all("run: uv run vrag corpus releases fetch" in s for s in with_token)
    assert not re.search(r"^\s*token\s*:", job, flags=re.MULTILINE)  # no token input to actions


def test_actions_are_pinned_and_data_is_never_cached() -> None:
    job = _job("corpus-rebuild")
    uses = re.findall(r"uses:\s*(\S+)", job)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)
    assert {u.split("@")[0] for u in uses} <= ALLOWED_ACTIONS
    assert "actions/cache" not in job
    assert not re.search(r"^\s*path:\s*\S*data", job, flags=re.MULTILINE)


def test_artifacts_are_exactly_the_three_evidence_files() -> None:
    uploads = [s for s in _steps(_job("corpus-rebuild")) if "actions/upload-artifact@" in s]
    assert len(uploads) == 1
    block = uploads[0].split("path: |\n", 1)[1]
    paths = []
    for line in block.splitlines():
        if not line.startswith(" " * 12):
            break
        paths.append(line.strip())
    assert paths == EVIDENCE
    assert not any(ch in p for p in paths for ch in "*?[")


def test_two_independent_checkouts_fetches_builds_and_comparison() -> None:
    job = _job("corpus-rebuild")
    steps = _steps(job)
    checkouts = [s for s in steps if "actions/checkout@" in s]
    assert [re.search(r"path:\s*(\S+)", s).group(1) for s in checkouts] == ["a", "b"]  # type: ignore[union-attr]
    assert all("persist-credentials: false" in s for s in checkouts)
    for side in ("a", "b"):
        in_side = [s for s in steps if f"working-directory: {side}\n" in s]
        runs = "\n".join(in_side)
        for command in (
            "uv run vrag corpus fetch",
            "uv run vrag corpus releases fetch",
            "uv run vrag corpus manifest --check",
            "uv run vrag corpus verify",
            "uv run vrag corpus releases verify",
            "uv run vrag corpus build\n",
            "git diff --exit-code",
        ):
            assert command in runs + "\n", (side, command)
    compare = next(s for s in steps if "Compare A and B" in s)
    assert "from version_aware_rag.corpus.build import ALL_OUTPUTS" in compare
    assert "::error::missing output" in compare and "diff -u a.sha256 b.sha256" in compare


def test_checkout_a_has_full_history_and_no_corpus_test_reads_earlier_commits() -> None:
    """actions/checkout is shallow (depth 1) by default. Checkout A, where the corpus tests run,
    keeps the full history; checkout B only builds and compares and keeps the default. No
    integration test may read an earlier commit: the S2-manifest check (S6d CI finding) compares
    pinned digests instead of `git show 7e4125f:...`, so the corpus tests also pass on a shallow or
    single-snapshot checkout."""
    history_test = (ROOT / "tests/integration/test_real_corpus.py").read_text(encoding="utf-8")
    assert "S2_MANIFEST_DIGESTS" in history_test
    for path in sorted((ROOT / "tests/integration").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""run\(\s*["'](show|log|cat-file|rev-list)["']""", text), path.name
    steps = _steps(_job("corpus-rebuild"))
    checkout_a, checkout_b = (s for s in steps if "actions/checkout@" in s)
    assert re.findall(r"^\s*fetch-depth:\s*(\S+)\s*$", checkout_a, flags=re.MULTILINE) == ["0"]
    assert "fetch-depth" not in checkout_b
    corpus_tests = next(s for s in steps if '"A: corpus tests' in s)
    assert "working-directory: a\n" in corpus_tests  # the tests run in the full-history checkout


def test_check_upstream_is_not_part_of_ci() -> None:
    assert "check-upstream" not in _text()


def test_expected_corpus_test_count_matches_the_collected_tests() -> None:
    expected = int(re.search(r'EXPECTED_CORPUS_TESTS:\s*"(\d+)"', _text()).group(1))  # type: ignore[union-attr]
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "corpus",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert len([ln for ln in out.splitlines() if re.match(r"^tests/\S+::\S", ln)]) == expected


# ---------------------------------------------------------------- the embedded JUnit check


def _junit_check() -> str:
    text = _text()
    start = text.rindex("\n", 0, text.index("# BEGIN corpus-junit-check")) + 1  # whole line
    end = text.index("# END corpus-junit-check")
    return textwrap.dedent(text[start:end])


def _junit(cases: list[str], **totals: int) -> str:
    attrs = {"tests": len(cases), "skipped": 0, "failures": 0, "errors": 0, **totals}
    head = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    body = "".join(cases)
    return f'<testsuites><testsuite name="pytest" {head}>{body}</testsuite></testsuites>'


OK_CASE = '<testcase classname="t" name="n"/>'


def _run(
    tmp_path: Path, collected: int, junit: str, expected: int
) -> subprocess.CompletedProcess[str]:
    collect = tmp_path / "collect.txt"
    ids = [f"tests/integration/test_x.py::test_{i}" for i in range(collected)]
    collect.write_text("\n".join([*ids, "", f"{collected}/99 tests collected"]) + "\n", "utf-8")
    xml = tmp_path / "junit.xml"
    xml.write_text(junit, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", "-c", _junit_check(), str(collect), str(xml), str(expected)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_junit_check_passes_only_when_all_counts_agree(tmp_path: Path) -> None:
    result = _run(tmp_path, 3, _junit([OK_CASE] * 3), 3)
    assert result.returncode == 0, result.stdout
    assert (
        "collected=3 executed=3 cases=3 skipped=0 failures=0 errors=0 expected=3" in result.stdout
    )


@pytest.mark.parametrize(
    ("collected", "cases", "totals", "message"),
    [
        (
            3,
            [OK_CASE, OK_CASE, '<testcase name="s"><skipped/></testcase>'],
            {"skipped": 1},
            "1 skipped",
        ),
        (
            3,
            [OK_CASE, OK_CASE, '<testcase name="f"><failure/></testcase>'],
            {"failures": 1},
            "1 failures",
        ),
        (
            3,
            [OK_CASE, OK_CASE, '<testcase name="e"><error/></testcase>'],
            {"errors": 1},
            "1 errors",
        ),
        (3, [OK_CASE, OK_CASE], {}, "executed 2"),
        (2, [OK_CASE] * 3, {}, "collected 2 corpus tests"),
        (3, [OK_CASE, OK_CASE, '<testcase name="s"><skipped/></testcase>'], {}, "with <skipped>"),
    ],
)
def test_junit_check_fails_on_skips_failures_errors_and_count_mismatches(
    tmp_path: Path, collected: int, cases: list[str], totals: dict[str, int], message: str
) -> None:
    result = _run(tmp_path, collected, _junit(cases, **totals), 3)
    assert result.returncode == 1
    assert "::error::corpus tests:" in result.stdout and message in result.stdout


# ---------------------------------------------------------------- the validate-log sanitizer

# Fixture headings standing in for source-derived text (not real corpus text).
SENSITIVE = (
    "Secret Fixture Heading",
    'Owner\'s "Quoted" Fixture Heading',
    "Back\\slash Fixture Heading",
    "Merged Fixture Section",
)
R8A_SPLIT = 'f"{where}: API entry {head.section_path} split over units"'
R8A_MERGED = 'f"{lc.where}: merged chunk contains API entry {merged}"'
WITHHELD = "<message withheld by the CI sanitizer: unrecognised format>"
SAMPLE = {  # one valid value per typed field
    "WHERE": "1.2.jsonl:7",
    "FILE": "1.2.jsonl",
    "PATH": "docs/1.2/sql/functions/list.md",
    "SPAN": "docs/1.2/sql/functions/list.md:3-9",
    "OUT": "data/build/build_report.json",
    "REPO": "docs/design/RAG-M3_S6_ci.md",
    "JSON": "build_manifest.json",
    "EXC": "ValueError",
    "CHUNK": "c-0123456789abcdef0123",
    "VER": "1.2",
    "SEMVER": "1.2.1",
    "SEMVER_OR_NONE": "None",
    "TAG": "v1.2.1",
    "INT": "3",
    "KIND": "docs_page",
    "STYPE": "changelog",
    "BLOCK": "table",
    "INPUT": "parser_version",
    "OVERSIZE": "{'code_block': 2, 'list_item': 1}",
    "NEAR": "docs/1.2/a.md (1.2, 3 words); docs/1.3/b.md (1.3, 4 words)",
    "REDACT": "free text that must never be copied",
}


def _snippet(marker: str) -> str:
    text = _text()
    start = text.rindex("\n", 0, text.index(f"# BEGIN {marker}")) + 1  # whole line
    end = text.index(f"# END {marker}")
    return textwrap.dedent(text[start:end])


def _sanitize(tmp_path: Path, raw: str) -> tuple[subprocess.CompletedProcess[str], str]:
    raw_file, out_file = tmp_path / "validate-raw.txt", tmp_path / "validate.txt"
    raw_file.write_text(raw, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _snippet("validate-log-sanitizer"),
            str(raw_file),
            str(out_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result, out_file.read_text(encoding="utf-8") if out_file.exists() else ""


def _sanitizer_tables(tmp_path: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    """FIELDS and TEMPLATES as defined in the workflow (the snippet runs on an empty log)."""
    raw, out = tmp_path / "empty.txt", tmp_path / "empty-out.txt"
    raw.write_text("", encoding="utf-8")
    namespace: dict[str, object] = {"__name__": "sanitizer"}
    argv = sys.argv
    try:
        sys.argv = ["sanitizer", str(raw), str(out)]
        exec(compile(_snippet("validate-log-sanitizer"), "ci.yml", "exec"), namespace)  # noqa: S102 (our own workflow code)
    finally:
        sys.argv = argv
    return namespace["FIELDS"], namespace["TEMPLATES"]  # type: ignore[return-value]


def test_validate_step_never_prints_or_uploads_raw_output_and_keeps_its_exit_status() -> None:
    step = next(s for s in _steps(_job("corpus-rebuild")) if '"A: validate' in s)
    assert "uv run vrag corpus validate > ../validate-raw.txt 2>&1 || status=$?" in step
    assert "../validate-raw.txt ../evidence/validate.txt <<'PY'" in step
    assert step.rstrip().endswith('exit "$status"')
    assert "tee" not in step and "cat ../validate-raw.txt" not in step
    assert "validate-raw" not in "\n".join(EVIDENCE)


def test_sensitive_section_headings_never_reach_the_uploaded_validation_log(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Privacy regression: the real `vrag corpus validate` output for findings built from the
    two validate.py templates that interpolate section paths, passed through the workflow's
    sanitizer, contains no heading text but keeps rule codes, locations, ids and counts."""
    source = (ROOT / "src/version_aware_rag/corpus/validate.py").read_text(encoding="utf-8")
    assert R8A_SPLIT in source and R8A_MERGED in source  # the templates this test mirrors
    section_path = SENSITIVE[:3]
    merged = (SENSITIVE[3], SENSITIVE[0])
    where_split, where_merged = "1.2.jsonl:17", "2.0.jsonl:4242"
    oversize = {"code_block": 164, "list_item": 3, "api_entry_block": 4}
    report = validate.ValidationReport(
        findings=[
            validate.Finding(
                "error", "R8a", f"{where_split}: API entry {section_path} split over units"
            ),
            validate.Finding(
                "error", "R8a", f"{where_merged}: merged chunk contains API entry {merged}"
            ),
            validate.Finding(
                "error", "R12a", "2.0.jsonl:9: chunk_id c-0123456789abcdef0123 also at 1.5.jsonl:3"
            ),
            validate.Finding("warning", "R11", "6 near-empty pages: docs/1.2/a.md (1.2, 19 words)"),
            validate.Finding(
                "warning", "W-oversize", f"{sum(oversize.values())} oversize chunks {oversize}"
            ),
        ],
        counts={"chunks": 31680, "by_version": {"1.2": 4768}, "leak_scan": {"files_scanned": 110}},
    )
    monkeypatch.setattr(commands, "validate_corpus", lambda inputs: report)
    assert main(["corpus", "validate", "--root", str(ROOT)]) == 1  # the failure is reported
    raw = capsys.readouterr().out
    assert all(h in raw or repr(h)[1:-1] in raw for h in SENSITIVE)  # the raw output leaks them
    result, cleaned = _sanitize(tmp_path, raw)
    assert result.returncode == 0, result.stderr
    for heading in SENSITIVE:
        for fragment in (heading, repr(heading)[1:-1], *heading.split()[:1]):
            if len(fragment) > 4:
                assert fragment not in cleaned, fragment
    assert f"ERROR   [R8a] {where_split}: API entry <redacted> split over units" in cleaned
    assert f"ERROR   [R8a] {where_merged}: merged chunk contains API entry <redacted>" in cleaned
    assert (
        "ERROR   [R12a] 2.0.jsonl:9: chunk_id c-0123456789abcdef0123 also at 1.5.jsonl:3" in cleaned
    )
    assert "WARNING [R11] 6 near-empty pages: docs/1.2/a.md (1.2, 19 words)" in cleaned
    assert f"WARNING [W-oversize] 171 oversize chunks {oversize}" in cleaned
    counts = '{"by_version": {"1.2": 4768}, "chunks": 31680, "leak_scan": {"files_scanned": 110}}'
    assert f"counts: {counts}" in cleaned
    assert "RESULT: FAILED (3 errors, 2 warnings)" in cleaned
    assert "7 lines, 2 fields redacted, 0 lines withheld" in cleaned


@pytest.mark.parametrize(
    ("raw_line", "kept_prefix", "expected_tail"),
    [
        # free-form slot of a known template: the payload is replaced, never copied
        (
            "ERROR   [R8a] 1.2.jsonl:17: API entry Secret Fixture Heading split over units",
            "ERROR   [R8a] ",
            "1.2.jsonl:17: API entry <redacted> split over units",
        ),
        # heading in a typed slot: no template matches, so the whole message is withheld
        (
            "ERROR   [R2a] 1.2.jsonl:3: source_path Secret Fixture Heading is not a manifest file",
            "ERROR   [R2a] ",
            WITHHELD,
        ),
        # heading appended to an otherwise valid message
        (
            "WARNING [R3w] 22 chunks use the section-path fallback Secret Fixture Heading",
            "WARNING [R3w] ",
            WITHHELD,
        ),
        # heading spliced into the fixed template text
        (
            "ERROR   [R3] 1.2.jsonl:5: section_path Secret Fixture Heading not rooted",
            "ERROR   [R3] ",
            WITHHELD,
        ),
        # heading in a list field of a warning
        (
            "WARNING [R11] 6 near-empty pages: Secret Fixture Heading (1.2, 3 words)",
            "WARNING [R11] ",
            WITHHELD,
        ),
        # an unknown rule
        ("ERROR   [R99] Secret Fixture Heading", "ERROR   [R99] ", WITHHELD),
        # a valid template of a different rule
        (
            "ERROR   [R3w] 1.2.jsonl:5: section_path empty or not rooted at the page title",
            "ERROR   [R3w] ",
            WITHHELD,
        ),
    ],
)
def test_unquoted_sensitive_text_never_survives_and_rule_codes_do(
    tmp_path: Path, raw_line: str, kept_prefix: str, expected_tail: str
) -> None:
    result, cleaned = _sanitize(tmp_path, raw_line + "\nRESULT: FAILED (1 errors, 0 warnings)\n")
    assert result.returncode == 0, result.stderr
    assert "Secret" not in cleaned and "Fixture" not in cleaned and "Heading" not in cleaned
    assert f"{kept_prefix}{expected_tail}\n" in cleaned
    assert "RESULT: FAILED (1 errors, 0 warnings)" in cleaned
    withheld = 1 if expected_tail == WITHHELD else 0
    assert f"{withheld} lines withheld" in cleaned


def test_every_known_template_is_rebuilt_exactly(tmp_path: Path) -> None:
    fields, templates = _sanitizer_tables(tmp_path)
    assert set(SAMPLE) == set(fields)
    lines, expected = [], []
    for rule, rule_templates in templates.items():
        for template in rule_templates:
            message = re.sub(r"\{([A-Z_]+)\}", lambda m: SAMPLE[m.group(1)], template)
            kept = re.sub(
                r"\{([A-Z_]+)\}",
                lambda m: "<redacted>" if m.group(1) == "REDACT" else SAMPLE[m.group(1)],
                template,
            )
            lines.append(f"ERROR   [{rule}] {message}")
            expected.append(f"ERROR   [{rule}] {kept}")
    result, cleaned = _sanitize(tmp_path, "\n".join(lines) + "\n")
    assert result.returncode == 0, result.stderr
    assert cleaned.splitlines()[:-1] == expected
    assert SAMPLE["REDACT"] not in cleaned
    assert f"{len(lines)} lines," in cleaned and "0 lines withheld" in cleaned


def test_sanitizer_enumerations_match_the_code(tmp_path: Path) -> None:
    fields, _ = _sanitizer_tables(tmp_path)

    def names(field: str) -> set[str]:
        return set(re.findall(r"[a-z][a-z_0-9]+", fields[field]))

    assert names("OVERSIZE") == validate.OVERSIZE_REASONS
    assert names("KIND") == set(get_args(ChunkSourceKind))
    assert names("STYPE") == set(get_args(SourceType))
    assert names("BLOCK") == set(get_args(BlockKind))
    manifest = json.loads((ROOT / "data/manifest/build_manifest.json").read_text("utf-8"))
    assert names("INPUT") == set(manifest["inputs"])


def test_sanitizer_withholds_unrecognised_lines_and_unexpected_values(tmp_path: Path) -> None:
    raw = "\n".join(
        [
            '{"level": "ERROR", "error": "ValueError: Secret Fixture Heading"}',
            "Secret Fixture Heading on a line of its own",
            'counts: {"Secret Fixture Heading": 1}',
            'counts: {"chunks": "Secret Fixture Heading"}',
            "counts: {not json",
            "MISSING data/build/chunks/1.2.jsonl",
            "MISSING Secret Fixture Heading",
            "corpus manifest cannot be loaded: ValueError: Secret Fixture Heading",
            "RESULT: FAILED (missing input)",
        ]
    )
    result, cleaned = _sanitize(tmp_path, raw + "\n")
    assert result.returncode == 0, result.stderr
    assert "Secret" not in cleaned
    assert "MISSING data/build/chunks/1.2.jsonl" in cleaned
    assert "corpus manifest cannot be loaded: <redacted>" in cleaned
    assert "RESULT: FAILED (missing input)" in cleaned
    assert "9 lines, 1 fields redacted, 6 lines withheld" in cleaned


# ---------------------------------------------------------------- template coverage (automated)

VALIDATE = ROOT / "src/version_aware_rag/corpus/validate.py"
# Helpers that build message text which validate.py reports through a pass-through call
# (report.error(rule, problem)): helper function name -> the rule it is reported under.
HELPER_RULES = {"source_coverage": "R8e"}


def _skeleton(node: ast.expr) -> str:
    """Fixed message text with each interpolated value as '@'. Module string constants used in
    an f-string (for example RELEASE_FILE) are resolved to their value, as templates spell them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
                continue
            assert isinstance(value, ast.FormattedValue)
            inner = value.value
            constant = getattr(validate, inner.id, None) if isinstance(inner, ast.Name) else None
            resolved = (
                isinstance(inner, ast.Name) and inner.id.isupper() and isinstance(constant, str)
            )
            parts.append(constant if resolved else "@")  # type: ignore[arg-type]
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _skeleton(node.left) + _skeleton(node.right)
    if isinstance(node, ast.Call):  # for example "; ".join(items): one interpolated value
        return "@"
    raise AssertionError(f"line {node.lineno}: unsupported message expression {ast.dump(node)}")


def _validate_message_sites(source_path: Path) -> set[tuple[str, str]]:
    """(rule, skeleton) for every message the given validate.py source can report."""
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    enclosing: dict[ast.AST, str] = {}
    for name, fn in functions.items():
        for node in ast.walk(fn):
            enclosing.setdefault(node, name)

    def rules_of(arg: ast.expr, call: ast.Call) -> list[str]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return [arg.value]
        assert isinstance(arg, ast.Name), f"line {call.lineno}: rule is not a constant or name"
        fn = functions[enclosing[call]]
        params = [a.arg for a in fn.args.args]
        assert arg.id in params, f"line {call.lineno}: rule {arg.id} is not a parameter"
        index = params.index(arg.id)
        found = []
        for c in ast.walk(tree):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == fn.name:
                passed = c.args[index] if len(c.args) > index else None
                if isinstance(passed, ast.Constant):
                    found.append(passed.value)
        assert found, f"line {call.lineno}: no constant rule passed to {fn.name}"
        return [str(r) for r in found]

    sites: set[tuple[str, str]] = set()
    pass_through: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        func = node.func
        if (
            func.attr in ("error", "warn")
            and isinstance(func.value, ast.Name)
            and func.value.id == "report"
        ):
            message = node.args[1]
            if isinstance(message, ast.Name):  # text built by a helper, reported as is
                pass_through.update(rules_of(node.args[0], node))
                continue
            sites.update((rule, _skeleton(message)) for rule in rules_of(node.args[0], node))
        elif func.attr == "append" and ast.unparse(func.value).endswith("problems"):
            helper = enclosing.get(node, "<module>")
            assert helper in HELPER_RULES, (
                f"line {node.lineno}: {helper} builds problem text; add it to HELPER_RULES"
            )
            sites.add((HELPER_RULES[helper], _skeleton(node.args[0])))
    assert set(HELPER_RULES.values()) <= pass_through, "a helper rule is never reported"
    finding_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Finding"
    ]
    assert all(enclosing[n] in ("error", "warn") for n in finding_calls)  # only via report
    return sites


def _template_skeletons(templates: dict[str, list[str]]) -> set[tuple[str, str]]:
    """(rule, skeleton) for every sanitizer template: each {FIELD} becomes '@'."""
    return {(rule, re.sub(r"\{[A-Z_]+\}", "@", t)) for rule, ts in templates.items() for t in ts}


def _coverage_gaps(
    sites: set[tuple[str, str]], templates: dict[str, list[str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(messages with no sanitizer template, sanitizer templates with no message)."""
    expected = _template_skeletons(templates)
    return sorted(sites - expected), sorted(expected - sites)


def test_every_validate_message_has_a_sanitizer_template(tmp_path: Path) -> None:
    """Every message site in validate.py (direct report calls, the rule-parameter helper and
    the coverage-problem helper) has exactly one matching sanitizer template under its rule,
    and every template still corresponds to a message. To fix a failure, update TEMPLATES in
    the workflow's validate-log sanitizer (see the comment above it); never edit validate.py
    to satisfy this test."""
    _, templates = _sanitizer_tables(tmp_path)
    without_template, stale_templates = _coverage_gaps(_validate_message_sites(VALIDATE), templates)
    assert not without_template, (
        f"validate.py messages with no sanitizer template: {without_template}"
    )
    assert not stale_templates, (
        f"sanitizer templates with no validate.py message: {stale_templates}"
    )


R3W_CALL = 'report.warn("R3w", f"{fallback} chunks use the section-path fallback")'
R3W_SITE = ("R3w", "@ chunks use the section-path fallback")
R3W_TEMPLATE = "{INT} chunks use the section-path fallback"


def _edited_source(tmp_path: Path, name: str, old: str, new: str) -> Path:
    """A temporary copy of validate.py with exactly one occurrence of ``old`` replaced."""
    original = VALIDATE.read_text(encoding="utf-8")
    assert original.count(old) == 1
    path = tmp_path / name
    path.write_text(original.replace(old, new), encoding="utf-8")
    assert path.read_text(encoding="utf-8") != original
    return path


def test_coverage_check_fails_for_a_new_message_until_its_template_is_added(
    tmp_path: Path,
) -> None:
    _, templates = _sanitizer_tables(tmp_path)
    new_call = 'report.warn("R3w", f"{fallback} brand-new message")'
    source = _edited_source(
        tmp_path, "validate_new.py", R3W_CALL, f"{R3W_CALL}\n        {new_call}"
    )
    new_site = ("R3w", "@ brand-new message")
    # The temporary source is the one inspected: the new site is found there and only there.
    assert new_call in source.read_text(encoding="utf-8")
    assert new_site in _validate_message_sites(source)
    assert new_site not in _validate_message_sites(VALIDATE)
    assert _validate_message_sites(source) - _validate_message_sites(VALIDATE) == {new_site}

    without_template, stale = _coverage_gaps(_validate_message_sites(source), templates)
    assert (without_template, stale) == ([new_site], [])  # fails: no template for it

    fixed = {**templates, "R3w": [*templates["R3w"], "{INT} brand-new message"]}
    assert _coverage_gaps(_validate_message_sites(source), fixed) == ([], [])  # passes once added


def test_coverage_check_fails_for_a_changed_message_until_its_template_is_updated(
    tmp_path: Path,
) -> None:
    _, templates = _sanitizer_tables(tmp_path)
    changed = 'report.warn("R3w", f"{fallback} chunks use a fallback path")'
    source = _edited_source(tmp_path, "validate_changed.py", R3W_CALL, changed)
    changed_site = ("R3w", "@ chunks use a fallback path")
    # The temporary source is the one inspected: the old site is gone, the new one present.
    sites = _validate_message_sites(source)
    assert changed in source.read_text(encoding="utf-8") and R3W_CALL not in source.read_text(
        encoding="utf-8"
    )
    assert changed_site in sites and R3W_SITE not in sites
    assert R3W_SITE in _validate_message_sites(VALIDATE)

    without_template, stale = _coverage_gaps(sites, templates)
    assert without_template == [changed_site]  # the changed message has no template
    assert stale == [R3W_SITE]  # and the old template is reported as stale (reverse check)

    updated = {
        **templates,
        "R3w": [
            "{INT} chunks use a fallback path" if t == R3W_TEMPLATE else t for t in templates["R3w"]
        ],
    }
    assert _coverage_gaps(sites, updated) == ([], [])  # passes once the template is updated


def test_coverage_check_reports_a_stale_template(tmp_path: Path) -> None:
    _, templates = _sanitizer_tables(tmp_path)
    extra = {**templates, "R3w": [*templates["R3w"], "{INT} message that no longer exists"]}
    assert _coverage_gaps(_validate_message_sites(VALIDATE), extra) == (
        [],
        [("R3w", "@ message that no longer exists")],
    )


# ---------------------------------------------------------------- real paths and skip reasons


def test_every_real_manifest_path_survives_the_actual_sanitizer(tmp_path: Path) -> None:
    """Every docs page, release post and release-note path of the committed corpus manifest is
    kept by the workflow's own sanitizer (PATH and SPAN fields), so no valid finding about a
    real source is withheld because of a too-narrow path pattern."""
    manifest, problems = load_manifest(ROOT / "data/manifest/corpus_manifest.json")
    assert manifest is not None and problems == []
    docs = [f.path for f in manifest.files]
    posts = [p.path for p in manifest.release_posts]
    releases = [f"releases/tag/{r.tag}" for r in manifest.release_notes.included]
    assert docs and posts and releases
    assert all(re.fullmatch(r"_posts/\d{4}-\d\d-\d\d-[a-z0-9-]+\.md", p) for p in posts)
    lines = [f"ERROR   [R9b] manifest file {p}: hash, blob or size missing" for p in docs]
    lines += [
        f"ERROR   [R9b] release post {p}: licence fields or attribution missing" for p in posts
    ]
    lines += [f"ERROR   [R8e] {p}: excluded page has chunks" for p in releases]
    lines += [f"ERROR   [R8e] {p}:3-9: heading not represented in any section path" for p in posts]
    result, cleaned = _sanitize(tmp_path, "\n".join(lines) + "\n")
    assert result.returncode == 0, result.stderr
    assert cleaned.splitlines()[:-1] == lines  # every line kept exactly
    assert f"{len(lines)} lines, 0 fields redacted, 0 lines withheld" in cleaned


def _corpus_pytest_flags() -> list[str]:
    """The reporting flags of the workflow's corpus pytest run (between -m corpus and
    --junitxml), read from the workflow itself."""
    step = next(s for s in _steps(_job("corpus-rebuild")) if '"A: corpus tests' in s)
    command = re.search(r"uv run pytest -m corpus (.*?) --junitxml=", step)
    assert command is not None
    return command.group(1).split()


def test_corpus_test_log_carries_no_skip_reasons_but_every_skip_still_fails(
    tmp_path: Path,
) -> None:
    flags = _corpus_pytest_flags()
    assert flags == ["--tb=no", "-rN"]
    demo = tmp_path / "test_demo.py"
    demo.write_text(
        "import pytest\n\n"
        "def test_ok():\n    pass\n\n"
        '@pytest.mark.skip(reason="SKIP-REASON-MARKER Secret Fixture Heading")\n'
        "def test_marked():\n    pass\n\n"
        "def test_runtime_skip():\n"
        '    pytest.skip("SKIP-REASON-MARKER from pytest.skip")\n',
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    run = subprocess.run(
        # -o addopts=-ra reproduces the project's default, which the workflow's flags override
        [
            sys.executable,
            "-m",
            "pytest",
            str(demo),
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=-ra",
            *flags,
            f"--junitxml={junit}",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    output = run.stdout + run.stderr
    assert "1 passed, 2 skipped" in output
    assert "SKIP-REASON-MARKER" not in output and "Secret" not in output  # no skip reasons
    # The skips are still recorded and the workflow's JUnit check still rejects them.
    collect = tmp_path / "collect.txt"
    collect.write_text("\n".join(f"tests/x.py::t{i}" for i in range(3)) + "\n", "utf-8")
    check = subprocess.run(
        [sys.executable, "-I", "-c", _junit_check(), str(collect), str(junit), "3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 1
    assert "2 skipped" in check.stdout and "2 test cases with <skipped>" in check.stdout
    assert "SKIP-REASON-MARKER" not in check.stdout


# ------------------------------------------------------------------ RAG-M9 evaluation gate job


def test_eval_gate_job_runs_the_check_and_never_writes_a_baseline() -> None:
    job = _job("eval-gate")
    assert "uv run vrag eval gate check --method hybrid" in job
    assert "gate baseline" not in job  # CI has no write path to the baseline (design §6.2)
    assert "--provisional" not in job
    assert 'exit "$status"' in job  # BLOCK (1) and ERROR (2) fail the pull request


def test_eval_gate_job_is_dormant_until_split_and_approved_baseline_exist() -> None:
    job = _job("eval-gate")
    assert "benchmark/splits/ci_gate.jsonl" in job and "benchmark/gate/baseline.json" in job
    assert "baseline_sha256" in job
    steps = _steps(job)
    gated = [s for s in steps if "steps.enabled.outputs.on == 'true'" in s]
    assert len(gated) == len(steps) - 2  # all but checkout and the enabled check itself


def test_eval_gate_job_uses_pinned_actions_and_only_the_builtin_token_for_releases() -> None:
    job = _job("eval-gate")
    uses = re.findall(r"uses:\s*(\S+)", job)
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)
    token_steps = [s for s in _steps(job) if "secrets.GITHUB_TOKEN" in s]
    assert len(token_steps) == 1 and "vrag corpus releases fetch" in token_steps[0]
    assert "VRAG_GEMINI_API_KEY" not in job and "vrag eval answers" not in job
    assert "actions/cache" not in job
