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
# NOTE: this default is an ephemeral SLOT path. A slot is parked and released by
# ordinary coordinator lifecycle, so this default WILL evaporate. It is kept only
# because the primary checkout's run_matrix.py still carries the narrower
# SCORECARD_HEADER and cannot round-trip the canonical scorecard (tracked
# separately as the 19-vs-canonical column skew). The fail-closed guard below is
# what makes the evaporation loud instead of silent.
RUN_MATRIX_DIR="${RUN_MATRIX_DIR:-$repo_root/worktrees/w7/hermit/tests/backend-parity}"

# FAIL CLOSED. Exiting 0 here reported "success" while running zero of the
# assertions below, which is indistinguishable at the consumer from a full pass
# -- the exact defect this file exists to catch, in this file's own harness.
# Set RUN_MATRIX_SKIP_OK=1 to opt into the old advisory behaviour deliberately.
if [ ! -f "$RUN_MATRIX_DIR/run_matrix.py" ]; then
  if [ "${RUN_MATRIX_SKIP_OK:-0}" = "1" ]; then
    echo "SKIP (explicitly allowed): run_matrix.py not found at $RUN_MATRIX_DIR" >&2
    exit 0
  fi
  echo "FAIL: run_matrix.py not found at $RUN_MATRIX_DIR -- 0 assertions ran." >&2
  echo "      Set RUN_MATRIX_DIR to a checkout's tests/backend-parity, or" >&2
  echo "      RUN_MATRIX_SKIP_OK=1 to accept an unverified run." >&2
  exit 2
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
# Check roundtrip's OWN exit code. run_matrix.append_parent_scorecard raises
# MatrixError on an incompatible header (the exit-2 path); discarding that rc
# surfaced it only as a confusing downstream "missing json" assertion failure
# that named the wrong cause.
roundtrip "$LEGACY20" "$tmp/legacy.json" >/dev/null 2>&1
check "LEGACY-20 roundtrip through run_matrix completed (exit propagated)" $?
python3 -c "
import json,sys
d=json.load(open('$tmp/legacy.json'))
sys.exit(0 if d['verify_compare']=='canonical' and d['bitwise_parity'] is None
              and d['compared_log_messages'] is None and d['tier'] is None else 1)"
check "20-col header keeps verify_compare and drops the other three" $?

echo "case CANONICAL — the migrated live header MUST carry all four"
roundtrip "$CANONICAL" "$tmp/canon.json" >/dev/null 2>&1
check "CANONICAL roundtrip through run_matrix completed (exit propagated)" $?
python3 -c "
import json,sys
d=json.load(open('$tmp/canon.json'))
sys.exit(0 if d=={'verify_compare':'canonical','bitwise_parity':'1',
                  'compared_log_messages':'348|348','tier':'bitwise'} else 1)"
check "live canonical header round-trips all four fields" $?

echo "case SCHEMA — the canonical scorecard carries the evidence columns"
for col in verify_compare bitwise_parity compared_log_messages tier; do
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

echo
if [ "$FAILURES" -ne 0 ]; then echo "FAIL ($FAILURES assertions)"; exit 1; fi
echo "PASS"
