#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
helper=$script_dir/local-validation-eligibility.sh
lander=$script_dir/land-pr.sh
hub=$script_dir/../ci-hub
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
ledger=$tmp/ledger.jsonl
pin=dddddddddddddddddddddddddddddddddddddddd
repo=$tmp/hermit
mkdir -p "$repo" "$tmp/bin"
git -C "$repo" init -q
git -C "$repo" config user.email ci-hub@example.invalid
git -C "$repo" config user.name 'ci-hub test'
printf '[package]\nname="eligibility-fixture"\nversion="0.1.0"\n[dependencies]\nreverie={git="https://github.com/rrnewton/reverie",rev="%s"}\n' \
  "$pin" >"$repo/Cargo.toml"
git -C "$repo" add Cargo.toml
git -C "$repo" commit -q -m fixture
valid=$(git -C "$repo" rev-parse HEAD)
printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ $1 == git && $2 == ls-remote ]]; then' \
  '  printf "%s\trefs/heads/main\\n" dddddddddddddddddddddddddddddddddddddddd' \
  '  exit 0' \
  'fi' \
  'exec "$@"' >"$tmp/bin/with-proxy"
chmod +x "$tmp/bin/with-proxy"
export PATH="$tmp/bin:$PATH"

missing=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
printf '%s\n' \
  "{\"schema_version\":6,\"commit\":\"$valid\",\"commit_anchored\":true,\"tree_dirty\":false,\"profile\":\"full\",\"selection_mode\":\"full\",\"result\":\"pass\",\"executed_tests\":42,\"filtered_tests\":0,\"coverage\":{\"planned_test_nodes\":1,\"executed_test_nodes\":1,\"zero_executed_nodes\":[],\"absent_nodes\":[]},\"reverie_binding\":{\"repository\":\"rrnewton/reverie\",\"ref\":\"refs/heads/main\",\"pinned_sha\":\"$pin\",\"resolved_sha\":\"$pin\"},\"finished_at\":\"2026-08-04T00:00:00Z\",\"host\":\"fixture\",\"real_seconds\":1}" \
  >"$ledger"

run_case() {
  local expected_rc=$1 sha=$2 labels=$3 output rc
  set +e
  output=$(CI_HUB_VALIDATE_LEDGER="$ledger" "$helper" "$sha" "$labels" "$repo" 2>&1)
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

# Pure green-source OR: neither source is privileged. These are inert typed
# values, not labels/checks/comments capable of authorizing a live PR.
"$hub" green-source-decision --local passed --hosted no-result >/dev/null
"$hub" green-source-decision --local no-result --hosted passed >/dev/null
set +e
"$hub" green-source-decision --local no-result --hosted no-result >/dev/null
no_result_rc=$?
"$hub" green-source-decision --local passed --hosted failed >/dev/null
failed_rc=$?
set -e
[ "$no_result_rc" -eq 4 ]
[ "$failed_rc" -eq 3 ]

# A hosted green remains only one conjunct of landing eligibility. Plant a
# stale semantic pin in this isolated repository; the pure hosted leg passes,
# while the independent fresh dependency authority refuses it. No live status,
# label, comment, or merge authorization is created by this fixture.
stale_pin=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
printf '[package]\nname="eligibility-fixture"\nversion="0.1.0"\n[dependencies]\nreverie={git="https://github.com/rrnewton/reverie",rev="%s"}\n# https://github.com/rrnewton/reverie.git rev="%s"\n' \
  "$stale_pin" "$pin" >"$repo/Cargo.toml"
git -C "$repo" add Cargo.toml
git -C "$repo" commit -q -m stale-pin-fixture
stale=$(git -C "$repo" rev-parse HEAD)
"$hub" green-source-decision --local no-result --hosted passed >/dev/null
set +e
"$hub" reverie-pin-status --hermit-repo "$repo" --sha "$stale" >/dev/null 2>&1
stale_pin_rc=$?
set -e
[ "$stale_pin_rc" -eq 4 ]

# The production consumer must use the helper and must not type the label via gh.
grep -Fq 'local-validation-eligibility.sh' "$lander"
if grep -Eq 'gh pr edit .*--add-label locally-validated' "$lander"; then
  echo "land-pr.sh still directly types locally-validated" >&2
  exit 1
fi

# The legacy server-side replay cannot atomically condition on target main's
# base SHA. Help/docs parsing remains usable, but every mutating invocation is
# refused before a network request or checkout.
set +e
legacy_output=$("$lander" 999 fixture --foreground 2>&1)
legacy_rc=$?
set -e
[ "$legacy_rc" -eq 4 ]
grep -Fq 'use safe-exact-head-land' <<<"$legacy_output"

printf 'PASS: unbacked label rejected 2/2; validated head admitted 2/2; local/hosted OR positive 2/2 and no-result/failure negative 2/2; hosted-green stale-pin refused 1/1; legacy lander fail-closed\n'
