#!/usr/bin/env bash
# End-to-end binding: the scorecard cell must consume tier_evidence.py's verdict.
#
# The fixtures differ only in the known divergence measured in
# ignored/w16-green/mut_full.json: canonical INFO comparison, bitwise_parity=0,
# and compared counts 169|186. The cached cross-backend tier is deliberately left
# unchanged. Before the wiring both files rendered 2/2 green; after it the planted
# cell is non-green while the clean control remains green at its stated FULL tier.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RENDER=${1:-$ROOT/compat-envelope/render-scorecard.rs}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

python3 - "$TMP" <<'PY'
import csv
import hashlib
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
header = [
    "run_id", "run_utc", "hermit_sha", "reverie_sha", "dirty",
    "run_mode", "lane", "bucket", "test_id", "test_mode", "backend",
    "cell_state", "outcome", "deterministic", "stdout_parity", "output_hash",
    "duration_ms", "max_rss_kb", "reason", "verify_compare", "bitwise_parity",
    "compared_log_messages", "tier", "ref_output_hash", "parity_comparator",
    "parity_tier", "profile_flags", "population_id", "selected_count",
    "executed_count", "evidence_count", "comparison_tier", "stack_parity",
    "heap_parity",
]
tests = ("clean-control", "known-divergence")
keys = sorted(
    "\t".join(("regression", "portable", "short", test, "verify", "ptrace", "enabled"))
    for test in tests
)
digest = hashlib.sha256(b"scorecard-population-v2\n")
for key in keys:
    digest.update(key.encode())
    digest.update(b"\n")
population = "sha256:" + digest.hexdigest()

def row(test):
    return {
        "run_id": "tier-evidence-fixture",
        "run_utc": "@1786125600",
        "hermit_sha": "a" * 40,
        "reverie_sha": "b" * 40,
        "dirty": "false",
        "run_mode": "regression",
        "lane": "portable",
        "bucket": "short",
        "test_id": test,
        "test_mode": "verify",
        "backend": "ptrace",
        "cell_state": "enabled",
        "outcome": "pass",
        "deterministic": "1",
        "stdout_parity": "1",
        "output_hash": "d" * 64,
        "duration_ms": "1",
        "max_rss_kb": "",
        "reason": "",
        "verify_compare": "canonical",
        "bitwise_parity": "1",
        "compared_log_messages": "127|127",
        "tier": "bitwise",
        "ref_output_hash": "d" * 64,
        "parity_comparator": "stdout-sha256-exact-v1",
        "parity_tier": "stdout-exact",
        "profile_flags": json.dumps({"comparison": ["run", "--strict"]}),
        "population_id": population,
        "selected_count": "2",
        "executed_count": "2",
        "evidence_count": "2",
        "comparison_tier": "full-stdout-info-stack-heap",
        "stack_parity": "1",
        "heap_parity": "1",
    }

baseline = [row(test) for test in tests]
mutant = [dict(item) for item in baseline]
plant = mutant[1]
plant["deterministic"] = "0"
plant["bitwise_parity"] = "0"
plant["compared_log_messages"] = "169|186"
plant["tier"] = "gap"

for name, rows in (("baseline", baseline), ("mutant", mutant)):
    with (out / f"{name}.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
PY

"$RENDER" --csv "$TMP/baseline.csv" --all --json >"$TMP/baseline.json" 2>"$TMP/baseline.err"
"$RENDER" --csv "$TMP/mutant.csv" --all --json >"$TMP/mutant.json" 2>"$TMP/mutant.err"

jq -e '
  .raw_pass_count == 2 and
  .declared_tier_green_count == 2 and
  .qualified_green_count == 2 and
  .tier_evidence.claims == 2 and
  .tier_evidence.upheld == 2 and
  .tier_evidence.rejected == 0 and
  .rows[-1].ptrace_count == 2 and
  (.tier_evidence.cells | all(.tier == "full-stdout-info-stack-heap" and .evidenced))
' "$TMP/baseline.json" >/dev/null

jq -e '
  .raw_pass_count == 2 and
  .declared_tier_green_count == 2 and
  .qualified_green_count == 1 and
  .tier_evidence.claims == 2 and
  .tier_evidence.upheld == 1 and
  .tier_evidence.rejected == 1 and
  .rows[-1].ptrace_count == 1 and
  ([.tier_evidence.cells[] | select(.test_id == "known-divergence")][0] |
    .tier == "full-stdout-info-stack-heap" and (.evidenced | not))
' "$TMP/mutant.json" >/dev/null

grep -F 'old-definition declared-tier green=2/2 raw passes; new-definition evidence-qualified green=2/2 raw passes' "$TMP/baseline.err" >/dev/null
grep -F 'old-definition declared-tier green=2/2 raw passes; new-definition evidence-qualified green=1/2 raw passes' "$TMP/mutant.err" >/dev/null

set +e
python3 "$ROOT/compat-envelope/tier_evidence.py" --csv "$TMP/mutant.csv" --json >"$TMP/evidence.json"
evidence_rc=$?
set -e
[ "$evidence_rc" -eq 1 ]
jq -e '
  .claims == 2 and .upheld == 1 and
  ([.violations[] | select(.test_id == "known-divergence")][0].reasons |
    any(startswith("diverged:info_log")))
' "$TMP/evidence.json" >/dev/null

echo "tier evidence wiring: clean FULL cells 2/2 green; planted bitwise divergence 1/2 green; per-cell tier and evidence verdict persisted"
