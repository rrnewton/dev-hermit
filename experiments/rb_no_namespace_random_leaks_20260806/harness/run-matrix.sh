#!/bin/bash
# Emits results.csv: for each (source, mode) the number of DISTINCT values over N runs.
# distinct==1 => determinized; distinct>1 => leak. N defaults to 10.
set -u
HERMIT="${HERMIT_BIN:?set HERMIT_BIN to the hermit binary}"
HERE="$(cd "$(dirname "$0")" && pwd)"
N="${N:-10}"
OUT="${OUT:-$HERE/../results.csv}"
export LC_ALL=C TZ=UTC

emit() { # $1=mode $2..=hermit prefix argv (empty for native)
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
  for src in at_random gettimeofday getpid bash_random procfs_uuid; do
    local distinct runs
    distinct=$(grep "^$src=" "$tmp" | sort -u | wc -l)
    runs=$(grep -c "^$src=" "$tmp")
    printf '%s,%s,%s,%s,%s\n' "$src" "$mode" "$runs" "$distinct" \
      "$([ "$distinct" -eq 1 ] && echo determinized || echo LEAK)"
  done
  rm -f "$tmp"
}

{
  echo "source,mode,runs,distinct_values,verdict"
  emit native
  emit hermit-default-namespace "$HERMIT" run
  emit hermit-no-namespace      "$HERMIT" run --no-namespace
} >"$OUT"
cat "$OUT"
