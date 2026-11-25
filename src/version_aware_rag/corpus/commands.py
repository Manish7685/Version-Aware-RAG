"""``vrag corpus …`` subcommands (RAG-M3 S1: fetch, manifest, verify; S2: releases;
S3: inspect, parse-page; S4c: chunk, chunk-page; S4d: releases chunk; S5a: build; S5b: validate).

All paths are relative to ``--root`` (default: the current directory, i.e. the repo root).
Exit codes follow the CLI convention: 0 ok, 1 check failed, 2 usage or environment error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from version_aware_rag.corpus import upstream
from version_aware_rag.corpus.acquire import GitError, GitRepo, fetch_pinned, open_cache
from version_aware_rag.corpus.build import BuildError, BuildInputs, output_entries, run_build
from version_aware_rag.corpus.chunk import chunk_documents, chunk_page, page_source
from version_aware_rag.corpus.chunk_output import (
    CHUNK_DIR,
    CHUNK_FILES,
    CHUNK_MANIFEST,
    CHUNK_REPORT,
    BuildContext,
    chunk_file_problems,
    parse_corpus_page,
    render_chunk_files,
    render_chunk_manifest,
    render_chunk_report,
)
from version_aware_rag.corpus.config import (
    CorpusConfig,
    load_chunking_config,
    load_config,
    load_parsing_config,
)
from version_aware_rag.corpus.inspect import (
    inspect_corpus,
    render_inspection_report,
    render_samples,
)
from version_aware_rag.corpus.manifest import (
    CorpusManifest,
    Discrepancy,
    build_manifest,
    compare_with_m1,
    load_manifest,
    read_manifest,
    render_manifest,
    stale_manifest_parts,
    verify_cache,
    write_manifest,
)
from version_aware_rag.corpus.parse import PARSER_VERSION, parse_page
from version_aware_rag.corpus.release_chunks import (
    RELEASE_DIR,
    RELEASE_FILE,
    RELEASE_MANIFEST,
    RELEASE_REPORT,
    ReleaseChunkError,
    chunk_release_notes,
    release_file_problems,
    render_release_file,
    render_release_manifest,
    render_release_report,
)
from version_aware_rag.corpus.releases import (
    ReleaseError,
    ReleaseMismatch,
    ReleaseVerifyReport,
    default_fetcher,
    fetch_all,
    verify_releases,
)
from version_aware_rag.corpus.sections import build_tree
from version_aware_rag.corpus.validate import (
    ValidateInputs,
    ValidationUsageError,
    validate_corpus,
)
from version_aware_rag.settings import Settings

log = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


def add_corpus_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    corpus = sub.add_parser("corpus", help="Corpus acquisition and manifest (RAG-M3).")
    actions = corpus.add_subparsers(dest="corpus_command", required=True, metavar="ACTION")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", type=Path, default=Path(), help="Repository root.")
        p.add_argument(
            "--config", type=Path, default=Path("configs/corpus.toml"), help="Relative to --root."
        )

    fetch = actions.add_parser("fetch", help="Fetch the pinned docs snapshot into the cache.")
    common(fetch)
    fetch.set_defaults(handler=_cmd_fetch)

    manifest = actions.add_parser(
        "manifest", help="Build the manifest from the cache and compare it with RAG-M1 evidence."
    )
    common(manifest)
    manifest.add_argument(
        "--check",
        action="store_true",
        help="Don't write; fail if the committed manifest differs from a fresh build.",
    )
    manifest.set_defaults(handler=_cmd_manifest)

    verify = actions.add_parser(
        "verify", help="Verify cache, committed manifest and RAG-M1 evidence agree (offline)."
    )
    common(verify)
    verify.set_defaults(handler=_cmd_verify)

    releases = actions.add_parser("releases", help="Approved GitHub release-note snapshots (S2).")
    rel_actions = releases.add_subparsers(dest="releases_command", required=True, metavar="STEP")
    rel_fetch = rel_actions.add_parser(
        "fetch", help="Fetch the approved releases by id and verify them against recorded hashes."
    )
    common(rel_fetch)
    rel_fetch.set_defaults(handler=_cmd_releases_fetch)
    rel_verify = rel_actions.add_parser(
        "verify", help="Check cached release bodies against recorded hashes (offline)."
    )
    common(rel_verify)
    rel_verify.set_defaults(handler=_cmd_releases_verify)

    inspect = actions.add_parser(
        "inspect", help="Parse every page and write the S3 inspection and samples reports."
    )
    common(inspect)
    inspect.add_argument(
        "--parsing", type=Path, default=Path("configs/parsing.toml"), help="Relative to --root."
    )
    inspect.add_argument("--out-dir", type=Path, default=Path("docs/reports"))
    inspect.set_defaults(handler=_cmd_inspect)

    page = actions.add_parser("parse-page", help="Parse one cached page and print its blocks.")
    common(page)
    page.add_argument(
        "--parsing", type=Path, default=Path("configs/parsing.toml"), help="Relative to --root."
    )
    page.add_argument("path", help="Repository path, e.g. docs/2.0/sql/functions/map.md")
    page.add_argument("--json", action="store_true", help="Full parse output as JSON.")
    page.set_defaults(handler=_cmd_parse_page)

    def chunk_configs(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--parsing", type=Path, default=Path("configs/parsing.toml"), help="Relative to --root."
        )
        p.add_argument(
            "--chunking",
            type=Path,
            default=Path("configs/chunking.toml"),
            help="Relative to --root.",
        )

    chunk = actions.add_parser(
        "chunk",
        help="Chunk the whole corpus: chunk files (git-ignored), chunk manifest and report (S4c).",
    )
    common(chunk)
    chunk_configs(chunk)
    chunk.add_argument(
        "--check",
        action="store_true",
        help="Rebuild in memory and compare with the files on disk; write nothing.",
    )
    chunk.set_defaults(handler=_cmd_chunk)

    chunk_one = actions.add_parser("chunk-page", help="Chunk one cached page and print its chunks.")
    common(chunk_one)
    chunk_configs(chunk_one)
    chunk_one.add_argument("path", help="Repository path, e.g. docs/2.0/sql/functions/map.md")
    chunk_one.add_argument("--json", action="store_true", help="Chunks and empty sections as JSON.")
    chunk_one.set_defaults(handler=_cmd_chunk_page)

    rel_chunk = rel_actions.add_parser(
        "chunk",
        help="Verify, parse, clean (Q7) and chunk the 4 release notes (S4d); text stays local.",
    )
    common(rel_chunk)
    chunk_configs(rel_chunk)
    rel_chunk.add_argument(
        "--check",
        action="store_true",
        help="Rebuild in memory and compare with the files on disk; write nothing.",
    )
    rel_chunk.set_defaults(handler=_cmd_releases_chunk)

    build = actions.add_parser(
        "build",
        help="Verify every input, then build the complete corpus (S5a; authoritative).",
    )
    common(build)
    chunk_configs(build)
    build.add_argument(
        "--check",
        action="store_true",
        help="Verify and rebuild in memory, compare with the files on disk; write nothing.",
    )
    build.set_defaults(handler=_cmd_build)

    validate = actions.add_parser(
        "validate", help="Validate the generated corpus (S5b; read-only, never rebuilds)."
    )
    common(validate)
    chunk_configs(validate)
    validate.set_defaults(handler=_cmd_validate)

    upstream_check = actions.add_parser(
        "check-upstream",
        help="Read-only drift check of the pinned docs commit and the approved release notes "
        "against the committed manifest (S6b; local or manual use, never in CI).",
    )
    common(upstream_check)
    upstream_check.set_defaults(handler=_cmd_check_upstream)


def _load(args: argparse.Namespace) -> tuple[CorpusConfig, str, Path]:
    cfg, cfg_sha = load_config(args.root / args.config)
    return cfg, cfg_sha, cfg.cache_path(args.root)


def _release_cache(args: argparse.Namespace, cfg: CorpusConfig) -> Path:
    root: Path = args.root
    return root / cfg.release_notes.cache_dir


def _print_release_report(report: ReleaseVerifyReport) -> None:
    print(f"{'release':<9}{'status':<10}{'expected sha256':<66}{'cached sha256':<66}last fetched")
    for c in report.checks:
        print(
            f"{c.tag:<9}{c.status.upper():<10}{c.expected_sha256:<66}"
            f"{(c.actual_sha256 or '-'):<66}{c.last_retrieved_at or '-'}"
        )


def _print_m1_table(
    manifest: CorpusManifest, discrepancies: list[Discrepancy], cfg: CorpusConfig
) -> None:
    bad = {(d.subject, d.field) for d in discrepancies}
    print(f"source: {manifest.source.repo} @ {manifest.source.commit}")
    lic_ok = not any(s == "licence" for s, _ in bad)
    print(
        f"licence file: {manifest.source.licence_file} "
        f"sha256 {manifest.source.licence_file_sha256} [{'MATCH' if lic_ok else 'MISMATCH'}]"
    )
    print(f"{'version':<8}{'files':>6}{'words':>10}  {'git_tree':<42}{'content_hash':<66}result")
    by_v = {v.version: v for v in manifest.versions}
    for spec in cfg.versions:
        rec = by_v[spec.version]
        ok = not any(s == spec.version for s, _ in bad)
        print(
            f"{rec.version:<8}{rec.md_files:>6}{rec.words:>10}  {rec.git_tree:<42}"
            f"{rec.content_hash:<66}{'MATCH' if ok else 'MISMATCH'}"
        )
    for d in discrepancies:
        print(f"DISCREPANCY {d.subject} {d.field}: expected {d.expected}, got {d.actual}")
    total_files = sum(v.md_files for v in manifest.versions)
    total_words = sum(v.words for v in manifest.versions)
    print(
        f"total: {total_files} files, {total_words} words, "
        f"{len(manifest.excluded_files)} non-Markdown files recorded as excluded"
    )
    print("RESULT: ALL MATCH RAG-M1" if not discrepancies else "RESULT: DISCREPANCY")


def _cmd_fetch(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, cache = _load(args)
        fetch_pinned(cfg, cache)
    except (OSError, ValidationError, GitError) as exc:
        log.error("corpus_fetch_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    print(f"fetched {cfg.docs_source.repo} @ {cfg.docs_source.commit} into {cache}")
    return EXIT_OK


def _cmd_manifest(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, cfg_sha, cache = _load(args)
        repo = open_cache(cfg, cache)
        manifest = build_manifest(cfg, repo, cfg_sha, _release_cache(args, cfg))
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_manifest_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    discrepancies = compare_with_m1(manifest, cfg)
    _print_m1_table(manifest, discrepancies, cfg)
    if discrepancies:
        print("manifest NOT written because recomputed values differ from the approved evidence")
        return EXIT_FAILURE
    target = args.root / cfg.paths.manifest
    if args.check:
        stale = stale_manifest_parts(manifest, target)
        _, problems = load_manifest(target)
        for line in [*stale, *problems]:
            print(f"  {line}")
        ok = not stale and not problems
        parts = len(render_manifest(manifest))
        print(
            f"committed manifest ({parts} parts) "
            f"{'is up to date' if ok else 'DIFFERS from a fresh build'}: {target}"
        )
        return EXIT_OK if ok else EXIT_FAILURE
    write_manifest(manifest, target)
    for rel, data in render_manifest(manifest).items():
        print(f"wrote {rel} ({len(data)} bytes)")
    return EXIT_OK


def _cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, cfg_sha, cache = _load(args)
        repo = open_cache(cfg, cache)
        committed, problems = load_manifest(args.root / cfg.paths.manifest)
        if committed is None or problems:
            print("manifest parts intact: NO")
            for line in problems:
                print(f"  {line}")
            print("RESULT: FAILED")
            return EXIT_FAILURE
        print(f"manifest parts intact: yes ({len(committed.versions)} per-version files indexed)")
        report = verify_cache(cfg, repo, committed, cfg_sha, _release_cache(args, cfg))
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_verify_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    print(f"pinned commit checked out: {'yes' if report.commit_ok else 'NO'}")
    print(
        f"committed manifest equals snapshot: {'yes' if report.manifest_matches_snapshot else 'NO'}"
    )
    for line in report.snapshot_differences:
        print(f"  {line}")
    print(
        "committed manifest equals RAG-M1 evidence: "
        f"{'yes' if not report.m1_discrepancies else 'NO'}"
    )
    for d in report.m1_discrepancies:
        print(f"  {d.subject} {d.field}: expected {d.expected}, got {d.actual}")
    print(
        f"working files missing: {len(report.missing_files)}; changed: {len(report.changed_files)}"
    )
    for p in report.missing_files:
        print(f"  MISSING {p}")
    for p in report.changed_files:
        print(f"  CHANGED {p}")
    print("release notes:")
    _print_release_report(report.releases)
    print("RESULT: VERIFIED" if report.ok else "RESULT: FAILED")
    return EXIT_OK if report.ok else EXIT_FAILURE


def _cmd_releases_fetch(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, _ = _load(args)
        cache = _release_cache(args, cfg)
        results = fetch_all(cfg.release_notes, cache, default_fetcher())
    except ReleaseMismatch as exc:
        print(f"STOPPED: {exc.tag} differs from the recorded evidence; nothing was overwritten.")
        for problem in exc.problems:
            print(f"  {problem}")
        if exc.saved is not None:
            print(f"  fetched text saved for review: {exc.saved}")
        if exc.diff:
            print("  diff against the verified local copy:")
            print(exc.diff)
        elif exc.saved is not None:
            print(
                "  no verified local copy to diff against (raw text is not committed by design); "
                "compare the saved file with the recorded hash and decide before accepting it"
            )
        return EXIT_FAILURE
    except (OSError, ValidationError, ReleaseError) as exc:
        log.error("corpus_releases_fetch_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        print(f"FAILED: {exc}")
        return EXIT_USAGE
    for r in results:
        print(
            f"{r.tag:<9}id={r.release_id:<10} VERIFIED sha256={r.body_sha256} "
            f"bytes={r.body_bytes} retrieved_at={r.retrieved_at}"
        )
    print(f"cache: {cache} (git-ignored; raw text is never committed)")
    return EXIT_OK


def _cmd_releases_verify(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, _ = _load(args)
        report = verify_releases(cfg.release_notes, _release_cache(args, cfg))
    except (OSError, ValidationError, ValueError) as exc:
        log.error("corpus_releases_verify_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    _print_release_report(report)
    print("RESULT: VERIFIED" if report.ok else "RESULT: FAILED")
    return EXIT_OK if report.ok else EXIT_FAILURE


def _cmd_inspect(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, cache = _load(args)
        open_cache(cfg, cache)
        manifest = read_manifest(args.root / cfg.paths.manifest)
        parsing, parsing_sha = load_parsing_config(args.root / args.parsing)
        insp = inspect_corpus(manifest, cache, parsing, parsing_sha)
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_inspect_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    out_dir: Path = args.root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "RAG-M3_S3_source_inspection.md"
    samples = out_dir / "RAG-M3_S3_samples.md"
    report.write_bytes(render_inspection_report(insp).encode("utf-8"))
    samples.write_bytes(render_samples(insp, parsing).encode("utf-8"))
    # Full samples including non-redistributable text: local review copy, git-ignored.
    local = args.root / "data/build/RAG-M3_S3_samples_local.md"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(render_samples(insp, parsing, include_restricted=True).encode("utf-8"))
    excluded = sum(not p.doc.include_in_corpus for p in insp.pages)
    warnings = sum(len(p.doc.warnings) for p in insp.pages)
    print(
        f"parsed {len(insp.pages)} pages ({excluded} excluded from the corpus); {warnings} warnings"
    )
    print(f"wrote {report}")
    print(f"wrote {samples}")
    print(f"wrote {local} (local only, git-ignored)")
    return EXIT_OK


def _cmd_parse_page(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, cache = _load(args)
        open_cache(cfg, cache)
        manifest = read_manifest(args.root / cfg.paths.manifest)
        parsing, _ = load_parsing_config(args.root / args.parsing)
        rec = next((f for f in manifest.files if f.path == args.path), None)
        if rec is None:
            print(f"not in the corpus manifest: {args.path}")
            return EXIT_USAGE
        doc = parse_page(
            raw=(cache / rec.path).read_bytes(),
            version=rec.version,
            path=rec.path,
            source_sha256=rec.sha256,
            site_base=parsing.site_base,
            excluded=parsing.exclusions(),
        )
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_parse_page_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    if args.json:
        print(doc.model_dump_json(indent=2))
        return EXIT_OK
    print(
        f"{doc.path} | title={doc.title!r} | {doc.extraction} | {doc.page_kind} | "
        f"{len(doc.blocks)} blocks | {len(doc.warnings)} warnings"
    )
    for b in doc.blocks:
        head = f"[{b.kind} {b.line_start}-{b.line_end}" + (f" L{b.level}" if b.level else "") + "]"
        print(head, b.text[:200].replace("\n", " / "))
    for w in doc.warnings:
        print(f"WARNING {w.code} line {w.line}: {w.detail}")
    return EXIT_OK


def _cmd_chunk(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, cache = _load(args)
        open_cache(cfg, cache)
        manifest_path = args.root / cfg.paths.manifest
        manifest = read_manifest(manifest_path)
        parsing, parsing_sha = load_parsing_config(args.root / args.parsing)
        chunking, chunking_sha = load_chunking_config(args.root / args.chunking)
        insp = inspect_corpus(manifest, cache, parsing, parsing_sha)
        pages = chunk_documents([p.doc for p in insp.pages], manifest, chunking, chunking_sha)
        ctx = BuildContext(
            source_commit=manifest.source.commit,
            parser_version=PARSER_VERSION,
            chunker_version=chunking.chunker_version,
            chunking_config_sha256=chunking_sha,
            corpus_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        )
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_chunk_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    files = render_chunk_files(pages)
    chunk_manifest = render_chunk_manifest(pages, files, ctx)
    report = render_chunk_report(pages, files, ctx).encode("utf-8")
    out_dir = args.root / CHUNK_DIR
    manifest_out = args.root / CHUNK_MANIFEST
    report_out = args.root / CHUNK_REPORT
    problems: list[str] = []
    if args.check:
        for path, data in [(manifest_out, chunk_manifest), (report_out, report)]:
            if not path.is_file() or path.read_bytes() != data:
                problems.append(f"{path.relative_to(args.root).as_posix()}: missing or differs")
        for name, data in files.items():
            target = out_dir / name
            if not target.is_file() or target.read_bytes() != data:
                problems.append(f"{CHUNK_DIR}/{name}: missing or differs from a fresh build")
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.parent.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (out_dir / name).write_bytes(data)
        manifest_out.write_bytes(chunk_manifest)
        report_out.write_bytes(report)
    on_disk = (
        {p.name: p.read_bytes() for p in sorted(out_dir.iterdir()) if p.is_file()}
        if out_dir.is_dir()
        else {}
    )
    committed = json.loads(manifest_out.read_bytes()) if manifest_out.is_file() else {}
    problems += chunk_file_problems(on_disk, committed)
    total = sum(len(page.chunks) for _, _, page in pages)
    empty = sum(len(page.empty_sections) for _, _, page in pages)
    print(f"chunked {len(pages)} pages: {total} chunks, {empty} empty sections")
    for name in CHUNK_FILES:
        data = files[name]
        digest = hashlib.sha256(data).hexdigest()
        print(f"  {CHUNK_DIR}/{name}: {data.count(b'\n')} records, {len(data)} bytes, {digest}")
    if not args.check:
        print(f"wrote {CHUNK_MANIFEST} and {CHUNK_REPORT}")
    for line in problems:
        print(f"  PROBLEM {line}")
    print(f"RESULT: {'OK' if not problems else 'FAILED'}")
    return EXIT_OK if not problems else EXIT_FAILURE


def _cmd_chunk_page(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, cache = _load(args)
        open_cache(cfg, cache)
        manifest = read_manifest(args.root / cfg.paths.manifest)
        parsing, _ = load_parsing_config(args.root / args.parsing)
        chunking, chunking_sha = load_chunking_config(args.root / args.chunking)
        doc = parse_corpus_page(manifest, cache, parsing, args.path)
        if doc is None:
            print(f"not in the corpus manifest: {args.path}")
            return EXIT_USAGE
        if not doc.include_in_corpus:
            print(f"{doc.path}: excluded from the corpus ({doc.exclusion_reason}); no chunks")
            return EXIT_OK
        tree = build_tree(doc, chunking)
        page = chunk_page(doc, tree, page_source(doc, manifest, chunking), chunking, chunking_sha)
    except (OSError, ValidationError, GitError, ValueError) as exc:
        log.error("corpus_chunk_page_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    if args.json:
        payload = {
            "chunks": [c.model_dump(mode="json") for c in page.chunks],
            "empty_sections": [e.model_dump(mode="json") for e in page.empty_sections],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"{doc.path} | {len(page.chunks)} chunks | {len(page.empty_sections)} empty sections")
    for c in page.chunks:
        flag = f" OVERSIZE {c.oversize_reason}" if c.oversize else ""
        print(
            f"[{c.chunk_id} {c.unit_kind} part {c.part_ordinal}/{c.part_count} "
            f"{c.token_count_approx}t lines {c.line_start}-{c.line_end}{flag}] "
            + " > ".join(c.section_path)
        )
        print("   ", c.content[:160].replace("\n", " / "))
    for e in page.empty_sections:
        print(f"EMPTY {e.reason} lines {e.line_start}-{e.line_end}: " + " > ".join(e.section_path))
    return EXIT_OK


def _cmd_releases_chunk(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, _, _ = _load(args)
        manifest_path = args.root / cfg.paths.manifest
        manifest = read_manifest(manifest_path)
        parsing, _ = load_parsing_config(args.root / args.parsing)
        chunking, chunking_sha = load_chunking_config(args.root / args.chunking)
        behaviour: dict[str, str] = {v.version: v.behaviour_package for v in manifest.versions}
        pages = chunk_release_notes(
            cfg.release_notes,
            manifest.release_notes,
            behaviour,
            _release_cache(args, cfg),
            parsing,
            chunking,
            chunking_sha,
        )
    except ReleaseChunkError as exc:
        for line in exc.problems:
            print(f"  PROBLEM {line}")
        print("RESULT: FAILED (nothing processed)")
        return EXIT_FAILURE
    except (OSError, ValidationError, ValueError) as exc:
        log.error("corpus_releases_chunk_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    ctx = BuildContext(
        source_commit=manifest.source.commit,
        parser_version=PARSER_VERSION,
        chunker_version=chunking.chunker_version,
        chunking_config_sha256=chunking_sha,
        corpus_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    )
    data = render_release_file(pages)
    rel_manifest = render_release_manifest(pages, data, ctx)
    report = render_release_report(pages, data, ctx).encode("utf-8")
    out_file = args.root / RELEASE_DIR / RELEASE_FILE
    manifest_out = args.root / RELEASE_MANIFEST
    report_out = args.root / RELEASE_REPORT
    targets = [(out_file, data), (manifest_out, rel_manifest), (report_out, report)]
    problems: list[str] = []
    if args.check:
        for path, expected in targets:
            if not path.is_file() or path.read_bytes() != expected:
                problems.append(f"{path.relative_to(args.root).as_posix()}: missing or differs")
    else:
        for path, content in targets:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if out_file.is_file() and manifest_out.is_file():
        problems += release_file_problems(
            out_file.read_bytes(), json.loads(manifest_out.read_bytes())
        )
    for r in pages:
        print(
            f"  {r.record.tag}: verified, {len(r.page.chunks)} chunks, "
            f"Q7 {dict(sorted(r.q7_counts.items()))}"
        )
    digest = hashlib.sha256(data).hexdigest()
    print(
        f"  {RELEASE_DIR}/{RELEASE_FILE}: {data.count(b'\n')} records, {len(data)} bytes, {digest}"
    )
    if not args.check:
        print(f"wrote {RELEASE_MANIFEST} and {RELEASE_REPORT}")
    for line in problems:
        print(f"  PROBLEM {line}")
    print(f"RESULT: {'OK' if not problems else 'FAILED'}")
    return EXIT_OK if not problems else EXIT_FAILURE


def _cmd_build(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, cfg_sha, cache = _load(args)
        parsing, parsing_sha = load_parsing_config(args.root / args.parsing)
        chunking, chunking_sha = load_chunking_config(args.root / args.chunking)
    except (OSError, ValidationError, ValueError) as exc:
        log.error("corpus_build_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    inputs = BuildInputs(
        root=args.root,
        cfg=cfg,
        cfg_sha256=cfg_sha,
        cache=cache,
        release_cache=_release_cache(args, cfg),
        manifest_path=args.root / cfg.paths.manifest,
        parsing=parsing,
        parsing_sha256=parsing_sha,
        chunking=chunking,
        chunking_sha256=chunking_sha,
    )
    try:
        result = run_build(inputs, check=args.check)
    except BuildError as exc:  # expected failures; unexpected exceptions propagate unchanged
        for line in exc.recovered:
            print(f"recovered from an interrupted build: {line}")
        print(f"build stopped at: {exc.stage}")
        for line in exc.problems:
            print(f"  PROBLEM {line}")
        if not exc.outputs_unchanged:
            print(
                "RESULT: FAILED (outputs may be partly changed; the journal and backups are kept; "
                "run `vrag corpus build` again to recover)"
            )
        elif exc.recovered:
            print(
                "RESULT: FAILED (the previous consistent output set was restored from the "
                "interrupted build; no new output was written)"
            )
        else:
            print("RESULT: FAILED (no output was changed)")
        return exc.exit_code
    for line in result.recovered:
        print(f"recovered from an interrupted build: {line}")
    for check in result.checks:
        print(f"verified: {check}")
    for entry in output_entries(result.outputs):
        path = str(entry["path"])
        state = (
            (
                "MISSING"
                if f"{path}: missing" in result.problems
                else "DIFFERS"
                if any(p.startswith(path + ":") for p in result.problems)
                else "ok"
            )
            if args.check
            else ("written" if path in result.written else "unchanged")
        )
        records = "" if entry["records"] is None else f"{entry['records']} records, "
        print(f"  {path}: {records}{entry['bytes']} bytes, {entry['sha256']} [{state}]")
    print(f"counts: {json.dumps(result.counts, sort_keys=True)}")
    for line in result.problems:
        print(f"  PROBLEM {line}")
    print(f"RESULT: {'OK' if not result.problems else 'FAILED'}")
    return EXIT_OK if not result.problems else EXIT_FAILURE


def _committable_files(root: Path) -> list[Path] | None:
    """Tracked plus untracked-but-not-ignored files (S5b-D5); None if git cannot list them."""
    try:
        repo = GitRepo(root)
        listed = repo.run("ls-files", "-z") + repo.run(
            "ls-files", "-z", "--others", "--exclude-standard"
        )
    except (GitError, OSError):
        return None
    names = {n.decode("utf-8") for n in listed.split(b"\0") if n}
    return sorted(root / n for n in names if (root / n).is_file())


def _cmd_validate(args: argparse.Namespace, settings: Settings) -> int:
    try:
        cfg, cfg_sha, cache = _load(args)
        parsing, parsing_sha = load_parsing_config(args.root / args.parsing)
        chunking, chunking_sha = load_chunking_config(args.root / args.chunking)
    except (OSError, ValidationError, ValueError) as exc:
        log.error("corpus_validate_failed", extra={"error": f"{type(exc).__name__}: {exc}"})
        return EXIT_USAGE
    release_cache = _release_cache(args, cfg)
    inputs = ValidateInputs(
        root=args.root,
        cfg=cfg,
        cfg_sha256=cfg_sha,
        manifest_path=args.root / cfg.paths.manifest,
        parsing=parsing,
        parsing_sha256=parsing_sha,
        chunking=chunking,
        chunking_sha256=chunking_sha,
        cache=cache if (cache / ".git").is_dir() else None,
        release_cache=release_cache if release_cache.is_dir() else None,
        committable_files=_committable_files(args.root),
    )
    try:
        report = validate_corpus(inputs)
    except ValidationUsageError as exc:
        print(f"corpus manifest cannot be loaded: {exc}")
        print("RESULT: FAILED (missing input)")
        return EXIT_USAGE
    for rel in report.missing:
        print(f"MISSING {rel}")
    for finding in report.errors:
        print(f"ERROR   [{finding.rule}] {finding.message}")
    for finding in report.warnings:
        print(f"WARNING [{finding.rule}] {finding.message}")
    print(f"counts: {json.dumps(report.counts, sort_keys=True)}")
    if report.missing:
        print(f"RESULT: FAILED ({len(report.missing)} required outputs missing)")
    elif report.errors:
        print(f"RESULT: FAILED ({len(report.errors)} errors, {len(report.warnings)} warnings)")
    else:
        print(f"RESULT: PASSED ({len(report.warnings)} warnings)")
    return report.exit_code


def _cmd_check_upstream(args: argparse.Namespace, settings: Settings) -> int:
    """S6b: exit 0 no drift, 1 drift, 2 incomplete (docs/design/RAG-M3_S6_ci.md §4). Every
    failure to complete the check is exit 2; only exception type names are ever reported."""
    try:
        cfg, _ = load_config(args.root / args.config)
    except Exception as exc:
        log.error("corpus_check_upstream_failed", extra={"error": type(exc).__name__})
        print("RESULT: INCOMPLETE (configuration unreadable)")
        return EXIT_USAGE
    try:
        manifest, problems = load_manifest(args.root / cfg.paths.manifest)
    except Exception as exc:  # for example an unreadable part file or directory
        log.error("corpus_check_upstream_failed", extra={"error": type(exc).__name__})
        manifest, problems = None, [type(exc).__name__]
    if manifest is None or problems:
        print(f"corpus manifest unreadable or inconsistent ({len(problems)} problems)")
        print("RESULT: INCOMPLETE (corpus manifest unreadable)")
        return EXIT_USAGE
    try:
        report = upstream.check_upstream(cfg, manifest, upstream.default_fetch())
        lines = report.lines()
    except Exception as exc:
        log.error("corpus_check_upstream_failed", extra={"error": type(exc).__name__})
        print(f"RESULT: INCOMPLETE (check failed unexpectedly: {type(exc).__name__})")
        return EXIT_USAGE
    for line in lines:
        print(line)
    return report.exit_code
