#!/usr/bin/env bash
# Regression test for the ambiguous-zero fixes A3 and A4 in prefix_depth.sh.
#
# Brackets BOTH ways, which is the point: it is easy to make every zero read
# "unknown" and call the hazard fixed. These cases assert that a genuine zero is
# reported as NOT-EXERCISED *with its denominator*, AND that a real result still
# scores normally.
#
#   A3  a backend that exits 0 while emitting nothing must be NO-RUN, not Y=0.
#   A4  a golden run with no records must be NO-GOLDEN and emit no backend rows.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
SUT="$here/../prefix_depth.sh"
export HERMIT="$here/stub_hermit.sh"
chmod +x "$HERMIT" "$SUT" 2>/dev/null

fail=0
check() { # $1=label $2=expected-substring $3=actual
  if grep -qF -- "$2" <<<"$3"; then
    printf '  ok    %s\n' "$1"
  else
    printf '  FAIL  %s\n         expected to find: %s\n' "$1" "$2"; fail=1
  fi
}
refute() { # $1=label $2=forbidden-substring $3=actual
  if grep -qF -- "$2" <<<"$3"; then
    printf '  FAIL  %s\n         must NOT contain: %s\n' "$1" "$2"; fail=1
  else
    printf '  ok    %s\n' "$1"
  fi
}

echo "POSITIVE CONTROL — a real result must still score"
out=$(OUT=$(mktemp -d) STUB_SPEC_ptrace=5:0:0 STUB_SPEC_dbi=5:0:0 STUB_SPEC_sabre=5:0:3 \
      STUB_SPEC_e9patch=5:0:0 "$SUT" 'demo=/bin/true' 2>&1); rc=$?
check "identical backend scores full depth 5/5" "dbi            5      5      5" "$out"
check "diverging backend scores partial depth 2/5" "sabre          2      5      5" "$out"
check "golden self-reference is 5/5"  "ptrace(golden)      5      5      5" "$out"
refute "no spurious NO-RUN on a good run" "NO-RUN" "$out"
[ "$rc" -eq 0 ] && echo "  ok    exit 0 when every guest had a denominator" || { echo "  FAIL  exit=$rc"; fail=1; }

echo "A3 — backend exits 0 while emitting nothing (sabre's documented failure mode)"
out=$(OUT=$(mktemp -d) STUB_SPEC_ptrace=5:0:0 STUB_SPEC_dbi=5:0:0 STUB_SPEC_sabre=0:0:0 \
      STUB_SPEC_e9patch=5:0:0 "$SUT" 'demo=/bin/true' 2>&1)
check "reported NO-RUN, not a depth"        "sabre     NO-RUN      5      0" "$out"
check "carries the denominator and reason"  "emitted 0 comparable records" "$out"
refute "must not publish Y=0 for it"        "sabre          0      5" "$out"
check "other backends still score"          "dbi            5      5      5" "$out"

echo "A4 — golden produced no comparable records"
out=$(OUT=$(mktemp -d) STUB_SPEC_ptrace=0:1:0 STUB_SPEC_dbi=5:0:0 STUB_SPEC_sabre=5:0:0 \
      STUB_SPEC_e9patch=5:0:0 "$SUT" 'demo=/bin/true' 2>&1); rc=$?
check "reported NO-GOLDEN"                  "NO-GOLDEN" "$out"
refute "no reassuring 0/0 self-match"       "ptrace(golden)      0      0" "$out"
refute "no backend row with a 0 denominator" "dbi            5      0" "$out"
[ "$rc" -eq 2 ] && echo "  ok    exit 2 distinguishes could-not-measure" || { echo "  FAIL  exit=$rc, want 2"; fail=1; }

echo
[ "$fail" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$fail"
