#!/usr/bin/env bash
# Controlled-load demonstration of the CPU-time-vs-wall thesis for the
# "Centralized test manifest and inventory" node (./ci/test_harness.sh validate).
#
# Method: confine the node AND a set of CPU burners to the same 4-CPU cpuset via
# taskset. With 8 burners on 4 CPUs (2x oversubscription) the node competes for
# CPU, so WALL should inflate ~2x while CPU time (user+sys, from getrusage /
# /usr/bin/time -v) stays ~constant. Node runs SEQUENTIALLY here (no shared-
# checkout races); each run writes its own .time file (no append interleave).
set -u
cd ~/work/dev-hermit/hermit
CSV=/tmp/vtimeout-load.csv
CPUS=0-3
NBURN=8
NRUN=10
echo "phase,idx,wall_s,user_s,sys_s,cpu_s,pct_cpu,maxrss_kb,exit" > "$CSV"

run_one() {
  local phase=$1 idx=$2
  local t=/tmp/vtl_${phase}_${idx}.time
  taskset -c "$CPUS" /usr/bin/time -v ./ci/test_harness.sh validate >/dev/null 2>"$t"
  local ex=$?
  local wall user sys pct rss
  wall=$(awk -F': ' '/Elapsed/{print $2}' "$t")
  user=$(awk -F': ' '/User time/{print $2}' "$t")
  sys=$(awk -F': ' '/System time/{print $2}' "$t")
  pct=$(awk -F': ' '/Percent of CPU/{print $2}' "$t" | tr -d '%')
  rss=$(awk -F': ' '/Maximum resident/{print $2}' "$t")
  local ws cpu
  ws=$(python3 -c "p='$wall'.split(':');print(float(p[0])*60+float(p[1]) if len(p)==2 else float(p[0]))" 2>/dev/null)
  cpu=$(python3 -c "print(round($user+$sys,2))" 2>/dev/null)
  echo "$phase,$idx,$ws,$user,$sys,$cpu,$pct,$rss,$ex" >> "$CSV"
}

# Baseline (no burners) confined to the same cpuset, for apples-to-apples.
for i in $(seq 1 5); do run_one base $i; done

# Start burners on the same cpuset.
pids=()
for i in $(seq 1 $NBURN); do
  taskset -c "$CPUS" bash -c 'while :; do :; done' &
  pids+=($!)
done

# Loaded runs.
for i in $(seq 1 $NRUN); do run_one load $i; done

# Kill burners.
for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
wait 2>/dev/null

echo "DONE $(date -Is)" >> "$CSV"
