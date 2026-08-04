#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
helper=$script_dir/local-validation-eligibility.sh
lander=$script_dir/land-pr.sh
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
ledger=$tmp/ledger.jsonl

valid=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
missing=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
printf '%s\n' \
  "{\"schema_version\":3,\"commit\":\"$valid\",\"commit_anchored\":true,\"tree_dirty\":false,\"profile\":\"full\",\"selection_mode\":\"full\",\"result\":\"pass\",\"finished_at\":\"2026-08-04T00:00:00Z\",\"host\":\"fixture\",\"real_seconds\":1}" \
  >"$ledger"

run_case() {
  local expected_rc=$1 sha=$2 labels=$3 output rc
  set +e
  output=$(CI_HUB_VALIDATE_LEDGER="$ledger" "$helper" "$sha" "$labels" 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne "$expected_rc" ]; then
    printf 'expected rc=%s, got rc=%s\n%s\n' "$expected_rc" "$rc" "$output" >&2
    exit 1
  fi
  grep '^ELIGIBILITY=' <<<"$output" | tail -1
}

# The same unbacked exact head is rejected with or without the cache label.
missing_without=$(run_case 4 "$missing" "")
missing_with=$(run_case 4 "$missing" "locally-validated")
[ "$missing_without" = "ELIGIBILITY=NOT_VALIDATED" ]
[ "$missing_with" = "$missing_without" ]

# The same ledger-backed exact head is admitted with or without the cache label.
valid_without=$(run_case 0 "$valid" "")
valid_with=$(run_case 0 "$valid" "locally-validated")
[ "$valid_without" = "ELIGIBILITY=VALIDATED" ]
[ "$valid_with" = "$valid_without" ]

# The production consumer must use the helper and must not type the label via gh.
grep -Fq 'local-validation-eligibility.sh' "$lander"
if grep -Eq 'gh pr edit .*--add-label locally-validated' "$lander"; then
  echo "land-pr.sh still directly types locally-validated" >&2
  exit 1
fi

printf 'PASS: unbacked label rejected 2/2; validated head admitted 2/2; lander uses ledger authority\n'
