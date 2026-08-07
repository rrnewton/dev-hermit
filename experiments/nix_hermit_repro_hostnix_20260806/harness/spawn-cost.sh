#!/usr/bin/env bash
# spawn-cost.sh — quantify THE gate on whole-package determinization.
#
# The exec-builder seam is cheap to apply and correct; what makes a real
# package expensive is that hermit determinizes by sequentializing, and an
# autotools/cmake configure is a few thousand short-lived processes. This
# measures the per-process cost directly so the README can state a number
# instead of "slow".
#
# Usage: spawn-cost.sh [N_PROCS] [REPS]
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$here/env.sh"

n="${1:-200}"; reps="${2:-3}"
prog='for i in $(seq 1 '"$n"'); do /bin/true; done'

t() { # echo elapsed seconds (float) of "$@"
  local s e; s=$(date +%s.%N); "$@" >/dev/null 2>&1; e=$(date +%s.%N)
  awk -v a="$s" -v b="$e" 'BEGIN{printf "%.3f", b-a}'
}

echo "spawn-cost: $n sequential /bin/true execs, $reps reps"
echo "mode,rep,wall_s,per_proc_ms"
for r in $(seq 1 "$reps"); do
  w=$(t /bin/bash -c "$prog")
  awk -v w="$w" -v n="$n" -v r="$r" 'BEGIN{printf "native,%d,%s,%.3f\n", r, w, w*1000/n}'
done
for r in $(seq 1 "$reps"); do
  # shellcheck disable=SC2086
  w=$(t "$HERMIT" $HERMIT_ARGS -- /bin/bash -c "$prog")
  awk -v w="$w" -v n="$n" -v r="$r" 'BEGIN{printf "hermit,%d,%s,%.3f\n", r, w, w*1000/n}'
done
