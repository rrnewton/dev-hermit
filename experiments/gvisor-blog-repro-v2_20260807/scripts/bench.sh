#!/usr/bin/env bash
# gvisor-blog-repro-v2 harness. Emits one CSV row per (experiment, regime, arm, N, rep).
#
# Per-syscall cost is derived from a TWO-POINT SLOPE, never from a single wall
# time: T(N2)-T(N1) over N2-N1 cancels sandbox/process startup, which for runsc
# dominates a short run (0.42s of a 0.42s N=50k run is mostly boot). A single
# division would have reported gVisor as ~100x worse than it is.
set -uo pipefail
D=/home/newton/work/dev-hermit/experiments/gvisor-blog-repro-v2_20260807
ROOT=/home/newton/work/dev-hermit
R=$ROOT/ignored/gvisor-runsc-same-host/bin/runsc-release-20260727.0
H=$ROOT/scratch/w14-baseline/hermit/target/release/hermit
G1=$ROOT/ignored/gvisor-runsc-same-host/bin/getpid-loop
G2=$D/scripts/getpid-threads
KBOX="python3 $ROOT/scratch/run-on-k-free-cores.py"
OUT=$D/raw/measurements.csv
TMO=${TMO:-300}
REPS=${REPS:-5}

# arm -> command prefix (evaluated with $GUEST $ARGS appended)
arms_native="native"
arms_runsc="runsc-systrap runsc-ptrace runsc-kvm"
arms_hermit="hermit-ptrace hermit-dbi hermit-sabre hermit-liteinst hermit-e9patch hermit-kvm"

cmd_for() { case "$1" in
  native)          echo "" ;;
  runsc-*)         echo "$R --platform=${1#runsc-} --network=none --rootless do" ;;
  hermit-*)        echo "$H run --backend ${1#hermit-} --" ;;
esac; }

echo "experiment,regime,arm,n,threads,rep,wall_s,rc,note" > "$OUT"

measure() { # $1=exp $2=regime(k1|par) $3=arm $4=guest $5=n $6=threads $7=rep
  local exp="$1" regime="$2" arm="$3" guest="$4" n="$5" thr="$6" rep="$7"
  local pre; pre=$(cmd_for "$arm")
  # BOTH regimes go through the SAME core-box helper (K=1 vs K=4) so its fixed
  # cost -- measured at 0.42s, almost all of it the 0.3s /proc/stat sampling --
  # is common-mode. Charging it only to K=1 fabricated the entire effect: native
  # runs this guest in 0.010s parallel, so a 0.42s handicap alone would have
  # "shown" a 31x sequentialization cost that does not exist.
  local box=""
  [ "$regime" = k1 ]  && box="$KBOX 1 --"
  [ "$regime" = par ] && box="$KBOX 4 --"
  local args="$n"; [ "$thr" != "-" ] && args="$n $thr"
  local s e rc
  s=$(date +%s.%N)
  # shellcheck disable=SC2086
  timeout "$TMO" $box $pre "$guest" $args >/dev/null 2>>"$D/raw/stderr.log"; rc=$?
  e=$(date +%s.%N)
  local w; w=$(echo "$e-$s" | bc)
  local note=""; [ "$rc" = 124 ] && note="TIMEOUT-${TMO}s-no-result"; [ "$rc" != 0 ] && [ "$rc" != 124 ] && note="FAILED-rc$rc-no-result"
  printf '%s,%s,%s,%s,%s,%s,%.3f,%s,%s\n' "$exp" "$regime" "$arm" "$n" "$thr" "$rep" "$w" "$rc" "$note" >> "$OUT"
  printf '[%s] %-9s %-16s N=%-7s rep%s %8.2fs rc=%s\n' "$(date -u +%H:%M:%SZ)" "$regime" "$arm" "$n" "$rep" "$w" "$rc"
  # reap anything of mine that outlived its bound (kvm hangs and ignores SIGTERM)
  for p in $(ps -o pid= -C hermit 2>/dev/null); do
    local a pp; a=$(ps -o args= -p "$p" 2>/dev/null); pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    case "$a" in *getpid-loop*|*getpid-threads*) [ "$pp" = 1 ] && kill -9 "$p" 2>/dev/null ;; esac
  done
}

# hermit-kvm hangs on this host (probe: rc=124 at 45s on N=50000, and again at
# 60s on N=100000). Establish that ONCE with evidence instead of spending
# REPS x 2 x TMO on a cell already known to produce no result.
MEASURED="native $arms_runsc hermit-ptrace hermit-dbi hermit-sabre hermit-liteinst hermit-e9patch"

echo "=== CALIBRATION: the core-box helper's own fixed cost, at both widths ==="
for rep in $(seq 1 5); do
  for regime in k1 par; do
    k=1; [ "$regime" = par ] && k=4
    cs=$(date +%s.%N); $KBOX $k -- /bin/true >/dev/null 2>&1; ce=$(date +%s.%N)
    printf 'calib,%s,helper,0,-,%s,%.3f,0,fixed-cost-of-the-core-box-helper\n' \
      "$regime" "$rep" "$(echo "$ce-$cs" | bc)" >> "$OUT"
  done
done

echo "=== EXP1 instrumentation cost: single-threaded getpid, K=1 box, two-point slope ==="
for n in 100000 300000; do measure exp1 k1 hermit-kvm "$G1" "$n" - 1; done
for rep in $(seq 1 $REPS); do
  for arm in $MEASURED; do for n in 100000 300000; do measure exp1 k1 "$arm" "$G1" "$n" - "$rep"; done; done
done

echo "=== EXP2 sequentialization cost: 4-thread getpid, K=1 vs K=4 ==="
for regime in k1 par; do measure exp2 "$regime" hermit-kvm "$G2" 200000 4 1; done
for rep in $(seq 1 $REPS); do
  for arm in $MEASURED; do for regime in k1 par; do measure exp2 "$regime" "$arm" "$G2" 200000 4 "$rep"; done; done
done
echo "=== BENCH DONE ==="
