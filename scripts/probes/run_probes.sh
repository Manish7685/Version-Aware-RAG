#!/usr/bin/env bash
# Re-run the RAG-M1 behavioural probes (manifest §5.1) in one hash-verified venv per DuckDB
# package, then compare the results with the expected RAG-M1 run.
#
# Usage: scripts/probes/run_probes.sh [OUTPUT_JSONL]
#   Default output: results/probes/probe_results_<os>.jsonl
# Requires: uv on PATH, or UV="python -m uv". Works on Linux, macOS and Git Bash on Windows.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
UV="${UV:-uv}"
PY_VERSION="3.12.16"
VERSIONS=(1.1.3 1.2.2 1.3.2 1.4.5 1.5.6 2.0.1)
EXPECTED_SCRIPT_SHA256="732c93e29abc589710a7d1438c8f32db64e1f3a9163968a23d845f5705ad7afc"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) OS_TAG="windows"; BIN="Scripts"; EXE=".exe" ;;
  Darwin*)              OS_TAG="macos";   BIN="bin";     EXE="" ;;
  *)                    OS_TAG="linux";   BIN="bin";     EXE="" ;;
esac

OUT="${1:-$ROOT/results/probes/probe_results_${OS_TAG}.jsonl}"
ENVS="$ROOT/.probe-envs"
mkdir -p "$(dirname "$OUT")" "$ENVS"

# Refuse to run a probe script that differs from the one pinned in the version manifest.
if command -v sha256sum >/dev/null; then
  actual_sha="$(sha256sum "$HERE/rag_m1_probes.py" | cut -d' ' -f1)"
else
  actual_sha="$(shasum -a 256 "$HERE/rag_m1_probes.py" | cut -d' ' -f1)"
fi
if [[ "$actual_sha" != "$EXPECTED_SCRIPT_SHA256" ]]; then
  echo "probe script sha256 mismatch: $actual_sha (expected $EXPECTED_SCRIPT_SHA256)" >&2
  exit 2
fi

$UV python install "$PY_VERSION" >&2
: > "$OUT"
for v in "${VERSIONS[@]}"; do
  env_dir="$ENVS/duckdb-$v"
  $UV venv -q --allow-existing --python "$PY_VERSION" "$env_dir" >&2
  $UV pip install -q --python "$env_dir/$BIN/python$EXE" --require-hashes \
    -r "$HERE/requirements/duckdb-$v.txt" >&2
  "$env_dir/$BIN/python$EXE" -I "$HERE/rag_m1_probes.py" >> "$OUT"
done
echo "wrote $OUT" >&2

cd "$ROOT"
$UV run --locked vrag probes compare \
  --expected "$HERE/expected/rag_m1_windows_probe_results.jsonl" \
  --actual "$OUT"
