#!/usr/bin/env bash
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="$ROOT/worktrees/274/hermit/target/release/hermit"
SC="$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh"
BOX="$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static"
IMG="$ROOT/ignored/bench-seeds/bko-161811.raw"
SCR="$ROOT/ignored/bench-seeds/pts_bench.scratch"
EXP="$ROOT/experiments/vtime_slowdown_seeds_benchmark_20260729"
WORK="$ROOT/ignored/bench-seeds/vtime_slowdown_replay"
mkdir -p "$WORK" "$EXP/replay"

guest() {
  grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"
}

printf 'config\trecord_exit\treplay_exit\trecord_sig\treplay_sig\tmatch\tepoch_transitions\n' > "$EXP/replay-proof.tsv"

for config in slowdown_constant slowdown_epoch_100us slowdown_epoch_10us slowdown_epoch_1us; do
  extra=(--chaos-per-thread-slowdown --chaos-slowdown-max-factor 10)
  case "$config" in
    slowdown_epoch_100us) extra+=(--chaos-epoch-length-ns 100000) ;;
    slowdown_epoch_10us) extra+=(--chaos-epoch-length-ns 10000) ;;
    slowdown_epoch_1us) extra+=(--chaos-epoch-length-ns 1000) ;;
  esac
  artifact="$EXP/replay/${config}.seed1.json"

  cp -f "$IMG" "$SCR"
  "$SC" --timeout 120 --output "$WORK/record.out" -- \
    "$H" run --chaos "${extra[@]}" --seed 1 --record-preemptions-to "$artifact" -- \
    "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
  record_exit=$(grep -o 'command_exit=[0-9]*' "$WORK/record.out.status" | cut -d= -f2)
  guest "$WORK/record.out" > "$WORK/record.guest"

  cp -f "$IMG" "$SCR"
  "$SC" --timeout 120 --output "$WORK/replay.out" -- \
    "$H" run --chaos --seed 1 --replay-preemptions-from "$artifact" -- \
    "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
  replay_exit=$(grep -o 'command_exit=[0-9]*' "$WORK/replay.out.status" | cut -d= -f2)
  guest "$WORK/replay.out" > "$WORK/replay.guest"

  record_sig=$(sha256sum "$WORK/record.guest" | cut -c1-12)
  replay_sig=$(sha256sum "$WORK/replay.guest" | cut -c1-12)
  if cmp -s "$WORK/record.guest" "$WORK/replay.guest" && [[ "$record_exit" == "$replay_exit" ]]; then
    match=yes
  else
    match=no
  fi
  transitions=$(jq '[.per_thread[]?.chaos_epochs | length] | add // 0' "$artifact")
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$config" "$record_exit" "$replay_exit" "$record_sig" "$replay_sig" "$match" "$transitions" >> "$EXP/replay-proof.tsv"
done
