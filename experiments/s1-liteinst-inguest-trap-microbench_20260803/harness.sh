#!/bin/bash
SO=/home/newton/work/dev-hermit/worktrees/e9patch/reverie/target/debug/deps/libreverie_liteinst.so; STR=/home/newton/work/dev-hermit/worktrees/e9patch/reverie/target/debug/reverie-liteinst-strace; C2=/home/newton/work/dev-hermit/worktrees/e9patch/reverie/target/debug/counter2; G=/tmp/s1bench/getpid_loop
run_arm() {  # arm N reps
  local arm=$1 N=$2 reps=$3 r t0 t1
  for r in $(seq 1 $reps); do
    case "$arm" in
      native)   t0=$(date +%s%N); "$G" "$N" 2>/dev/null; t1=$(date +%s%N);;
      liteinst) t0=$(date +%s%N); REVERIE_LITEINST_PRELOAD="$SO" "$STR" "$G" "$N" 2>/dev/null; t1=$(date +%s%N);;
      ptrace)   t0=$(date +%s%N); "$C2" -- "$G" "$N" >/dev/null 2>&1; t1=$(date +%s%N);;
    esac
    if [ "$r" -le 2 ]; then echo "$arm,$N,w$r,$((t1-t0))"; else echo "$arm,$N,$r,$((t1-t0))"; fi
  done
}
run_arm native   500000  12
run_arm native   2500000 12
run_arm liteinst 100000  12
run_arm liteinst 600000  12
run_arm ptrace   10000   12
run_arm ptrace   50000   12
