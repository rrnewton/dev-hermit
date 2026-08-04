#!/bin/bash
# measure_lto_runtime.sh (v3) — runtime delta of the three LTO release binaries.
#
# LTO only optimizes hermit's OWN user-space code. For the ptrace supervisor the
# hot metric is CPU time, split user (hermit code — LTO-sensitive) vs system
# (kernel ptrace round-trips — NOT LTO-touchable). CPU time is load-insensitive
# (same-core-pinning finding), so we run UNPINNED (fast, multi-core) and compare
# user/sys/wall across LTO levels. syscall_bound_100k is the supervisor-hot guest;
# compute_bound_80m is the guest-compute control (supervisor barely runs).
#
# Boxing note: we do NOT use systemd-run here — `systemd-run --user
# --property=AllowedCPUs=...` makes hermit exit 101 silently (scope/ptrace-setup
# interaction). Plain /usr/bin/time as a direct parent works fine. Placement is
# left to the kernel; the compared metric (CPU time) does not depend on it.
set -u
EXP=/home/newton/work/dev-hermit/experiments/ci-build-profile-lto_20260804
G=$EXP/guests-scaled
REPS=7
CSV="$EXP/runtime-lto.csv"
echo "variant,guest,rep,user_s,sys_s,cpu_s,wall_s,rc" > "$CSV"

declare -A BIN=(
  [no-lto]="$EXP/target-lto-no-lto/release/hermit"
  [thin-lto]="$EXP/target-lto-thin-lto/release/hermit"
  [full-lto]="$EXP/target-lto-full-lto/release/hermit"
)
for v in no-lto thin-lto full-lto; do
  [ -x "${BIN[$v]}" ] || { echo "MISSING $v"; exit 2; }
done

run_one () {
  local variant="$1" guest="$2" rep="$3"
  local hb="${BIN[$variant]}" tf; tf=$(mktemp)
  timeout 180 /usr/bin/time -v "$hb" run -- "$G/$guest" >/dev/null 2>"$tf"
  local rc=$?
  local u s w
  u=$(awk -F': ' '/User time/{print $2}' "$tf")
  s=$(awk -F': ' '/System time/{print $2}' "$tf")
  w=$(awk -F': ' '/Elapsed \(wall/{print $2}' "$tf")
  local wsec; wsec=$(awk -v t="$w" 'BEGIN{n=split(t,a,":");x=0;for(i=1;i<=n;i++)x=x*60+a[i];printf "%.2f",x}')
  local cpu; cpu=$(awk -v a="$u" -v b="$s" 'BEGIN{printf "%.2f",a+b}')
  echo "$variant,$guest,$rep,${u:-NA},${s:-NA},$cpu,$wsec,$rc" >> "$CSV"
  echo "  $variant/$guest rep$rep: user=${u} sys=${s} cpu=${cpu} wall=${wsec} rc=$rc"
  rm -f "$tf"
}

# warmup (unrecorded)
for guest in syscall_bound_100k compute_bound_80m; do
  for variant in no-lto thin-lto full-lto; do run_one "$variant" "$guest" 0 >/dev/null 2>&1; done
done
: > "$CSV"; echo "variant,guest,rep,user_s,sys_s,cpu_s,wall_s,rc" > "$CSV"

echo "=== runtime delta: unpinned, CPU-time metric, reps=$REPS interleaved ==="
for rep in $(seq 1 $REPS); do
  for guest in syscall_bound_100k compute_bound_80m; do
    for variant in no-lto thin-lto full-lto; do run_one "$variant" "$guest" "$rep"; done
  done
done
echo "=== DONE ==="; cat "$CSV"
