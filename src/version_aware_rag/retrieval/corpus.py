"""The retrieval corpus: permitted chunks of every corpus version, with version compatibility."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from version_aware_rag.corpus.compat import is_compatible

VERSIONS = ("1.2", "1.3", "1.4", "1.5", "2.0")
PERMITTED_KINDS = frozenset({"docs_page", "migration_guide"})


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    version: str
    source_kind: str
    source_type: str
    introduced_in: str | None
    source_path: str
    section_path: tuple[str, ...]
    content: str
    line_start: int = 0  # source line range (1-based, inclusive); 0 when unknown
    line_end: int = 0


@dataclass
class RetrievalCorpus:
    chunks: list[Chunk]
    packages: Mapping[str, str]  # docs series -> behaviour package

    @cached_property
    def by_id(self) -> dict[str, int]:
        return {c.chunk_id: i for i, c in enumerate(self.chunks)}

    def compatible(self, index: int, target: str) -> bool:
        c = self.chunks[index]
        return is_compatible(c.version, c.source_type, c.introduced_in, target, self.packages)

    @cached_property
    def _compat_masks(self) -> dict[str, list[bool]]:
        return {
            v: [self.compatible(i, v) for i in range(len(self.chunks))]
            for v in self.packages
            if v in VERSIONS
        }

    def mask(self, target: str) -> list[bool]:
        return self._compat_masks[target]


def load_corpus(
    chunk_dir: Path, packages: Mapping[str, str], versions: Sequence[str] = VERSIONS
) -> RetrievalCorpus:
    chunks: list[Chunk] = []
    for v in versions:
        for line in (chunk_dir / f"{v}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            row = json.loads(line)
            if row["source_kind"] not in PERMITTED_KINDS:
                continue  # restricted (release posts): never indexed
            chunks.append(
                Chunk(
                    row["chunk_id"],
                    row["version"],
                    row["source_kind"],
                    row["source_type"],
                    row["introduced_in"],
                    row["source_path"],
                    tuple(row["section_path"]),
                    row["content"],
                    int(row.get("line_start") or 0),
                    int(row.get("line_end") or 0),
                )
            )
    if any(c.source_kind not in PERMITTED_KINDS for c in chunks):
        raise RuntimeError("a restricted chunk reached the retrieval corpus")
    return RetrievalCorpus(chunks, dict(packages))
