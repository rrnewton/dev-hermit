#!/usr/bin/env bash
# Verify that an evidence comment resolves to an immutable, counted receipt.
set -uo pipefail

receipt_repo=rrnewton/dev-hermit
receipt_branch=validation-receipts
repo=rrnewton/hermit
sha=
comments_file=
fixture_root=

usage() {
    cat >&2 <<'EOF'
Usage: verify-local-validation-receipt.sh --sha SHA --comments FILE [options]

Options:
  --repo OWNER/REPO       PR repository (default: rrnewton/hermit)
  --fixture-receipts DIR  Read DIR/<receipt-commit>/<receipt-path> instead of GitHub
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha) sha=${2:-}; shift 2 ;;
        --comments) comments_file=${2:-}; shift 2 ;;
        --repo) repo=${2:-}; shift 2 ;;
        --fixture-receipts) fixture_root=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done

if [[ ! $sha =~ ^[0-9a-f]{40}$ ]] || [[ -z $comments_file || ! -r $comments_file ]]; then
    usage
    exit 2
fi

owner=${repo%%/*}
mapfile -t candidates < <(jq -r --arg owner "$owner" '
    [ .[][]?
      | select(.user.login == $owner)
      | select((.body // "") | startswith("[impl agent, ci-hub]\n"))
      | .body
      | split("\n")[]
      | capture("^<!-- locally-validated-receipt commit=(?<commit>[0-9a-f]{40}) path=(?<path>[^ ]+) sha256=(?<digest>[0-9a-f]{64}) -->$")?
    ] | reverse[] | [.commit, .path, .digest] | @tsv
' "$comments_file" 2>/dev/null)

gh_cmd=(gh)
if command -v with-proxy >/dev/null 2>&1; then
    gh_cmd=(with-proxy gh)
fi

# Resolve the ONE shared qualifying-receipt predicate. In the merge gate this
# script AND this JSON both come from the immutable parent commit, never the PR
# under test (check-not-in-branch). $QUALIFYING_RECEIPT_PREDICATE overrides for
# the mutation test; NEVER point it at the live file. Restating the clauses
# inline is the drift this delegation removes (task
# one-shared-qualifying-receipt-predicate-five-consumers-bypass-the-registry).
predicate_file=${QUALIFYING_RECEIPT_PREDICATE:-}
if [[ -z $predicate_file ]]; then
    script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
    predicate_file="$script_dir/../validate/qualifying-receipt.json"
fi
if [[ ! -r $predicate_file ]]; then
    printf 'qualifying-receipt predicate unreadable: %s\n' "$predicate_file" >&2
    exit 2
fi
# Pull the fields the gate keys on. A malformed/partial predicate is a deploy
# defect -> exit 2 (loud), never a silent lenient fallback. `-r` (not `-e`) so a
# legitimately `false` boolean field is not mistaken for a jq error.
p_counts_schema=$(jq -r '.counts_schema' "$predicate_file" 2>/dev/null)
p_exec_min=$(jq -r '.require.executed_tests_min' "$predicate_file" 2>/dev/null)
p_cov_schema=$(jq -r '.coverage.applies_at_schema_min' "$predicate_file" 2>/dev/null)
p_cov_pernode=$(jq -r '.coverage.per_node' "$predicate_file" 2>/dev/null)
p_gate_filtered=$(jq -r '(.gate_filtered_tests // false)' "$predicate_file" 2>/dev/null)
p_failures_max=$(jq -r '.require.failures_max' "$predicate_file" 2>/dev/null)
p_profile=$(jq -r '.require.profile' "$predicate_file" 2>/dev/null)
p_selection=$(jq -r '.require.selection_mode' "$predicate_file" 2>/dev/null)
p_result=$(jq -r '.require.result' "$predicate_file" 2>/dev/null)
p_commit_anchored=$(jq -r '.require.commit_anchored' "$predicate_file" 2>/dev/null)
p_tree_dirty=$(jq -r '.require.tree_dirty' "$predicate_file" 2>/dev/null)
for _v in "$p_counts_schema" "$p_exec_min" "$p_cov_schema" "$p_cov_pernode" \
          "$p_gate_filtered" "$p_failures_max" "$p_profile" "$p_selection" \
          "$p_result" "$p_commit_anchored" "$p_tree_dirty"; do
    if [[ -z $_v || $_v == null ]]; then
        printf 'malformed qualifying-receipt predicate: %s\n' "$predicate_file" >&2
        exit 2
    fi
done

for candidate in "${candidates[@]}"; do
    IFS=$'\t' read -r receipt_commit receipt_path receipt_digest <<<"$candidate"
    expected_prefix="validation-receipts/${repo}/${sha}/"
    if [[ $receipt_path != "$expected_prefix"*.json ]] || \
       [[ ${receipt_path##*/} != "${receipt_digest}.json" ]]; then
        continue
    fi

    receipt_file=$(mktemp)
    if [[ -n $fixture_root ]]; then
        source_file="$fixture_root/$receipt_commit/$receipt_path"
        if [[ ! -f $source_file ]] || ! cp -- "$source_file" "$receipt_file"; then
            rm -f -- "$receipt_file"
            continue
        fi
    else
        comparison=$("${gh_cmd[@]}" api \
            "repos/${receipt_repo}/compare/${receipt_commit}...${receipt_branch}" \
            --jq .status 2>/dev/null) || comparison=
        if [[ $comparison != ahead && $comparison != identical ]]; then
            rm -f -- "$receipt_file"
            continue
        fi
        if ! "${gh_cmd[@]}" api \
            "repos/${receipt_repo}/contents/${receipt_path}?ref=${receipt_commit}" \
            --jq .content 2>/dev/null | tr -d '\n' | base64 --decode >"$receipt_file"; then
            rm -f -- "$receipt_file"
            continue
        fi
    fi

    actual_digest=$(sha256sum "$receipt_file" | awk '{print $1}')
    if [[ $actual_digest != "$receipt_digest" ]]; then
        rm -f -- "$receipt_file"
        continue
    fi

    # The receipt-WRAPPER checks (schema_version 1, repo/commit/run_id/log
    # binding) stay inline -- they are this script's own envelope contract, not
    # the shared row predicate. The ledger_record clauses below mirror the shared
    # `row_qualifies` (Rust/Python) clause for clause, sourcing every constant
    # from $predicate_file so a JSON tightening moves this consumer too.
    if jq -e \
        --arg sha "$sha" --arg repo "$repo" \
        --arg req_profile "$p_profile" \
        --arg req_selection "$p_selection" \
        --arg req_result "$p_result" \
        --argjson req_commit_anchored "$p_commit_anchored" \
        --argjson req_tree_dirty "$p_tree_dirty" \
        --argjson req_failures_max "$p_failures_max" \
        --argjson exec_min "$p_exec_min" \
        --argjson counts_schema "$p_counts_schema" \
        --argjson cov_schema "$p_cov_schema" \
        --argjson cov_pernode "$p_cov_pernode" \
        --argjson gate_filtered "$p_gate_filtered" '
        .schema_version == 1
        and .repository == $repo
        and .commit == $sha
        and (.run_id == ($sha + "@" + .ledger_record.started_at))
        and (.log_sha256 | test("^[0-9a-f]{64}$"))
        and .ledger_record.commit == $sha
        and .ledger_record.profile == $req_profile
        and .ledger_record.selection_mode == $req_selection
        and .ledger_record.commit_anchored == $req_commit_anchored
        and .ledger_record.tree_dirty == $req_tree_dirty
        and .ledger_record.result == $req_result
        and ((.ledger_record.failures // 0) <= $req_failures_max)
        and .ledger_record.log_file == .source_log_file
        and (.durable_log_file | startswith("/"))
        # Universal zero-execution guard: a demonstrated zero-test run is never
        # a full green, at any schema (mirrors `executed_tests == Some(0)`).
        and (.ledger_record.executed_tests != 0)
        and (
          (.ledger_record.schema_version // 0) as $schema
          | ($schema >= $counts_schema) as $count_capable
          | ((.ledger_record.executed_tests | type == "number")
             and (.ledger_record.filtered_tests | type == "number")) as $counts_present
          | (.ledger_record.executed_tests | type == "number" and . >= $exec_min) as $executed_ok
          | (if $gate_filtered then (.ledger_record.filtered_tests == 0) else true end) as $filtered_ok
          | $filtered_ok and (
              if $count_capable then
                # Completeness is COVERAGE, not the executed count.
                $executed_ok and (
                  if ($cov_pernode and ($schema >= $cov_schema)) then
                    (.ledger_record.coverage.planned_test_nodes > 0
                     and .ledger_record.coverage.zero_executed_nodes == []
                     and .ledger_record.coverage.absent_nodes == [])
                  else true end)
              elif $counts_present then
                # Old schema predating coverage: nonzero execution is the most
                # it can prove.
                $executed_ok
              else false end)
        )
    ' "$receipt_file" >/dev/null; then
        printf 'receipt_commit=%s receipt_path=%s receipt_sha256=%s\n' \
            "$receipt_commit" "$receipt_path" "$receipt_digest"
        rm -f -- "$receipt_file"
        exit 0
    fi
    rm -f -- "$receipt_file"
done

printf 'no immutable counted local-validation receipt for exact head %s\n' "$sha" >&2
exit 1

