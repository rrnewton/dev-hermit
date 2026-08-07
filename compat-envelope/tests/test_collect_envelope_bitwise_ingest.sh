#!/usr/bin/env bash
# Bracket the typed Hermit-harness verdict at the scorecard ingestion boundary.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
envelope="$(dirname "$here")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
repo="$tmp/hermit"
mkdir -p "$repo/ci"

cat >"$repo/Cargo.lock" <<'EOF'
source = "git+https://github.com/rrnewton/reverie.git?rev=0123456789012345678901234567890123456789#0123456789012345678901234567890123456789"
EOF
cat >"$repo/ci/test_harness.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  plan)
    printf '%s\n' '[{"category":"fixture","test":"fixture/probe","mode":"verify","backend":"ptrace"}]'
    ;;
  audit-gaps)
    printf '%s\n' '[]'
    ;;
  run)
    shift
    results=
    while (($#)); do
      if [[ $1 == --results ]]; then results=$2; shift 2; else shift; fi
    done
    case "${HARNESS_CASE:?}" in
      positive)
        verification='{"verified":true,"bitwise_parity":true,"verdict":"matched","comparison":{"strictness":"canonical","compare_logs":true},"compared_log_messages":{"left":9,"right":9}}'
        outcome=PASS
        ;;
      mismatch)
        verification='{"verified":false,"bitwise_parity":false,"verdict":"diverged","comparison":{"strictness":"canonical","compare_logs":true},"compared_log_messages":{"left":9,"right":9}}'
        outcome=FAIL
        ;;
      absent)
        verification=null
        outcome=PASS
        ;;
      zero-count-claim)
        verification='{"verified":true,"bitwise_parity":true,"verdict":"matched","comparison":{"strictness":"canonical","compare_logs":true},"compared_log_messages":{"left":0,"right":0}}'
        outcome=PASS
        ;;
    esac
    jq -cn --arg outcome "$outcome" --argjson verification "$verification" \
      '{test:"fixture/probe",mode:"verify",outcome:$outcome,duration_ms:1,
        reason:null,verification:$verification}' >"$results"
    ;;
  *) exit 2 ;;
esac
EOF
chmod +x "$repo/ci/test_harness.sh"
git -C "$repo" init -q
git -C "$repo" add Cargo.lock ci/test_harness.sh
git -C "$repo" -c user.name=test -c user.email=test@example.invalid commit -qm fixture

header="$(head -1 "$envelope/scorecard.csv")"

run_case() {
  local name=$1 csv="$tmp/$1.csv"
  printf '%s\n' "$header" >"$csv"
  HARNESS_CASE="$name" "$envelope/collect-envelope.rs" \
    --mode regression --lane portable --repo "$repo" --csv "$csv" --run-id "$name"
  printf '%s\n' "$csv"
}

positive=$(run_case positive)
python3 - "$positive" <<'PY'
import csv, sys
r = list(csv.DictReader(open(sys.argv[1])))[0]
assert (r["deterministic"], r["verify_compare"], r["bitwise_parity"],
        r["compared_log_messages"], r["tier"]) == ("1", "canonical", "1", "9|9", "bitwise")
flags = __import__("json").loads(r["profile_flags"])["comparison"]
assert "--verify" in flags and "--verify-strict" in flags
assert "--verify-json=<per-cell-verdict.json>" in flags
PY

mismatch=$(run_case mismatch)
python3 - "$mismatch" <<'PY'
import csv, sys
r = list(csv.DictReader(open(sys.argv[1])))[0]
assert r["deterministic"] == "0"
assert (r["verify_compare"], r["bitwise_parity"],
        r["compared_log_messages"], r["tier"]) == ("canonical", "0", "9|9", "gap")
PY
# Plant the exact overclaim a consumer must refuse: preserve the observed
# mismatch but relabel it as a deterministic bitwise positive.
python3 - "$mismatch" "$tmp/planted-mismatch.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
rows[0]["deterministic"] = "1"
rows[0]["tier"] = "bitwise"
with open(sys.argv[2], "w", newline="") as out:
    writer = csv.DictWriter(out, fieldnames=rows[0].keys())
    writer.writeheader(); writer.writerows(rows)
PY
set +e
"$envelope/check-determinism-earned.sh" "$tmp/planted-mismatch.csv" \
  >"$tmp/planted.out" 2>"$tmp/planted.err"
planted_rc=$?
set -e
[[ $planted_rc -eq 1 ]]
grep -F "bitwise_parity='0', must be '1'" "$tmp/planted.err" >/dev/null

absent=$(run_case absent)
python3 - "$absent" <<'PY'
import csv, sys
r = list(csv.DictReader(open(sys.argv[1])))[0]
assert (r["deterministic"], r["verify_compare"], r["bitwise_parity"],
        r["compared_log_messages"], r["tier"]) == ("1", "stripped", "", "", "stripped-uncounted")
PY

zero_csv="$tmp/zero-count-claim.csv"
printf '%s\n' "$header" >"$zero_csv"
set +e
HARNESS_CASE=zero-count-claim "$envelope/collect-envelope.rs" \
  --mode regression --lane portable --repo "$repo" --csv "$zero_csv" \
  --run-id zero-count-claim >"$tmp/zero.out" 2>"$tmp/zero.err"
rc=$?
set -e
[[ $rc -eq 2 ]]
grep -F 'bitwise_parity=true without verified canonical nonzero evidence' "$tmp/zero.err" >/dev/null
[[ $(wc -l <"$zero_csv") -eq 1 ]]

echo "bitwise ingest: positive, mismatch, absent fallback, and zero-count refusal passed"
