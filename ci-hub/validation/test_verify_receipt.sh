#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Overrides let provisioning tests drive this complete semantic suite through
# the exact verifier tree they materialized. The default remains the in-tree
# authority used by normal parent validation.
verifier=${VERIFY_RECEIPT:-$script_dir/verify_receipt.sh}
publisher=$script_dir/publish_receipt.py
receipt_digest=${RECEIPT_DIGEST:-$script_dir/../ci-hub}
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

[[ -x $verifier ]] || {
    printf 'FAIL: verifier under test is not executable: %s\n' "$verifier" >&2
    exit 1
}
[[ -x $receipt_digest ]] || {
    printf 'FAIL: receipt-digest authority under test is not executable: %s\n' \
        "$receipt_digest" >&2
    exit 1
}

sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
receipt_commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p "$tmp/receipts/$receipt_commit"

make_receipt() {
    local executed=$1 output=$2
    local raw=$tmp/receipt-build.json selected
    jq -cnS --arg sha "$sha" --argjson executed "$executed" '{
      schema_version: 1,
      repository: "rrnewton/hermit",
      commit: $sha,
      run_id: ($sha + "@2026-08-04T12:00:00Z@fixture-host"),
      source_log_file: "/tmp/validate.log",
      durable_log_file: "/durable/validate.log",
      log_sha256: ("c" * 64),
      selected_receipt_identity: {
        digest_algorithm: "sha256",
        canonicalization: "serde_json::to_vec(HistoryRow)-v1",
        digest: "pending"
      },
      ledger_record: {
        schema_version: 4,
        started_at: "2026-08-04T12:00:00Z",
        finished_at: "2026-08-04T12:01:00Z",
        host: "fixture-host",
        slot: "fixture-slot",
        repo: "hermit",
        commit: $sha,
        tree: ("e" * 40),
        profile: "full",
        selection_mode: "full",
        commit_anchored: true,
        tree_dirty: false,
        result: "pass",
        raw_result: "pass",
        exit_code: 0,
        checks: 2,
        gates_run: 2,
        gates_expected: 2,
        gates: [
          {name: "fmt", result: "pass", exit_code: 0},
          {name: "test", result: "pass", exit_code: 0}
        ],
        failures: 0,
        executed_tests: $executed,
        filtered_tests: 0,
        log_file: "/tmp/validate.log"
      }
    }' >"$raw"
    selected=$(jq -c '.ledger_record' "$raw" | \
        "$receipt_digest" receipt-digest --sha "$sha")
    jq --arg selected "$selected" \
        '.selected_receipt_identity.digest = $selected' "$raw" >"$output"
}

refresh_selected_identity() {
    local input=$1 output=$2 selected
    selected=$(jq -c '.ledger_record' "$input" | \
        "$receipt_digest" receipt-digest --sha "$sha")
    jq --arg selected "$selected" \
        '.selected_receipt_identity.digest = $selected' "$input" >"$output"
}

write_comments() {
    local path=$1 digest=$2 role_tag=${3:-'[impl agent, ci-hub]'}
    jq -cn --arg commit "$receipt_commit" --arg path "$path" \
        --arg digest "$digest" --arg role_tag "$role_tag" '{
      user: {login: "rrnewton"},
      body: ($role_tag + "\n\n<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + $digest + " -->")
    } | [[.]]' >"$tmp/comments.json"
}

verify_file() {
    local file=$1 expected=$2 label=$3 role_tag=${4:-'[impl agent, ci-hub]'}
    local file_digest file_path status=0
    file_digest=$(sha256sum "$file" | awk '{print $1}')
    file_path="validation-receipts/rrnewton/hermit/$sha/$file_digest.json"
    mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$file_path")"
    cp "$file" "$tmp/receipts/$receipt_commit/$file_path"
    write_comments "$file_path" "$file_digest" "$role_tag"
    "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1 || status=$?
    if [[ $expected == pass && $status != 0 ]] || [[ $expected == fail && $status == 0 ]]; then
        printf 'FAIL: %s expected %s, verifier exit=%s\n' "$label" "$expected" "$status" >&2
        exit 1
    fi
}

# Missing but perfectly shaped: this is the negative #1578 omitted.
forged_digest=$(printf 'd%.0s' {1..64})
forged_path="validation-receipts/rrnewton/hermit/$sha/$forged_digest.json"
write_comments "$forged_path" "$forged_digest"
if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: well-shaped nonexistent receipt was accepted" >&2
    exit 1
fi

# One legitimate counted receipt is admitted.
make_receipt 12 "$tmp/receipt.json"
digest=$(sha256sum "$tmp/receipt.json" | awk '{print $1}')
path="validation-receipts/rrnewton/hermit/$sha/$digest.json"
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$path")"
cp "$tmp/receipt.json" "$tmp/receipts/$receipt_commit/$path"
write_comments "$path" "$digest"
"$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null

# The wrapper's digest-addressed path is not permission to forge the selected
# row identity. Change only that inner digest; verify_file recomputes the outer
# artifact digest/path, and the final verifier must still refuse it.
jq '.selected_receipt_identity.digest = ("f" * 64)' \
    "$tmp/receipt.json" >"$tmp/tampered-selected-digest.json"
verify_file "$tmp/tampered-selected-digest.json" fail \
    "tampered selected digest with recomputed outer artifact identity" \
    '[coordinator, gpt-5.6-sol]'

# Only the explicitly documented historical service-actor tag may consume an
# older artifact that predates the canonical selected-row identity.
jq '
  del(.selected_receipt_identity)
  | .ledger_record.schema_version = 1
  | del(
      .ledger_record.slot,
      .ledger_record.repo,
      .ledger_record.tree,
      .ledger_record.raw_result,
      .ledger_record.exit_code,
      .ledger_record.gates_run,
      .ledger_record.gates_expected,
      .ledger_record.gates
    )
' "$tmp/receipt.json" >"$tmp/historical.json"
verify_file "$tmp/historical.json" pass "legacy service artifact without selected identity"
verify_file "$tmp/historical.json" fail "current role artifact without selected identity" \
    '[coordinator, gpt-5.6-sol]'
jq '.selected_receipt_identity = false' "$tmp/receipt.json" >"$tmp/malformed-identity.json"
verify_file "$tmp/malformed-identity.json" fail "legacy artifact with malformed selected identity"
jq '
  .ledger_record.checks = 0
  | .ledger_record.gates_run = 0
  | .ledger_record.gates = []
' "$tmp/receipt.json" >"$tmp/current-weak-row-raw.json"
refresh_selected_identity "$tmp/current-weak-row-raw.json" "$tmp/current-weak-row.json"
verify_file "$tmp/current-weak-row.json" fail "current role artifact with weak selected row" \
    '[coordinator, gpt-5.6-sol]'

# Preserve the historical automated service tag and accept each current
# AGENTS.md role-tag form. Tags outside those exact forms remain inert.
valid_role_tags=(
    '[impl agent, ci-hub]'
    '[impl agent, gpt-5.6-sol]'
    '[adversarial-reviewer agent, gpt-5.6-sol]'
    '[coordinator, gpt-5.6-sol]'
    '[Human]'
)
for role_tag in "${valid_role_tags[@]}"; do
    write_comments "$path" "$digest" "$role_tag"
    if ! "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
        printf 'FAIL: valid receipt role tag was refused: %s\n' "$role_tag" >&2
        exit 1
    fi
done

invalid_role_tags=(
    '[assistant, gpt-5.6-sol]'
    '[coordinator, ]'
    '[Human, gpt-5.6-sol]'
    'prefix [coordinator, gpt-5.6-sol]'
)
for role_tag in "${invalid_role_tags[@]}"; do
    write_comments "$path" "$digest" "$role_tag"
    if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
        printf 'FAIL: malformed receipt role tag was accepted: %s\n' "$role_tag" >&2
        exit 1
    fi
done
write_comments "$path" "$digest" '[coordinator, gpt-5.6-sol]'

# The same legitimate receipt must not authorize a different (rebased) head.
stale_sha=ffffffffffffffffffffffffffffffffffffffff
if "$verifier" --sha "$stale_sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: receipt for the prior head authorized a rebased head" >&2
    exit 1
fi

# A tampered body and a real zero-executed receipt are both refused.
printf '\n' >>"$tmp/receipts/$receipt_commit/$path"
if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: tampered receipt was accepted" >&2
    exit 1
fi
make_receipt 0 "$tmp/zero.json"
zero_digest=$(sha256sum "$tmp/zero.json" | awk '{print $1}')
zero_path="validation-receipts/rrnewton/hermit/$sha/$zero_digest.json"
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$zero_path")"
cp "$tmp/zero.json" "$tmp/receipts/$receipt_commit/$zero_path"
write_comments "$zero_path" "$zero_digest"
if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: zero-executed receipt was accepted" >&2
    exit 1
fi

# Host provenance binds the wrapper identity for legacy and current roles.
make_receipt 12 "$tmp/host-good.json"
jq -cS '.run_id = (.commit + "@" + .ledger_record.started_at + "@other-host")' \
    "$tmp/host-good.json" >"$tmp/host-mismatch.json"
verify_file "$tmp/host-mismatch.json" fail "run_id host disagrees with ledger host"
jq -cS 'del(.ledger_record.host)' "$tmp/host-good.json" >"$tmp/host-absent-raw.json"
refresh_selected_identity "$tmp/host-absent-raw.json" "$tmp/host-absent.json"
verify_file "$tmp/host-absent.json" fail "ledger host absent"

# End-to-end current producer contract: one strong verifier-selected row is
# passed as exact bytes with its canonical digest, the mechanical publisher
# emits an artifact-SHA-addressed body, and a current role-tagged marker
# dereferences through the landing verifier.
sha=dddddddddddddddddddddddddddddddddddddddd
strong_log=$tmp/strong-validate.log
printf 'running 12 tests\ntest result: ok. 12 passed; 0 failed\n' >"$strong_log"
jq -cn --arg sha "$sha" --arg log "$strong_log" '{
  schema_version: 4,
  started_at: "2026-08-04T13:00:00Z",
  finished_at: "2026-08-04T13:02:00Z",
  host: "fixture-host",
  slot: "fixture-slot",
  profile: "full",
  selection_mode: "full",
  commit: $sha,
  tree: ("f" * 40),
  commit_anchored: true,
  tree_dirty: false,
  result: "pass",
  raw_result: "pass",
  exit_code: 0,
  executed_tests: 12,
  filtered_tests: 3,
  checks: 2,
  gates_run: 2,
  gates_expected: 2,
  failures: 0,
  log_file: $log,
  gates: [
    {name: "fmt", result: "pass", exit_code: 0},
    {name: "test", result: "pass", exit_code: 0}
  ]
}' | tr -d '\n' >"$tmp/strong-row.json"
"$receipt_digest" receipt-digest --sha "$sha" --canonical-row \
    <"$tmp/strong-row.json" >"$tmp/strong-canonical-row.json"
selected_digest=$(sha256sum "$tmp/strong-canonical-row.json" | awk '{print $1}')
strong_report=$(python3 "$publisher" \
    --repo rrnewton/hermit \
    --sha "$sha" \
    --ledger "$tmp/strong-ledger.jsonl" \
    --selected-receipt-sha256 "$selected_digest" \
    --canonicalization 'serde_json::to_vec(HistoryRow)-v1' \
    --dry-run <"$tmp/strong-canonical-row.json")
artifact_digest=$(jq -r '.artifact_sha256' <<<"$strong_report")
artifact_path=$(jq -r '.path' <<<"$strong_report")
jq -jr '.artifact_body' <<<"$strong_report" >"$tmp/strong-artifact.json"
if [[ $(sha256sum "$tmp/strong-artifact.json" | awk '{print $1}') != "$artifact_digest" ]] || \
   [[ $artifact_path != "validation-receipts/rrnewton/hermit/$sha/$artifact_digest.json" ]]; then
    echo "FAIL: publisher did not bind exact artifact bytes to its digest-addressed path" >&2
    exit 1
fi
if ! jq -e --arg selected "$selected_digest" '
    .selected_receipt_identity.digest_algorithm == "sha256"
    and .selected_receipt_identity.canonicalization == "serde_json::to_vec(HistoryRow)-v1"
    and .selected_receipt_identity.digest == $selected
    and .ledger_record.checks == 2
    and .ledger_record.gates_run == 2
    and (.ledger_record.gates | length) == 2
' "$tmp/strong-artifact.json" >/dev/null; then
    echo "FAIL: artifact lost the verifier-selected strong row identity" >&2
    exit 1
fi
mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$artifact_path")"
cp "$tmp/strong-artifact.json" "$tmp/receipts/$receipt_commit/$artifact_path"
write_comments "$artifact_path" "$artifact_digest" '[coordinator, gpt-5.6-sol]'
if ! "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" >/dev/null 2>&1; then
    echo "FAIL: strong-row -> artifact-digest -> marker chain was refused" >&2
    exit 1
fi

# Count-capable receipts additionally bind the per-node coverage obligation.
# Use a second exact head so the two positive controls represent two distinct
# legitimate landing authorizations rather than repeated parsing of one row.
sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
make_receipt 12 "$tmp/schema5-base.json"
jq '.ledger_record.schema_version = 5' "$tmp/schema5-base.json" >"$tmp/schema5-missing-raw.json"
refresh_selected_identity "$tmp/schema5-missing-raw.json" "$tmp/schema5-missing.json"
verify_file "$tmp/schema5-missing.json" fail "schema5 missing coverage"
jq '.ledger_record.coverage = {
      planned_test_nodes: 0, executed_test_nodes: 0,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-zero-planned-raw.json"
refresh_selected_identity "$tmp/schema5-zero-planned-raw.json" "$tmp/schema5-zero-planned.json"
verify_file "$tmp/schema5-zero-planned.json" fail "schema5 zero planned nodes"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 1,
      zero_executed_nodes: [], absent_nodes: ["test.missing"]
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-absent-raw.json"
refresh_selected_identity "$tmp/schema5-absent-raw.json" "$tmp/schema5-absent.json"
verify_file "$tmp/schema5-absent.json" fail "schema5 absent node"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-valid-raw.json"
refresh_selected_identity "$tmp/schema5-valid-raw.json" "$tmp/schema5-valid.json"
verify_file "$tmp/schema5-valid.json" pass "schema5 complete coverage"

plant_root=$tmp
rm -rf -- "$plant_root"
if [[ -e $plant_root ]]; then
    echo "FAIL: receipt fixture plant was not deleted cleanly: $plant_root" >&2
    exit 1
fi
trap - EXIT

echo "PASS: 2/2 legitimate exact-head landing receipts accepted; 2/2 additional identity/compatibility receipts and 5/5 role tags accepted; current-tagged identity omission, malformed legacy identity, tampered selected-row digest after outer rehash, current-tagged weak row, 4/4 malformed role tags, stale-head, forged, tampered, zero-executed, host-mismatch, host-absent, and three incomplete schema5 controls refused; fixture plant deleted cleanly"
