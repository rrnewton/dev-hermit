#!/usr/bin/env bash
# Run every compat-envelope unit test and refuse a zero-test "pass".
#
# WHY THIS EXISTS: until now nothing invoked these tests. A grep for
# `compat-envelope/tests` across *.sh/*.py/*.rs/*.json/*.yml/Makefile matched
# only the scripts' own usage lines. That is how the A1 empty-denominator guard
# stayed unlanded for a day while a committed test that self-reports
# `A1-NOT-LANDED` sat beside the tool, never run by anything. A test nobody runs
# is documentation.
#
# Discovery is a glob, so a new `tests/test_*.sh` is picked up with no edit here
# and cannot be forgotten.
#
# Each test takes an optional renderer path as $1, so they can also be pointed
# at an in-flight copy:
#   compat-envelope/tests/test_render_scorecard_empty_denominator.sh /path/to/render-scorecard.rs
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Known-excluded, reported every run so the gap cannot go quiet. Silent
# truncation reads as "covered everything" when it did not.
EXCLUDED_PATH="$HERE/../test-render-scorecard.sh"
EXCLUDED_WHY="drives the renderer with --oracle-registry, a flag the renderer no longer accepts (exit 2, usage). Stale against the current CLI: it tests a surface that is gone, so wiring it in would red this gate for a reason unrelated to the scorecard. Needs a decision on whether the absolute-oracles surface is coming back."

discovered=0
passed=0
failed=0
failures=()

for test in "$HERE"/test_*.sh; do
  [ -e "$test" ] || continue
  discovered=$((discovered + 1))
  name=$(basename "$test")
  if [ ! -x "$test" ]; then
    printf 'FAIL  %-58s (not executable)\n' "$name"
    failed=$((failed + 1)); failures+=("$name (not executable)")
    continue
  fi
  if output=$("$test" 2>&1); then
    printf 'ok    %-58s\n' "$name"
    passed=$((passed + 1))
  else
    rc=$?
    printf 'FAIL  %-58s exit=%s\n' "$name" "$rc"
    printf '%s\n' "$output" | sed 's/^/        /'
    failed=$((failed + 1)); failures+=("$name (exit $rc)")
  fi
done

echo
echo "compat-envelope tests: discovered=$discovered passed=$passed failed=$failed"
echo "excluded (1): $(basename "$EXCLUDED_PATH") -- $EXCLUDED_WHY"

# A zero-test run is a NO-RESULT, never a pass: `ok` with nothing executed is
# exactly the ambiguous zero these tests exist to reject.
if [ "$discovered" -eq 0 ]; then
  echo "REFUSED: discovered 0 tests, which is a no-result, not a pass." >&2
  exit 3
fi

if [ "$failed" -ne 0 ]; then
  echo "FAILED: ${failures[*]}" >&2
  exit 1
fi

echo "PASS"
