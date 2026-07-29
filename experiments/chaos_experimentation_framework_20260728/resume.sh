#!/usr/bin/env bash
# Resume the framework sweep: run ONLY configs from configs.tsv that are not yet
# present in results.tsv, appending their rows. Idempotent -- safe to re-run.
# Same knob surface / oracle / pinned-path discipline as sweep.sh.
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
BOX="${BOX:-$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static}"
IMG="${IMG:-$ROOT/ignored/bench-seeds/bko-161811.raw}"
SCR="${SCR:-$ROOT/ignored/bench-seeds/framework.scratch}"   # PINNED path
N="${N:-30}"
EXP="${EXP:-$ROOT/experiments/chaos_experimentation_framework_20260728}"
WORK="${WORK:-$ROOT/ignored/bench-seeds/fwork}"; mkdir -p "$WORK" "$EXP/outputs"
CFG="$EXP/configs.tsv"
RES="$EXP/results.tsv"
PERRUN="${PERRUN:-300}"

guest(){ grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"; }

[ -f "$RES" ] || printf 'config\tseed\texit\tsig12\tscanlines\n' > "$RES"
done_cfgs=$(tail -n +2 "$RES" | cut -f1 | sort -u)

tail -n +2 "$CFG" | while IFS=$'\t' read -r name args; do
  [ -z "$name" ] && continue
  if grep -qx "$name" <<<"$done_cfgs"; then
    echo "skip (done): $name"; continue
  fi
  echo "run: $name  [$args]"
  for s in $(seq 1 "$N"); do
    cp -f "$IMG" "$SCR"
    # shellcheck disable=SC2086
    "$SC" --timeout "$PERRUN" --output "$WORK/r.out" -- \
        "$H" $args --seed "$s" -- "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
    ex=$(grep -o 'command_exit=[0-9]*' "$WORK/r.out.status" 2>/dev/null | cut -d= -f2)
    guest "$WORK/r.out" > "$WORK/g.txt"
    sig=$(sha256sum "$WORK/g.txt" | cut -c1-12)
    sl=$(grep -c 'Scanning:' "$WORK/g.txt")
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$s" "${ex:-NA}" "$sig" "$sl" >> "$RES"
  done
  cp -f "$WORK/g.txt" "$EXP/outputs/${name}.seed${N}.guest.txt"
  echo "done: $name"
done
echo "resume complete -> $RES"
