#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
verifier=$script_dir/verify_receipt.sh
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

pin=dddddddddddddddddddddddddddddddddddddddd
hermit_repo="$tmp/hermit"
mkdir -p "$hermit_repo" "$tmp/bin"
git -C "$hermit_repo" init -q
git -C "$hermit_repo" config user.email ci-hub@example.invalid
git -C "$hermit_repo" config user.name 'ci-hub test'
printf '[package]\nname="receipt-fixture"\nversion="0.1.0"\n[dependencies]\nreverie={git="https://github.com/rrnewton/reverie.git",rev="%s"}\n' \
    "$pin" >"$hermit_repo/Cargo.toml"
git -C "$hermit_repo" add Cargo.toml
git -C "$hermit_repo" commit -q -m 'fixture one'
sha=$(git -C "$hermit_repo" rev-parse HEAD)
printf 'second\n' >"$hermit_repo/README.md"
git -C "$hermit_repo" add README.md
git -C "$hermit_repo" commit -q -m 'fixture two'
sha2=$(git -C "$hermit_repo" rev-parse HEAD)
tip_file="$tmp/reverie-tip"
printf '%s\n' "$pin" >"$tip_file"
export CI_HUB_TEST_TIP_FILE="$tip_file"
printf '%s\n' '#!/usr/bin/env bash' \
    'if [[ $1 == git && $2 == ls-remote ]]; then' \
    '  tip=$(<"$CI_HUB_TEST_TIP_FILE")' \
    '  printf "%s\trefs/heads/main\\n" "$tip"' \
    '  exit 0' \
    'fi' \
    'exec "$@"' >"$tmp/bin/with-proxy"
chmod +x "$tmp/bin/with-proxy"
export PATH="$tmp/bin:$PATH"
receipt_commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
mkdir -p "$tmp/receipts/$receipt_commit"

make_receipt() {
    local executed=$1 output=$2
    jq -cnS --arg sha "$sha" --argjson executed "$executed" '{
      schema_version: 1,
      repository: "rrnewton/hermit",
      commit: $sha,
      run_id: ($sha + "@2026-08-04T12:00:00Z@test-host"),
      source_log_file: "/tmp/validate.log",
      durable_log_file: "/durable/validate.log",
      log_sha256: ("c" * 64),
      ledger_record: {
        schema_version: 6,
        started_at: "2026-08-04T12:00:00Z",
        finished_at: "2026-08-04T12:01:00Z",
        host: "test-host",
        commit: $sha,
        profile: "full",
        selection_mode: "full",
        commit_anchored: true,
        tree_dirty: false,
        result: "pass",
        checks: 5,
        failures: 0,
        executed_tests: $executed,
        filtered_tests: 0,
        coverage: {
          planned_test_nodes: 1, executed_test_nodes: 1,
          zero_executed_nodes: [], absent_nodes: []
        },
        reverie_binding: {
          repository: "rrnewton/reverie",
          ref: "refs/heads/main",
          pinned_sha: ("d" * 40),
          resolved_sha: ("d" * 40)
        },
        log_file: "/tmp/validate.log"
      }
    }' >"$output"
}

write_comments() {
    local path=$1 digest=$2
    jq -cn --arg commit "$receipt_commit" --arg path "$path" --arg digest "$digest" '{
      user: {login: "rrnewton"},
      body: ("[impl agent, ci-hub]\n\n<!-- locally-validated-receipt commit=" + $commit + " path=" + $path + " sha256=" + $digest + " -->")
    } | [[.]]' >"$tmp/comments.json"
}

verify_file() {
    local file=$1 expected=$2 label=$3
    local file_digest file_path status=0
    file_digest=$(sha256sum "$file" | awk '{print $1}')
    file_path="validation-receipts/rrnewton/hermit/$sha/$file_digest.json"
    mkdir -p "$tmp/receipts/$receipt_commit/$(dirname "$file_path")"
    cp "$file" "$tmp/receipts/$receipt_commit/$file_path"
    write_comments "$file_path" "$file_digest"
    "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" \
        >/dev/null 2>&1 || status=$?
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
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null 2>&1; then
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
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null

# Moving only the live Reverie tip invalidates the otherwise immutable receipt.
printf '%s\n' cccccccccccccccccccccccccccccccccccccccc >"$tip_file"
if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null 2>&1; then
    echo "FAIL: receipt remained valid after Reverie main moved" >&2
    exit 1
fi
printf '%s\n' "$pin" >"$tip_file"

# The same legitimate receipt must not authorize a different (rebased) head.
stale_sha=ffffffffffffffffffffffffffffffffffffffff
if "$verifier" --sha "$stale_sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null 2>&1; then
    echo "FAIL: receipt for the prior head authorized a rebased head" >&2
    exit 1
fi

# A tampered body and a real zero-executed receipt are both refused.
printf '\n' >>"$tmp/receipts/$receipt_commit/$path"
if "$verifier" --sha "$sha" --comments "$tmp/comments.json" \
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null 2>&1; then
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
    --fixture-receipts "$tmp/receipts" --hermit-repo "$hermit_repo" >/dev/null 2>&1; then
    echo "FAIL: zero-executed receipt was accepted" >&2
    exit 1
fi

# Host-in-identity negative: a receipt whose run_id host segment disagrees with
# the ledger_record.host it embeds is refused (the host cannot be swapped without
# breaking the tamper-evident run_id).
make_receipt 12 "$tmp/host-good.json"
jq -cS '.run_id = (.commit + "@" + .ledger_record.started_at + "@other-host")' \
    "$tmp/host-good.json" >"$tmp/host-mismatch.json"
verify_file "$tmp/host-mismatch.json" fail "run_id host disagrees with ledger host"
# A receipt whose ledger_record omits host entirely is likewise refused.
jq -cS 'del(.ledger_record.host)' "$tmp/host-good.json" >"$tmp/host-absent.json"
verify_file "$tmp/host-absent.json" fail "ledger host absent"
jq -cS 'del(.ledger_record.reverie_binding)' "$tmp/host-good.json" >"$tmp/binding-absent.json"
verify_file "$tmp/binding-absent.json" fail "Reverie binding absent"
jq -cS '.ledger_record.reverie_binding.resolved_sha = ("c" * 40)' \
    "$tmp/host-good.json" >"$tmp/binding-tampered.json"
verify_file "$tmp/binding-tampered.json" fail "Reverie binding tampered"

# Count-capable receipts additionally bind the per-node coverage obligation.
# Use a second exact head so the two positive controls represent two distinct
# legitimate landing authorizations rather than repeated parsing of one row.
sha=$sha2
make_receipt 12 "$tmp/schema5-base.json"
jq 'del(.ledger_record.coverage)' "$tmp/schema5-base.json" >"$tmp/schema5-missing.json"
verify_file "$tmp/schema5-missing.json" fail "schema6 missing coverage"
jq '.ledger_record.coverage = {
      planned_test_nodes: 0, executed_test_nodes: 0,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-zero-planned.json"
verify_file "$tmp/schema5-zero-planned.json" fail "schema6 zero planned nodes"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 1,
      zero_executed_nodes: [], absent_nodes: ["test.missing"]
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-absent.json"
verify_file "$tmp/schema5-absent.json" fail "schema6 absent node"
jq '.ledger_record.coverage = {
      planned_test_nodes: 2, executed_test_nodes: 2,
      zero_executed_nodes: [], absent_nodes: []
    }' "$tmp/schema5-missing.json" >"$tmp/schema5-valid.json"
verify_file "$tmp/schema5-valid.json" pass "schema6 complete coverage"

plant_root=$tmp
rm -rf -- "$plant_root"
if [[ -e $plant_root ]]; then
    echo "FAIL: receipt fixture plant was not deleted cleanly: $plant_root" >&2
    exit 1
fi
trap - EXIT

echo "PASS: 2/2 legitimate exact-head landing receipts accepted at the same Reverie tip; moved-tip, missing/tampered binding, stale-head, forged, digest-tampered, zero-executed, host-mismatch/absent, and three incomplete schema6 controls refused; fixture plant deleted cleanly"
