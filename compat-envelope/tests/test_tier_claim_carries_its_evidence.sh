#!/usr/bin/env bash
# A qualifying comparison tier must carry the evidence its NAME claims.
#
# THE TIERING IS A REAL COST CONSTRAINT, NOT A LOOPHOLE. Stack/heap hashing is
# too expensive for the large tests, so the owner's standard is deliberately
# tiered: short tests get the full standard (stdout + INFO + stack + heap),
# large tests get stdout + INFO every run with stack/heap on a spot-check
# cadence. The goal of this guard is NOT to eliminate the cheaper tier. It is
# that the cheaper tier is HONESTLY LABELLED and the dearer one is substantiated.
#
# WHAT IS WRONG TODAY. `qualifies_as_green` in render-scorecard.rs is a pure
# string match:
#     matches!(tier, FULL_COMPARISON_TIER | SPOT_CHECK_COMPARISON_TIER)
# so a cell is green because its LABEL SAYS SO. Nothing dereferences stack or
# heap evidence, and nothing dates the spot-check. Worse, the schema cannot
# express either: there is no stack/heap evidence column and no spot-check date
# column in any of the four published scorecards. So
# `full-stdout-info-stack-heap` names four signals while the schema can record
# at most two, and `stdout-info-stack-heap-spot-check` qualifies as green
# FOREVER because nothing records when the spot-check happened.
#
# That makes both qualifying tiers UNFALSIFIABLE — assertion, not measurement.
# This guard states the evidence contract that makes them falsifiable:
#
#   full-stdout-info-stack-heap        requires non-blank stack_hash AND heap_hash
#   stdout-info-stack-heap-spot-check  requires a non-blank spot_check_utc, and is
#                                      STALE (not current) past the cadence
#
# A tier claim that cannot be substantiated is REFUSED — it is not counted green
# and it is not counted as a confirmed failure either; it is unqualified.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
CADENCE_DAYS=${TIER_SPOT_CHECK_CADENCE_DAYS:-30}
fails=0; checks=0
check() { checks=$((checks+1))
  if [ "$2" = "$3" ]; then printf '  ok   %-54s %s\n' "$1" "$3"
  else printf '  FAIL %-54s expected=%s actual=%s\n' "$1" "$2" "$3"; fails=$((fails+1)); fi; }

# audit <csv> <now_epoch> -> "QUALIFYING=<n> REFUSED=<n> STALE=<n> OK=<n>"
# Counts are printed even when zero, because "no qualifying rows examined" and
# "all qualifying rows passed" are different facts that must never collapse into
# a bare PASS.
audit() {
  python3 - "$1" "$2" "$CADENCE_DAYS" <<'PY'
import csv, sys
path, now, cadence = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
FULL, SPOT = "full-stdout-info-stack-heap", "stdout-info-stack-heap-spot-check"
rows = list(csv.DictReader(open(path, newline="")))
cols = rows[0].keys() if rows else []
g = lambda r, c: (r.get(c) or "").strip()
q = refused = stale = ok = 0
for r in rows:
    tier = g(r, "comparison_tier")
    if tier not in (FULL, SPOT):
        continue                      # unqualified tiers are history, not claims
    q += 1
    if tier == FULL:
        # The dearer tier must show the two signals its name adds over stdout+INFO.
        if g(r, "stack_hash") and g(r, "heap_hash"): ok += 1
        else: refused += 1
    else:
        when = g(r, "spot_check_utc")
        if not when:
            refused += 1              # undated spot-check can never go stale
        else:
            try: t = int(when.lstrip("@"))
            except ValueError: refused += 1; continue
            stale += 1 if (now - t) > cadence * 86400 else 0
            ok += 1 if (now - t) <= cadence * 86400 else 0
print(f"QUALIFYING={q} REFUSED={refused} STALE={stale} OK={ok}")
PY
}

echo "== fixtures: each tier claim, substantiated and not =="
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
NOW=1785000000
H="comparison_tier,stack_hash,heap_hash,spot_check_utc"
FRESH=$((NOW - 5*86400)); OLD=$((NOW - 400*86400))

printf '%s\nfull-stdout-info-stack-heap,,,\n' "$H" > "$TMP/full_bare.csv"
check "FULL tier with no stack/heap evidence -> REFUSED" \
  "QUALIFYING=1 REFUSED=1 STALE=0 OK=0" "$(audit "$TMP/full_bare.csv" $NOW)"
printf '%s\nfull-stdout-info-stack-heap,abc,def,\n' "$H" > "$TMP/full_evid.csv"
check "FULL tier WITH stack+heap evidence -> OK" \
  "QUALIFYING=1 REFUSED=0 STALE=0 OK=1" "$(audit "$TMP/full_evid.csv" $NOW)"
# One signal is not both: the tier name adds stack AND heap.
printf '%s\nfull-stdout-info-stack-heap,abc,,\n' "$H" > "$TMP/full_half.csv"
check "FULL tier with stack but NO heap -> REFUSED" \
  "QUALIFYING=1 REFUSED=1 STALE=0 OK=0" "$(audit "$TMP/full_half.csv" $NOW)"

printf '%s\nstdout-info-stack-heap-spot-check,,,\n' "$H" > "$TMP/spot_undated.csv"
check "SPOT-CHECK with no date -> REFUSED" \
  "QUALIFYING=1 REFUSED=1 STALE=0 OK=0" "$(audit "$TMP/spot_undated.csv" $NOW)"
printf '%s\nstdout-info-stack-heap-spot-check,,,%s\n' "$H" "$FRESH" > "$TMP/spot_fresh.csv"
check "SPOT-CHECK within cadence -> OK, current" \
  "QUALIFYING=1 REFUSED=0 STALE=0 OK=1" "$(audit "$TMP/spot_fresh.csv" $NOW)"
printf '%s\nstdout-info-stack-heap-spot-check,,,%s\n' "$H" "$OLD" > "$TMP/spot_stale.csv"
check "SPOT-CHECK past cadence -> STALE, not counted current" \
  "QUALIFYING=1 REFUSED=0 STALE=1 OK=0" "$(audit "$TMP/spot_stale.csv" $NOW)"

# An unqualified tier is history, never a claim, and must not be audited as one.
printf '%s\nlegacy-unqualified,,,\n' "$H" > "$TMP/legacy.csv"
check "unqualified tier is not a claim -> 0 qualifying" \
  "QUALIFYING=0 REFUSED=0 STALE=0 OK=0" "$(audit "$TMP/legacy.csv" $NOW)"

echo "== live: published scorecards =="
NOW_LIVE=$(date -u +%s)
liveq=0
for csv in "$ROOT"/compat-envelope/*scorecard*.csv; do
  [ -e "$csv" ] || continue
  res=$(audit "$csv" "$NOW_LIVE")
  printf '  %-42s %s\n' "$(basename "$csv")" "$res"
  n=${res#QUALIFYING=}; n=${n%% *}; liveq=$((liveq + n))
  r=${res#*REFUSED=}; r=${r%% *}
  s=${res#*STALE=}; s=${s%% *}
  if [ "$r" != "0" ] || [ "$s" != "0" ]; then
    checks=$((checks+1)); fails=$((fails+1))
    printf '  FAIL %-54s refused=%s stale=%s\n' "$(basename "$csv")" "$r" "$s"
  fi
done
# State the denominator. A guard with nothing to examine must SAY so rather than
# print a bare PASS that reads as "all tier claims are substantiated".
echo "  NOTE qualifying tier claims examined across all published scorecards: $liveq"
if [ "$liveq" -eq 0 ]; then
  echo "  NOTE 0 qualifying claims exist today (every row is an unqualified tier),"
  echo "       so the live half is ARMED BUT VACUOUS. The fixtures above are what"
  echo "       currently demonstrate the predicate fires."
fi

echo
if [ "$fails" -eq 0 ]; then echo "PASS ($checks checks, $liveq live qualifying claims)"; exit 0; fi
echo "FAIL ($fails of $checks checks)"
echo "A qualifying tier must carry the evidence its name claims: FULL needs"
echo "stack_hash+heap_hash; SPOT-CHECK needs spot_check_utc within the cadence."
echo "Do not satisfy this by relabelling a cell to an unqualified tier it did not earn."
exit 1
