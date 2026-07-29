#!/usr/bin/env bash
# Chaos experimentation framework: OUTER parameter grid x INNER seed sweep.
#
# For each parameter config in configs.tsv, run an inner seed sweep (seeds 1..N)
# of a scheduling-dependent workload and record the guest-output SIGNATURE per
# (config, seed). analyze.py then computes, per config: hit-rate (schedule !=
# blind baseline), distinct-schedule coverage / diversity, and seeds-to-first-
# repro -> which PARAMETERS minimize seeds-to-reproduce.
#
# Workload: btrfs-progs v7.1 `rescue chunk-recover -y -v <dev>` (scan worker +
# progress-reporter thread). The progress-tick interleaving is the observable
# schedule signal; timeslice granularity controls how many ticks occur, hence
# schedule diversity. See report-hermit-chaos-controls for the knob surface:
# hermit has per-thread chaos PRNG + exponential per-thread slice draws +
# persistent priorities, but NO RR-style stable per-thread slowdown factors and
# NO scheduler epochs -- so we sweep the EXISTING knobs (timeslice, heuristic,
# sticky-random-param, target-races, fuzz-futexes).
#
# Scratch device path is PINNED constant (interleaving is path-string sensitive).
# SCOPED pgid kills only. Output kept small: per-run guest text is transient;
# only signatures + one representative transcript per config are retained.
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
BOX="${BOX:-$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static}"
IMG="${IMG:-$ROOT/ignored/bench-seeds/bko-161811.raw}"
SCR="${SCR:-$ROOT/ignored/bench-seeds/framework.scratch}"   # PINNED path
N="${N:-30}"
EXP="${EXP:-$ROOT/experiments/chaos_experimentation_framework_20260728}"
WORK="${WORK:-$ROOT/ignored/bench-seeds/fwork}"; mkdir -p "$WORK"
CFG="$EXP/configs.tsv"
RES="$EXP/results.tsv"

guest(){ grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"; }

printf 'config\tseed\texit\tsig12\tscanlines\n' > "$RES"

nconf=$(($(wc -l < "$CFG") - 1))
i=0
# read config table (skip header); args column may contain spaces
tail -n +2 "$CFG" | while IFS=$'\t' read -r name args; do
  [ -z "$name" ] && continue
  i=$((i+1))
  for s in $(seq 1 "$N"); do
    cp -f "$IMG" "$SCR"
    # shellcheck disable=SC2086
    "$SC" --timeout 300 --output "$WORK/r.out" -- \
        "$H" $args --seed "$s" -- "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
    ex=$(grep -o 'command_exit=[0-9]*' "$WORK/r.out.status" 2>/dev/null | cut -d= -f2)
    guest "$WORK/r.out" > "$WORK/g.txt"
    sig=$(sha256sum "$WORK/g.txt" | cut -c1-12)
    sl=$(grep -c 'Scanning:' "$WORK/g.txt")
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$s" "${ex:-NA}" "$sig" "$sl" >> "$RES"
  done
  cp -f "$WORK/g.txt" "$EXP/outputs/${name}.seed${N}.guest.txt"
  echo "[$i/$nconf] done $name"
done
echo "results -> $RES"
