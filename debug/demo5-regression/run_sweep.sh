#!/usr/bin/env bash
# Driver: sweep --target-timeslice K across orders of magnitude, both modes,
# SERIALLY (one run at a time) to keep per-turn metrics clean under host load.
# Load-independent metric of record = vtime_per_turn (vtime_burned/turns) and
# escape; wall-based burn_rate is recorded but load-contaminated (ignore for
# linearity). Raw logs land under ignored/ (gitignored).
set -uo pipefail
HERE=/home/newton/work/dev-hermit/debug/demo5-regression
OUT=$HERE/ignored/sweep
SW=$HERE/spin_sweep.sh
mkdir -p "$OUT"
echo "### norcb sweep (the suspected missing-burn-out regime) ###"
for K in 1000 10000 100000 1000000 10000000; do
  "$SW" norcb "$K" 120 "$OUT" "norcb-K$K"
done
echo "### rcbtime sweep (burn-out present control; partial-wall) ###"
for K in 10000 100000 1000000; do
  "$SW" rcbtime "$K" 150 "$OUT" "rcbtime-K$K"
done
echo "### SWEEP DONE ###"
column -t -s, "$OUT/sweep.csv"