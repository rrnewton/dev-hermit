#!/bin/bash
# Emits results.csv. For each (source, mode): the number of DISTINCT values over
# N runs. distinct==1 => determinized; distinct>1 => leak.
#
# Two extra rows per mode record WRITE VISIBILITY, which is what makes a mode
# usable for Nix at all: `write_visible_tmp` (Nix's build directory lives under
# host /tmp when `sandbox = false`) and `write_visible_out` (a path outside
# /tmp, standing in for $out in /nix/store). A mode is only a solution if it
# determinizes AND both writes survive.
set -u
HERMIT="${HERMIT_BIN:?set HERMIT_BIN to the hermit binary}"
HERE="$(cd "$(dirname "$0")" && pwd)"
N="${N:-10}"
OUT="${OUT:-$HERE/../results.csv}"
export LC_ALL=C TZ=UTC

RB_TMP_TARGET=/tmp/rb-witness-$$
RB_OUT_TARGET="$HERE/../.witness-out-$$"
export RB_TMP_TARGET RB_OUT_TARGET

emit() { # $1=mode label; $2.. = hermit prefix argv (empty for native)
  local mode="$1"; shift
  local -a pre=("$@")
  local tmp; tmp="$(mktemp)"
  for _ in $(seq "$N"); do
    if [ "${#pre[@]}" -eq 0 ]; then
      "$HERE/probes"; /bin/bash "$HERE/shell-probes.sh"
    else
      "${pre[@]}" -- "$HERE/probes"
      "${pre[@]}" -- /bin/bash "$HERE/shell-probes.sh"
    fi
  done 2>/dev/null >"$tmp"
  local src distinct runs
  for src in at_random gettimeofday getpid bash_random procfs_uuid; do
    distinct=$(grep "^$src=" "$tmp" | sort -u | wc -l)
    runs=$(grep -c "^$src=" "$tmp")
    printf '%s,%s,%s,%s,%s\n' "$src" "$mode" "$runs" "$distinct" \
      "$([ "$distinct" -eq 1 ] && echo determinized || echo LEAK)"
  done
  rm -f "$tmp"

  # Write visibility: run once, then look from the host.
  rm -rf "$RB_TMP_TARGET" "$RB_OUT_TARGET"
  if [ "${#pre[@]}" -eq 0 ]; then
    /bin/bash "$HERE/write-probe.sh" >/dev/null 2>&1
  else
    "${pre[@]}" -- /bin/bash "$HERE/write-probe.sh" >/dev/null 2>&1
  fi
  local vis
  vis=$([ -f "$RB_TMP_TARGET/witness" ] && echo visible || echo DISCARDED)
  printf 'write_visible_tmp,%s,1,-,%s\n' "$mode" "$vis"
  vis=$([ -f "$RB_OUT_TARGET/witness" ] && echo visible || echo DISCARDED)
  printf 'write_visible_out,%s,1,-,%s\n' "$mode" "$vis"
  rm -rf "$RB_TMP_TARGET" "$RB_OUT_TARGET"
}

{
  echo "source,mode,runs,distinct_values,verdict"
  emit native
  emit hermit-default-namespace  "$HERMIT" run
  emit hermit-tmp-host           "$HERMIT" run --tmp=/tmp
  emit hermit-no-namespace       "$HERMIT" run --no-namespace
} >"$OUT"
cat "$OUT"
