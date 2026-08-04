#!/bin/bash
# measure_lto_compile.sh — three-row LTO compile-wall comparison for the CI release profile.
#
# Question (owner task ci-build-profile-release-no-lto-or-fast-lto):
#   full-LTO vs thin-LTO vs no-LTO, compile wall per row. ONLY THE TOTAL DECIDES,
#   but compile wall is the LTO-sensitive term (LTO is a link-time serial phase).
#
# Unit under test = EXACTLY what validate.sh builds for the release-consuming jobs:
#   cargo build --release -p hermit --features third-party-backends
# The ONLY knob varied across rows is CARGO_PROFILE_RELEASE_LTO. debug-assertions,
# overflow-checks and panic strategy are NOT touched by that env var and stay at the
# release-profile defaults (false/false/unwind) => semantic guard holds by construction.
#
# Each row builds from CLEAN into its own target dir so the wall reflects the real cold
# build (incremental would be ~0 and hide the LTO tail). Runs SERIALLY and boxed via
# systemd-run --user (asks systemd for a scope = still boxed, not a bypass) so it is a
# good citizen while hermit-kvm coalesces reverie.
set -u
HERMIT=/home/newton/work/dev-hermit/hermit
OUT=/home/newton/work/dev-hermit/experiments/ci-build-profile-lto_20260804
JOBS=32
QUOTA=3200%   # match JOBS so boxing does not throttle below the parallelism we measure
CSV="$OUT/compile-lto.csv"
echo "row,lto,jobs,wall_s,user_s,sys_s,cpu_s,loadavg_1m,rc" > "$CSV"

run_row () {
  local row="$1" lto="$2"
  local tdir="$OUT/target-lto-$row"
  local stamp; stamp=$(date -u +%Y%m%dT%H%M%SZ)
  local unit="ltocompile-$row-$stamp"
  local log="$OUT/build-$row-$stamp.log"
  local timef="$OUT/time-$row.txt"
  rm -rf "$tdir"                       # CLEAN: real cold build
  local la; la=$(cut -d' ' -f1 /proc/loadavg)
  echo "=== row=$row lto=$lto jobs=$JOBS load1m=$la target=$tdir ==="
  systemd-run --user --wait --collect --quiet --unit="$unit" \
    --property=CPUQuota=$QUOTA \
    --working-directory="$HERMIT" \
    --setenv=HOME=/home/newton --setenv=PATH="$PATH" \
    --setenv=CARGO_TARGET_DIR="$tdir" \
    --setenv=CARGO_BUILD_JOBS=$JOBS \
    --setenv=CARGO_PROFILE_RELEASE_LTO="$lto" \
    /usr/bin/time -v -o "$timef" \
    /bin/bash -c "exec with-proxy cargo build --release -p hermit --features third-party-backends" \
    > "$log" 2>&1
  local rc=$?
  # parse /usr/bin/time -v
  local wall user sys
  wall=$(awk -F': ' '/Elapsed \(wall/{print $2}' "$timef")
  user=$(awk -F': ' '/User time/{print $2}' "$timef")
  sys=$(awk -F': ' '/System time/{print $2}' "$timef")
  # wall is h:mm:ss or m:ss -> seconds
  local wsec; wsec=$(awk -v t="$wall" 'BEGIN{n=split(t,a,":"); s=0; for(i=1;i<=n;i++) s=s*60+a[i]; printf "%.2f", s}')
  local cpu; cpu=$(awk -v u="$user" -v s="$sys" 'BEGIN{printf "%.2f", u+s}')
  echo "$row,$lto,$JOBS,$wsec,$user,$sys,$cpu,$la,$rc" >> "$CSV"
  echo "row=$row rc=$rc wall=${wsec}s cpu=${cpu}s (log=$log)"
  ls -la "$tdir/release/hermit" 2>/dev/null || echo "  (no hermit binary produced)"
}

run_row no-lto   false
run_row thin-lto thin
run_row full-lto fat

echo "=== DONE. Results: ==="
cat "$CSV"
