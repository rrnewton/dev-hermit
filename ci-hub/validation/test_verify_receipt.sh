#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)
bundle=$script_dir/verify_receipt_bundle.sh
bundle_sha=$(git -C "$root" rev-parse HEAD)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

pin=dddddddddddddddddddddddddddddddddddddddd
target_repo="$tmp/hermit"
mkdir -p "$target_repo/ci/dag" "$tmp/bin"
git -C "$target_repo" init -q
git -C "$target_repo" config user.email ci-hub@example.invalid
git -C "$target_repo" config user.name 'ci-hub test'
printf '[package]\nname="receipt-fixture"\nversion="0.1.0"\n[dependencies]\nreverie={git="https://github.com/rrnewton/reverie.git",rev="%s"}\n' \
    "$pin" >"$target_repo/Cargo.toml"
printf 'version=3\n[[package]]\nname="reverie-core"\nversion="0.2.0"\nsource="git+https://github.com/rrnewton/reverie.git?rev=%s#%s"\n' \
    "$pin" "$pin" >"$target_repo/Cargo.lock"
printf '{"steps":[{"group":"test","job":"portable"}]}\n' \
    >"$target_repo/ci/dag/portable.json"
printf '{"steps":[{"group":"test","job":"privileged"}]}\n' \
    >"$target_repo/ci/dag/privileged.json"
git -C "$target_repo" add Cargo.toml Cargo.lock ci/dag/portable.json ci/dag/privileged.json
git -C "$target_repo" commit -q -m fixture
sha=$(git -C "$target_repo" rev-parse HEAD)
tree=$(git -C "$target_repo" rev-parse "$sha^{tree}")
cache_sha=$(git -C "$target_repo" commit-tree "$tree" -p "$sha" \
    -m 'identical-tree cache fixture')

tip_file="$tmp/reverie-tip"
printf '%s\n' "$pin" >"$tip_file"
export CI_HUB_TEST_TIP_FILE="$tip_file"
cp "$script_dir/tests/receipt_with_proxy_fixture.sh" "$tmp/bin/with-proxy"
chmod +x "$tmp/bin/with-proxy"
export PATH="$tmp/bin:$PATH"

log="$tmp/validate.log"
for node in portable privileged; do
    printf '[test.%s] running 6 tests\n' "$node"
    printf '[test.%s] test result: ok. 6 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n' "$node"
    printf '[test.%s] ✓ PASS [test result: ok. 6 passed]\n' "$node"
done >"$log"

ledger="$tmp/ledger.jsonl"
jq -cn --arg sha "$sha" --arg log "$log" '{
  schema_version:4,
  started_at:"2026-08-05T12:00:00Z",
  finished_at:"2026-08-05T12:01:00Z",
  host:"test-host",
  commit:$sha,
  profile:"full",
  selection_mode:"full",
  commit_anchored:true,
  tree_dirty:false,
  result:"pass",
  checks:5,
  failures:0,
  log_file:$log
}' >"$ledger"
python3 "$root/ci-hub/validate/finalize_receipt.py" \
    --repo rrnewton/hermit --sha "$sha" --ledger "$ledger" \
    --hermit-checkout "$target_repo" >/dev/null

status="$tmp/status.json"
"$root/ci-hub/ci-hub" validate-status --repo rrnewton/hermit --sha "$sha" \
    --ledger "$ledger" --hermit-repo "$target_repo" --json >"$status"
selected="$tmp/selected.json"
jq -r '.newest_qualifying_canonical_hex' "$status" | xxd -r -p >"$selected"
selected_digest=$(jq -r '.newest_qualifying_identity.digest' "$status")

report="$tmp/publish-report.json"
python3 "$script_dir/publish_receipt.py" --repo rrnewton/hermit --sha "$sha" \
    --ledger "$ledger" --selected-receipt-sha256 "$selected_digest" \
    --canonicalization 'serde_json::to_vec(HistoryRow)-v1' --dry-run \
    <"$selected" >"$report"

branch_tip=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
fixture="$tmp/receipts/$branch_tip"
receipt_path=$(jq -r .path "$report")
outcome_path=$(jq -r .outcome_path "$report")
log_path=$(jq -r .durable_log_path "$report")
mkdir -p "$fixture/$(dirname "$receipt_path")" \
         "$fixture/$(dirname "$outcome_path")" \
         "$fixture/$(dirname "$log_path")"
jq -jr .artifact_body "$report" >"$fixture/$receipt_path"
jq -jr .outcome_body "$report" >"$fixture/$outcome_path"
cp "$log" "$fixture/$log_path"
printf '[]\n' >"$tmp/comments.json"

verify() {
    "$bundle" --repo rrnewton/hermit --sha "$sha" \
        --target-repo "$target_repo" --comments "$tmp/comments.json" \
        --expected-bundle-sha "$bundle_sha" \
        --fixture-receipts "$tmp/receipts" --fixture-branch-tip "$branch_tip"
}

# Positive: a real source row was finalized, published, rediscovered from the
# append-only branch-tip outcome set, and exactly recomputed from its durable log.
verify >/dev/null

# A caller cannot redirect the authority to a different qualifying predicate.
# A malformed explicit override would make the Rust verifier fail loudly if the
# bundle forwarded it; passing proves the manifest-checked predicate is used.
printf '{not valid json\n' >"$tmp/caller-predicate.json"
if QUALIFYING_RECEIPT_PREDICATE="$tmp/caller-predicate.json" \
    CI_HUB_TEST_PREDICATE_OVERRIDE=1 \
    "$script_dir/verify_receipt.sh" --repo rrnewton/hermit --sha "$sha" \
        --hermit-repo "$target_repo" --comments "$tmp/comments.json" \
        --fixture-receipts "$tmp/receipts" --fixture-branch-tip "$branch_tip" \
        >/dev/null 2>&1; then
    echo 'FAIL: planted predicate override did not affect the unbound verifier' >&2
    exit 1
fi
QUALIFYING_RECEIPT_PREDICATE="$tmp/caller-predicate.json" \
    CI_HUB_TEST_PREDICATE_OVERRIDE=1 verify >/dev/null

# A successful identical-tree cache return is useful soft-green evidence only.
# It cannot mint an exact-SHA receipt when the new commit ran zero gates/tests.
cache_ledger="$tmp/cache-ledger.jsonl"
jq -cn --arg sha "$cache_sha" --arg pin "$pin" '{
  schema_version:6,
  started_at:"2026-08-05T12:02:00Z",
  finished_at:"2026-08-05T12:02:01Z",
  host:"test-host",
  commit:$sha,
  profile:"full",
  selection_mode:"full",
  commit_anchored:true,
  tree_dirty:false,
  result:"pass",
  checks:0,
  gates_run:0,
  gates_expected:0,
  failures:0,
  executed_tests:0,
  filtered_tests:0,
  coverage:{planned_test_nodes:0,executed_test_nodes:0,zero_executed_nodes:[],absent_nodes:[],failed_nodes:[]},
  reverie_binding:{repository:"rrnewton/reverie",ref:"refs/heads/main",pinned_sha:$pin,resolved_sha:$pin}
}' >"$cache_ledger"
if "$root/ci-hub/ci-hub" validate-status --repo rrnewton/hermit \
    --sha "$cache_sha" --ledger "$cache_ledger" \
    --hermit-repo "$target_repo" >/dev/null 2>&1; then
    echo 'FAIL: identical-tree zero-gate cache hit became exact-SHA hard green' >&2
    exit 1
fi
export CI_HUB_TEST_PR_HEAD="$cache_sha"
cache_apply=$("$root/ci-hub/ci-hub" apply-local-label --pr 1 \
    --repo rrnewton/hermit --ledger "$cache_ledger" \
    --hermit-repo "$target_repo" --dry-run 2>&1)
grep -q 'would-publish-outcome NOT-VALIDATED' <<<"$cache_apply"
if grep -q 'would-bind' <<<"$cache_apply"; then
    echo 'FAIL: identical-tree zero-gate cache hit reached receipt binding' >&2
    exit 1
fi

# The human-facing label is only a cache, but apply-local-label must not leave
# it on a head that moves during publication/binding. First bracket the stable
# positive, then move the head after add-label and require compensating removal.
export CI_HUB_TEST_PR_HEAD="$sha"
export CI_HUB_TEST_RECEIPT_COMMIT="$branch_tip"
export CI_HUB_TEST_RECEIPT_ROOT="$tmp/live-receipts"
export CI_HUB_TEST_COMMENT_BODY="$tmp/comment-body"
export CI_HUB_TEST_EDIT_LOG="$tmp/edit-log"
: >"$CI_HUB_TEST_EDIT_LOG"
"$root/ci-hub/ci-hub" apply-local-label --pr 1 --repo rrnewton/hermit \
    --ledger "$ledger" --hermit-repo "$target_repo" >/dev/null
grep -q -- '--add-label locally-validated' "$CI_HUB_TEST_EDIT_LOG"
if grep -q -- '--remove-label locally-validated' "$CI_HUB_TEST_EDIT_LOG"; then
    echo 'FAIL: stable PR head caused cache-label removal' >&2
    exit 1
fi

heads="$tmp/pr-heads"
moved_head=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
printf '%s\n%s\n%s\n' "$sha" "$sha" "$moved_head" >"$heads"
export CI_HUB_TEST_PR_HEADS_FILE="$heads"
: >"$CI_HUB_TEST_EDIT_LOG"
if "$root/ci-hub/ci-hub" apply-local-label --pr 1 --repo rrnewton/hermit \
    --ledger "$ledger" --hermit-repo "$target_repo" >/dev/null 2>&1; then
    echo 'FAIL: cache label survived a concurrent PR head move' >&2
    exit 1
fi
grep -q -- '--add-label locally-validated' "$CI_HUB_TEST_EDIT_LOG"
grep -q -- '--remove-label locally-validated' "$CI_HUB_TEST_EDIT_LOG"
unset CI_HUB_TEST_PR_HEADS_FILE

# The old bypass: a one-line arbitrary log cannot carry claimed full coverage,
# even when supplied beside the otherwise legitimate row and snapshot.
printf 'arbitrary one-line log\n' >"$tmp/arbitrary.log"
if python3 "$root/ci-hub/validate/finalize_receipt.py" \
    --repo rrnewton/hermit --sha "$sha" --hermit-checkout "$target_repo" \
    --log "$tmp/arbitrary.log" --ledger-snapshot <(jq -c .ledger_records "$fixture/$outcome_path") \
    --verify-finalized-row "$selected" >/dev/null 2>&1; then
    echo 'FAIL: self-asserted coverage survived durable-log recomputation' >&2
    exit 1
fi

# A genuine same-SHA failure published later is monotonic: unioning every
# immutable outcome snapshot makes it beat the older pass forever.
jq -cn --arg sha "$sha" '{
  schema_version:6, profile:"full", selection_mode:"full", commit:$sha,
  commit_anchored:true, tree_dirty:false, result:"fail", exit_code:1,
  checks:5, gates_run:5, gates_expected:5, failures:1, executed_tests:765,
  dag_jobs:4, concurrent_validates:0, known_flaky_failure:false,
  solo_rerun_confirmation:false,
  gates:[{name:"portable CI DAG lane",result:"fail",exit_code:1,
          real_seconds:2,failure_origin:"lane_substep",
          failed_substeps:["test.portable"]}]
}' >>"$ledger"
failure_report="$tmp/failure-report.json"
python3 "$script_dir/publish_outcome.py" --repo rrnewton/hermit --sha "$sha" \
    --ledger "$ledger" --verdict FAILED --dry-run >"$failure_report"
failure_path=$(jq -r .outcome_path "$failure_report")
mkdir -p "$fixture/$(dirname "$failure_path")"
jq -jr .outcome_body "$failure_report" >"$fixture/$failure_path"
if verify >/dev/null 2>&1; then
    echo 'FAIL: immutable later failure did not beat the older pass' >&2
    exit 1
fi
rm -- "$fixture/$failure_path"
verify >/dev/null

# Every exact-SHA outcome and durable log is content-addressed and dereferenced
# at one branch tip; tamper or absence is a refusal.
cp "$fixture/$outcome_path" "$tmp/outcome.saved"
printf '\n' >>"$fixture/$outcome_path"
if verify >/dev/null 2>&1; then
    echo 'FAIL: digest-tampered outcome was accepted' >&2
    exit 1
fi
mv "$tmp/outcome.saved" "$fixture/$outcome_path"
mv "$fixture/$log_path" "$tmp/log.saved"
if verify >/dev/null 2>&1; then
    echo 'FAIL: absent durable log was accepted' >&2
    exit 1
fi
mv "$tmp/log.saved" "$fixture/$log_path"

# Fresh dependency identity remains part of every decision.
printf '%s\n' cccccccccccccccccccccccccccccccccccccccc >"$tip_file"
if verify >/dev/null 2>&1; then
    echo 'FAIL: receipt remained green after live Reverie main moved' >&2
    exit 1
fi
printf '%s\n' "$pin" >"$tip_file"
verify >/dev/null

# Plant the exact replacement-ref attack against the bundle itself. Unsanitized
# `git diff HEAD -- required-path` sees the replacement commit and falsely calls
# the modified contract clean; the bundle's safe Git environment must refuse it.
replace_clone="$tmp/replace-bundle"
git clone -q --no-hardlinks "$root" "$replace_clone"
replace_head=$(git -C "$replace_clone" rev-parse HEAD)
jq '.producer_role = "[coordinator, planted-replacement]"' \
    "$replace_clone/ci-hub/validation/receipt-comment-contract.json" \
    >"$tmp/planted-contract.json"
cp "$tmp/planted-contract.json" \
    "$replace_clone/ci-hub/validation/receipt-comment-contract.json"
git -C "$replace_clone" add ci-hub/validation/receipt-comment-contract.json
git -C "$replace_clone" -c user.email=ci-hub@example.invalid \
    -c user.name='ci-hub test' commit -q -m planted-replacement
replace_commit=$(git -C "$replace_clone" rev-parse HEAD)
GIT_NO_REPLACE_OBJECTS=1 git -C "$replace_clone" checkout -q --detach "$replace_head"
cp "$tmp/planted-contract.json" \
    "$replace_clone/ci-hub/validation/receipt-comment-contract.json"
git -C "$replace_clone" update-ref "refs/replace/$replace_head" "$replace_commit"
if ! git -C "$replace_clone" diff --quiet "$replace_head" -- \
    ci-hub/validation/receipt-comment-contract.json; then
    echo 'FAIL: replacement-ref attack plant did not mask the modified bundle path' >&2
    exit 1
fi
if "$replace_clone/ci-hub/validation/verify_receipt_bundle.sh" \
    --repo rrnewton/hermit --sha "$sha" --target-repo "$target_repo" \
    --comments "$tmp/comments.json" --expected-bundle-sha "$replace_head" \
    --fixture-receipts "$tmp/receipts" --fixture-branch-tip "$branch_tip" \
    >"$tmp/replace-bundle.out" 2>&1; then
    echo 'FAIL: replacement ref made a modified verifier bundle appear clean' >&2
    exit 1
fi
grep -q 'bundle path is missing, untracked, or modified' "$tmp/replace-bundle.out"

# The tracked executable symlink is part of the bundle too; checking only its
# Rust target would let a working-tree link redirect the consumer elsewhere.
symlink_clone="$tmp/symlink-bundle"
git clone -q --no-hardlinks "$root" "$symlink_clone"
symlink_head=$(git -C "$symlink_clone" rev-parse HEAD)
ln -sfn /bin/true "$symlink_clone/ci-hub/ci-hub"
if "$symlink_clone/ci-hub/validation/verify_receipt_bundle.sh" \
    --repo rrnewton/hermit --sha "$sha" --target-repo "$target_repo" \
    --comments "$tmp/comments.json" --expected-bundle-sha "$symlink_head" \
    --fixture-receipts "$tmp/receipts" --fixture-branch-tip "$branch_tip" \
    >"$tmp/symlink-bundle.out" 2>&1; then
    echo 'FAIL: redirected ci-hub executable symlink was omitted from the bundle' >&2
    exit 1
fi
grep -q 'bundle path is missing, untracked, or modified: ci-hub/ci-hub' \
    "$tmp/symlink-bundle.out"

echo 'PASS: branch-tip outcomes, monotonic failure precedence, exact log/finalizer recomputation, content addressing, predicate binding, zero-gate identical-tree cache refusal, head-race cache cleanup, fresh Reverie binding, and replacement-ref hardening bracketed'
