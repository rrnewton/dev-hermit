#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
verifier=$script_dir/verify_receipt.sh
publisher=$script_dir/publish_receipt.py
receipt_digest=$script_dir/../ci-hub
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
receipt_commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p "$tmp/receipts/$receipt_commit"

# --- PRODUCER DEFINITION BINDING (task bind_receipt_to_producer) -------------
# The merged verifier requires every receipt to name the check definition that
# produced it, and to match the REGISTERED current one exactly. Point the
# bracket at a FIXTURE registry so these cases stay stable against real blob
# churn on hermit main -- registering real blobs here would make the bracket
# fail every time validate.sh legitimately changes.
# ONE fixture producer checkout and ONE registry for the whole bracket. The
# end-to-end leg mints through the real publisher, which derives the definition
# from `git rev-parse <sha>:<path>`, so the registered blobs must be this repo's
# REAL blobs -- and the synthetic receipts built by make_receipt must embed the
# SAME values, or they stop matching the moment the two disagree.
producer_repo=$tmp/producer-checkout
mkdir -p "$producer_repo/.github/workflows"
printf '#!/usr/bin/env bash\necho validate\n' >"$producer_repo/validate.sh"
printf 'name: CI\n' >"$producer_repo/.github/workflows/ci-portable.yml"
git -C "$producer_repo" init -q
git -C "$producer_repo" -c user.name=t -c user.email=t@e add -A
git -C "$producer_repo" -c user.name=t -c user.email=t@e commit -qm 'producer fixture'
producer_sha=$(git -C "$producer_repo" rev-parse HEAD)
REG_VALIDATE=$(git -C "$producer_repo" rev-parse HEAD:validate.sh)
REG_PORTABLE=$(git -C "$producer_repo" rev-parse HEAD:.github/workflows/ci-portable.yml)
cat >"$tmp/producer-registry.json" <<REG
{"registered": {"validate.sh": "$REG_VALIDATE",
                ".github/workflows/ci-portable.yml": "$REG_PORTABLE"}}
REG
export PRODUCER_DEFINITION_REGISTRY=$tmp/producer-registry.json

make_receipt() {
    local executed=$1 output=$2
    local raw=$tmp/receipt-build.json selected
    jq -cnS --arg sha "$sha" --argjson executed "$executed" \
            --arg reg_validate "$REG_VALIDATE" --arg reg_portable "$REG_PORTABLE" '{
      schema_version: 1,
      repository: "rrnewton/hermit",
      commit: $sha,
      run_id: ($sha + "@2026-08-04T12:00:00Z@fixture-host"),
      source_log_file: "/tmp/validate.log",
      durable_log_file: "/durable/validate.log",
      log_sha256: ("c" * 64),
      producer: {
        resolved_from: "/fixture/worktree",
        definition: {
          "validate.sh": $reg_validate,
          ".github/workflows/ci-portable.yml": $reg_portable
        }
      },
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

# --- mutation anchors -------------------------------------------------------
# Every mutated fixture must be shown to have ACTUALLY changed the receipt
# before its refusal is believed. A mutation expression that silently no-ops
# would otherwise be scored as "the guard refused it", i.e. this bracket would
# report robustness it never tested -- a proxy-binding defect in the bracket.
#
# The pre-merge harness compared with `cmp -s`. A byte compare is VACUOUS on
# this harness: make_receipt/refresh_selected_identity emit pretty-printed JSON
# while several mutations use `jq -cS`, so base and mutant differ in FORMATTING
# alone and a no-op expression would still look mutated. Measured: a `jq -cS .`
# no-op applied to a pretty base differs byte-wise but is identical once
# normalized. So the anchor compares CANONICAL CONTENT (`jq -S -c`), which is
# what "the mutation changed the receipt" actually means.
mutation_anchor_failures=0
mutation_anchors_total=10
assert_mutated() { # assert_mutated <base> <mutant> <label>
    local a b
    a=$(jq -S -c . "$1" 2>/dev/null) || a="<unparseable:$1>"
    b=$(jq -S -c . "$2" 2>/dev/null) || b="<unparseable:$2>"
    if [[ "$a" == "$b" ]]; then
        printf 'BAD  ANCHOR    mutation did not change the receipt: %s\n' "$3" >&2
        mutation_anchor_failures=$((mutation_anchor_failures + 1))
    fi
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
assert_mutated "$tmp/receipt.json" "$tmp/tampered-selected-digest.json" "IDENTITY tampered selected digest"
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
assert_mutated "$tmp/receipt.json" "$tmp/historical.json" "IDENTITY legacy artifact without selected identity"
verify_file "$tmp/historical.json" pass "legacy service artifact without selected identity"
verify_file "$tmp/historical.json" fail "current role artifact without selected identity" \
    '[coordinator, gpt-5.6-sol]'
jq '.selected_receipt_identity = false' "$tmp/receipt.json" >"$tmp/malformed-identity.json"
assert_mutated "$tmp/receipt.json" "$tmp/malformed-identity.json" "IDENTITY malformed selected identity"
verify_file "$tmp/malformed-identity.json" fail "legacy artifact with malformed selected identity"
jq '
  .ledger_record.checks = 0
  | .ledger_record.gates_run = 0
  | .ledger_record.gates = []
' "$tmp/receipt.json" >"$tmp/current-weak-row-raw.json"
assert_mutated "$tmp/receipt.json" "$tmp/current-weak-row-raw.json" "ROW weak selected row"
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
assert_mutated "$tmp/host-good.json" "$tmp/host-mismatch.json" "HOST run_id host mismatch"
verify_file "$tmp/host-mismatch.json" fail "run_id host disagrees with ledger host"
jq -cS 'del(.ledger_record.host)' "$tmp/host-good.json" >"$tmp/host-absent-raw.json"
assert_mutated "$tmp/host-good.json" "$tmp/host-absent-raw.json" "HOST ledger host absent"
refresh_selected_identity "$tmp/host-absent-raw.json" "$tmp/host-absent.json"
verify_file "$tmp/host-absent.json" fail "ledger host absent"

# End-to-end current producer contract: one strong verifier-selected row is
# passed as exact bytes with its canonical digest, the mechanical publisher
# emits an artifact-SHA-addressed body, and a current role-tagged marker
# dereferences through the landing verifier.
# Reuse the single fixture producer checkout created at the top, so the minted
# definition and the registered one are the same blobs by construction.
sha=$producer_sha
strong_log=$tmp/strong-validate.log
printf 'running 12 tests\ntest result: ok. 12 passed; 0 failed\n' >"$strong_log"
jq -cn --arg sha "$sha" --arg log "$strong_log" --arg cwd "$producer_repo" '{
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
  cwd: $cwd,
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
assert_mutated "$tmp/schema5-base.json" "$tmp/schema5-missing-raw.json" "COVERAGE schema5 missing coverage block"
refresh_selected_identity "$tmp/schema5-missing-raw.json" "$tmp/schema5-missing.json"
verify_file "$tmp/schema5-missing.json" fail "schema5 missing coverage"
jq '.ledger_record.coverage = {
      planned_test_nodes: 0, executed_test_nodes: 0,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-zero-planned-raw.json"
assert_mutated "$tmp/schema5-missing.json" "$tmp/schema5-zero-planned-raw.json" "COVERAGE schema5 zero planned nodes"
refresh_selected_identity "$tmp/schema5-zero-planned-raw.json" "$tmp/schema5-zero-planned.json"
verify_file "$tmp/schema5-zero-planned.json" fail "schema5 zero planned nodes"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 1,
      zero_executed_nodes: [], absent_nodes: ["test.missing"]
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-absent-raw.json"
assert_mutated "$tmp/schema5-missing.json" "$tmp/schema5-absent-raw.json" "COVERAGE schema5 absent node"
refresh_selected_identity "$tmp/schema5-absent-raw.json" "$tmp/schema5-absent.json"
verify_file "$tmp/schema5-absent.json" fail "schema5 absent node"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      zero_executed_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-no-absent-list-raw.json"
assert_mutated "$tmp/schema5-missing.json" \
    "$tmp/schema5-no-absent-list-raw.json" \
    "COVERAGE schema5 omitted absent_nodes list"
refresh_selected_identity "$tmp/schema5-no-absent-list-raw.json" \
    "$tmp/schema5-no-absent-list.json"
verify_file "$tmp/schema5-no-absent-list.json" fail \
    "schema5 omitted absent_nodes list"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-no-zero-list-raw.json"
assert_mutated "$tmp/schema5-missing.json" \
    "$tmp/schema5-no-zero-list-raw.json" \
    "COVERAGE schema5 omitted zero_executed_nodes list"
refresh_selected_identity "$tmp/schema5-no-zero-list-raw.json" \
    "$tmp/schema5-no-zero-list.json"
verify_file "$tmp/schema5-no-zero-list.json" fail \
    "schema5 omitted zero_executed_nodes list"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-valid-raw.json"
assert_mutated "$tmp/schema5-missing.json" "$tmp/schema5-valid-raw.json" "COVERAGE schema5 complete coverage (positive)"
refresh_selected_identity "$tmp/schema5-valid-raw.json" "$tmp/schema5-valid.json"
verify_file "$tmp/schema5-valid.json" pass "schema5 complete coverage"

plant_root=$tmp
rm -rf -- "$plant_root"
if [[ -e $plant_root ]]; then
    echo "FAIL: receipt fixture plant was not deleted cleanly: $plant_root" >&2
    exit 1
fi
trap - EXIT

# Every mutant must be shown to have actually changed the receipt. A silently
# no-op mutation would otherwise be scored as "the guard refused it", i.e. the
# harness would report robustness it never tested.
printf 'MUTATION ANCHORS: %s\n' \
    "$([[ $mutation_anchor_failures -eq 0 ]] && echo "all $mutation_anchors_total mutants differed from their base" \
       || echo "$mutation_anchor_failures of $mutation_anchors_total MUTANT(S) DID NOT DIFFER -- results not believable")"
if [[ $mutation_anchor_failures -ne 0 ]]; then
    echo "FAIL: receipt-consumer bracket: mutation anchors" >&2
    exit 1
fi

echo "PASS: 2/2 legitimate exact-head landing receipts accepted; 2/2 additional identity/compatibility receipts and 5/5 role tags accepted; current-tagged identity omission, malformed legacy identity, tampered selected-row digest after outer rehash, current-tagged weak row, 4/4 malformed role tags, stale-head, forged, tampered, zero-executed, host-mismatch, host-absent, and five incomplete schema5 controls refused; fixture plant deleted cleanly"
