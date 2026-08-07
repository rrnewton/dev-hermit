#!/usr/bin/env bash
# Exercise the SAME tier-evidence authority the renderer consumes.
#
# This file formerly re-derived a weaker verdict: FULL required only nonblank
# stack_hash + heap_hash, so the six live rows printed OK despite blank stdout
# and no stack/heap parity verdict columns. A shared name is not a shared
# implementation. The Python unit suite brackets each component and this shell
# test binds that authority to the published scorecards and the one-time drop.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CHECKER="$ROOT/compat-envelope/tier_evidence.py"
UNIT="$ROOT/compat-envelope/test_tier_evidence.py"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

python3 "$UNIT" >/dev/null

set +e
python3 "$CHECKER" --root "$ROOT/compat-envelope" --json >"$TMP/live.json"
live_rc=$?
set -e
[ "$live_rc" -eq 1 ] || {
    echo "expected the recorded one-time live drop (rc=1), got rc=$live_rc" >&2
    cat "$TMP/live.json" >&2
    exit 1
}

jq -e '
  .rows >= 2290 and .claims == 6 and .upheld == 0 and
  (.violations | length) == 6 and
  (.violations | all(
    .tier == "full-stdout-info-stack-heap" and
    (.reasons | any(startswith("missing:stdout"))) and
    (.reasons | any(startswith("schema-cannot-express:stack"))) and
    (.reasons | any(startswith("schema-cannot-express:heap")))
  ))
' "$TMP/live.json" >/dev/null

echo "tier evidence authority: unit positives/negatives pass; live old-definition claims=6, evidence-qualified=0"
