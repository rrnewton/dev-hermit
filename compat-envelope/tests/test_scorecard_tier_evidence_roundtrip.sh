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

echo
if [ "$FAILURES" -ne 0 ]; then echo "FAIL ($FAILURES assertions)"; exit 1; fi
echo "PASS"
