#!/bin/bash
# Measure RCB PMU skid (overshoot beyond programmed target) under VARYING LOAD.
# Self-cleaning: all burners are killed on exit. Read-only w.r.t. the repo.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
RAW="$HERE/ignored"
CSV="$HERE/results.csv"
SKID="/tmp/pmu_skid_h250"
BURN="$HERE/ignored/burner"
PIN_CPU=8
PERIOD=100000
ITERS=1000
REPS=3
NCORE=$(nproc)

mkdir -p "$RAW"
cc -O2 "$HERE/burner.c" -o "$BURN" || { echo "burner build failed"; exit 1; }
[ -x "$SKID" ] || { echo "missing $SKID"; exit 1; }

BURN_PIDS=()
cleanup() { for p in "${BURN_PIDS[@]:-}"; do kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap cleanup EXIT INT TERM

add_burners() {  # $1 = how many to add
  for _ in $(seq 1 "$1"); do "$BURN" & BURN_PIDS+=($!); done
}

echo "level,added_burners,rep,loadavg1,skid_min,skid_max,skid_mean,skid_p99,rec_margin" > "$CSV"

run_level() {  # $1 = label, $2 = total added burners target
  local label="$1" target="$2"
  local have=${#BURN_PIDS[@]}
  if [ "$target" -gt "$have" ]; then add_burners $((target - have)); fi
  sleep 8   # let load settle
  for r in $(seq 1 "$REPS"); do
    local log="$RAW/skid_${label}_r${r}.txt"
    "$SKID" --iterations "$ITERS" --period "$PERIOD" --cpu "$PIN_CPU" > "$log" 2>&1
    local la; la=$(cut -d' ' -f1 /proc/loadavg)
    local line; line=$(grep '^Skid' "$log")
    local mn mx me p9 rm
    mn=$(echo "$line" | sed -n 's/.*min=\([-0-9]*\).*/\1/p')
    mx=$(echo "$line" | sed -n 's/.*max=\([-0-9]*\).*/\1/p')
    me=$(echo "$line" | sed -n 's/.*mean=\([0-9.]*\).*/\1/p')
    p9=$(echo "$line" | sed -n 's/.*p99=\([-0-9]*\).*/\1/p')
    rm=$(grep 'Recommended margin' "$log" | sed -n 's/.*margin: \([0-9]*\).*/\1/p')
    echo "$label,$target,$r,$la,$mn,$mx,$me,$p9,$rm" >> "$CSV"
    echo "[$label added=$target rep=$r] load=$la skid: min=$mn max=$mx mean=$me p99=$p9 rec=$rm"
  done
}

echo "### box: $NCORE cores; period=$PERIOD iters=$ITERS pin=cpu$PIN_CPU reps=$REPS"
run_level baseline 0
run_level load_half   $((NCORE/2))
run_level load_1x     $((NCORE))
run_level load_2x     $((NCORE*2))
echo "### done; killing burners"
