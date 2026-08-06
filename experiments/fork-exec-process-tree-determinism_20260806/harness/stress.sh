#!/usr/bin/env bash
# L4 leg: repeat each process-tree guest N times and require ONE process-event
# trace across all N, optionally with C runs in flight at once.
#
# WHY CONCURRENCY IS PART OF THE INSTRUMENT, NOT A STRESS GARNISH: the known
# vfork+ptrace-stop hazard (detcore_misc vfork_parent_resumes_after_child_exec,
# reverie PR #355) is a DEATH RACE between the notifier's waitid and its
# PTRACE_GETEVENTMSG. It reproduces at 16-wide under host load and usually
# PASSES standalone. A serial N=20 sweep is therefore not evidence about it;
# only a concurrent one is. Its signature is a TIMEOUT (rc 124), not a diff, so
# rc is recorded separately from trace equality.
#
# Kill safety: every child is started here and waited on by PID. No pkill, no
# pattern matching -- eighteen agents share this box (Hard Invariant 15).
set -u
ROOT=/home/newton/work/dev-hermit
BASE=$ROOT/ignored/fork-exec-parity
export HERMIT_BIN="${HERMIT_BIN:?}"
export LD_LIBRARY_PATH="$ROOT/ignored/lu-parity/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
OUT="${OUT:-$BASE/stress}"
N="${N:-20}"
CONC="${CONC:-1}"
BACKEND="${BACKEND:-ptrace}"
mkdir -p "$OUT"
CSV="$OUT/results.csv"
echo "guest,backend,n,conc,ok_runs,timeouts,other_fail,distinct_pev,distinct_out,verdict" > "$CSV"

for spec in "$@"; do
  name="${spec%%=*}"; cmd="${spec#*=}"
  read -r -a CMDA <<< "$cmd"
  d="$OUT/$name"; rm -rf "$d"; mkdir -p "$d"

  i=0
  while [ $i -lt "$N" ]; do
    pids=()
    j=0
    while [ $j -lt "$CONC" ] && [ $i -lt "$N" ]; do
      "$BASE/run-cell.sh" "$BACKEND" "$d" "r$i" "${CMDA[@]}" &
      pids+=($!)
      i=$((i+1)); j=$((j+1))
    done
    for p in "${pids[@]}"; do wait "$p"; done
  done

  ok=0; to=0; of=0
  for k in $(seq 0 $((N-1))); do
    rc=$(cat "$d/r$k.rc" 2>/dev/null || echo 99)
    case "$rc" in
      0)   ok=$((ok+1)); python3 "$BASE/pevents.py" "$d/r$k.log" > "$d/r$k.pev" 2>/dev/null ;;
      124) to=$((to+1)) ;;
      *)   of=$((of+1)) ;;
    esac
  done
  dp=$(cat "$d"/r*.pev 2>/dev/null >/dev/null; for f in "$d"/r*.pev; do [ -f "$f" ] && sha256sum < "$f"; done | sort -u | wc -l)
  do_=$(for k in $(seq 0 $((N-1))); do [ "$(cat "$d/r$k.rc" 2>/dev/null)" = 0 ] && sha256sum < "$d/r$k.out"; done | sort -u | wc -l)

  if [ "$to" -gt 0 ]; then v=TIMEOUT
  elif [ "$of" -gt 0 ]; then v=RUNFAIL
  elif [ "$dp" -eq 1 ] && [ "$do_" -eq 1 ]; then v=L4-CLEAN
  else v=DIVERGED; fi
  echo "$name,$BACKEND,$N,$CONC,$ok,$to,$of,$dp,$do_,$v" >> "$CSV"
done
column -s, -t < "$CSV"
