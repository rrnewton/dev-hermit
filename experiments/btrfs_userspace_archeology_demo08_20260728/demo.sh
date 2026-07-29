#!/usr/bin/env bash
# Demo 08 — deterministic reproduction of a historical, fuzzer-found btrfs-progs
# userspace FS-logic bug (kdave/btrfs-progs issue #207) under hermit.
#
# Bug:   BUG_ON(eb->refs < 0) abort in extent_io.c during open_ctree(), caused by
#        a fuzzed image whose log_root bytenr (61440) overlaps the primary
#        superblock physical range [61440,77824). Purely userspace btree/disk-io
#        logic; found by a fuzzer; reported against v5.2.1/v5.2.2.
# Buggy: 3dcce48fd7038efbf0c40707d3ff26c1c080ae50  (btrfs-progs v5.4, parent of fix)
# Fixed: 6a061158617f3aa670df861c912ef76d11aa69e4  (btrfs-progs: disk-io: Verify
#        the bytenr passed in is mapped for read_tree_block(); first in v5.4.1)
#
# This script assumes the buggy/fixed btrfs binaries and the reproducer image are
# already staged (see README.md "Reproduce from scratch"). It runs the exact,
# recorded hermit configs and checks the differential + bitwise reproducibility.
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
B="${B:-$ROOT/ignored/bp-buggy/btrfs}"
F="${F:-$ROOT/ignored/bp-fixed/btrfs}"
IMG="${IMG:-$ROOT/ignored/demo08-repro/crash.btrfs}"
OUT="${OUT:-/tmp/d08-demo}"
mkdir -p "$OUT"

# hermit prints its own ISO-timestamped tracing lines to stderr; the guest's own
# output (the crash / the graceful error) is everything else.
guest() { grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"; }

# ---- EXACT RECORDED CONFIGS -------------------------------------------------
STRICT=(run --strict --seed 1)
CHAOS=(run --chaos --chaos-target-races --sched-seed 1 --rng-seed 1 \
       --target-timeslice 100000 --max-timeslice 1000000000)

run() { # <label> <binary> <hermit-args...>
  local label="$1" bin="$2"; shift 2
  "$SC" --timeout 300 --output "$OUT/$label.out" -- "$H" "$@" -- "$bin" check "$IMG" >/dev/null 2>&1
  local st; st=$(grep -o 'command_exit=[0-9]*' "$OUT/$label.out.status" 2>/dev/null | cut -d= -f2)
  printf '%-22s exit=%-4s guest_sha=%s\n' "$label" "${st:-?}" "$(guest "$OUT/$label.out" | sha256sum | cut -c1-32)"
}

echo "== BUGGY ($(basename "$(dirname "$B")")) =="
run buggy_strict       "$B" "${STRICT[@]}"
run buggy_strict_rep   "$B" "${STRICT[@]}"     # repeat -> must match (bitwise repro)
run buggy_chaos        "$B" "${CHAOS[@]}"
run buggy_chaos_rep    "$B" "${CHAOS[@]}"      # repeat -> must match
echo "== FIXED ($(basename "$(dirname "$F")")) =="
run fixed_strict       "$F" "${STRICT[@]}"
run fixed_chaos        "$F" "${CHAOS[@]}"

echo
echo "== ASSERTIONS =="
cmp -s <(guest "$OUT/buggy_strict.out") <(guest "$OUT/buggy_strict_rep.out") \
  && echo "PASS strict buggy bitwise-reproducible" || echo "FAIL strict repro"
cmp -s <(guest "$OUT/buggy_chaos.out")  <(guest "$OUT/buggy_chaos_rep.out")  \
  && echo "PASS chaos  buggy bitwise-reproducible" || echo "FAIL chaos repro"
grep -q 'BUG_ON `eb->refs < 0`' <(guest "$OUT/buggy_strict.out") \
  && echo "PASS buggy aborts with the issue-#207 BUG_ON" || echo "FAIL no BUG_ON"
grep -q 'cannot open file system' <(guest "$OUT/fixed_strict.out") \
  && echo "PASS fixed rejects image gracefully (differential)" || echo "FAIL differential"
