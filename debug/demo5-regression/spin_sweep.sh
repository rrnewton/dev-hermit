#!/usr/bin/env bash
# LOCAL/GITIGNORED. demo5-spin-boundedness-linear-response experiment.
#
# Owner's decisive test: is the demo5 livelock spin BOUNDED (a tuning problem —
# vtime-burn-rate knobs LINEARLY scale spin duration / escape time) or UNBOUNDED
# (the burn-out mechanism is MISSING — spin is INSENSITIVE to the burn knob)?
#
# Method: run the controller boot under Hermit for a FIXED WALL window (partial
# runs are fine) while sweeping the timeslice knob, in two modes:
#   norcb   = --strict --no-rcb-time --target-timeslice <K> --max-timeslice disabled
#             (PMU/RCB preemption OFF -> the suspected missing-burn-out regime)
#   rcbtime = --strict --target-timeslice <K> --max-timeslice 200000000
#             (PMU/RCB preemption ON -> burn-out present; boots as control)
# For each run extract from the INFO log (no completion needed):
#   vtime_burned = last_committed - first_committed  (COMMIT ... previously committed <abs>s)
#   turns, su0 (SleepUntil(LogicalTime(0))), su_future (SleepUntil(LogicalTime(!=0))),
#   preempt (inbound timer preemption event = the burn-out firing),
#   guest_ts (last serial [x.xxx]; >~1.8 = escaped/booting past HPET),
#   burn_rate = vtime_burned/wall,  su0_rate = su0/wall
# LINEAR response => burn_rate / escape scales with K.  INSENSITIVE => missing.
#
# Usage: spin_sweep.sh <mode> <target_timeslice> <wall_s> <outdir> [tag]
set -uo pipefail
MODE="${1:?mode norcb|rcbtime}"; K="${2:?target_timeslice}"; WALL="${3:?wall_s}"; OUT="${4:?outdir}"; TAG="${5:-$MODE-$K}"
BASE=/home/newton/work/dev-hermit/experiments/demo5_bisect_20260731/ignored
BIN=$BASE/hermit-670209ba
CTRL=/home/newton/temp/dev-hermit/demos/lib/qemu_controller.py
PY=$(command -v python3.12 || command -v python3)
QEMU=$(command -v qemu-system-x86_64)
KERNEL=$BASE/ctrl-rcb/assets/bzImage; INITRD=$BASE/ctrl-rcb/assets/initramfs.cpio.gz
EPOCH=1767225600
case "$MODE" in
  norcb)   FLAGS=(run --strict --no-rcb-time --target-timeslice "$K" --max-timeslice disabled) ;;
  rcbtime) FLAGS=(run --strict --target-timeslice "$K" --max-timeslice 200000000) ;;
  *) echo "bad mode"; exit 1 ;;
esac
mkdir -p "$OUT"; CSV=$OUT/sweep.csv; LOCK=$CSV.lock
[ -f "$CSV" ] || echo "tag,mode,target_timeslice,wall_s,vtime_burned_s,burn_rate_vt_per_wall,turns,su0,su0_rate,su_future,preempt_events,guest_ts,escaped,rc" >"$CSV"

wd="$OUT/$TAG"; rm -rf "$wd"; mkdir -p "$wd"
info="$wd/info.log"; snap="$wd/snap.qcow2"; ser="$wd/serial.log"; qmp="$wd/qmp.sock"
qemu-img create -q -f qcow2 "$snap" 64M >/dev/null
start=$(date +%s)
PYTHONDONTWRITEBYTECODE=1 RUST_LOG="warn,detcore=info,reverie_ptrace::task=info" \
setsid timeout --signal=TERM --kill-after=20 "${WALL}s" \
  "$BIN" --log info --log-file "$info" "${FLAGS[@]}" -- \
    "$PY" "$CTRL" boot --qemu "$QEMU" --qmp-socket "$qmp" --serial-log "$ser" \
      --disk "$snap" --kernel "$KERNEL" --initrd "$INITRD" \
      --snapshot-name hermit-boot --timeout 100000 >"$wd/console.log" 2>&1 &
tpid=$!; wait "$tpid"; rc=$?
end=$(date +%s); wall=$((end-start))
kill -9 -"$tpid" 2>/dev/null; for p in $(pgrep -f "$wd/" 2>/dev/null); do kill -9 "$p" 2>/dev/null; done
# --- extract metrics from partial INFO log ---
cfirst=$(grep -oE 'previously committed [0-9_]+\.[0-9_]+s' "$info" 2>/dev/null | head -1 | grep -oE '[0-9_]+\.[0-9_]+' | tr -d _)
clast=$(grep -oE 'previously committed [0-9_]+\.[0-9_]+s' "$info" 2>/dev/null | tail -1 | grep -oE '[0-9_]+\.[0-9_]+' | tr -d _)
[ -z "$cfirst" ] && cfirst=$EPOCH; [ -z "$clast" ] && clast=$EPOCH
vburn=$(awk -v a="$cfirst" -v b="$clast" 'BEGIN{printf "%.6f", b-a}')
brate=$(awk -v v="$vburn" -v w="$wall" 'BEGIN{ if(w>0) printf "%.4f", v/w; else print 0 }')
turns=$(grep -oE 'COMMIT turn [0-9_]+' "$info" 2>/dev/null | tail -1 | grep -oE '[0-9_]+' | tr -d _); [ -z "$turns" ] && turns=0
su0=$(grep -c 'SleepUntil(LogicalTime(0))' "$info" 2>/dev/null)
su_future=$(grep -oE 'SleepUntil\(LogicalTime\([0-9_]+\)\)' "$info" 2>/dev/null | grep -vc 'LogicalTime(0)')
preempt=$(grep -c 'inbound timer preemption event' "$info" 2>/dev/null)
gts=$(grep -oE '\[[[:space:]]*[0-9]+\.[0-9]+\]' "$ser" 2>/dev/null | tail -1 | tr -d '[] '); [ -z "$gts" ] && gts=0
esc=no; awk -v t="$gts" 'BEGIN{exit !((t+0)>=1.8)}' && esc=yes
srate=$(awk -v s="$su0" -v w="$wall" 'BEGIN{ if(w>0) printf "%.1f", s/w; else print 0 }')
rm -f "$qmp" "$snap"
( flock 9; echo "$TAG,$MODE,$K,$wall,$vburn,$brate,$turns,$su0,$srate,$su_future,$preempt,$gts,$esc,$rc" >>"$CSV" ) 9>"$LOCK"
echo "[$TAG] mode=$MODE K=$K wall=${wall}s vburn=${vburn}s burn_rate=$brate su0=$su0 su0_rate=$srate su_future=$su_future preempt=$preempt guest_ts=$gts escaped=$esc rc=$rc"