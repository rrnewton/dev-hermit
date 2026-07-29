#!/usr/bin/env bash
# Seeds/runs-to-reproduce benchmark: how many random seeds does each hermit
# scheduling strategy need to reproduce a target execution, per bug class?
#
# Two bug classes:
#   1. SCHEDULING-DEPENDENT (multithreaded): btrfs-progs v7.1 `rescue chunk-recover`
#      on a fuzz image. The scan worker + progress-reporter threads race on whether
#      the intermediate "Scanning: 0 in dev0" tick is emitted before "DONE".
#      Two benign interleavings:
#         sigA = "...Scanning: 0 in dev0   Scanning: DONE..."   (tick emitted)
#         sigB = "...Scanning: DONE..."                          (tick lost)
#      Baseline (what a NON-exploring run yields) = sigA. "Reproducing the race"
#      = observing sigB (a distinct interleaving). We measure per strategy:
#      hit-rate of sigB, distinct-signature coverage, seeds-to-first-sigB.
#   2. DETERMINISTIC (single-threaded logic bug): demo-08 btrfs check on the
#      issue-#207 crash image -> BUG_ON SIGABRT. Reproduces on ANY seed/strategy
#      => trivially 1 seed, 100% hit-rate. Included as the control row.
#
# Strategies swept over seeds 1..N (scratch device path is PINNED constant, since
# chunk-recover interleaving is path-string sensitive):
#   plain   : run --seed N                                   (default backend)
#   strict  : run --strict --seed N                          (deterministic sched)
#   chaos   : run --chaos --seed N                           (chaos sched)
#   ctr     : run --chaos --chaos-target-races --seed N      (race-targeted chaos)
#   ctr_ts  : ctr + --target-timeslice 100000 --max-timeslice 1000000000
#
# Output: results.tsv  (bug  strategy  seed  exit  sig12  siglabel)
# SCOPED pgid kills only (run_scoped.sh). Keep heavy output out of git (ignored/).
set -uo pipefail

ROOT="${ROOT:-$HOME/work/dev-hermit}"
H="${H:-$ROOT/hermit/target/release/hermit}"
SC="${SC:-$ROOT/experiments/btrfs_userspace_logic_20260728/run_scoped.sh}"
BOX="${BOX:-$ROOT/ignored/btrfs-progs-v7.1-bin/btrfs.box.static}"
CRIMG="${CRIMG:-$ROOT/ignored/bench-seeds/bko-161811.raw}"     # chunk-recover fuzz image
BUGGY="${BUGGY:-$ROOT/ignored/bp-buggy/btrfs}"                 # demo-08 buggy btrfs
CRASH="${CRASH:-$ROOT/ignored/demo08-repro/crash.btrfs}"       # demo-08 crash image
SCR="${SCR:-$ROOT/ignored/bench-seeds/bench.scratch}"          # PINNED scratch path
N="${N:-60}"
NCTRL="${NCTRL:-10}"   # deterministic control needs few seeds (outcome is seed-invariant)
OUT="${OUT:-$ROOT/experiments/btrfs_seeds_to_reproduce_benchmark_20260728}"
WORK="${WORK:-$ROOT/ignored/bench-seeds/work}"; mkdir -p "$WORK"
RES="$OUT/results.tsv"

# hermit's own ISO-timestamped tracing lines vs the guest's own output
guest() { grep -vE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z +(ERROR|WARN|INFO|DEBUG|TRACE) ' "$1"; }

# Strategy -> hermit arg template (seed substituted for %S)
strat_args() {
  case "$1" in
    plain)  echo "run --seed %S" ;;
    strict) echo "run --strict --seed %S" ;;
    chaos)  echo "run --chaos --seed %S" ;;
    ctr)    echo "run --chaos --chaos-target-races --seed %S" ;;
    ctr_ts) echo "run --chaos --chaos-target-races --seed %S --target-timeslice 100000 --max-timeslice 1000000000" ;;
  esac
}

printf 'bug\tstrategy\tseed\texit\tsig12\n' > "$RES"

run_one() { # <bug> <strategy> <seed> <guest-out-file> ; runs the guest, echoes "exit sig12"
  local bug="$1" strat="$2" seed="$3" gf="$4"
  local tmpl args st sig
  tmpl="$(strat_args "$strat")"; args="${tmpl//%S/$seed}"
  if [ "$bug" = chunk_recover ]; then
    cp -f "$CRIMG" "$SCR"
    # shellcheck disable=SC2086
    "$SC" --timeout 300 --output "$WORK/r.out" -- "$H" $args -- "$BOX" rescue chunk-recover -y -v "$SCR" >/dev/null 2>&1
  else # demo08_check (deterministic control)
    # shellcheck disable=SC2086
    "$SC" --timeout 300 --output "$WORK/r.out" -- "$H" $args -- "$BUGGY" check "$CRASH" >/dev/null 2>&1
  fi
  st=$(grep -o 'command_exit=[0-9]*' "$WORK/r.out.status" 2>/dev/null | cut -d= -f2)
  guest "$WORK/r.out" > "$gf"
  sig=$(sha256sum "$gf" | cut -c1-12)
  echo "${st:-NA} $sig"
}

echo "== benchmark: N=$N seeds x 5 strategies x 2 bugs =="
for bug in chunk_recover demo08_check; do
  nseeds="$N"; [ "$bug" = demo08_check ] && nseeds="$NCTRL"
  for strat in plain strict chaos ctr ctr_ts; do
    for s in $(seq 1 "$nseeds"); do
      read -r ex sig < <(run_one "$bug" "$strat" "$s" "$WORK/g.txt")
      printf '%s\t%s\t%s\t%s\t%s\n' "$bug" "$strat" "$s" "$ex" "$sig" >> "$RES"
    done
    # keep ONE representative transcript per (bug,strategy) for the record
    cp -f "$WORK/g.txt" "$OUT/outputs/${bug}.${strat}.seed${N}.guest.txt"
    echo "  done $bug/$strat"
  done
done
echo "results -> $RES"
