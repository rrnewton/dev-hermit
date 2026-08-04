#!/bin/bash
# getcpu (zero-kernel, Determinized-local) 100%-handler codegen probe.
# Reuses the persisted .so builds from the getpid experiment: A=opt3(ships), D=opt0(control).
# Metric: CPU-time (user+sys) SLOPE across N_LO->N_HI => ns per in-guest getcpu handler call.
set -u
BASE=/home/newton/work/dev-hermit/scratch/inguest-codegen-getcpu
HERMIT=/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target/release/hermit
GUEST=$BASE/getcpu_loop
SO_A=/home/newton/work/dev-hermit/scratch/inguest-codegen/so/A.so
SO_D=/home/newton/work/dev-hermit/scratch/inguest-codegen/so/D.so
RAW=$BASE/raw
CSV=$BASE/results.csv
N_LO=100000
N_HI=2000000
REPS=5
WARMUP=1
echo "variant,so_sha8,N,rep,rc,user_s,sys_s,cpu_s,direct_hook,ptrace_installation" > "$CSV"

get() { awk -F': ' -v k="$1" '$0 ~ k {gsub(/^[ \t]+/,"",$2); print $2; exit}' "$2"; }

run_cfg() {
  local variant="$1" so="$2"
  local so_sha; so_sha=$(sha256sum "$so" | cut -c1-8)
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      local tf="$RAW/${variant}_N${N}_r${rep}.time"
      local ef="$RAW/${variant}_N${N}_r${rep}.err"
      /usr/bin/time -v -o "$tf" \
        taskset -c 300 env RUST_LOG=hermit::backend_stats=info HERMIT_LITEINST_RUNTIME="$so" \
        "$HERMIT" run --backend liteinst -- "$GUEST" "$N" >/dev/null 2>"$ef"
      local rc=$?
      [ "$rep" -le "$WARMUP" ] && continue
      local u s cpu line dh pi
      u=$(get "^\s*User time" "$tf"); s=$(get "^\s*System time" "$tf")
      cpu=$(awk -v a="$u" -v b="$s" 'BEGIN{printf "%.4f", a+b}')
      line=$(grep -m1 "backend run complete" "$ef")
      dh=$(echo "$line" | grep -o 'direct_hook=[0-9]*' | cut -d= -f2)
      pi=$(echo "$line" | grep -o 'ptrace_installation=[0-9]*' | cut -d= -f2)
      echo "$variant,$so_sha,$N,$rep,$rc,${u:-NA},${s:-NA},$cpu,${dh:-NA},${pi:-NA}" >> "$CSV"
      echo "  [$variant N=$N r=$rep] rc=$rc cpu=${cpu}s dh=${dh} pi=${pi}"
    done
  done
}
run_native() {
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      local tf="$RAW/native_N${N}_r${rep}.time"
      /usr/bin/time -v -o "$tf" taskset -c 300 "$GUEST" "$N" >/dev/null 2>/dev/null
      [ "$rep" -le "$WARMUP" ] && continue
      local u s cpu
      u=$(get "^\s*User time" "$tf"); s=$(get "^\s*System time" "$tf")
      cpu=$(awk -v a="$u" -v b="$s" 'BEGIN{printf "%.4f", a+b}')
      echo "native,-,$N,$rep,0,${u:-NA},${s:-NA},$cpu,-,-" >> "$CSV"
      echo "  [native N=$N r=$rep] cpu=${cpu}s"
    done
  done
}
echo "=== NATIVE ==="; run_native
echo "=== A opt3 (SHIPS) ==="; run_cfg A "$SO_A"
echo "=== D opt0 (CONTROL) ==="; run_cfg D "$SO_D"
echo "ALL DONE -> $CSV"
