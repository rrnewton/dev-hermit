#!/usr/bin/env bash
# Bracket the load-bearing parity provenance verifier both ways.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
envelope="$(dirname "$here")"
checker="$envelope/check-scorecard-provenance.py"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failures=0

check() {
  if [ "$2" -eq 0 ]; then
    echo "  ok    $1"
  else
    echo "  FAIL  $1"
    failures=$((failures + 1))
  fi
}

python3 - "$tmp/good.csv" <<'PY'
import csv, hashlib, json, sys

path = sys.argv[1]
header = [
    "run_id", "run_utc", "hermit_sha", "reverie_sha", "dirty", "run_mode",
    "lane", "bucket", "test_id", "test_mode", "backend", "cell_state",
    "outcome", "deterministic", "stdout_parity", "output_hash", "duration_ms",
    "max_rss_kb", "reason", "verify_compare", "bitwise_parity",
    "compared_log_messages", "tier", "legacy_parity_unqualified",
    "ref_output_hash", "parity_comparator", "parity_tier", "profile_flags",
    "population_id", "selected_count", "executed_count", "evidence_count",
]
base = {column: "" for column in header}
base.update(
    run_id="fixture-run", run_utc="@1", hermit_sha="a" * 40,
    reverie_sha="b" * 40, dirty="false", run_mode="regression", lane="portable",
    bucket="c-programs", test_id="c-programs/provenance", test_mode="verify",
    cell_state="enabled", outcome="pass", deterministic="1",
    duration_ms="1", verify_compare="stripped", tier="stripped-uncounted",
    profile_flags=json.dumps({"comparison": ["run", "--backend", "dbi", "--strict", "--"]}, separators=(",", ":")),
    selected_count="2", executed_count="2",
    evidence_count="1",
)
ptrace = dict(base, backend="ptrace", stdout_parity="", output_hash="d" * 64)
candidate = dict(
    base, backend="dbi", stdout_parity="1", output_hash="d" * 64,
    ref_output_hash="d" * 64, parity_comparator="stdout-sha256-exact-v1",
    parity_tier="stdout-exact",
)
rows = [ptrace, candidate]
keys = sorted("\t".join(row[field] for field in (
    "run_mode", "lane", "bucket", "test_id", "test_mode", "backend", "cell_state"
)) for row in rows)
population = hashlib.sha256(("scorecard-population-v2\n" + "\n".join(keys) + "\n").encode()).hexdigest()
for row in rows:
    row["population_id"] = "sha256:" + population
with open(path, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=header)
    writer.writeheader()
    writer.writerows(rows)
PY

echo "case POSITIVE — one exact, counted row must re-derive"
out="$($checker "$tmp/good.csv" 2>&1)"
rc=$?
[ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q 'claims=1 rederived=1'
check "well-shaped row accepted and re-derived (rows=2 selected=2 executed=2 evidence=1)" $?

plant() {
  python3 - "$tmp/good.csv" "$tmp/$1.csv" "$2" <<'PY'
import csv, json, sys
src, out, mutation = sys.argv[1:]
rows = list(csv.DictReader(open(src)))
rows[-1].update(json.loads(mutation))
with open(out, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
PY
}

echo "case NEGATIVE — a missing reference operand is refused"
plant missing-ref '{"ref_output_hash":""}'
"$checker" "$tmp/missing-ref.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "missing ref_output_hash refused" $?

echo "case NEGATIVE — a tampered reference re-derives the opposite verdict"
plant tampered-ref "{\"ref_output_hash\":\"$(printf 'e%.0s' {1..64})\"}"
"$checker" "$tmp/tampered-ref.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "tampered reference hash refused" $?

echo "case NEGATIVE — unknown Reverie state is not exact provenance"
plant unknown-reverie '{"reverie_sha":"unknown"}'
"$checker" "$tmp/unknown-reverie.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "unknown Reverie SHA refused" $?

echo "case NEGATIVE — aggregate cannot silently pool run identities"
python3 - "$tmp/good.csv" "$tmp/mixed.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
other = [dict(row, run_id="fixture-run-2", run_utc="@2") for row in rows]
with open(sys.argv[2], "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows + other)
PY
"$checker" "$tmp/mixed.csv" --aggregate >/dev/null 2>&1
[ $? -eq 1 ]; check "mixed-run aggregate refused" $?

echo "case NEGATIVE — one run identity cannot pool two code states"
plant mixed-code-state '{"hermit_sha":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'
"$checker" "$tmp/mixed-code-state.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "same-run mixed Hermit SHAs refused" $?

echo "case NEGATIVE — population receipt must bind the selected row identities"
plant tampered-population '{"population_id":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}'
python3 - "$tmp/tampered-population.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
for row in rows:
    row["population_id"] = "sha256:" + "e" * 64
with open(sys.argv[1], "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
PY
"$checker" "$tmp/tampered-population.csv" >/dev/null 2>&1
[ $? -eq 1 ]; check "tampered population identity refused" $?

echo "case MIGRATION — legacy booleans survive only as explicitly unqualified data"
cp "$tmp/good.csv" "$tmp/legacy.csv"
python3 - "$tmp/legacy.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
rows[-1]["ref_output_hash"] = ""
rows[-1]["parity_comparator"] = ""
rows[-1]["parity_tier"] = ""
rows[-1]["profile_flags"] = ""
rows[-1]["population_id"] = ""
rows[-1]["selected_count"] = ""
rows[-1]["executed_count"] = ""
rows[-1]["evidence_count"] = ""
with open(sys.argv[1], "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
PY
python3 "$envelope/migrate-scorecard-schema.py" "$tmp/legacy.csv" --apply >/dev/null 2>&1
python3 - "$tmp/legacy.csv" <<'PY'
import csv, sys
row = list(csv.DictReader(open(sys.argv[1])))[-1]
sys.exit(0 if row["stdout_parity"] == "" and
                 row["legacy_parity_unqualified"] == "stdout_parity:1" else 1)
PY
check "unbound legacy verdict preserved but removed from qualified parity" $?

echo
if [ "$failures" -ne 0 ]; then
  echo "FAIL ($failures assertions)"
  exit 1
fi
echo "PASS (8 assertions; positive=1, malformed/tampered/mixed negatives=6, migration=1)"
