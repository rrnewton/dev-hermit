#!/bin/bash
# Collect DETLOG streams: N runs x {ptrace, liteinst} x each cell.
# No stack/heap hash flags — DETLOG is measured as its OWN emission stream, so a
# stack result can neither be inferred from this nor contaminate it.
H="$1"; OUT="$2"; RUNS="${3:-3}"
mkdir -p "$OUT"
GUESTS=/home/newton/work/dev-hermit/scratch/w7-liteinst-maps
cells=(
  "notsc=$GUESTS/notsc"
  "detlog_syscalls=$GUESTS/pg/detlog_syscalls"
  "heap_fragment_reuse=$GUESTS/pg/heap_fragment_reuse"
  "stack_deep_recursion=$GUESTS/pg/stack_deep_recursion"
  "stdout_bytes=$GUESTS/pg/stdout_bytes"
  "bin_true=/bin/true"
  "bin_echo=/bin/echo"
)
for spec in "${cells[@]}"; do
  tag="${spec%%=*}"; cmd="${spec#*=}"
  for be in ptrace liteinst; do
    for r in $(seq 1 "$RUNS"); do
      if [ "$be" = ptrace ]; then bflag=(); else bflag=(--backend=liteinst); fi
      timeout 300 "$H" --log=info "${bflag[@]}" run --strict --base-env=minimal \
          -- "$cmd" >/dev/null 2>"$OUT/$tag.$be.$r.raw"
      echo "$tag $be run$r rc=$?" >> "$OUT/collect.log"
      grep -o 'DETLOG .*' "$OUT/$tag.$be.$r.raw" > "$OUT/$tag.$be.$r.d"
      rm -f "$OUT/$tag.$be.$r.raw"
    done
  done
done
