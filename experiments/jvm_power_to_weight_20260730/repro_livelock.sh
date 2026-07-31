#!/usr/bin/env bash
# Root-cause matrix: does --max-timeslice=disabled livelock the JVM, and does it
# depend on JIT (compiler threads) vs -Xint (interpreter, no compiler threads)?
# 2x2: {Hello, Threads} x {-Xint, JIT-on} x {timeslice-disabled, RCB-preempt-on}
set -u
cd "$(dirname "$0")"
HB=~/work/dev-hermit/hermit/target/release/hermit
CP=classes
CAP=70   # a passing --verify finishes < 25s; >CAP => livelock

javac -d "$CP" src/Threads.java 2>/dev/null

run() {
  local label="$1"; shift
  local t0 t1 dt rc
  t0=$(date +%s.%N)
  timeout --kill-after=10s ${CAP}s "$HB" --log=info run --strict --verify \
      --no-virtualize-cpuid "$@" >/tmp/repro_$$.log 2>&1
  rc=$?
  t1=$(date +%s.%N)
  dt=$(echo "$t1 - $t0" | bc)
  local verdict
  if [ $rc -eq 124 ] || [ $rc -eq 137 ]; then verdict="LIVELOCK(rc=$rc)"
  elif grep -q "Determinism verified" /tmp/repro_$$.log; then verdict="L2-verified"
  elif [ $rc -ne 0 ]; then verdict="ERR(rc=$rc):$(grep -ioE 'nondetermin|panic|error' /tmp/repro_$$.log | head -1)"
  else verdict="rc0-noverdict"; fi
  printf "%-46s %-18s %6.1fs\n" "$label" "$verdict" "$dt"
}

echo "=== JVM livelock root-cause matrix (JIT threads vs preemption) ==="
echo "program/mode                                   verdict            wall"
for prog in Hello Threads; do
  # -Xint (JIT OFF, matches current java_vm_args) + timeslice disabled
  run "$prog -Xint  + --max-timeslice=disabled"  --max-timeslice=disabled -- java -Xint -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -cp $CP $prog
  # -Xint + RCB preemption ON
  run "$prog -Xint  + RCB-preempt-on"                                     -- java -Xint -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -cp $CP $prog
  # JIT ON + timeslice disabled
  run "$prog JIT-on + --max-timeslice=disabled"  --max-timeslice=disabled -- java -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -cp $CP $prog
  # JIT ON + RCB preemption ON
  run "$prog JIT-on + RCB-preempt-on"                                     -- java -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -cp $CP $prog
done
rm -f /tmp/repro_$$.log
echo "=== done ==="
