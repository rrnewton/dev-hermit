#!/usr/bin/env bash
# A stored scorecard row must name the population it belongs to.
#
# WHY A FILENAME IS NOT A CONTRACT. e9patch reaches 20/20 on its DEDICATED
# corpus and 4/137 on the SHARED full corpus. Same backend; only the population
# separates near-complete coverage from near-zero. Today that distinction lives
# in whichever filename a caller happened to open, and filenames get copied,
# renamed, and quoted out of context. Anyone reading a number straight out of a
# CSV is reading an unlabelled population. This is the denominator rule applied
# to WHICH SET rather than HOW MANY, made durable in storage instead of only at
# print time.
#
# THE FIELD ALREADY EXISTS. It is `population_id`, present in all four published
# scorecards and BLANK on 2284 of 2284 rows. So this guard binds to it rather
# than adding a second column meaning the same thing -- this schema already
# carries `parity`/`stdout_parity` and `tier`/`parity_tier`/`comparison_tier`
# duplicate pairs, and a third is not an improvement.
#
# A LABEL IS NOT EVIDENCE. A bare string would satisfy "the row names its
# corpus" while still permitting a mislabel, which is the label-as-authority
# shape. So a named population must be CHECKABLE: a row claiming the shared full
# corpus must have its test_id present in corpus-manifest.csv, and a population
# name with no manifest is an UNKNOWN population -- which is not a licence.
#
# ENFORCEMENT IS A RATCHET, NOT A CLIFF. Requiring a non-blank population_id
# outright would fail every published scorecard on the first run and be switched
# off the same day. The blank count may not INCREASE past the recorded baseline:
# legacy rows are grandfathered with the count stated out loud, and any NEW
# unlabelled row fails immediately.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
MANIFEST="$ROOT/compat-envelope/corpus-manifest.csv"
# Baseline measured on origin/main. Raising this number is a policy decision and
# should be justified in the commit that does it; lowering it is always welcome.
BASELINE_BLANK=${POPULATION_ID_BLANK_BASELINE:-2284}
KNOWN_CORPUS="full-corpus"   # the one population we hold a manifest for
fails=0; checks=0
check() { checks=$((checks+1))
  if [ "$2" = "$3" ]; then printf '  ok   %-52s %s\n' "$1" "$3"
  else printf '  FAIL %-52s expected=%s actual=%s\n' "$1" "$2" "$3"; fails=$((fails+1)); fi; }

# audit <csv> <manifest> -> "BLANK=<n> UNKNOWN=<n> MISLABEL=<n> OK=<n>"
# Every class is printed even at zero: "no rows examined" and "all rows labelled"
# are different facts and must not collapse into one reassuring number.
audit() {
  python3 - "$1" "$2" "$KNOWN_CORPUS" <<'PY'
import csv, sys, os
path, manifest_path, known = sys.argv[1], sys.argv[2], sys.argv[3]
members = set()
if os.path.exists(manifest_path):
    for r in csv.DictReader(open(manifest_path, newline="")):
        t = (r.get("test_id") or "").strip()
        if t: members.add(t)
rows = list(csv.DictReader(open(path, newline="")))
if rows and "population_id" not in rows[0].keys():
    print("NOCOLUMN"); raise SystemExit
blank = unknown = mislabel = ok = 0
for r in rows:
    pop = (r.get("population_id") or "").strip()
    if not pop:
        blank += 1
    elif pop != known:
        # An unrecognised population cannot be checked, so it cannot be trusted.
        unknown += 1
    elif (r.get("test_id") or "").strip() not in members:
        # Claims the shared corpus but is not in it.
        mislabel += 1
    else:
        ok += 1
print(f"BLANK={blank} UNKNOWN={unknown} MISLABEL={mislabel} OK={ok}")
PY
}

echo "== fixtures: every class, both directions =="
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
printf 'bucket,test_id\napplications,applications/timed-progress-bar\nc-programs,c-programs/kcmp-eperm\n' > "$TMP/man.csv"
H="test_id,population_id"

printf '%s\napplications/timed-progress-bar,\n' "$H" > "$TMP/blank.csv"
check "no population_id -> BLANK" "BLANK=1 UNKNOWN=0 MISLABEL=0 OK=0" "$(audit "$TMP/blank.csv" "$TMP/man.csv")"

printf '%s\napplications/timed-progress-bar,full-corpus\n' "$H" > "$TMP/good.csv"
check "labelled AND a real member -> OK" "BLANK=0 UNKNOWN=0 MISLABEL=0 OK=1" \
  "$(audit "$TMP/good.csv" "$TMP/man.csv")"

# THE PLANTED MISLABEL: identical to the good row except the test_id is not a
# member of the corpus it claims. One variable.
printf '%s\nnot-in-corpus/fabricated,full-corpus\n' "$H" > "$TMP/mislabel.csv"
check "claims full-corpus but is NOT a member -> MISLABEL" \
  "BLANK=0 UNKNOWN=0 MISLABEL=1 OK=0" "$(audit "$TMP/mislabel.csv" "$TMP/man.csv")"

printf '%s\napplications/timed-progress-bar,some-corpus-we-have-no-manifest-for\n' "$H" > "$TMP/unknown.csv"
check "unknown population is not a licence -> UNKNOWN" \
  "BLANK=0 UNKNOWN=1 MISLABEL=0 OK=0" "$(audit "$TMP/unknown.csv" "$TMP/man.csv")"

printf 'test_id\napplications/timed-progress-bar\n' > "$TMP/nocol.csv"
check "absent column is NOCOLUMN, not zero blanks" "NOCOLUMN" \
  "$(audit "$TMP/nocol.csv" "$TMP/man.csv")"

echo "== live: published scorecards (ratchet on the blank count) =="
[ -f "$MANIFEST" ] || { echo "  FAIL manifest missing: $MANIFEST"; fails=$((fails+1)); checks=$((checks+1)); }
live_blank=0; live_bad=0
for csv in "$ROOT"/compat-envelope/*scorecard*.csv; do
  [ -e "$csv" ] || continue
  res=$(audit "$csv" "$MANIFEST")
  printf '  %-40s %s\n' "$(basename "$csv")" "$res"
  case "$res" in NOCOLUMN) continue ;; esac
  b=${res#BLANK=}; b=${b%% *}; live_blank=$((live_blank + b))
  m=${res#*MISLABEL=}; m=${m%% *}; live_bad=$((live_bad + m))
done
checks=$((checks+1))
if [ "$live_bad" -ne 0 ]; then
  printf '  FAIL %-52s mislabelled rows=%s\n' "corpus membership" "$live_bad"; fails=$((fails+1))
else
  printf '  ok   %-52s 0 mislabelled\n' "corpus membership"
fi
checks=$((checks+1))
if [ "$live_blank" -gt "$BASELINE_BLANK" ]; then
  printf '  FAIL %-52s blank=%s exceeds baseline %s\n' "unlabelled-row ratchet" "$live_blank" "$BASELINE_BLANK"
  fails=$((fails+1))
else
  printf '  ok   %-52s blank=%s <= baseline %s\n' "unlabelled-row ratchet" "$live_blank" "$BASELINE_BLANK"
fi
echo "  NOTE $live_blank of the published rows still carry NO population. They are"
echo "       grandfathered by the ratchet, NOT compliant. Lower POPULATION_ID_BLANK_BASELINE"
echo "       as they are backfilled; a new unlabelled row fails immediately."

echo
if [ "$fails" -eq 0 ]; then echo "PASS ($checks checks; $live_blank unlabelled rows outstanding)"; exit 0; fi
echo "FAIL ($fails of $checks checks)"
echo "A stored row must name a population that can be checked. Fill population_id,"
echo "and make sure the name is one we hold a manifest for -- an unknown population"
echo "is not a licence, and a filename is not a contract."
exit 1
