#!/usr/bin/env bash
# Research-only rerun of the chunk-recover seeds-to-reproduce benchmark.
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/worktrees/274/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
BOX="${BOX:-$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static}"
IMG="${IMG:-$ROOT/ignored/bench-seeds/bko-161811.raw}"
SCR="${SCR:-$ROOT/ignored/bench-seeds/pts_bench.scratch}"
EXP="${EXP:-$ROOT/experiments/vtime_slowdown_seeds_benchmark_20260729}"
WORK="${WORK:-$ROOT/ignored/bench-seeds/vtime_slowdown_work}"
N="${N:-60}"
PERRUN="${PERRUN:-120}"
RES="$EXP/results.tsv"

mkdir -p "$WORK" "$EXP/outputs"

guest() {
  grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"
}

strategy_args() {
  case "$1" in
    blind) echo "run --seed %S" ;;
    chaos) echo "run --chaos --seed %S" ;;
    target_races) echo "run --chaos --chaos-target-races --seed %S" ;;
    slowdown_constant) echo "run --chaos --chaos-per-thread-slowdown --chaos-slowdown-max-factor 10 --seed %S" ;;
    slowdown_epoch_100us) echo "run --chaos --chaos-per-thread-slowdown --chaos-slowdown-max-factor 10 --chaos-epoch-length-ns 100000 --seed %S" ;;
    *) echo "unknown strategy: $1" >&2; return 2 ;;
  esac
}

printf 'strategy\tseed\texit\tsig12\tscanlines\telapsed_ms\n' > "$RES"

for strategy in blind chaos target_races slowdown_constant slowdown_epoch_100us; do
  template="$(strategy_args "$strategy")"
  for seed in $(seq 1 "$N"); do
    args="${template//%S/$seed}"
    cp -f "$IMG" "$SCR"
    # shellcheck disable=SC2086
    "$SC" --timeout "$PERRUN" --output "$WORK/run.out" -- \
      "$H" $args -- "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
    exit_code=$(grep -o 'command_exit=[0-9]*' "$WORK/run.out.status" 2>/dev/null | cut -d= -f2)
    elapsed_ms=$(grep -o 'elapsed_ms=[0-9]*' "$WORK/run.out.status" 2>/dev/null | cut -d= -f2)
    guest "$WORK/run.out" > "$WORK/guest.txt"
    signature=$(sha256sum "$WORK/guest.txt" | cut -c1-12)
    scanlines=$(grep -c 'Scanning:' "$WORK/guest.txt")
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$strategy" "$seed" "${exit_code:-NA}" "$signature" "$scanlines" "${elapsed_ms:-NA}" >> "$RES"
  done
  cp -f "$WORK/guest.txt" "$EXP/outputs/${strategy}.seed${N}.guest.txt"
  printf 'completed %s (%s seeds)\n' "$strategy" "$N"
done

printf 'results: %s\n' "$RES"
