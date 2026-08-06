#!/bin/bash
# Denominator for the NOT-EXERCISED bucket over the backend-parity-c fixture family.
#
# `exercised` is a property of the TEST, not of the backend: if the bare host already produces the
# golden output, no backend can fail the cell. So one native+ptrace pair per fixture settles the
# whole row, which is why this is affordable where a full per-cell sweep is not.
set -u
R=${HERMIT:?set HERMIT}
FIXDIR=/home/newton/work/dev-hermit/worktrees/regress/hermit/tests/backend-parity/fixtures
OUT=/home/newton/work/dev-hermit/ignored/regress-kvm-livelock/exercise-audit.csv
BUILD=/home/newton/work/dev-hermit/ignored/regress-kvm-livelock/exbuild; mkdir -p "$BUILD"
export LD_LIBRARY_PATH=/home/newton/.local/hermit-deps/lu/usr/lib64
printf 'fixture,build,native_hash,ptrace_hash,verdict\n' > "$OUT"
for c in "$FIXDIR"/*.c; do
  n=$(basename "$c" .c); b="$BUILD/$n"
  if ! cc -std=c11 -O2 -D_GNU_SOURCE -pthread -o "$b" "$c" 2>/dev/null; then
    printf '%s,build-fail,,,SKIP-BUILD\n' "$n" >> "$OUT"; continue
  fi
  nat=$(env -i PATH=/usr/bin:/bin LC_ALL=C TZ=UTC timeout 60 "$b" 2>/dev/null | sha256sum | cut -c1-16)
  natrc=${PIPESTATUS[0]:-0}
  pt=$(timeout 120 "$R" run --backend ptrace --strict --base-env minimal -e LC_ALL=C -e TZ=UTC \
        --no-virtualize-cpuid --max-timeslice=disabled -- "$b" 2>/dev/null | sha256sum | cut -c1-16)
  if [ -z "$pt" ] || [ "$pt" = "e3b0c44298fc1c14" ]; then v=NO-PAYLOAD
  elif [ "$nat" = "$pt" ]; then v=NOT-EXERCISED
  else v=EXERCISED; fi
  printf '%s,ok,%s,%s,%s\n' "$n" "$nat" "$pt" "$v" >> "$OUT"
done
echo "--- denominator ---"; awk -F, 'NR>1{c[$5]++; t++} END{for(k in c) printf "  %-14s %3d\n",k,c[k]; printf "  %-14s %3d\n","TOTAL",t}' "$OUT"
echo "--- NOT-EXERCISED fixtures ---"; awk -F, 'NR>1 && $5=="NOT-EXERCISED"{print "  "$1}' "$OUT"
