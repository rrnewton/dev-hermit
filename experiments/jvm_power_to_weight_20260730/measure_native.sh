#!/usr/bin/env bash
# Native measurement pass for the JVM power-to-weight matrix.
# For each candidate: unique+total syscalls (strace -f -c), JIT compile counts
# (C1 vs C2 via -XX:+PrintCompilation), and native wall-time (JIT on).
set -u
cd "$(dirname "$0")"
CP=classes
OUT=results
mkdir -p "$OUT/strace" "$OUT/jit"

# name | invocation (after `java`)
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

echo "program,native_wall_s,unique_syscalls,total_syscalls,jit_total,jit_c1,jit_c2" > "$OUT/native.csv"

for name in $ORDER; do
  args=${PROG[$name]}
  # --- wall time (JIT on, 3 runs, take min) ---
  best=99999
  for r in 1 2 3; do
    t0=$(date +%s.%N)
    java $args >/dev/null 2>&1
    t1=$(date +%s.%N)
    dt=$(echo "$t1 - $t0" | bc)
    awk "BEGIN{exit !($dt < $best)}" && best=$dt
  done

  # --- strace syscall surface (follow threads) ---
  strace -f -c -o "$OUT/strace/$name.txt" java $args >/dev/null 2>&1
  # parse the -c summary table: rows with a syscall name in the last column
  uniq=$(awk 'NR>2 && $NF!="total" && $NF!="" && $1 ~ /^[0-9]/ {c++} END{print c+0}' "$OUT/strace/$name.txt")
  totl=$(awk 'NR>2 && $NF!="total" && $1 ~ /^[0-9]/ {s+=$4} END{print s+0}' "$OUT/strace/$name.txt")

  # --- JIT compilation counts (tiered) ---
  java -XX:+PrintCompilation $args >"$OUT/jit/$name.txt" 2>&1
  # PrintCompilation cols: stamp id attrs [tier] method ; tier is a bare int col 4
  jit_total=$(grep -cE '^\s+[0-9]+\s+[0-9]+' "$OUT/jit/$name.txt")
  jit_c2=$(awk '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {for(i=3;i<=NF;i++) if($i=="4"){c++;break}} END{print c+0}' "$OUT/jit/$name.txt")
  jit_c1=$(awk '$1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ {for(i=3;i<=NF;i++) if($i=="1"||$i=="2"||$i=="3"){c++;break}} END{print c+0}' "$OUT/jit/$name.txt")

  printf "%s,%.3f,%s,%s,%s,%s,%s\n" "$name" "$best" "$uniq" "$totl" "$jit_total" "$jit_c1" "$jit_c2" | tee -a "$OUT/native.csv"
done
echo "=== native pass done ==="
