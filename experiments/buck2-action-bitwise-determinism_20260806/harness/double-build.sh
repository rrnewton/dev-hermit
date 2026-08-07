#!/bin/bash
# Run one buck2 target twice and report divergent actions.
#
# METHOD NOTE (this is the whole point of the script -- read before changing it):
# the obvious way to force two independent executions is two `--isolation-dir`s,
# which buck2 documents as causing cache misses. That method is UNSOUND for
# determinism testing. buck-out paths contain the isolation-dir name, and any
# action that writes a buck-out path into its output then diverges BY
# CONSTRUCTION. Measured: comparing isolation dirs `det-probe-A` and
# `det-probe-B` reported
#   fbsource//third-party/rust/vendor/derive_more-impl:_1 (write .../__derive_more_impl-link_dwo_paths.txt)
# as divergent, and the two outputs differed only in the substring
# `det-probe-A` vs `det-probe-B`. A false positive.
#
# So: ONE isolation dir, `buck2 clean` between runs. Output paths are then
# byte-identical across runs and cannot themselves be the difference.
#
# Usage: double-build.sh <target> [isolation-dir]
set -uo pipefail

TARGET="${1:?usage: double-build.sh <target> [isolation-dir]}"
ISO="${2:-det-same}"
FBSOURCE="${FBSOURCE:-/home/newton/fbsource}"
TMO="${TMO:-1800}"

cd "$FBSOURCE" || exit 2

# DAEMON HYGIENE -- load-bearing. Each --isolation-dir starts and KEEPS its own
# buck2 daemon. Leaving several alive on a shared box gets them OOM-killed, and
# buck2 reports that as `BUILD FAILED` with a gRPC broken-pipe cause that reads
# nothing like a memory problem -- which this experiment then misread as the
# targets being unbuildable. Reuse one isolation dir and kill it on the way out.
# NEVER `buck2 killall`: other agents share this box and it would kill theirs.
trap 'buck2 --isolation-dir "$ISO" kill >/dev/null 2>&1' EXIT

traces=()
for run in 1 2; do
  echo "=== run $run: $TARGET ===" >&2
  out=$(timeout "$TMO" buck2 --isolation-dir "$ISO" build "$TARGET" \
          --local-only --no-remote-cache 2>&1)
  rc=$?
  trace=$(grep -oE 'buck2/[0-9a-f-]{36}' <<<"$out" | head -1 | cut -d/ -f2)
  echo "  rc=$rc trace=$trace" >&2
  if [ "$rc" -ne 0 ]; then
    echo "BUILD-FAILED $TARGET run$run rc=$rc" >&2
    grep -E "BUILD FAILED|Error" <<<"$out" | head -3 >&2
    exit "$rc"
  fi
  traces+=("$trace")
  # Clean between runs so run 2 genuinely re-executes every action into the
  # SAME paths run 1 used.
  if [ "$run" = 1 ]; then
    timeout 900 buck2 --isolation-dir "$ISO" clean >/dev/null 2>&1
  fi
done

echo "=== action-divergence ${traces[0]} vs ${traces[1]} ===" >&2
timeout 600 buck2 log diff action-divergence \
  --trace-id1 "${traces[0]}" --trace-id2 "${traces[1]}" 2>&1
