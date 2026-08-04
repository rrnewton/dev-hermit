#!/bin/bash
# measure_lto_runtime.sh — runtime delta of the three LTO release binaries.
#
# "Test wall" for the release-consuming CI jobs (strict_compat + backend-parity)
# is dominated by how fast the release `hermit` supervisor executes guests, so the
# LTO-sensitive term in test wall is the per-guest RUNTIME of the release binary.
# This measures that runtime for each of no-lto / thin-lto / full-lto.
#
# Protocol (benchmark skill): K=1 cgroup, SAME single fixed core for all variants
# (same-core placement dominates ptrace syscall wall & minimises variance), NO
# per-thread pinning inside the set, interleaved variant order, median of reps,
# raw rows retained. Correctness-gated on exit status. syscall_bound is the
# supervisor-sensitive workload; compute_bound is the guest-compute control.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ci-build-profile-lto_20260804
GBIN=/home/newton/work/dev-hermit/experiments/hermit-build-profile-compute-vs-syscall_20260804/guests/bin
CORE=8                 # fixed single core for the K=1 set (record it)
REPS=5
CSV="$EXP/runtime-lto.csv"
echo "variant,guest,rep,core,wall_s,user_s,sys_s,rc" > "$CSV"

declare -A BIN=(
  [no-lto]="$EXP/target-lto-no-lto/release/hermit"
  [thin-lto]="$EXP/target-lto-thin-lto/release/hermit"
  [full-lto]="$EXP/target-lto-full-lto/release/hermit"
)

# verify all three exist before starting
for v in no-lto thin-lto full-lto; do
  [ -x "${BIN[$v]}" ] || { echo "MISSING binary for $v: ${BIN[$v]} — aborting"; exit 2; }
done

run_one () {
  local variant="$1" guest="$2" rep="$3"
  local hb="${BIN[$variant]}"
  local tf; tf=$(mktemp)
  local unit="ltort-$variant-$guest-$rep-$(date -u +%H%M%S%N)"
  # K=1: pin the whole hermit process tree to ONE core via AllowedCPUs; kernel
  # schedules supervisor+guest within that single-core set (no per-thread pinning).
  systemd-run --user --wait --collect --quiet --unit="$unit" \
    --property=AllowedCPUs=$CORE \
    --setenv=HOME=/home/newton --setenv=PATH="$PATH" \
    /usr/bin/time -f '%e %U %S' -o "$tf" \
    "$hb" run -- "$GBIN/$guest" >/dev/null 2>/dev/null
  local rc=$?
  local line; line=$(cat "$tf" 2>/dev/null)
  local wall user sys; read -r wall user sys <<<"$line"
  echo "$variant,$guest,$rep,$CORE,${wall:-NA},${user:-NA},${sys:-NA},$rc" >> "$CSV"
  echo "  $variant/$guest rep$rep: wall=${wall}s rc=$rc"
  rm -f "$tf"
}

echo "=== runtime delta: K=1 core=$CORE reps=$REPS interleaved ==="
for rep in $(seq 1 $REPS); do
  for guest in syscall_bound compute_bound; do
    for variant in no-lto thin-lto full-lto; do   # interleave variants each rep
      run_one "$variant" "$guest" "$rep"
    done
  done
done
echo "=== DONE ==="; cat "$CSV"
