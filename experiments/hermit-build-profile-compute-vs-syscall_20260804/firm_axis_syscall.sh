#!/usr/bin/env bash
# Firm the two weak cells for the build-profile answer: syscall_bound runtime
# under release-o0 (was N=1) and debug (never measured). CPU-s is the metric
# (contention-proof); unpinned is fine. Private OUT so shared results.csv is safe.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
export OUT="$HERE/results.axis-firm.csv"
export GUESTS="syscall_bound"
rm -f "$OUT"
echo "=== firm-axis start $(date -u +%FT%TZ) PGID=$$ ==="
bash "$HERE/harness.sh" release-o0 "$HERE/target-release-o0/release/hermit" 3
bash "$HERE/harness.sh" debug      "$HERE/target-debug/debug/hermit"       3
echo "=== firm-axis DONE $(date -u +%FT%TZ) ==="
cat "$OUT"
