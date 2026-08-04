#!/usr/bin/env bash
# capture-residual.sh <BIN> <CONC> <BUDGET_S> <WAVES> <OUTDIR>
# Run BIN (a tests_misc binary) CONC-wide per wave against the vfork test; when
# any instance survives past BUDGET_S it is a HANG. On the FIRST hang, snapshot
# the wedged process tree so we can pin the exact site:
#   - strace -f the root reverie process for 3s -> syscall histogram (hot vs passive)
#   - every thread's /proc/<tid>/{syscall,wchan,stack,comm} for the whole tree
#   - guest child /proc state (State, ShdPnd/SigPnd, syscall)
# Then kill that tree and keep going (log ratio). Snapshot only first N hangs.
set -o pipefail
BIN="$1"; CONC="${2:-48}"; BUDGET="${3:-30}"; WAVES="${4:-400}"; OUT="${5:-ignored/residual-capture}"
TEST='vfork::vfork_parent_resumes_after_child_exec'
EXP="$(cd "$(dirname "$0")" && pwd)"; cd "$EXP"
mkdir -p "$OUT"
LOG="$OUT/run.log"
MAXSNAP=3; snaps=0
say(){ echo "[cap $(date -u +%H:%M:%S)] $*" | tee -a "$LOG"; }
say "start bin=$BIN conc=$CONC budget=$BUDGET waves=$WAVES load=$(cut -d' ' -f1 /proc/loadavg)"

snapshot(){
  local root="$1"
  local wave="$2"
  local d="$OUT/hang_w${wave}_p${root}_$(date +%s)"
  mkdir -p "$d"
  say "HANG root=$root wave=$wave -> snapshot $d"
  # descendant tids of root (whole tree)
  local allpids; allpids=$(pstree -p "$root" 2>/dev/null | grep -oE '\([0-9]+\)' | tr -d '()' | sort -u)
  [ -z "$allpids" ] && allpids="$root"
  {
    echo "== pstree =="; pstree -alp "$root" 2>/dev/null
    echo "== ps =="; ps -o pid,ppid,tid,stat,wchan:32,comm,cmd -T -p "$(echo "$allpids" | paste -sd,)" 2>/dev/null
  } > "$d/tree.txt"
  # per-thread proc snapshot across the whole tree
  for pid in $allpids; do
    for t in /proc/$pid/task/*; do
      [ -d "$t" ] || continue; tid=$(basename "$t")
      {
        echo "### tid=$tid comm=$(cat $t/comm 2>/dev/null) state=$(awk '{print $3}' $t/stat 2>/dev/null)"
        echo "syscall: $(cat $t/syscall 2>/dev/null)"
        echo "wchan:   $(cat $t/wchan 2>/dev/null)"
        echo "-- stack --"; cat $t/stack 2>/dev/null
        echo "-- status(sig) --"; grep -E 'State|SigPnd|ShdPnd|SigBlk' $t/status 2>/dev/null
      } >> "$d/threads.txt"
    done
  done
  # hot-vs-passive: 3s strace on the whole tree, histogram
  timeout 4 strace -f -qq -c -p "$root" 2>"$d/strace-summary.txt" || true
  # also a raw 1.5s slice to see the injected-syscall pattern (SETREGSET/SINGLESTEP/wait4)
  timeout 2 strace -f -qq -e trace=ptrace,wait4,waitid -p "$root" 2>"$d/strace-raw.txt" || true
  say "snapshot done $d"
}

hangs=0; total=0; wave=0
while [ "$wave" -lt "$WAVES" ]; do
  wave=$((wave+1)); pids=()
  for c in $(seq 1 "$CONC"); do "$BIN" "$TEST" --exact --test-threads=1 >/dev/null 2>&1 & pids+=($!); done
  for ((s=0; s<BUDGET; s++)); do
    alive=0; for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && alive=$((alive+1)); done
    [ "$alive" -eq 0 ] && break; sleep 1
  done
  total=$((total+CONC))
  for p in "${pids[@]}"; do
    if kill -0 "$p" 2>/dev/null; then
      hangs=$((hangs+1))
      if [ "$snaps" -lt "$MAXSNAP" ]; then snaps=$((snaps+1)); snapshot "$p" "$wave"; fi
      pkill -9 -P "$p" 2>/dev/null; kill -9 "$p" 2>/dev/null
    fi
  done
  # sweep any orphaned guest children by comm
  say "wave $wave/$WAVES total=$total hangs=$hangs load=$(cut -d' ' -f1 /proc/loadavg)"
done
say "DONE total=$total hangs=$hangs rate=$(awk "BEGIN{printf \"%.4f\", $hangs/$total}")"
