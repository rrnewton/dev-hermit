#!/usr/bin/env bash
# Re-run ONLY the runtime harness (bins already built; compile.csv already good).
# Launched NON-NESTED (not inside a systemd-run unit) so harness.sh's per-measure
# `systemd-run --user` boxing can reach the user bus. Do NOT run build_and_run.sh
# here — that would overwrite compile.csv with incremental ~0s times.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
N="${1:-7}"
REL=$HERE/target-release/release/hermit
RO0=$HERE/target-release-o0/release/hermit
DBG=$HERE/target-debug/debug/hermit
for b in "$REL" "$RO0" "$DBG"; do [ -x "$b" ] || { echo "MISSING BIN $b"; exit 5; }; done
# ISOLATE: the experiment dir is SHARED; another agent may run harness.sh here
# concurrently against the plain results.csv. Use a PRIVATE output so we never
# collide, and NEVER rm the shared results.csv (that would delete their data).
export OUT="$HERE/results.mine.csv"
export RESULTS_CSV="results.mine.csv"     # analyze.py honors this
rm -f "$OUT"                              # fresh; harness re-creates header (private file only)
echo "=== runtime harness (N=$N), non-nested, $(date -u +%FT%TZ) ==="
bash "$HERE/harness.sh" native     "-"    "$N"
bash "$HERE/harness.sh" release    "$REL" "$N"
bash "$HERE/harness.sh" release-o0 "$RO0" "$N"
bash "$HERE/harness.sh" debug      "$DBG" "$N"
echo "=== harness DONE $(date -u +%FT%TZ); running analyze ==="
python3 "$HERE/analyze.py"
echo "=== ALL DONE ==="
