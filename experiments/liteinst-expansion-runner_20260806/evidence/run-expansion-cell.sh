#!/usr/bin/env bash
# run-expansion-cell.sh <repo> <cell_dir> <lane> <bucket> <test_id> <mode> <backend>
# Captures per-cell evidence; exits with the target backend's outcome.
set -u
repo="$1"; cell="$2"; lane="$3"; bucket="$4"; test="$5"; mode="$6"; backend="$7"
mkdir -p "$cell"

run_one() { # <backend> <outdir>
  local be="$1" out="$2"
  mkdir -p "$out"
  ( cd "$repo" && ./ci/test_harness.sh run \
      --lane "$lane" --category "$bucket" --test "$test" --mode "$mode" \
      --backend "$be" --include-manual \
      --results "$out/results.jsonl" ) >"$out/stdout" 2>"$out/stderr"
  local rc=$?
  # INFO log = the --log=info stream the harness routes to stderr.
  grep -aE ' (INFO|WARN|ERROR) ' "$out/stderr" > "$out/info.log" 2>/dev/null || true
  # Machine-readable exec stats = the harness's own per-cell JSONL.
  if [ -s "$out/results.jsonl" ]; then
    cp "$out/results.jsonl" "$out/stats.json"
  else
    printf '{"backend":"%s","exit":%d,"note":"no harness JSONL emitted"}\n' "$be" "$rc" > "$out/stats.json"
  fi
  return $rc
}

run_one "$backend" "$cell"
rc=$?
if [ "$backend" != "ptrace" ]; then
  run_one ptrace "$cell/ptrace-ref" || true
fi
exit $rc
