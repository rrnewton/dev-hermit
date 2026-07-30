#!/usr/bin/env bash
# Hermit --strict --verify measurement pass (JIT ON, RCB preemption default).
# Captures wall-time of the two-run --verify invocation and the L2 verdict.
set -u
cd "$(dirname "$0")"
HB=~/work/dev-hermit/hermit/target/release/hermit
CP=classes
OUT=results
mkdir -p "$OUT/hermit"

declare -A PROG=(
  [java-version]="-version"
  [Hello]="-cp $CP Hello"
  [JitHotLoop]="-cp $CP JitHotLoop"
  [ThreadCounter]="-cp $CP ThreadCounter"
  [GcStress]="-cp $CP GcStress"
  [HashMapString]="-cp $CP HashMapString"
  [NioFile]="-cp $CP NioFile"
  [NioSocket]="-cp $CP NioSocket"
)
ORDER="java-version Hello JitHotLoop ThreadCounter GcStress HashMapString NioFile NioSocket"

echo "program,hermit_verify_wall_s,verdict" > "$OUT/hermit.csv"

for name in $ORDER; do
  args=${PROG[$name]}
  log="$OUT/hermit/$name.log"
  t0=$(date +%s.%N)
  timeout --kill-after=10s 120s "$HB" --log=info run --strict --verify \
     --no-virtualize-cpuid -- java $args >"$log" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  dt=$(echo "$t1 - $t0" | bc)
  if [ $rc -eq 124 ] || [ $rc -eq 137 ]; then
    verdict="TIMEOUT/livelock(rc=$rc)"
  elif grep -q "Determinism verified" "$log"; then
    verdict="L2-verified"
  elif grep -qi "nondetermin" "$log"; then
    verdict="L1-nondeterministic-verify"
  elif [ $rc -ne 0 ]; then
    verdict="ERROR(rc=$rc)"
  else
    verdict="UNKNOWN(rc=$rc)"
  fi
  printf "%s,%.3f,%s\n" "$name" "$dt" "$verdict" | tee -a "$OUT/hermit.csv"
done
echo "=== hermit pass done ==="
