#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
RENDER=${1:-$ROOT/compat-envelope/render-scorecard.rs}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

COMMON_PREFIX=run_id,run_utc,hermit_sha,reverie_sha,dirty,run_mode,lane,bucket,test_id,test_mode,backend,cell_state,outcome,deterministic
COMMON_SUFFIX=output_hash,duration_ms,max_rss_kb,reason

write_stdout_csv() {
    local path=$1 column=$2
    cat >"$path" <<EOF
$COMMON_PREFIX,$column,$COMMON_SUFFIX
r,@0,h,r,false,regression,portable,b,t,verify,ptrace,enabled,pass,1,1,hash,1,,reference
r,@0,h,r,false,regression,portable,b,t,verify,dbi,enabled,pass,1,1,hash,1,,match
EOF
}

write_tool_csv() {
    local path=$1 column=$2
    cat >"$path" <<EOF
$COMMON_PREFIX,$column,$COMMON_SUFFIX
r,@0,h,r,false,reverie,portable,b,t,counter,ptrace,enabled,pass,1,1,12,1,,reference
r,@0,h,r,false,reverie,portable,b,t,counter,kvm,enabled,pass,1,1,12,1,,match
EOF
}

write_stdout_csv "$TMP/stdout-current.csv" stdout_parity
write_stdout_csv "$TMP/stdout-legacy.csv" parity
write_tool_csv "$TMP/tool-current.csv" tool_count_parity
write_tool_csv "$TMP/tool-legacy.csv" parity

"$RENDER" --csv "$TMP/stdout-current.csv" --all --backends dbi --json >"$TMP/stdout-current.json"
"$RENDER" --csv "$TMP/stdout-legacy.csv" --all --backends dbi --json >"$TMP/stdout-legacy.json"
jq -S 'del(.source_csv)' "$TMP/stdout-current.json" >"$TMP/stdout-current.normalized.json"
jq -S 'del(.source_csv)' "$TMP/stdout-legacy.json" >"$TMP/stdout-legacy.normalized.json"
cmp "$TMP/stdout-current.normalized.json" "$TMP/stdout-legacy.normalized.json"
jq -e '.rows[-1].backends.dbi.stdout_parity_count == 1' "$TMP/stdout-current.json" >/dev/null

"$RENDER" --csv "$TMP/tool-current.csv" --all --denominator counter \
    --backends kvm --observable tool-count --json >"$TMP/tool-current.json"
"$RENDER" --csv "$TMP/tool-legacy.csv" --all --denominator counter \
    --backends kvm --observable tool-count --json >"$TMP/tool-legacy.json"
jq -S 'del(.source_csv)' "$TMP/tool-current.json" >"$TMP/tool-current.normalized.json"
jq -S 'del(.source_csv)' "$TMP/tool-legacy.json" >"$TMP/tool-legacy.normalized.json"
cmp "$TMP/tool-current.normalized.json" "$TMP/tool-legacy.normalized.json"
jq -e '.rows[-1].backends.kvm.tool_count_parity_count == 1' "$TMP/tool-current.json" >/dev/null

expect_refused() {
    local name=$1 needle=$2
    shift 2
    set +e
    "$RENDER" "$@" >"$TMP/$name.out" 2>&1
    local rc=$?
    set -e
    if [ "$rc" -ne 2 ]; then
        echo "$name: expected refusal exit 2, got $rc" >&2
        cat "$TMP/$name.out" >&2
        exit 1
    fi
    grep -F "$needle" "$TMP/$name.out" >/dev/null || {
        echo "$name: refusal did not name '$needle'" >&2
        cat "$TMP/$name.out" >&2
        exit 1
    }
}

# A current schema is bound to its declared observable, not merely to a boolean
# in the same ordinal position.
# Backticks are literal renderer diagnostics.
# shellcheck disable=SC2016
expect_refused stdout-as-tool 'found `stdout_parity`, expected `tool_count_parity`' \
    --csv "$TMP/stdout-current.csv" --observable tool-count
# Backticks are literal renderer diagnostics.
# shellcheck disable=SC2016
expect_refused tool-as-stdout 'found `tool_count_parity`, expected `stdout_parity`' \
    --csv "$TMP/tool-current.csv"

# A transition file carrying both spellings is ambiguous and must not silently
# prefer whichever column happens to appear first.
cat >"$TMP/ambiguous.csv" <<EOF
$COMMON_PREFIX,stdout_parity,parity,$COMMON_SUFFIX
r,@0,h,r,false,regression,portable,b,t,verify,ptrace,enabled,pass,1,1,0,hash,1,,conflict
EOF
# Backticks are literal renderer diagnostics.
# shellcheck disable=SC2016
expect_refused ambiguous 'ambiguous observable columns `stdout_parity` and legacy `parity`' \
    --csv "$TMP/ambiguous.csv"

cat >"$TMP/missing.csv" <<EOF
$COMMON_PREFIX,comparison,$COMMON_SUFFIX
r,@0,h,r,false,regression,portable,b,t,verify,ptrace,enabled,pass,1,1,hash,1,,missing
EOF
# Backticks are literal renderer diagnostics.
# shellcheck disable=SC2016
expect_refused missing 'missing observable column `stdout_parity`' --csv "$TMP/missing.csv"

echo "observable schema: 4 positive current/legacy reads; 4 mismatched/ambiguous/missing schemas refused"
