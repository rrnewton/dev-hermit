#!/usr/bin/env bash
# Does tier evidence actually SURVIVE a write into the canonical scorecard?
#
# THE BUG THIS EXISTS FOR. The producers learned to emit `verify_compare`,
# `bitwise_parity`, `compared_log_messages` and `tier`, and the focused producer
# tests passed -- but the canonical scorecard.csv was 20 columns wide and carried
# only `verify_compare`. The writer binds to the FILE's schema and drops keys the
# file has no column for, so three of the four fields were silently discarded on
# the way in. Every producer-side test still passed. The claim was correct and
# the evidence never arrived.
#
# So this test refuses to test the producer in isolation. It writes through the
# real writer into a file with the REAL canonical header and reads the row back
# with a plain CSV reader -- the same thing a consumer does.
#
# Bracketed both ways: a pre-migration 20-column header MUST lose the three
# fields (that is the regression, reproduced, so this test can fail), and the
# migrated header MUST carry all four.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
envelope="$(dirname "$here")"
repo_root="$(dirname "$envelope")"
RUN_MATRIX_DIR="${RUN_MATRIX_DIR:-$repo_root/worktrees/w7/hermit/tests/backend-parity}"

if [ ! -f "$RUN_MATRIX_DIR/run_matrix.py" ]; then
  echo "SKIP: run_matrix.py not found at $RUN_MATRIX_DIR (set RUN_MATRIX_DIR)" >&2
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
FAILURES=0
check() { # label, condition-rc
  if [ "$2" -eq 0 ]; then echo "  ok    $1"; else echo "  FAIL  $1"; FAILURES=$((FAILURES+1)); fi
}

LEGACY20="run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason,verify_compare"
CANONICAL="$(head -1 "$envelope/scorecard.csv")"

roundtrip() { # $1 = header line, $2 = output json path
  printf '%s\n' "$1" > "$tmp/sc.csv"
  python3 - "$tmp" "$RUN_MATRIX_DIR" "$2" <<'PY'
import sys, csv, json
sys.path.insert(0, sys.argv[2])
from pathlib import Path
import run_matrix as rm
rows = [{"test_name": "roundtrip_probe", "backend": "ptrace", "expectation": "bitwise",
         "result": "PASS", "seconds": "1.0", "detail": "probe",
         "evidence": {"verify_compare": "canonical", "bitwise_parity": "1",
                      "compared_log_messages": "348|348", "tier": "bitwise"}}]
rm.append_parent_scorecard(Path(sys.argv[1]) / "sc.csv", rows,
                           strict=True, verify=True, probe_gaps=False)
row = list(csv.DictReader(open(Path(sys.argv[1]) / "sc.csv")))[-1]
Path(sys.argv[3]).write_text(json.dumps(
    {k: row.get(k) for k in ("verify_compare", "bitwise_parity",
                             "compared_log_messages", "tier")}))
PY
}

echo "case LEGACY-20 — the pre-migration header MUST lose the evidence (regression reproduced)"
roundtrip "$LEGACY20" "$tmp/legacy.json" >/dev/null 2>&1
python3 -c "
import json,sys
d=json.load(open('$tmp/legacy.json'))
sys.exit(0 if d['verify_compare']=='canonical' and d['bitwise_parity'] is None
              and d['compared_log_messages'] is None and d['tier'] is None else 1)"
check "20-col header keeps verify_compare and drops the other three" $?

echo "case CANONICAL — the migrated live header MUST carry all four"
roundtrip "$CANONICAL" "$tmp/canon.json" >/dev/null 2>&1
python3 -c "
import json,sys
d=json.load(open('$tmp/canon.json'))
sys.exit(0 if d=={'verify_compare':'canonical','bitwise_parity':'1',
                  'compared_log_messages':'348|348','tier':'bitwise'} else 1)"
check "live canonical header round-trips all four fields" $?

echo "case SCHEMA — the canonical scorecard carries tier and provenance columns"
for col in verify_compare bitwise_parity compared_log_messages tier \
           ref_output_hash parity_comparator parity_tier profile_flags \
           population_id selected_count executed_count evidence_count; do
  printf '%s' "$CANONICAL" | tr ',' '\n' | grep -qx "$col"
  check "canonical header has $col" $?
done

echo "case MIGRATION — idempotent, and refuses a header it does not recognise"
python3 "$envelope/migrate-scorecard-schema.py" "$envelope/scorecard.csv" --apply >/dev/null 2>&1
check "re-running the migration is a no-op (rc=0)" $?
printf 'a,b,c\n1,2,3\n' > "$tmp/bogus.csv"
python3 "$envelope/migrate-scorecard-schema.py" "$tmp/bogus.csv" --apply >/dev/null 2>&1
[ $? -eq 2 ] ; check "a header with no verify_compare is REFUSED (rc=2)" $?

echo "case PRODUCER-WIDTH — every canonical column must be producible, and rows must not overflow"
# THE BUG: collect-envelope's own HEADER is wider than the canonical scorecard (it also
# records stdout_parity/parity_exercised/backend_engaged/native_output_hash/
# ref_output_hash/run_flags). It appended its own row shape regardless of the target
# file's header, so appending into the canonical 23-column scorecard wrote 28-field
# rows -- five values past the last column, which surface only as csv.DictReader's
# None key. Latent until the collector next runs, then silently corrupting.
CANON_COLS="$(printf '%s' "$CANONICAL" | tr ',' '\n')"
PROD_HEADER="$(grep -o 'run_id,run_utc[^"]*' "$envelope/collect-envelope.rs" | head -1)"
missing=0
for c in $CANON_COLS; do
  case "$c" in parity) want="stdout_parity";; *) want="$c";; esac
  printf '%s' "$PROD_HEADER" | tr ',' '\n' | grep -qx "$want" || { echo "     producer cannot fill canonical column: $c"; missing=$((missing+1)); }
done
[ "$missing" -eq 0 ]; check "collect-envelope can fill every canonical column (parity<-stdout_parity)" $?

grep -q 'target_header' "$envelope/collect-envelope.rs"
check "collect-envelope binds its write to the TARGET file header" $?
grep -q 'let line = row.iter().map(|f| csv_field(f)).collect::<Vec<_>>().join(",");' "$envelope/collect-envelope.rs"
[ $? -ne 0 ]; check "the unconditional own-shape join is gone (regression guard)" $?

# Demonstrate the failure mode this guards, so the test can fail:
printf '%s\n' "$CANONICAL" > "$tmp/overflow.csv"
python3 - "$tmp/overflow.csv" <<'PYX'
import csv, sys
hdr = open(sys.argv[1]).read().strip().split(",")
open(sys.argv[1], "a").write(",".join(["x"] * (len(hdr) + 5)) + "\n")
row = list(csv.DictReader(open(sys.argv[1])))[0]
sys.exit(0 if row.get(None) is not None and len(row[None]) == 5 else 1)
PYX
check "an over-wide append IS detectable as 5 overflow fields (fixture is live)" $?

echo "case REVERIE-PATH — the reverie scorecard must satisfy the same wired checker"
"$envelope/check-determinism-earned.sh" "$envelope/reverie-scorecard.csv" >/dev/null 2>&1
check "reverie-scorecard.csv passes the production checker" $?
"$envelope/check-scorecard-provenance.py" "$envelope/reverie-scorecard.csv" \
  --observable tool-count >/dev/null 2>&1
check "reverie-scorecard.csv passes the parity provenance verifier" $?
head -1 "$envelope/reverie-scorecard.csv" | tr ',' '\n' | grep -qx tier
check "reverie-scorecard.csv carries the tier column" $?

echo "case FAIL-CLOSED — a deterministic positive with a blank tier is refused"
python3 - "$tmp" "$envelope" <<'PYX'
import csv, sys
from pathlib import Path
out, env = Path(sys.argv[1]), Path(sys.argv[2])
base = list(csv.DictReader((env / "scorecard.csv").open()))
rows = [dict(r) for r in base]
n = 0
for r in rows:
    if r.get("deterministic") == "1" and n < 3:
        r["tier"] = ""; r["verify_compare"] = "unknown-thing"
        r["bitwise_parity"] = ""; r["compared_log_messages"] = ""; n += 1
with (out / "blank_tier.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(base[0].keys())); w.writeheader(); w.writerows(rows)
PYX
"$envelope/check-determinism-earned.sh" "$tmp/blank_tier.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "blank-tier deterministic=1 is REFUSED (legacy bypass removed)" $?

echo "case COLLECTOR-CONTRACT — a FRESH collector row must pass its own production verifier"
# THE BUG: collect-envelope emitted tier=stripped while recording no counts, but the
# wired verifier requires a count from anything claiming `stripped` (a log compare
# that cannot say how much it compared could have compared nothing). So the producer
# generated rows its own verifier refused -- green locally, red in production.
grep -q 'stripped-uncounted' "$envelope/collect-envelope.rs"
check "collect-envelope emits the uncounted tier, not bare stripped" $?
printf '%s\n' "$CANONICAL" > "$tmp/fresh.csv"
python3 - "$tmp/fresh.csv" <<'PYX'
import csv, sys
hdr = open(sys.argv[1]).read().strip().split(",")
row = {c: "" for c in hdr}
row.update(run_id="fresh", test_id="x/y", backend="ptrace", test_mode="verify",
           outcome="pass", deterministic="1", verify_compare="stripped",
           tier="stripped-uncounted", bitwise_parity="", compared_log_messages="")
with open(sys.argv[1], "a", newline="") as f:
    csv.DictWriter(f, fieldnames=hdr).writerow(row)
PYX
"$envelope/check-determinism-earned.sh" "$tmp/fresh.csv" >/dev/null 2>&1
check "a fresh stripped collector row is ACCEPTED by the wired verifier" $?

echo "case NON-POSITIVE-TIER — tier=gap cannot carry a determinism positive"
plant() { # $1 name, $2.. python kwargs
  python3 - "$tmp/$1.csv" "$envelope/scorecard.csv" "$2" <<'PYX'
import csv, sys, json
out, src, kw = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
base = list(csv.DictReader(open(src))); rows = [dict(r) for r in base]
for r in [x for x in rows if x.get("deterministic") == "1"][:3]:
    r.update(kw)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(base[0].keys())); w.writeheader(); w.writerows(rows)
PYX
}
plant gap '{"tier":"gap"}'
"$envelope/check-determinism-earned.sh" "$tmp/gap.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "deterministic=1 with tier=gap is REFUSED" $?

echo "case COMPARATOR-ALLOWLIST — an unknown policy is refused at every tier, not just bitwise"
plant unkcmp '{"tier":"stripped","verify_compare":"mystery","compared_log_messages":"9|9"}'
"$envelope/check-determinism-earned.sh" "$tmp/unkcmp.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "unknown comparator at tier=stripped is REFUSED" $?
plant unkcmp2 '{"tier":"stripped-uncounted","verify_compare":"mystery"}'
"$envelope/check-determinism-earned.sh" "$tmp/unkcmp2.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "unknown comparator at tier=stripped-uncounted is REFUSED" $?

echo
if [ "$FAILURES" -ne 0 ]; then echo "FAIL ($FAILURES assertions)"; exit 1; fi
echo "PASS"
