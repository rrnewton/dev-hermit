#!/usr/bin/env bash
# Re-run the recheck. SEQUENTIAL BY CONSTRUCTION -- see README "Slow-drain".
set -u
E="$(cd "$(dirname "$0")" && pwd)"
H="${HERMIT:?set HERMIT to the hermit binary under test}"
for g in clean_ctrl mut_stdout mut_exit mut_detlog_only mut_addr mut_path; do
  rm -f "/tmp/w2state_$g" /tmp/w2path_${g}_*
  s=$(date +%s)
  timeout 500 "$H" run --strict --verify --verify-strict --verify-allow both \
    --base-env=minimal --max-timeslice=disabled --tmp=/tmp \
    --verify-json "$E/logs/$g.json" -- "$E/mutants/$g" \
    > "$E/logs/$g.out" 2> "$E/logs/$g.err"
  echo "$? $(( $(date +%s) - s ))" > "$E/logs/$g.rcsecs"
done
