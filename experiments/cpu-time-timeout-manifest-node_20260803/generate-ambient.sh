#!/usr/bin/env bash
# Generate wall + cpu(user+sys) samples for `./ci/test_harness.sh validate`.
cd ~/work/dev-hermit/hermit
CSV=/tmp/vtimeout-samples.csv
echo "phase,idx,wall_s,user_s,sys_s,cpu_s,pct_cpu,maxrss_kb,exit" > "$CSV"
run_one() {
  local phase=$1 idx=$2 t=/tmp/vt_${phase}_${idx}.time
  /usr/bin/time -v ./ci/test_harness.sh validate >/dev/null 2>"$t"
  local ex=$?
  local wall user sys pct rss
  wall=$(awk -F': ' '/Elapsed/{print $2}' "$t")
  user=$(awk -F': ' '/User time/{print $2}' "$t")
  sys=$(awk -F': ' '/System time/{print $2}' "$t")
  pct=$(awk -F': ' '/Percent of CPU/{print $2}' "$t" | tr -d '%')
  rss=$(awk -F': ' '/Maximum resident/{print $2}' "$t")
  # wall mm:ss.xx -> seconds
  local ws=$(python3 -c "import sys;p='$wall'.split(':');print(float(p[0])*60+float(p[1]) if len(p)==2 else float(p[0]))" 2>/dev/null)
  local cpu=$(python3 -c "print(round($user+$sys,2))" 2>/dev/null)
  echo "$phase,$idx,$ws,$user,$sys,$cpu,$pct,$rss,$ex" >> "$CSV"
}
# Phase A: 20 sequential (ambient shared-box load)
for i in $(seq 1 20); do run_one seq $i; done
# Phase B: 8 concurrent (self-induced load — wall should inflate, cpu should not)
for i in $(seq 1 8); do run_one conc $i & done
wait
echo "DONE $(date -Is)" >> "$CSV"
