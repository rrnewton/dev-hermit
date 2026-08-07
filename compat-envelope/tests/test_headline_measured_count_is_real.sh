#!/usr/bin/env bash
# A published headline may not claim more measured cells than its CSV can support.
#
# WHY. A run that measured nothing is indistinguishable from one that measured
# everything and passed -- unless the executed-cell count travels with the
# headline AND that count dereferences the column the percentage is about. The
# live instance this guard was written for: SCORECARD-CURRENT.md publishes
# `sabre 78.8% stdout-parity, measured 179/179` and the prose "Coverage is
# complete here ... no cell is a 'never measured' zero", while `stdout_parity`
# is BLANK on 200 of 200 sabre rows -- and on 1000 of 1000 rows across all five
# backends. The percentage is computed from `legacy_parity_unqualified`, a
# column whose own name says it does not qualify (142/180 = 78.9%, which is the
# published 78.8).
#
# So the failure is not a missing count. It is a count that is PRESENT, LOUD, and
# BOUND TO THE WRONG THING -- the worst case, because it reads as reassurance.
#
# THE PREDICATE. For each backend a headline publishes a parity percentage for,
# the qualified observable must be non-blank on at least one row of that
# backend's own rows. Zero qualified cells renders as 0 and CANNOT pass. This
# does not judge whether a percentage is correct; a static check cannot. It
# asserts that a percentage is not published over an empty measurement.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
fails=0
checks=0

check() { # $1=label $2=expected $3=actual
  checks=$((checks + 1))
  if [ "$2" = "$3" ]; then printf '  ok   %-56s %s\n' "$1" "$3"
  else printf '  FAIL %-56s expected=%s actual=%s\n' "$1" "$2" "$3"; fails=$((fails + 1)); fi
}

# qualified_measured <csv> <backend> -> count of rows whose qualified observable
# column is non-blank. The observable is resolved by NAME, and a CSV carrying
# neither column is an error rather than a zero: "column absent" and "column
# present and empty" are different facts and must not collapse.
qualified_measured() {
  python3 - "$1" "$2" <<'PY'
import csv, sys
path, backend = sys.argv[1], sys.argv[2]
rows = list(csv.DictReader(open(path, newline="")))
if not rows:
    print("NOROWS"); raise SystemExit
cols = rows[0].keys()
obs = next((c for c in ("stdout_parity", "parity") if c in cols), None)
if obs is None:
    print("NOCOLUMN"); raise SystemExit
mine = [r for r in rows if (r.get("backend") or "").strip() == backend]
print(sum(1 for r in mine if (r.get(obs) or "").strip()))
PY
}

echo "== fixture: both directions, one variable =="
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
HDR="run_id,backend,outcome,stdout_parity,legacy_parity_unqualified"
# NEGATIVE: the qualified observable is blank, exactly like the live sabre rows,
# while an unqualified column is populated and would happily yield a percentage.
printf '%s\nr,sabre,pass,,stdout_parity:1\nr,sabre,pass,,stdout_parity:1\n' "$HDR" > "$TMP/blank.csv"
check "blank qualified observable -> 0 measured" "0" "$(qualified_measured "$TMP/blank.csv" sabre)"
# POSITIVE: identical but for the one field under test, so the fixtures differ in
# nothing else. Without this the guard could be returning 0 unconditionally.
printf '%s\nr,sabre,pass,1,stdout_parity:1\nr,sabre,pass,0,stdout_parity:1\n' "$HDR" > "$TMP/evid.csv"
check "populated qualified observable -> 2 measured" "2" "$(qualified_measured "$TMP/evid.csv" sabre)"
# A CSV with no observable column at all must say so, not report a zero.
printf 'run_id,backend,outcome\nr,sabre,pass\n' > "$TMP/nocol.csv"
check "absent observable column is NOCOLUMN, not 0" "NOCOLUMN" "$(qualified_measured "$TMP/nocol.csv" sabre)"
# A backend with no rows is 0 measured, which is correct and distinct from NOCOLUMN.
check "backend absent from a valid CSV -> 0" "0" "$(qualified_measured "$TMP/evid.csv" kvm)"

echo "== live: every backend a headline quotes a percentage for =="
DOC="$ROOT/compat-envelope/SCORECARD-CURRENT.md"
CSV="$ROOT/compat-envelope/fullcorpus-scorecard.csv"
if [ ! -f "$DOC" ] || [ ! -f "$CSV" ]; then
  # A missing input is UNAVAILABLE, never a silent pass: a guard that skips when
  # blind is the same defect it exists to catch.
  echo "  FAIL missing input (doc=$DOC csv=$CSV)"; fails=$((fails + 1)); checks=$((checks + 1))
else
  for be in dbi kvm sabre liteinst; do
    # Only assert against backends the document actually publishes a number for.
    row=$(grep -E "^\| $be(\b| )" "$DOC" | grep -E '\| *[0-9]' | head -n1)
    if [ -n "$row" ]; then
      n=$(qualified_measured "$CSV" "$be")
      checks=$((checks + 1))
      # A percentage over zero qualified cells is allowed to STAND only if the row
      # DECLARES itself unqualified. Withdrawing the number entirely would delete
      # real information; leaving it undeclared is the fake green. So the guard
      # can be satisfied by measuring, or by declaring -- never by silence.
      if [ "$n" != "0" ] && [ "$n" != "NOCOLUMN" ] && [ "$n" != "NOROWS" ]; then
        printf '  ok   %-56s qualified_measured=%s\n' "$be" "$n"
      elif printf '%s' "$row" | grep -qi 'unqualified'; then
        printf '  ok   %-56s qualified_measured=%s, row DECLARES unqualified\n' "$be" "$n"
      else
        printf '  FAIL %-56s qualified_measured=%s, percentage published undeclared\n' "$be" "$n"
        fails=$((fails + 1))
      fi
    fi
  done
fi

echo
if [ "$fails" -eq 0 ]; then echo "PASS ($checks checks)"; exit 0; fi
echo "FAIL ($fails of $checks checks)"
echo "A published parity percentage must be backed by a non-zero count of QUALIFIED"
echo "observable cells for that backend. Either populate the qualified observable,"
echo "or withdraw the percentage and state the count as 0 -- a zero renders as 0"
echo "and cannot pass. Do not satisfy this by pointing it at the unqualified column."
exit 1
