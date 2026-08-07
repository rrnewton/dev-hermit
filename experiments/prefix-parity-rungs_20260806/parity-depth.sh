#!/usr/bin/env bash
# Prefix-parity depth per rung: how many leading INFO-log records a backend
# keeps IDENTICAL to the ptrace golden. Reports Y/Z, always with Z.
#
# PRECONDITION enforced per rung: the ptrace golden must be double-run identical.
# A rung whose golden is not self-deterministic is reported NOT MEASURABLE, never
# as a backend depth -- the backend would "diverge" for a reason no backend fix
# can close.
set -uo pipefail
cd /home/newton/work/dev-hermit
export PKG_CONFIG_PATH=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64/pkgconfig
export LIBRARY_PATH=/home/newton/work/dev-hermit/ignored/lu-parity/usr/lib64
export LD_LIBRARY_PATH=/home/newton/fbsource/fbcode/third-party-buck/platform010/build/libunwind/lib

HARNESS=ignored/det4-detlogdiff/hermit/scripts/xbdiff-richer-stream.rs
HERMIT=${HERMIT_BIN:?set HERMIT_BIN}
OUT=ignored/det4-parity-depth.tsv
: > "$OUT"
printf 'rung\tbackend\tverdict\tY_depth\tZ_total\tnote\n' >> "$OUT"

run() { # rung_label backendpair -- guest...
  local label=$1 pair=$2; shift 2; shift   # drop the literal --
  timeout 900 "$HARNESS" --hermit "$HERMIT" --backends "$pair" --context 1 -- "$@" 2>&1
}

declare -a RUNGS=(
  "true|/bin/true"
  "echo|/bin/echo hi"
  "coreutil-cat|/bin/cat /etc/hostname"
  "coreutil-wc|/bin/wc -c /etc/hostname"
  "fork-exec-pipeline|/bin/sh -c echo\ a\ |\ /bin/wc -c"
)

for entry in "${RUNGS[@]}"; do
  label=${entry%%|*}; guest=${entry#*|}
  # shellcheck disable=SC2206
  argv=($guest)
  echo "################ RUNG: $label -- ${argv[*]}"

  # --- PRECONDITION: ptrace golden self-determinism -------------------------
  self=$(run "$label" ptrace,ptrace -- "${argv[@]}")
  # A TOOL failure must never be reported as a golden failure. Only a real
  # FIRST DIVERGENCE counts as non-self-determinism; anything that produced
  # neither verdict is the harness breaking, and is reported as such.
  if ! echo "$self" | grep -qE "STREAMS AGREE|FIRST DIVERGENCE"; then
    echo "  golden self-determinism: TOOL ERROR (no verdict emitted) -- NOT a golden failure"
    echo "$self" | tail -3 | sed 's/^/    /'
    printf '%s\tptrace(golden self-check)\tTOOL-ERROR\t-\t-\tharness emitted no verdict; rung not attempted\n' "$label" >> "$OUT"
    continue
  fi
  if echo "$self" | grep -q "STREAMS AGREE"; then
    Z=$(echo "$self" | grep -oE 'STREAMS AGREE: [0-9]+' | grep -oE '[0-9]+')
    echo "  golden self-determinism: PASS (Z=$Z records)"
    printf '%s\tptrace(golden self-check)\tPASS\t%s\t%s\tdouble-run identical\n' "$label" "$Z" "$Z" >> "$OUT"
  else
    y=$(echo "$self" | grep -oE 'record index [0-9]+' | grep -oE '[0-9]+' | head -1)
    echo "  golden self-determinism: *** FAIL *** (diverges at ${y:-?})"
    printf '%s\tptrace(golden self-check)\tFAIL\t%s\t?\tgolden not self-deterministic; rung NOT MEASURABLE\n' \
      "$label" "${y:-?}" >> "$OUT"
    echo "$self" | sed -n '/FIRST DIVERGENCE/,+6p' | sed 's/^/    /'
    continue
  fi

  # --- the measurement, per backend -----------------------------------------
  for b in sabre dbi; do
    o=$(run "$label" "ptrace,$b" -- "${argv[@]}")
    if echo "$o" | grep -q "STREAMS AGREE"; then
      n=$(echo "$o" | grep -oE 'STREAMS AGREE: [0-9]+' | grep -oE '[0-9]+')
      echo "  $b: FULL PARITY $n/$n"
      printf '%s\t%s\tFULL\t%s\t%s\tidentical for the whole log\n' "$label" "$b" "$n" "$n" >> "$OUT"
    elif echo "$o" | grep -q "FIRST DIVERGENCE"; then
      y=$(echo "$o" | grep -oE 'record index [0-9]+' | grep -oE '[0-9]+' | head -1)
      z=$(echo "$o" | grep -oE 'ptrace records: [0-9]+' | grep -oE '[0-9]+' | head -1)
      [[ -z $y ]] && y=$(echo "$o" | grep -oE 'agree for all [0-9]+' | grep -oE '[0-9]+')
      echo "  $b: depth ${y:-?}/${z:-$Z}"
      printf '%s\t%s\tDIVERGE\t%s\t%s\t%s\n' "$label" "$b" "${y:-?}" "${z:-$Z}" \
        "$(echo "$o" | grep -A2 'divergent record' | tail -2 | tr '\n' ' ' | cut -c1-160)" >> "$OUT"
    else
      echo "  $b: NOT MEASURABLE"
      printf '%s\t%s\tNO-RUN\t-\t%s\t%s\n' "$label" "$b" "$Z" \
        "$(echo "$o" | grep -iE 'error|unavailable|no deterministic' | head -1 | cut -c1-140)" >> "$OUT"
    fi
  done
done
echo; echo "=== $OUT ==="; column -t -s$'\t' "$OUT"
