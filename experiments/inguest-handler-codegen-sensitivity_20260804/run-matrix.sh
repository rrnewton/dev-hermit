#!/bin/bash
# In-guest handler codegen-sensitivity matrix.
# Metric: CPU time (user+sys) SLOPE across two N values => ns per in-guest getpid
# handler invocation (direct_hook path). Fixed startup/patch/teardown cancels in
# the slope. CPU-time is immune to the ~2x wall noise floor (contention deschedules
# but adds no CPU time). Supervisor (hermit release bin) held FIXED; ONLY the
# libreverie_liteinst.so codegen varies, selected per-run via HERMIT_LITEINST_RUNTIME.
set -u
BASE=/home/newton/work/dev-hermit/scratch/inguest-codegen
HERMIT=/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target/release/hermit
SL=/home/newton/work/dev-hermit/experiments/dbi_perf_leader_baseline_20260801/src/syscall_loop
RAW=$BASE/raw
CSV=$BASE/results.csv
mkdir -p "$RAW"
N_LO=100000
N_HI=2000000
REPS=5
WARMUP=1

echo "variant,so_sha8,N,rep,rc,user_s,sys_s,cpu_s,direct_hook,ptrace_installation" > "$CSV"

extract_time() { # $1=timefile field -> seconds
  # /usr/bin/time -v: "User time (seconds): X.XX" / "System time (seconds): X.XX"
  awk -F': ' -v k="$1" '$1 ~ k {gsub(/^[ \t]+/,"",$2); print $2}' "$2"
}

run_cfg() { # variant so_path
  local variant="$1"; local so="$2"
  local so_sha; so_sha=$(sha256sum "$so" 2>/dev/null | cut -c1-8)
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      local tf="$RAW/${variant}_N${N}_r${rep}.time"
      local ef="$RAW/${variant}_N${N}_r${rep}.err"
      /usr/bin/time -v -o "$tf" \
        taskset -c 300 env RUST_LOG=hermit::backend_stats=info HERMIT_LITEINST_RUNTIME="$so" \
        "$HERMIT" run --backend liteinst -- "$SL" "$N" >/dev/null 2>"$ef"
      local rc=$?
      [ "$rep" -le "$WARMUP" ] && continue   # discard warmup
      local u s cpu dh pi line
      u=$(extract_time "^User time" "$tf")
      s=$(extract_time "^System time" "$tf")
      cpu=$(awk -v a="$u" -v b="$s" 'BEGIN{printf "%.4f", a+b}')
      line=$(grep -m1 "backend run complete" "$ef")
      dh=$(echo "$line"  | grep -o 'direct_hook=[0-9]*' | head -1 | cut -d= -f2)
      pi=$(echo "$line"  | grep -o 'ptrace_installation=[0-9]*' | head -1 | cut -d= -f2)
      echo "$variant,$so_sha,$N,$rep,$rc,${u:-NA},${s:-NA},$cpu,${dh:-NA},${pi:-NA}" >> "$CSV"
      echo "  [$variant N=$N r=$rep] rc=$rc cpu=${cpu}s direct_hook=${dh} ptrace_inst=${pi}"
    done
  done
}

# Native baseline (discriminator): raw getpid loop, no hermit at all.
run_native() {
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      local tf="$RAW/native_N${N}_r${rep}.time"
      /usr/bin/time -v -o "$tf" taskset -c 300 "$SL" "$N" >/dev/null 2>/dev/null
      [ "$rep" -le "$WARMUP" ] && continue
      local u s cpu
      u=$(extract_time "^User time" "$tf"); s=$(extract_time "^System time" "$tf")
      cpu=$(awk -v a="$u" -v b="$s" 'BEGIN{printf "%.4f", a+b}')
      echo "native,-,$N,$rep,0,${u:-NA},${s:-NA},$cpu,-,-" >> "$CSV"
      echo "  [native N=$N r=$rep] cpu=${cpu}s"
    done
  done
}

echo "=== NATIVE baseline ==="; run_native
for v in "$@"; do
  variant="${v%%:*}"; so="${v#*:}"
  echo "=== variant $variant  so=$so ==="
  run_cfg "$variant" "$so"
done
echo "ALL DONE -> $CSV"
