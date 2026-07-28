#!/usr/bin/env bash
# LiteInst corpus sweep harness.
#
# Compiles the C corpus, runs each program natively and under the hermit ptrace
# and liteinst backends (both with --strict --verify), and emits a CSV plus a
# per-program verdict.
#
# li_verdict values:
#   L2-MATCH  : liteinst verified L2 AND guest stdout equals native (or the
#               program is intentionally determinized, so a native mismatch is
#               correct — see DETERMINIZED set).
#   L2-WRONG  : liteinst verified L2 (rc 0, "Determinism verified") BUT guest
#               stdout differs from native for a program that should match —
#               a functionally degraded result blessed as deterministic.
#   REJECT    : liteinst exited nonzero (fail-closed).
#   HANG      : liteinst exceeded the per-run timeout.
#   CCFAIL    : program did not compile.
#
# ptrace runs are a determinism baseline. NOTE: the ptrace backend discards
# guest stdout under --verify (headless double-run + log compare), so its
# out_sha is the empty-string hash; only ptrace_verify (L2/FAIL) is meaningful.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
HERMIT="${HERMIT:-/home/newton/work/dev-hermit/worktrees/liteinst/hermit/target/release/hermit}"
TIMEOUT="${TIMEOUT:-60}"
SRC="$ROOT/src"; BIN="$ROOT/bin"; OUT="$ROOT/results"
LOGDIR="$OUT/logs"; mkdir -p "$BIN" "$OUT" "$LOGDIR"
CSV="$OUT/results.csv"

# Extra link flags per program.
declare -A EXTRA
EXTRA[08_mathlib]="-lm"
EXTRA[17_pthread]="-lpthread"

# Programs whose output is intentionally determinized by hermit and therefore
# will NOT match the native run (that mismatch is correct, not a bug).
DETERMINIZED=" 05_getpid 14_getrandom "

sha() { sha256sum | cut -c1-12; }

echo "prog,native_rc,native_out_sha,ptrace_verify,li_rc,li_verify,li_out_sha,li_matches_native,li_verdict" > "$CSV"

run_backend() { # $1=backend $2=prog $3=bin -> RC, VERIFY, OUTSHA
  local backend="$1" prog="$2" bin="$3"
  local outf="$LOGDIR/${prog}.${backend}.out" errf="$LOGDIR/${prog}.${backend}.err"
  timeout "$TIMEOUT" "$HERMIT" --backend "$backend" run --strict --verify -- "$bin" >"$outf" 2>"$errf"
  RC=$?
  OUTSHA=$(sha < "$outf")
  if [ "$RC" -eq 124 ]; then
    VERIFY="HANG"
  elif [ "$RC" -eq 0 ] && grep -q "Determinism verified" "$errf"; then
    VERIFY="L2"
  else
    VERIFY="FAIL"
  fi
}

for c in "$SRC"/*.c; do
  prog=$(basename "$c" .c)
  bin="$BIN/$prog"
  cc -O1 -o "$bin" "$c" ${EXTRA[$prog]:-} 2>"$LOGDIR/${prog}.cc.err"
  if [ ! -x "$bin" ]; then
    echo "$prog,CCFAIL,-,-,-,-,-,-,CCFAIL" >> "$CSV"
    printf '%-16s CCFAIL\n' "$prog"; continue
  fi
  nout="$LOGDIR/${prog}.native.out"
  timeout "$TIMEOUT" "$bin" >"$nout" 2>/dev/null; NRC=$?; NSHA=$(sha < "$nout")
  run_backend ptrace   "$prog" "$bin"; PV=$VERIFY
  run_backend liteinst "$prog" "$bin"; LRC=$RC; LV=$VERIFY; LSHA=$OUTSHA
  if [ "$LSHA" = "$NSHA" ]; then MATCH="yes"; else MATCH="no"; fi
  # verdict
  if [ "$LV" = "HANG" ]; then
    VERDICT="HANG"
  elif [ "$LV" = "FAIL" ]; then
    VERDICT="REJECT"
  elif [ "$LV" = "L2" ]; then
    if [ "$MATCH" = "yes" ] || [[ "$DETERMINIZED" == *" $prog "* ]]; then
      VERDICT="L2-MATCH"
    else
      VERDICT="L2-WRONG"
    fi
  else
    VERDICT="UNKNOWN"
  fi
  echo "$prog,$NRC,$NSHA,$PV,$LRC,$LV,$LSHA,$MATCH,$VERDICT" >> "$CSV"
  printf '%-16s native_rc=%s ptrace=%s li_rc=%s li=%s match=%s => %s\n' \
    "$prog" "$NRC" "$PV" "$LRC" "$LV" "$MATCH" "$VERDICT"
done
echo "=== CSV: $CSV ==="
column -s, -t "$CSV"
