#!/usr/bin/env bash
# Chaos leg: the sharpest available ordering probe for process trees.
#
# TWO-SIDED, so a pass cannot be an inert pass:
#   SAME SEED, twice      -> the process-event trace MUST be identical
#                            (chaos is documented as reproducible under a fixed seed)
#   DIFFERENT SEEDS       -> the trace SHOULD differ for at least one guest, otherwise
#                            --chaos is not actually perturbing this corpus and the
#                            same-seed pass proves nothing about ordering.
# Reporting both is the point: `same_seed_eq=1` alone is a proxy unless
# `any_seed_varies=1` shows the knob has teeth on the same guests.
set -u
ROOT=/home/newton/work/dev-hermit
BASE=$ROOT/ignored/fork-exec-parity
export HERMIT_BIN="${HERMIT_BIN:?}"
export LD_LIBRARY_PATH="$ROOT/ignored/lu-parity/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
OUT="${OUT:-$BASE/chaos}"
BACKEND="${BACKEND:-ptrace}"
SEEDS="${SEEDS:-1 2 3}"
mkdir -p "$OUT"
CSV="$OUT/results.csv"
echo "guest,backend,seed_a_run1_rc,same_seed_pev_eq,same_seed_out_eq,distinct_pev_across_seeds,n_seeds,verdict" > "$CSV"

for spec in "$@"; do
  name="${spec%%=*}"; cmd="${spec#*=}"
  read -r -a CMDA <<< "$cmd"
  d="$OUT/$name"; rm -rf "$d"; mkdir -p "$d"

  nseeds=0
  for s in $SEEDS; do
    EXTRA_RUN_FLAGS="--chaos --sched-seed $s" "$BASE/run-cell.sh" "$BACKEND" "$d" "s$s.a" "${CMDA[@]}"
    nseeds=$((nseeds+1))
  done
  # repeat the FIRST seed to test same-seed reproducibility
  first=$(echo $SEEDS | awk '{print $1}')
  EXTRA_RUN_FLAGS="--chaos --sched-seed $first" "$BASE/run-cell.sh" "$BACKEND" "$d" "s$first.b" "${CMDA[@]}"

  rc=$(cat "$d/s$first.a.rc" 2>/dev/null || echo 99)
  if [ "$rc" != 0 ]; then
    echo "$name,$BACKEND,$rc,,,,$nseeds,RUNFAIL" >> "$CSV"; continue
  fi
  for s in $SEEDS; do python3 "$BASE/pevents.py" "$d/s$s.a.log" > "$d/s$s.a.pev" 2>/dev/null; done
  python3 "$BASE/pevents.py" "$d/s$first.b.log" > "$d/s$first.b.pev" 2>/dev/null

  cmp -s "$d/s$first.a.pev" "$d/s$first.b.pev" && eq=1 || eq=0
  cmp -s "$d/s$first.a.out" "$d/s$first.b.out" && oeq=1 || oeq=0
  dp=$(for s in $SEEDS; do sha256sum < "$d/s$s.a.pev"; done | sort -u | wc -l)

  if [ "$eq" = 1 ] && [ "$dp" -gt 1 ]; then v=REPRODUCIBLE-AND-PERTURBING
  elif [ "$eq" = 1 ]; then v=REPRODUCIBLE-BUT-SEED-INERT
  else v=NONDETERMINISTIC; fi
  echo "$name,$BACKEND,$rc,$eq,$oeq,$dp,$nseeds,$v" >> "$CSV"
done
column -s, -t < "$CSV"
