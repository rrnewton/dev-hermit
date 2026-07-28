#!/usr/bin/env bash
# Benchmark: does RR-style stable per-thread slowdown (--chaos-per-thread-slowdown)
# lower seeds-to-reproduce a scheduling interleaving vs plain --chaos?
#
# Same oracle/harness as experiments/chaos_experimentation_framework_20260728:
# btrfs-progs v7.1 `rescue chunk-recover -y -v` benign progress-tick race;
# per-run signature = sha256 of hermit-filtered guest output; distinct sigs =
# distinct interleavings. SCOPED pgid kills only. PINNED scratch path.
#
# We compare at the DEFAULT (coarse) timeslice — the "binary regime" where plain
# chaos finds the alternate schedule in ~15 seeds median (per the framework
# finding) — because that is where a stable per-thread factor should help most:
# it consistently biases one thread ahead/behind instead of averaging out.
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/worktrees/chaos/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
BOX="${BOX:-$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static}"
IMG="${IMG:-$ROOT/ignored/bench-seeds/bko-161811.raw}"
SCR="${SCR:-$ROOT/ignored/bench-seeds/pts_bench.scratch}"   # PINNED path
N="${N:-40}"
EXP="${EXP:-$ROOT/experiments/chaos_per_thread_slowdown_bench_20260728}"
WORK="${WORK:-$ROOT/ignored/bench-seeds/pts_work}"; mkdir -p "$WORK" "$EXP/outputs"
RES="$EXP/results.tsv"
PERRUN="${PERRUN:-120}"

guest(){ grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"; }

# config-name<TAB>args (default coarse timeslice for all; only the feature varies)
CONFIGS=$(cat <<'EOF'
chaos_plain	run --chaos
chaos_pts_r10	run --chaos --chaos-per-thread-slowdown --chaos-slowdown-max-factor 10
chaos_pts_r100	run --chaos --chaos-per-thread-slowdown --chaos-slowdown-max-factor 100
chaos_pts_r10_ts1e4	run --chaos --chaos-per-thread-slowdown --chaos-slowdown-max-factor 10 --target-timeslice 10000
chaos_plain_ts1e4	run --chaos --target-timeslice 10000
EOF
)

[ -f "$RES" ] || printf 'config\tseed\texit\tsig12\tscanlines\telapsed_ms\n' > "$RES"
done_cfgs=$(tail -n +2 "$RES" | cut -f1 | sort -u)

while IFS=$'\t' read -r name args; do
  [ -z "$name" ] && continue
  if grep -qx "$name" <<<"$done_cfgs"; then echo "skip (done): $name"; continue; fi
  echo "run: $name  [$args]"
  for s in $(seq 1 "$N"); do
    cp -f "$IMG" "$SCR"
    # shellcheck disable=SC2086
    "$SC" --timeout "$PERRUN" --output "$WORK/r.out" -- \
        "$H" $args --seed "$s" -- "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
    ex=$(grep -o 'command_exit=[0-9]*' "$WORK/r.out.status" 2>/dev/null | cut -d= -f2)
    ems=$(grep -o 'elapsed_ms=[0-9]*' "$WORK/r.out.status" 2>/dev/null | cut -d= -f2)
    guest "$WORK/r.out" > "$WORK/g.txt"
    sig=$(sha256sum "$WORK/g.txt" | cut -c1-12)
    sl=$(grep -c 'Scanning:' "$WORK/g.txt")
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$s" "${ex:-NA}" "$sig" "$sl" "${ems:-NA}" >> "$RES"
  done
  cp -f "$WORK/g.txt" "$EXP/outputs/${name}.seed${N}.guest.txt"
  echo "done: $name"
done <<<"$CONFIGS"
echo "sweep complete -> $RES"
