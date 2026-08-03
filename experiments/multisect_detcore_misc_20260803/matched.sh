#!/usr/bin/env bash
# matched.sh <conc> <timeout_s> <waves> <label:binpath>...
#
# Matched-load co-scheduling probe (owner's 30x-concurrent trinary design).
# For each wave, launch <conc> single-shot instances of EVERY label's binary,
# ALL interleaved and started together, so every commit is sampled under the
# SAME instantaneous host load. Single-shot only (no per-iteration loops / no
# `date` in the hot path) to stay under the BPFJailer exec-rate enforcer.
#
# Trinary per (label,wave):  all-pass=PASS  all-hang/zero-pass=FAIL  mixed=FLAKY.
# FLAKY is a RED condition. Include a known-bad calibrator label (e.g. head): a
# wave is only VALID if the calibrator comes back FAIL/FLAKY; a calibrator that
# reports 30/30 clean means the wave was under-powered -> discard it.
#
# Emits per-wave lines then a per-label aggregate over VALID waves.
set -uo pipefail
EXP="$(cd "$(dirname "$0")" && pwd)"
CONC="$1"; TMO="$2"; WAVES="$3"; shift 3
TEST="vfork::vfork_parent_resumes_after_child_exec"
STAMP="$(date +%Y%m%dT%H%M%S)"
WD="$EXP/ignored/matched/$STAMP"
mkdir -p "$WD"

specs=("$@")
labels=(); declare -A BIN
for spec in "${specs[@]}"; do
  l="${spec%%:*}"; b="${spec#*:}"
  labels+=("$l"); BIN[$l]="$b"
  [ -x "$b" ] || { echo "MISSING BIN $l: $b" >&2; exit 3; }
done

# Optional evidence capture (attribution). STRESS_CAPTURE_DIR set => each FAILING
# instance preserves a bundle (stdout/stderr/exit/host-conditions) so a flake
# gets a CAUSE, not just a rate. Off by default => the hot loop below stays the
# exact old >/dev/null idiom. Pure bash so the loop never forks per-instance
# Python (BPFJailer exec-rate). Resolve capture-run.sh from the repo root.
CAPTURE_DIR="${STRESS_CAPTURE_DIR:-}"
CAPTURE_SH="$(cd "$EXP/../.." 2>/dev/null && pwd)/ci-hub/attribution/capture-run.sh"
if [ -n "$CAPTURE_DIR" ] && [ ! -x "$CAPTURE_SH" ]; then
  echo "matched.sh: STRESS_CAPTURE_DIR set but $CAPTURE_SH missing/x; capture OFF" >&2
  CAPTURE_DIR=""
fi
[ -n "$CAPTURE_DIR" ] && mkdir -p "$CAPTURE_DIR"

classify() { # <file> -> echo "PASS|FAIL|FLAKY hangs passes other total"
  local f="$1" h p o t
  h=$(grep -cx 124 "$f" 2>/dev/null); h=${h:-0}
  p=$(grep -cx 0 "$f" 2>/dev/null); p=${p:-0}
  t=$(wc -l < "$f" 2>/dev/null); t=${t:-0}
  o=$((t - h - p))
  local c
  if [ "$t" -eq 0 ]; then c=NORUN
  elif [ "$p" -eq "$t" ]; then c=PASS
  elif [ "$p" -eq 0 ]; then c=FAIL
  else c=FLAKY; fi
  echo "$c $h $p $o $t"
}

echo "=== MATCHED conc=$CONC timeout=${TMO}s waves=$WAVES labels=${labels[*]} ==="
echo "nproc=$(nproc) load.start=$(cat /proc/loadavg)"

set +m
for w in $(seq 1 "$WAVES"); do
  for l in "${labels[@]}"; do rm -f "$WD/w$w.$l"; done
  # Interleave launches: round-robin across labels so all share the same window.
  for i in $(seq 1 "$CONC"); do
    for l in "${labels[@]}"; do
      if [ -n "$CAPTURE_DIR" ]; then
        ( ec="$("$CAPTURE_SH" "$CAPTURE_DIR" "w$w-$l" "$TMO" -- \
                 "${BIN[$l]}" "$TEST" --exact --test-threads=1)"
          echo "$ec" >> "$WD/w$w.$l" ) &
      else
        ( timeout "$TMO" "${BIN[$l]}" "$TEST" --exact --test-threads=1 >/dev/null 2>&1; echo $? >> "$WD/w$w.$l" ) &
      fi
    done
  done
  wait
  # Reap strays by basename of each binary.
  for l in "${labels[@]}"; do pkill -9 -x "$(basename "${BIN[$l]}")" 2>/dev/null; done
  line="wave$w load=$(cut -d' ' -f1 /proc/loadavg) |"
  for l in "${labels[@]}"; do
    read c h p o t <<<"$(classify "$WD/w$w.$l")"
    line="$line  $l:$c($h/$t)"
  done
  echo "$line"
done
echo "load.end=$(cat /proc/loadavg)"
echo "results: $WD"
