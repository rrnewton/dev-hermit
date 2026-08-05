#!/usr/bin/env bash
# python-hashseed --verify failing-run catcher (determinism-lane instrument #1).
#
# WHY: language-runtimes/python-hashseed --verify flakes INTERMITTENTLY in CI
# (893991ac: 3 fails then 5/5). hermit-kvm got 0/317 at MODERATE load; the only
# profile that ever produced a fail is a full validate DAG (heavy concurrent
# cc1plus + memory pressure + CORE CONTENTION, no core isolation). This catcher
# runs the EXACT manifest verify cmd in a loop, records the LOAD per row, and on
# any FAIL preserves the retained run1/run2 logs + auto-runs log-diff to localize
# the FIRST divergence (COMMIT line => schedule/RCB-skid = internal; DETLOG line
# => unvirtualized source = environmental).
#
# FAITHFUL to CI: plain --verify (strip_lines=true, verify.rs:158). A fail here
# is the SAME class CI sees; a divergence surviving stripping is structural
# (most likely a COMMIT (turn,dettid) schedule diff).
#
# Third-party Verify: ignored/capture.csv has one row per run with a load column;
# PASS/total = rate AT the stated load. Any FAIL has ignored/fails/fail-<iter>/
# with output.log, run1.log, run2.log, logdiff.txt, load.txt.
set -uo pipefail

ROOT=/home/newton/work/dev-hermit
EXP="$ROOT/experiments/python-hashseed-catcher_20260804"
IGN="$EXP/ignored"
REPO="$ROOT/worktrees/dagmeasure/hermit"
BIN="$REPO/target/release/hermit"
CSV="$IGN/capture.csv"
NRUNS="${1:-2000}"           # iterations (default 2000; bounded backstop)
LABEL="${2:-baseline}"       # load-profile label, STATE THE LOAD
LOADPROBE="$ROOT/ci-hub/bin/load-probe"
# HCPUSET (env): if set (e.g. "4-7"), pin each hermit --verify run to that
# cpuset via taskset. Combined with co-pinned CPU hogs on the SAME cpuset this
# FORCES OS-level core contention on the guest (the RCB-skid condition) while
# leaving the other ~312 cores and all sibling agents untouched.
PIN=(); [ -n "${HCPUSET:-}" ] && PIN=(taskset -c "$HCPUSET")

BINSHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)"
echo "# catcher start label=$LABEL nruns=$NRUNS bin=$BIN binsha=$BINSHA" >&2
[ -f "$CSV" ] || echo "iter,epoch,rc,verdict,loadavg1,executing_cpu_pct,mem_avail_pct,guest_order,divergence_class" > "$CSV"

# Snapshot full load-probe (durable) at start; periodic + on-fail snapshots too.
"$LOADPROBE" > "$IGN/loadprobe-start-$LABEL.txt" 2>&1 || true

pass=0; fail=0
for ((i=1; i<=NRUNS; i++)); do
  CELL="$(mktemp -d "$IGN/tmp/cell.XXXXXX")"; mkdir -p "$CELL/home" "$CELL/xdg"
  OUT="$CELL/out.log"
  # Isolate verify's kept temp logs into this cell via TMPDIR (no /tmp pollution).
  TMPDIR="$CELL" timeout 120 "${PIN[@]}" env LC_ALL=C TZ=UTC HOME="$CELL/home" XDG_CONFIG_HOME="$CELL/xdg" \
    "$BIN" --log=info run --backend ptrace --strict --verify \
    --no-virtualize-cpuid --max-timeslice=disabled \
    -- "$REPO/tests/e2e/language-runtimes/python-hashseed.sh" --run > "$OUT" 2>&1
  rc=$?
  epoch=$(date +%s)
  la1=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)
  order=$(grep -m1 -oE 'order=[a-z,]+' "$OUT" 2>/dev/null | head -c 80)
  verdict=PASS; [ "$rc" -ne 0 ] && verdict=FAIL
  ecpu=""; mem=""; dclass=""
  # Cheap loadavg every row; expensive load-probe every 25 rows and on FAIL.
  if [ "$verdict" = FAIL ] || (( i % 25 == 1 )); then
    lp=$("$LOADPROBE" 2>/dev/null)
    ecpu=$(echo "$lp" | grep -oE 'executing=[0-9.]+%' | head -1 | tr -dc '0-9.')
    mem=$(echo "$lp" | grep -oE 'available=[0-9.]+%' | head -1 | tr -dc '0-9.')
  fi
  if [ "$verdict" = FAIL ]; then
    fail=$((fail+1))
    FD="$IGN/fails/fail-$i"; mkdir -p "$FD"
    cp "$OUT" "$FD/output.log" 2>/dev/null
    echo "$lp" > "$FD/load.txt" 2>/dev/null
    # Parse the retained log paths from verify's own message and preserve them.
    read -r L1 L2 < <(grep -oE '/[^ ]*run[12]_log[^ ]*' "$OUT" 2>/dev/null | tr '\n' ' ')
    if [ -n "${L1:-}" ] && [ -f "$L1" ]; then cp "$L1" "$FD/run1.log" 2>/dev/null; fi
    if [ -n "${L2:-}" ] && [ -f "$L2" ]; then cp "$L2" "$FD/run2.log" 2>/dev/null; fi
    # Auto-localize the FIRST divergence, un-normalized, with syscall context.
    if [ -f "$FD/run1.log" ] && [ -f "$FD/run2.log" ]; then
      "$BIN" log-diff --syscall-history 5 "$FD/run1.log" "$FD/run2.log" > "$FD/logdiff.txt" 2>&1 || true
      if grep -qm1 'COMMIT turn' "$FD/logdiff.txt"; then dclass=SCHEDULE-COMMIT
      elif grep -qm1 'DETLOG' "$FD/logdiff.txt"; then dclass=DETLOG-VALUE
      else dclass=UNKNOWN; fi
    else dclass=NO-RETAINED-LOGS; fi
    echo "# FAIL iter=$i rc=$rc class=$dclass ecpu=$ecpu la1=$la1 -> $FD" >&2
  else
    pass=$((pass+1))
    rm -rf "$CELL"   # keep only failures
  fi
  echo "$i,$epoch,$rc,$verdict,$la1,$ecpu,$mem,$order,$dclass" >> "$CSV"
  (( i % 100 == 0 )) && echo "# progress iter=$i pass=$pass fail=$fail la1=$la1" >&2
done
"$LOADPROBE" > "$IGN/loadprobe-end-$LABEL.txt" 2>&1 || true
echo "# catcher done label=$LABEL pass=$pass fail=$fail total=$((pass+fail))" >&2
