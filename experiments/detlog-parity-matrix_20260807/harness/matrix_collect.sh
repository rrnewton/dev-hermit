#!/bin/bash
# Cross-backend DETLOG matrix collection.
#
# EVERY ATTEMPT IS RECORDED, including the ones that fail. A backend that cannot
# run is a NO-RESULT row with the exact refusal text, not an omitted row -- an
# omitted row silently shrinks the denominator and reads as "not applicable".
#
# No --detlog-stack / --detlog-heap: DETLOG is measured as its OWN emission
# stream, so nothing here can be inferred from, or contaminate, memory hashes.
H="$1"; OUT="$2"; RUNS="${3:-3}"
mkdir -p "$OUT"
G=/home/newton/work/dev-hermit/scratch/w7-liteinst-maps
: > "$OUT/attempts.tsv"
printf 'cell\tbackend\trun\trc\tdetlog_records\trefusal\n' >> "$OUT/attempts.tsv"
cells=(
  "notsc=$G/notsc"
  "detlog_syscalls=$G/pg/detlog_syscalls"
  "heap_fragment_reuse=$G/pg/heap_fragment_reuse"
  "stack_deep_recursion=$G/pg/stack_deep_recursion"
  "stdout_bytes=$G/pg/stdout_bytes"
  "bin_true=/bin/true"
  "bin_echo=/bin/echo"
)
for spec in "${cells[@]}"; do
  tag="${spec%%=*}"; cmd="${spec#*=}"
  for be in ptrace kvm liteinst dbi sabre e9patch; do
    for r in $(seq 1 "$RUNS"); do
      timeout 120 "$H" --log=info --backend="$be" run --strict --base-env=minimal \
          -- "$cmd" >/dev/null 2>"$OUT/.err"
      rc=$?
      grep -o 'DETLOG .*' "$OUT/.err" > "$OUT/$tag.$be.$r.d"
      n=$(wc -l < "$OUT/$tag.$be.$r.d")
      ref=""
      [ "$n" -eq 0 ] && ref=$(grep -m1 -o 'Error:.*' "$OUT/.err" | cut -c1-160)
      [ "$rc" -eq 124 ] && ref="TIMEOUT after 120s"
      printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$tag" "$be" "$r" "$rc" "$n" "$ref" >> "$OUT/attempts.tsv"
    done
  done
done
rm -f "$OUT/.err"
