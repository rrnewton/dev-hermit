#!/bin/bash
# Reproduce the heap-domain parity measurement.
#
#   ./run.sh [OUTDIR]
#
# Builds the guest-side domain enumerator, runs it under each available backend
# (twice, plus once with a planted one-byte heap mutation), and writes the raw
# per-region records plus results.csv.
#
# The guest MUST NOT live under /tmp -- hermit isolates the guest's /tmp, so a
# guest staged there fails to resolve its own path.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${REPO:-/home/newton/work/dev-hermit}"
HERMIT="${HERMIT:-$REPO/hermit/target/debug/hermit}"   # release lacks the sabre feature
OUT="${1:-$REPO/ignored/w10-heapdomain}"

# libunwind at runtime; see the task env block.
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib}"

mkdir -p "$OUT" || exit 2
gcc -O0 -Wall -Wextra -o "$OUT/probe" "$HERE/heap_domain_probe.c" -lpthread || exit 2

# `--base-env minimal -e LC_ALL=C -e TZ=UTC` pins the guest environment. Without
# it the ambient shell decides the guest's initial stack address and, through the
# env block's size, perturbs the layout the domain is measured against.
run_one() { # backend label extra-args
  timeout 300 "$HERMIT" run --backend "$1" --strict \
      --base-env minimal -e LC_ALL=C -e TZ=UTC \
      -- "$OUT/probe" $3 > "$OUT/$2.txt" 2> "$OUT/$2.err"
  echo "  $2 rc=$? regions=$(grep -c REGION "$OUT/$2.txt")"
}

for b in ptrace sabre dbi; do
  echo "backend $b"
  run_one "$b" "${b}_r1"  ""
  run_one "$b" "${b}_r2"  ""
  run_one "$b" "${b}_mut" "--mutate"
done

python3 "$HERE/analyze.py" "$OUT" > "$OUT/results.csv"
echo "wrote $OUT/results.csv"
