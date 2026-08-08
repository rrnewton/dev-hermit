#!/usr/bin/env bash
# Bracket the SIZE-DELTA behaviour of the pre-commit hygiene gate.
#
# WHY DELTA. An absolute-only limit fires on every commit to a legitimately
# growing tracked file, and a gate that fires on every legitimate operation
# trains routine override until it protects nothing. The validate ledger shard
# crossed 1024 KiB on its first import, so every future import needed the
# override. Meanwhile SEVEN tracked files already exceed the limit and six are
# raw logs under experiments/ -- the exact class the gate exists to refuse.
#
# The two populations separate by DELTA, not by size: the six arrived large in
# ONE commit with no prior version; the ledger got there by repeated small
# appends. So this suite's job is to prove the gate now tells them apart, and
# that widening it for the first did not widen it for the second.
#
#   ALLOW   a routine append to an already-tracked over-limit file, NO OVERRIDE
#   REFUSE  the same size arriving as a NEW file (no baseline to grow from)
#   REFUSE  an abnormal jump appended to a tracked file
#   REFUSE  anything over the 2 MiB coordinator threshold -- INCLUDING under
#           HERMIT_HYGIENE_OVERRIDE, and including when reached by a small delta
#   ALLOW   each escape hatch, or every "REFUSE" above would prove nothing
#
# The escape-hatch cases are not decoration: without them a hook that refused
# unconditionally would pass every negative test in this file.
set -u

HOOK_SRC="${1:-.githooks/pre-commit}"
[ -f "$HOOK_SRC" ] || { echo "FATAL: hook not found at '$HOOK_SRC'" >&2; exit 2; }
HOOK_SRC=$(cd "$(dirname "$HOOK_SRC")" && pwd)/$(basename "$HOOK_SRC")

TMPROOT=$(mktemp -d "${TMPDIR:-/tmp}/size-delta-XXXXXX") || exit 2
trap 'rm -rf "$TMPROOT"' EXIT

PASS=0; FAIL=0
declare -a FAILURES=()

kib_file() { # <path> <KiB>
  head -c $(( $2 * 1024 )) /dev/zero | tr '\0' 'x' > "$1"
}

# run_case <expect: refuse|allow> <name> <baseline-KiB|-> <staged-KiB> [env...]
#
# A refusal is only counted if it is a SIZE refusal. Asserting the exit code
# alone would let an unrelated gate (a missing serialization tool, say) satisfy
# every negative case while the size logic was broken or inert.
#
# baseline "-" means the path does NOT exist in HEAD (a brand-new file); any
# number seeds a first commit at that size so the hook has a real baseline to
# measure growth against. That distinction is the whole point of the suite.
run_case() {
  local expect="$1" name="$2" baseline="$3" staged="$4"; shift 4
  local repo="$TMPROOT/repo.$RANDOM$RANDOM"
  mkdir -p "$repo/.githooks/tests" "$repo/scripts"
  # NOT on `main`: the hook's first gate is the parent-main serialization check,
  # which refuses when scripts/parent-main-write is absent. In a fixture that
  # would make EVERY case refuse -- and the negative cases would have "passed"
  # for a reason that has nothing to do with size. Hence the branch, and hence
  # the reason-assertion below.
  git -C "$repo" init -q -b probe
  git -C "$repo" config user.email delta@test.local
  git -C "$repo" config user.name "size delta bracket"
  git -C "$repo" config core.hooksPath .githooks
  cp "$HOOK_SRC" "$repo/.githooks/pre-commit"; chmod +x "$repo/.githooks/pre-commit"

  echo seed > "$repo/seed.txt"
  git -C "$repo" add seed.txt
  git -C "$repo" -c core.hooksPath=/dev/null commit -qm seed

  if [ "$baseline" != "-" ]; then
    kib_file "$repo/data.jsonl" "$baseline"
    git -C "$repo" add data.jsonl
    # Seeded with the hook disabled: the baseline is a precondition, not a
    # behaviour under test, and it may itself be over the limit.
    git -C "$repo" -c core.hooksPath=/dev/null commit -qm baseline
  fi
  kib_file "$repo/data.jsonl" "$staged"
  git -C "$repo" add data.jsonl

  local out rc
  out=$(cd "$repo" && env "$@" git commit -qm probe -- data.jsonl 2>&1); rc=$?
  local got; [ "$rc" -eq 0 ] && got=allow || got=refuse

  # Guard against passing for the wrong reason.
  if [ "$got" = refuse ] && ! printf '%s' "$out" | grep -qE "soft limit|CEILING"; then
    printf '  FAIL  %-64s refused, but NOT by the size gate\n' "$name"
    FAIL=$((FAIL + 1)); FAILURES+=("$name (wrong refusal reason)")
    printf '%s\n' "$out" | sed 's/^/          /' | head -4
    rm -rf "$repo"; return
  fi

  if [ "$got" = "$expect" ]; then
    printf '  PASS  %-64s (%s)\n' "$name" "$got"
    PASS=$((PASS + 1))
  else
    printf '  FAIL  %-64s expected %s got %s\n' "$name" "$expect" "$got"
    FAIL=$((FAIL + 1))
    FAILURES+=("$name")
    printf '%s\n' "$out" | sed 's/^/          /' | head -6
  fi
  rm -rf "$repo"
}

echo "-- POSITIVE: a legitimately growing tracked file must commit with NO override --"
# The real ledger shape: 1066 KiB -> 1221 KiB, +155 KiB, 81 rows.
run_case allow  "routine ledger-shaped append (1100 KiB +150) needs no override" 1100 1250
run_case allow  "append exactly at the 256 KiB growth allowance"                 1100 1356
run_case allow  "small new file is unaffected"                                   -      8
run_case allow  "growth on a file still UNDER the limit"                         200  400

echo
echo "-- NEGATIVE: the gate must still refuse what it exists to refuse --"
# This is the six experiments/ logs: large in one commit, no prior version.
run_case refuse "NEW 1200 KiB file (no baseline) is still refused"               -    1200
run_case refuse "abnormal jump appended to a tracked file (+600 KiB)"            1100 1700
run_case refuse "new file just over the limit"                                   -    1100

echo
echo "-- CEILING: the 2 MiB coordinator threshold must hold, and must NOT collapse --"
run_case refuse "new file over the 2048 KiB ceiling"                             -    2500
run_case refuse "ceiling holds even under HERMIT_HYGIENE_OVERRIDE=1" \
                -    2500  HERMIT_HYGIENE_OVERRIDE=1
run_case refuse "a SMALL delta cannot buy past the ceiling (1900 +200)"          1900 2100
run_case refuse "ceiling holds for a small delta under HERMIT_HYGIENE_OVERRIDE=1" \
                1900 2100  HERMIT_HYGIENE_OVERRIDE=1

echo
echo "-- ESCAPE HATCHES: each must release, or every REFUSE above proves nothing --"
run_case allow  "HERMIT_HYGIENE_OVERRIDE=1 still releases the SOFT limit" \
                -    1200  HERMIT_HYGIENE_OVERRIDE=1
run_case allow  "HERMIT_HYGIENE_CEILING_OVERRIDE=1 releases the CEILING" \
                -    2500  HERMIT_HYGIENE_CEILING_OVERRIDE=1
run_case allow  "HERMIT_HYGIENE_MAX_GROWTH_KB widens the allowance explicitly" \
                1100 1700  HERMIT_HYGIENE_MAX_GROWTH_KB=1024

echo
echo "======================================================================"
echo "allows expected and observed: 7    refuses expected and observed: 7"
echo "assertions: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  printf 'FAILED: %s\n' "${FAILURES[@]}"
  echo "RESULT: FAIL"
  exit 1
fi
echo "RESULT: PASS"
