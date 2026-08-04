#!/bin/bash
# LiteInst instrumentation-stats measurement harness.
# Runs `hermit run --backend liteinst` under RUST_LOG=hermit::backend_stats=info,
# extracts the single "backend run complete ... stats=..." line, and records it
# with full conditions (guest, workload, run#, mode) to a CSV of raw stat lines.
set -u
HERMIT=/home/newton/work/dev-hermit/worktrees/liteinst/hermit/target/debug/hermit
SL=/home/newton/work/dev-hermit/experiments/dbi_perf_leader_baseline_20260801/src/syscall_loop
BH=/home/newton/work/dev-hermit/experiments/dbi_perf_leader_baseline_20260801/src/branch_heavy
OUT=/home/newton/work/dev-hermit/scratch/liteinst-stats-run/raw-stats.txt
: > "$OUT"

run_one() {
  local label="$1"; local mode="$2"; local runidx="$3"; shift 3
  local line
  line=$(timeout 300 env RUST_LOG=hermit::backend_stats=info "$HERMIT" run --backend liteinst "$@" >/dev/null 2>/tmp/liteinst-stats.err; \
         grep -m1 "backend run complete" /tmp/liteinst-stats.err)
  local rc=$?
  if [ -z "$line" ]; then
    line="(NO STATS LINE; rc=$rc; err_tail=$(tail -1 /tmp/liteinst-stats.err 2>/dev/null))"
  fi
  echo "COND label=$label mode=$mode run=$runidx :: $line" >> "$OUT"
  echo "  [$label/$mode run=$runidx] done"
}

echo "=== syscall_loop getpid x N (per-syscall regime), mode=run (L1, no strict) ==="
for N in 10000 100000 1000000; do
  for r in 1 2 3; do
    run_one "syscall_loop_getpid_N=$N" run $r -- "$SL" "$N"
  done
done

echo "=== branch_heavy (compute-bound, few syscalls), mode=run ==="
for r in 1 2 3; do
  run_one "branch_heavy_2000000" run $r -- "$BH" 2000000
done

echo "=== real-guest spread, mode=run ==="
for r in 1 2 3; do run_one "bin_echo_hello" run $r -- /bin/echo hello; done
for r in 1 2 3; do run_one "bin_ls_slash" run $r -- /bin/ls /; done
for r in 1 2; do run_one "python3_print" run $r -- /usr/bin/python3 -c 'print(sum(range(1000)))'; done

echo "=== syscall_loop N=100000 under --strict --verify (L2) for mode comparison ==="
for r in 1 2; do run_one "syscall_loop_getpid_N=100000" strict_verify $r --strict --verify -- "$SL" 100000; done

echo "ALL DONE"
