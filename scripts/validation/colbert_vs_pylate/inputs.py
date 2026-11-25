"""Inputs for the ColBERT equivalence check: synthetic text plus permitted MIT docs chunks."""

import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
OUT = Path(sys.argv[2])
queries = [
    "What does date_trunc return when applied to a DATE?",
    "How do I read a Parquet file?",
    "Which function packs values into a STRUCT?",
    "Is list_reverse available?",
]
docs = [
    "date_trunc truncates a date to the given precision and returns a TIMESTAMP.",
    "CREATE TABLE creates a new table in the catalog.",
]
wanted = 6
for line in (ROOT / "data/build/chunks/1.5.jsonl").read_text("utf-8").splitlines():
    row = json.loads(line)
    if row["source_kind"] == "docs_page" and 200 < len(row["content"]) < 2500:
        docs.append(row["content"])
        if len(docs) >= 2 + wanted:
            break
OUT.write_text(json.dumps({"queries": queries, "docs": docs}), "utf-8")
print(f"{len(queries)} queries, {len(docs)} documents")
