#!/usr/bin/env bash
# CARGO_BUILD_JOBS speedup + peak-memory sweep for the CI critical-path release build.
#
# Measures wall-time and TRUE peak aggregate RSS (summed across the whole build
# process group: cargo + rustc + cc1 + cmake/make for DynamoRIO) for a CLEAN
# release build of the exact ci/dag/portable.json `build.dbi_release` node:
#
#   cargo build --release --locked -p hermit --features third-party-backends \
#     -p detcore-dbi -p hermit-install
#
# Usage: run-sweep.sh <label> <cpuset-or-"all"> <j1,j2,...>
#   label     : tag written into results.csv (e.g. full316 | cpuset4)
#   cpuset    : "all" for the whole box, or a taskset -c spec like "0-3"
#   jlist     : comma-separated CARGO_BUILD_JOBS values, e.g. 8,32,64,128,316
#
# Each build uses a throwaway CARGO_TARGET_DIR that is removed before the run so
# every measurement is a true clean build. Results append to results.csv.
set -u

HERMIT_SRC="${HERMIT_SRC:-$HOME/work/dev-hermit/worktrees/ci/hermit}"
EXP_DIR="$(cd "$(dirname "$0")" && pwd)"
CSV="$EXP_DIR/results.csv"
SCRATCH="${SCRATCH:-/home/newton/work/dev-hermit/scratch/cbj-sweep-target}"

label="$1"; cpuset="$2"; IFS=',' read -r -a jlist <<< "$3"

if [[ ! -f "$CSV" ]]; then
  echo "label,cpuset,jobs,wall_s,peak_rss_mb,exit_code,timestamp_unix" > "$CSV"
fi

sample_peak_rss() {
  # Sum RSS (KB) over every process in process group $1; print max seen.
  local pgid="$1" max=0 cur
  while kill -0 -- "-$pgid" 2>/dev/null; do
    cur=$(ps -e -o pgid=,rss= 2>/dev/null | awk -v pg="$pgid" '$1==pg{s+=$2} END{print s+0}')
    (( cur > max )) && max=$cur
    sleep 2
  done
  echo "$max"  # KB
}

for j in "${jlist[@]}"; do
  echo "=== [$label] clean release build @ CARGO_BUILD_JOBS=$j cpuset=$cpuset ==="
  rm -rf "$SCRATCH"; mkdir -p "$SCRATCH"

  pre="taskset -c $cpuset"
  [[ "$cpuset" == "all" ]] && pre=""

  peakfile="$(mktemp)"
  start=$(date +%s)

  # Launch build in its own process group so the sampler can sum the whole tree.
  setsid bash -c "
    cd '$HERMIT_SRC' || exit 97
    exec $pre env CARGO_BUILD_JOBS=$j THIRD_PARTY_BUILD_JOBS=$j \
      CARGO_TARGET_DIR='$SCRATCH' \
      HERMIT_INSTALL_FORCE_RESTAGE=sweep-$label-$j \
      cargo build --release --locked -p hermit --features third-party-backends \
        -p detcore-dbi -p hermit-install
  " >"$EXP_DIR/build-$label-j$j.log" 2>&1 &
  bpid=$!
  pgid=$(ps -o pgid= -p "$bpid" | tr -d ' ')

  ( sample_peak_rss "$pgid" > "$peakfile" ) &
  spid=$!

  wait "$bpid"; rc=$?
  wait "$spid" 2>/dev/null
  end=$(date +%s)

  wall=$(( end - start ))
  peak_kb=$(cat "$peakfile" 2>/dev/null || echo 0)
  peak_mb=$(( peak_kb / 1024 ))
  rm -f "$peakfile"

  echo "$label,$cpuset,$j,$wall,$peak_mb,$rc,$end" >> "$CSV"
  echo "    -> wall=${wall}s peak_rss=${peak_mb}MB exit=$rc"
done

rm -rf "$SCRATCH"
echo "=== sweep [$label] complete; results in $CSV ==="
