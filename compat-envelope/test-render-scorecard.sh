#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
RENDER="$ROOT/compat-envelope/render-scorecard.rs"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

SHA=82a8e853357584a3a567fd80812e015572a607c7
TEST=c-programs/meminfo-free-deterministic
SOURCE_SHA=1632498bb1ef22d76a939941b81e1fd0ef3f8b742ee24ea6bb2f3923d4f4488e
mkdir -p "$TMP/compat"
cp "$ROOT/experiments/parity-scorecard-vacuity-audit_20260804/meminfo-host-negative-control.tsv" "$TMP/negative.tsv"
NEGATIVE_SHA=$(sha256sum "$TMP/negative.tsv" | cut -d' ' -f1)

cat >"$TMP/compat/absolute-oracles.csv" <<EOF
oracle_id,test_id,source_path,source_sha256,negative_control_path,negative_control_sha256
meminfo-free-v1,$TEST,tests/c/meminfo_free_deterministic.c,$SOURCE_SHA,negative.tsv,$NEGATIVE_SHA
EOF

cat >"$TMP/evidence.json" <<EOF
{
  "schema": "cross-backend-bitwise-v1",
  "hermit_sha": "$SHA",
  "test_id": "$TEST",
  "backend": "dbi",
  "reference_backend": "ptrace",
  "comparison_contract": "BitwiseInfoV1",
  "bitwise_result": true,
  "reference_log_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "backend_log_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "comparison": {
    "log_scope": "full-info",
    "stripped_prefixes": ["real-wall-clock-prefix/v1"],
    "canonicalizations": ["host-address-to-first-appearance-ordinal/v1"],
    "exact_remainder": true,
    "ignore_lines": [],
    "skip_commit": false,
    "skip_detlog": false,
    "compare_logs": true,
    "output_only_fallback": false
  }
}
EOF

write_csv() {
  local evidence_path=$1 evidence_sha=$2
  cat >"$TMP/score.csv" <<EOF
run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason,comparison_contract,bitwise_result,bitwise_evidence_path,bitwise_evidence_sha256
fixture,@0,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,ptrace,enabled,pass,1,1,hash,1,,,,,,,
fixture,@0,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,dbi,enabled,pass,1,1,hash,1,,,BitwiseInfoV1,1,$evidence_path,$evidence_sha
EOF
}

render() {
  XDG_CACHE_HOME="${XDG_CACHE_HOME:-$TMP/cache}" "$RENDER" \
    --csv "$TMP/score.csv" \
    --oracle-registry "$TMP/compat/absolute-oracles.csv" \
    --repo "$ROOT/hermit" --all --backends dbi --json
}

render_latest() {
  XDG_CACHE_HOME="${XDG_CACHE_HOME:-$TMP/cache}" "$RENDER" \
    --csv "$TMP/score.csv" \
    --oracle-registry "$TMP/compat/absolute-oracles.csv" \
    --repo "$ROOT/hermit" --latest --backends dbi --json
}

# Positive absolute control, paired with a deliberately fabricated bitwise
# tuple. The absolute oracle must qualify; the shape-only bitwise claim must not.
EVIDENCE_SHA=$(sha256sum "$TMP/evidence.json" | cut -d' ' -f1)
write_csv evidence.json "$EVIDENCE_SHA"
render | jq -e '
  .rows[-1].backends.dbi.bitwise_comparison_count == 0 and
  .rows[-1].backends.dbi.absolute_assertion_count == 1 and
  .rows[-1].backends.dbi.high_confidence_pass_count == 0
' >/dev/null

# A plausible tuple naming a nonexistent artifact also remains unqualified.
write_csv nonexistent.json aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
render | jq -e '
  .rows[-1].backends.dbi.bitwise_comparison_count == 0 and
  .rows[-1].backends.dbi.absolute_assertion_count == 1 and
  .rows[-1].backends.dbi.high_confidence_pass_count == 0
' >/dev/null

# Negative absolute control: changing the planted host failure invalidates it.
write_csv evidence.json "$EVIDENCE_SHA"
printf '\ntampered\n' >>"$TMP/negative.tsv"
render | jq -e '
  .rows[-1].backends.dbi.bitwise_comparison_count == 0 and
  .rows[-1].backends.dbi.absolute_assertion_count == 0 and
  .rows[-1].backends.dbi.high_confidence_pass_count == 0
' >/dev/null

# Negative absolute control: a well-shaped but wrong source hash is refused.
cp "$ROOT/experiments/parity-scorecard-vacuity-audit_20260804/meminfo-host-negative-control.tsv" "$TMP/negative.tsv"
sed -i "s/$SOURCE_SHA/0000000000000000000000000000000000000000000000000000000000000000/" \
  "$TMP/compat/absolute-oracles.csv"
render | jq -e '.rows[-1].backends.dbi.absolute_assertion_count == 0' >/dev/null

# Append order is not chronology: a delayed older run must not mask the newer
# pass for either the aggregate or latest-run view.
cat >"$TMP/score.csv" <<EOF
run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic,parity,output_hash,duration_ms,max_rss_kb,reason
new,@200,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,ptrace,enabled,pass,1,1,hash,1,,
new,@200,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,dbi,enabled,pass,1,1,hash,1,,
old,@100,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,ptrace,enabled,pass,1,1,hash,1,,
old,@100,$SHA,unknown,false,regression,portable,c-programs,$TEST,verify,dbi,enabled,fail,0,0,hash,1,,
EOF
render | jq -e '.rows[-1].backends.dbi.stdout_equality_count == 1' >/dev/null
render_latest | jq -e '
  .run_scope == "new" and
  .rows[-1].backends.dbi.stdout_equality_count == 1
' >/dev/null

echo "render-scorecard evidence controls: 1 positive accepted; 4 shape/tamper negatives refused; 2 stale-append controls refused"
