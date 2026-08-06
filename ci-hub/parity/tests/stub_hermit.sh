#!/usr/bin/env bash
# Stub `hermit` for prefix_depth.sh tests. Lets a test control, per backend and
# INDEPENDENTLY, how many comparable records are emitted and what exit code is
# returned -- which is the whole point, since the A3 defect was exactly a backend
# that exits 0 while emitting nothing.
#
# Control: STUB_SPEC_<backend>="<records>:<rc>:<diverge_at>"
#   records    how many COMMIT lines to emit (0 = emit nothing)
#   rc         exit code to return
#   diverge_at 1-based record index from which content differs from the golden
#              body (0 or > records = never diverge, i.e. identical prefix)
set -uo pipefail

log_file=""; backend=""
for a in "$@"; do
  case "$a" in
    --log-file=*) log_file="${a#--log-file=}" ;;
    --backend) backend="__next__" ;;
    *) [ "$backend" = "__next__" ] && backend="$a" ;;
  esac
done
[ -n "$backend" ] && [ "$backend" != "__next__" ] || backend=ptrace

spec_var="STUB_SPEC_${backend}"
spec="${!spec_var:-0:0:0}"
records="${spec%%:*}"; rest="${spec#*:}"; rc="${rest%%:*}"; diverge="${rest##*:}"

if [ -n "$log_file" ] && [ "$records" -gt 0 ]; then
  : > "$log_file"
  for i in $(seq 1 "$records"); do
    if [ "$diverge" -gt 0 ] && [ "$i" -ge "$diverge" ]; then
      printf 'COMMIT turn %d, dettid 3 using resources {DIVERGED-%s}\n' "$i" "$backend" >> "$log_file"
    else
      printf 'COMMIT turn %d, dettid 3 using resources {Path(0x%04x): R}\n' "$i" "$i" >> "$log_file"
    fi
  done
fi
exit "$rc"
