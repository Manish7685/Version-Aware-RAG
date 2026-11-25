"""``vrag serve``: run the FastAPI backend (and the built frontend, when present) with uvicorn."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from version_aware_rag.settings import Settings


def add_serve_command(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    s = sub.add_parser("serve", help="Run the HTTP API and UI (RAG-M11).")
    s.add_argument("--root", type=Path, default=Path())
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument(
        "--no-warm",
        action="store_true",
        help="Skip loading the indexes and models before serving (first requests are then slow).",
    )
    s.set_defaults(handler=_cmd_serve)


def _cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    from version_aware_rag.api.app import Engine, create_app

    index = args.root / "data/build/index/bm25"
    if not index.is_dir():
        print("RESULT: INCOMPLETE (no retrieval index; run `vrag index build` first)")
        return 2
    engine = Engine(args.root)
    if not args.no_warm:
        # Load the corpus, indexes and query encoders once, so no user waits for a cold start.
        t0 = time.perf_counter()
        engine.load()
        for method in ("hybrid", "rerank"):
            if method in engine.retrievers:
                engine.retrievers[method].retrieve("warm up", None, 1)
        print(f"warmed {sorted(engine.retrievers)} in {time.perf_counter() - t0:.0f} s")
    uvicorn.run(create_app(args.root, engine), host=args.host, port=args.port, log_level="info")
    return 0
