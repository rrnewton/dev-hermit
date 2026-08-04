#!/bin/bash
set -u
BASE=/home/newton/work/dev-hermit/scratch/inguest-codegen-getcpu
HERMIT=/home/newton/work/dev-hermit/worktrees/e9patch/hermit/target/release/hermit
RAW=$BASE/raw
SO_DIR=/home/newton/work/dev-hermit/scratch/inguest-codegen/so
N_LO=100000; N_HI=2000000; REPS=5; WARMUP=1
run_cfg() { # tag guest so_path variant
  local tag="$1" guest="$2" so="$3" variant="$4"
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      local tf="$RAW/${tag}_${variant}_N${N}_r${rep}.time"
      local ef="$RAW/${tag}_${variant}_N${N}_r${rep}.err"
      /usr/bin/time -v -o "$tf" \
        taskset -c 300 env RUST_LOG=hermit::backend_stats=info HERMIT_LITEINST_RUNTIME="$so" \
        "$HERMIT" run --backend liteinst -- "$guest" "$N" >/dev/null 2>"$ef"
      echo "  [$tag $variant N=$N r=$rep] rc=$? $(grep -o 'direct_hook=[0-9]*\|ptrace_installation=[0-9]*' "$ef" | tr '\n' ' ')"
    done
  done
}
run_native() { # tag guest
  local tag="$1" guest="$2"
  for N in $N_LO $N_HI; do
    for rep in $(seq 1 $((WARMUP+REPS))); do
      /usr/bin/time -v -o "$RAW/${tag}_native_N${N}_r${rep}.time" taskset -c 300 "$guest" "$N" >/dev/null 2>/dev/null
      echo "  [$tag native N=$N r=$rep] rc=$?"
    done
  done
}
# getcpu across all four codegen variants (secondary-knob confirmation on confound-free path)
run_native getcpu2 "$BASE/getcpu_loop"
for v in A B C D; do run_cfg getcpu2 "$BASE/getcpu_loop" "$SO_DIR/$v.so" "$v"; done
# sched_getaffinity cross-probe (A opt3 vs D opt0)
run_native aff "$BASE/affinity_loop"
for v in A D; do run_cfg aff "$BASE/affinity_loop" "$SO_DIR/$v.so" "$v"; done
echo "ALL DONE"
