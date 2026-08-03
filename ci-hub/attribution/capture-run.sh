#!/usr/bin/env bash
# capture-run.sh — evidence-preserving run wrapper for BASH harnesses.
#
# The drop-in replacement for the evidence-destroying idiom that pervades our
# stress/corpus harnesses:
#
#     ( timeout "$T" "$BIN" args >/dev/null 2>&1; echo $? >> exitcodes )
#
# That keeps only an integer exit code, so by the time a flake is noticed the
# evidence needed to ATTRIBUTE it (was it infra load, hermit nondeterminism, or
# a varying host read?) is already gone. This wrapper runs the command, and ON
# FAILURE preserves a bundle — full stdout, stderr, exit code, wall time, and
# the HOST CONDITIONS AT THAT MOMENT — in the same layout attribution.py reads
# (stdout, stderr, meta.json). On success it discards (cheap), matching the old
# behavior's footprint.
#
# It writes NO Python in the hot path (a 64x10-instance burst cannot afford a
# per-instance interpreter start, and the BpfJailer exec-rate enforcer would
# throttle it) — pure bash + one printf'd meta.json.
#
# USAGE:
#   capture-run.sh <bundle_root> <label> <timeout_s> -- <cmd> [args...]
#     -> prints the command's exit code on stdout (so callers can `ec=$(...)`).
#     -> exit status mirrors the command (124 on timeout).
#
# ENV:
#   ATTR_PROC_PATTERN   comm substring to count for concurrent-proc pressure
#                       (default "hermit").
#   ATTR_CAPTURE_MAX    cap on preserved bundles under <bundle_root> across all
#                       callers sharing it; extra failures are COUNTED (a
#                       .dropped file) not silently dropped (default 200, 0=∞).
#   ATTR_CAPTURE_KEEP_OK=1  also preserve successful runs (debugging only).
set -uo pipefail

BROOT="$1"; LABEL="$2"; TMO="$3"; shift 3
[ "${1:-}" = "--" ] && shift
PATTERN="${ATTR_PROC_PATTERN:-hermit}"
CAP="${ATTR_CAPTURE_MAX:-200}"

# --- run, capturing streams to temp files (deleted unless we preserve them) ----
tout="$(mktemp)"; terr="$(mktemp)"
start=$(date +%s.%N 2>/dev/null || date +%s)
timeout "$TMO" "$@" >"$tout" 2>"$terr"
rc=$?
end=$(date +%s.%N 2>/dev/null || date +%s)
wall=$(awk "BEGIN{printf \"%.3f\", $end-$start}" 2>/dev/null || echo "")
timed_out=false
[ "$rc" -eq 124 ] && timed_out=true

# --- success (rc 0) → discard, matching the old >/dev/null footprint ----------
if [ "$rc" -eq 0 ] && [ -z "${ATTR_CAPTURE_KEEP_OK:-}" ]; then
  rm -f "$tout" "$terr"
  echo "$rc"
  exit "$rc"
fi

# --- failure → preserve a bundle (respecting the logged cap) ------------------
if [ -n "$BROOT" ]; then
  mkdir -p "$BROOT"
  # Enforce the cap by counting existing bundles; over the cap, just tally.
  if [ "$CAP" -gt 0 ]; then
    n=$(find "$BROOT" -mindepth 1 -maxdepth 1 -type d -name "${LABEL}-*" 2>/dev/null | wc -l)
    if [ "$n" -ge "$CAP" ]; then
      # DO NOT drop silently: record that we dropped one (owner: no silent caps).
      echo 1 >> "$BROOT/.dropped-over-cap"
      rm -f "$tout" "$terr"
      echo "$rc"; exit "$rc"
    fi
  fi
  stamp="$(date -u +%Y%m%dT%H%M%S)"
  bundle="$BROOT/${LABEL}-${stamp}-$$-${RANDOM}"
  mkdir -p "$bundle"
  mv "$tout" "$bundle/stdout"; mv "$terr" "$bundle/stderr"

  # Host conditions at this instant.
  read -r l1 l5 l15 _ < /proc/loadavg 2>/dev/null || { l1=; l5=; l15=; }
  nproc_n="$(nproc 2>/dev/null || echo)"
  procs="$(grep -lc . /proc/[0-9]*/comm 2>/dev/null >/dev/null; \
           grep -sl "$PATTERN" /proc/[0-9]*/comm 2>/dev/null | wc -l)"
  cpupsi="$(sed -n 's/^some .*avg10=\([0-9.]*\).*/\1/p' /proc/pressure/cpu 2>/dev/null | head -1)"
  cmd_str="$*"
  # Minimal JSON (attribution.py recomputes shape from exit_code + streams).
  jstr() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
  {
    printf '{\n'
    printf '  "schema_version": 1,\n'
    printf '  "label": "%s",\n' "$(jstr "$LABEL")"
    printf '  "cmd_str": "%s",\n' "$(jstr "$cmd_str")"
    printf '  "exit_code": %s,\n' "$rc"
    printf '  "timed_out": %s,\n' "$timed_out"
    printf '  "wall_s": %s,\n' "${wall:-null}"
    printf '  "captured_at": "%sZ",\n' "$(date -u +%Y-%m-%dT%H:%M:%S)"
    printf '  "host_after": {\n'
    printf '    "load1": %s,\n' "${l1:-null}"
    printf '    "load5": %s,\n' "${l5:-null}"
    printf '    "load15": %s,\n' "${l15:-null}"
    printf '    "nproc": %s,\n' "${nproc_n:-null}"
    printf '    "concurrent_procs": %s,\n' "${procs:-null}"
    printf '    "proc_pattern": "%s",\n' "$(jstr "$PATTERN")"
    printf '    "cpu_pressure_avg10": %s,\n' "${cpupsi:-null}"
    printf '    "captured_at": "%sZ"\n' "$(date -u +%Y-%m-%dT%H:%M:%S)"
    printf '  }\n'
    printf '}\n'
  } > "$bundle/meta.json"
else
  rm -f "$tout" "$terr"
fi

echo "$rc"
exit "$rc"
