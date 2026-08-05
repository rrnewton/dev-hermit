#!/usr/bin/env bash
# Verify the append-only exact-SHA outcome set and one recomputed pass receipt.
set -uo pipefail

receipt_repo=rrnewton/dev-hermit
receipt_branch=validation-receipts
repo=rrnewton/hermit
sha=
comments_file=
fixture_root=
fixture_tip=
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)
ci_hub="$root/ci-hub/ci-hub"
target_repo="$root/hermit"
finalizer="$root/ci-hub/validate/finalize_receipt.py"

usage() {
    cat >&2 <<'EOF'
Usage: verify_receipt.sh --sha SHA [options]

Options:
  --repo OWNER/REPO       Validation target (currently rrnewton/hermit)
  --hermit-repo DIR       Exact target object store containing SHA
  --comments FILE         Deprecated routing input; never receipt authority
  --fixture-receipts DIR  Test-only receipt-branch fixture root
  --fixture-branch-tip S  Exact fixture branch-tip directory (required with fixture)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha) sha=${2:-}; shift 2 ;;
        --repo) repo=${2:-}; shift 2 ;;
        --hermit-repo) target_repo=${2:-}; shift 2 ;;
        --comments) comments_file=${2:-}; shift 2 ;;
        --fixture-receipts) fixture_root=${2:-}; shift 2 ;;
        --fixture-branch-tip) fixture_tip=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done

if [[ $repo != rrnewton/hermit ]] || [[ ! $sha =~ ^[0-9a-f]{40}$ ]] || \
   [[ -n $comments_file && ! -r $comments_file ]] || \
   [[ -n $fixture_root && ! $fixture_tip =~ ^[0-9a-f]{40}$ ]]; then
    usage
    exit 2
fi

gh_cmd=(gh)
if command -v with-proxy >/dev/null 2>&1; then
    gh_cmd=(with-proxy gh)
fi

tmp=$(mktemp -d /tmp/ci-hub-receipt-verify.XXXXXX)
cleanup() {
    [[ -d $tmp && $tmp == /tmp/ci-hub-receipt-verify.* ]] && rm -rf -- "$tmp"
}
trap cleanup EXIT

fetch_at_tip() {
    local path=$1 output=$2
    if [[ -n $fixture_root ]]; then
        local source_file="$fixture_root/$authority_tip/$path"
        [[ -f $source_file ]] && cp -- "$source_file" "$output"
    else
        "${gh_cmd[@]}" api \
            "repos/${receipt_repo}/contents/${path}?ref=${authority_tip}" \
            --jq .content 2>/dev/null | tr -d '\n' | base64 --decode >"$output"
    fi
}

outcome_prefix="validation-outcomes/${repo}/${sha}/"
if [[ -n $fixture_root ]]; then
    authority_tip=$fixture_tip
    outcome_dir="$fixture_root/$authority_tip/$outcome_prefix"
    [[ -d $outcome_dir ]] || {
        printf 'no immutable validation outcome set for exact head %s\n' "$sha" >&2
        exit 1
    }
    mapfile -t outcome_paths < <(
        find "$outcome_dir" -maxdepth 1 -type f -printf '%f\n' | sort | \
            sed "s#^#$outcome_prefix#"
    )
else
    authority_tip=$("${gh_cmd[@]}" api \
        "repos/${receipt_repo}/git/ref/heads/${receipt_branch}" --jq .object.sha 2>/dev/null) || \
        authority_tip=
    [[ $authority_tip =~ ^[0-9a-f]{40}$ ]] || {
        echo 'cannot resolve canonical validation-receipts branch tip' >&2
        exit 1
    }
    tree_file="$tmp/tree.json"
    if ! "${gh_cmd[@]}" api \
        "repos/${receipt_repo}/git/trees/${authority_tip}?recursive=1" >"$tree_file" 2>/dev/null || \
       ! jq -e '.truncated == false and (.tree | type == "array")' "$tree_file" >/dev/null; then
        echo 'cannot enumerate the complete canonical outcome set' >&2
        exit 1
    fi
    mapfile -t outcome_paths < <(jq -r --arg prefix "$outcome_prefix" '
        .tree[] | select(.type == "blob") | .path | select(startswith($prefix))
    ' "$tree_file" | sort)
fi

if [[ ${#outcome_paths[@]} -eq 0 ]]; then
    printf 'no immutable validation outcome set for exact head %s\n' "$sha" >&2
    exit 1
fi

combined_ledger="$tmp/combined.jsonl"
: >"$combined_ledger"
pass_outcomes=()
index=0
for outcome_path in "${outcome_paths[@]}"; do
    if [[ ! $outcome_path =~ ^${outcome_prefix}([0-9a-f]{64})\.json$ ]]; then
        printf 'malformed exact-head outcome path: %s\n' "$outcome_path" >&2
        exit 1
    fi
    expected_digest=${BASH_REMATCH[1]}
    outcome_file="$tmp/outcome-$index.json"
    if ! fetch_at_tip "$outcome_path" "$outcome_file" || \
       [[ $(sha256sum "$outcome_file" | awk '{print $1}') != "$expected_digest" ]] || \
       ! jq -e --arg repo "$repo" --arg sha "$sha" '
          .schema_version == 1
          and .repository == $repo
          and .commit == $sha
          and (.verdict | IN("VALIDATED", "FAILED", "TRUNCATED", "NEEDS-RERUN", "NO-RESULT", "NOT-VALIDATED"))
          and (.ledger_records | type == "array" and length > 0)
       ' "$outcome_file" >/dev/null; then
        printf 'tampered or malformed exact-head outcome: %s\n' "$outcome_path" >&2
        exit 1
    fi
    jq -c '.ledger_records[]' "$outcome_file" >>"$combined_ledger"
    if [[ $(jq -r .verdict "$outcome_file") == VALIDATED ]]; then
        pass_outcomes+=("$outcome_file")
    fi
    index=$((index + 1))
done

# ONE semantic verifier sees the union of every immutable snapshot. A genuine
# failure in any later or earlier outcome therefore wins monotonically forever.
status_report="$tmp/status.json"
if ! "$ci_hub" validate-status --repo "$repo" --sha "$sha" \
      --ledger "$combined_ledger" --hermit-repo "$target_repo" --json \
      >"$status_report" 2>/dev/null; then
    printf 'immutable outcome set is not green for exact head %s\n' "$sha" >&2
    exit 1
fi
selected_digest=$(jq -er \
    '.newest_qualifying_identity.digest | select(test("^[0-9a-f]{64}$"))' \
    "$status_report") || {
    echo 'canonical verifier returned no selected receipt identity' >&2
    exit 1
}

matched=0
for outcome_file in "${pass_outcomes[@]}"; do
    if [[ $(jq -r '.selected_receipt_identity.digest // ""' "$outcome_file") != "$selected_digest" ]]; then
        continue
    fi
    matched=$((matched + 1))
    receipt_path=$(jq -r '.receipt.path // ""' "$outcome_file")
    receipt_digest=$(jq -r '.receipt.sha256 // ""' "$outcome_file")
    expected_receipt_prefix="validation-receipts/${repo}/${sha}/"
    if [[ ! $receipt_digest =~ ^[0-9a-f]{64}$ ]] || \
       [[ $receipt_path != "$expected_receipt_prefix$receipt_digest.json" ]]; then
        echo 'selected outcome has malformed receipt reference' >&2
        exit 1
    fi
    receipt_file="$tmp/receipt-$matched.json"
    if ! fetch_at_tip "$receipt_path" "$receipt_file" || \
       [[ $(sha256sum "$receipt_file" | awk '{print $1}') != "$receipt_digest" ]] || \
       ! jq -e --arg sha "$sha" --arg repo "$repo" --arg selected "$selected_digest" \
          --arg receipt_repo "$receipt_repo" '
          .schema_version == 1
          and .repository == $repo
          and .commit == $sha
          and (.ledger_record.host | type == "string" and length > 0)
          and .run_id == ($sha + "@" + .ledger_record.started_at + "@" + .ledger_record.host)
          and .ledger_record.commit == $sha
          and .ledger_record.log_file == .source_log_file
          and .ledger_record.source_log_sha256 == .log_sha256
          and .durable_log_repository == $receipt_repo
          and .selected_receipt_identity.digest_algorithm == "sha256"
          and .selected_receipt_identity.canonicalization == "serde_json::to_vec(HistoryRow)-v1"
          and .selected_receipt_identity.digest == $selected
       ' "$receipt_file" >/dev/null; then
        echo 'selected immutable receipt is absent, tampered, or malformed' >&2
        exit 1
    fi
    durable_log_path=$(jq -r .durable_log_path "$receipt_file")
    durable_log_digest=$(jq -r .log_sha256 "$receipt_file")
    expected_log_path="validation-logs/${repo}/${sha}/${durable_log_digest}.log"
    if [[ ! $durable_log_digest =~ ^[0-9a-f]{64}$ ]] || \
       [[ $durable_log_path != "$expected_log_path" ]] || \
       [[ $(jq -r '.receipt.durable_log_path' "$outcome_file") != "$durable_log_path" ]] || \
       [[ $(jq -r '.receipt.durable_log_sha256' "$outcome_file") != "$durable_log_digest" ]]; then
        echo 'selected receipt has malformed durable-log binding' >&2
        exit 1
    fi
    durable_log_file="$tmp/log-$matched"
    if ! fetch_at_tip "$durable_log_path" "$durable_log_file" || \
       [[ $(sha256sum "$durable_log_file" | awk '{print $1}') != "$durable_log_digest" ]]; then
        echo 'selected durable log is absent or digest-mismatched' >&2
        exit 1
    fi
    selected_row="$tmp/selected-$matched.json"
    snapshot="$tmp/snapshot-$matched.json"
    jq -c '.ledger_record' "$receipt_file" >"$selected_row"
    jq -c '.ledger_records' "$outcome_file" >"$snapshot"
    if ! python3 "$finalizer" --repo "$repo" --sha "$sha" \
          --hermit-checkout "$target_repo" --log "$durable_log_file" \
          --ledger-snapshot "$snapshot" --verify-finalized-row "$selected_row" \
          >/dev/null 2>&1; then
        echo 'selected receipt log/finalizer provenance did not recompute exactly' >&2
        exit 1
    fi
done

if [[ $matched -eq 0 ]]; then
    echo 'outcome set has no immutable receipt for the canonical selected pass' >&2
    exit 1
fi

printf 'outcome_tip=%s outcome_count=%s selected_receipt_identity=%s\n' \
    "$authority_tip" "${#outcome_paths[@]}" "$selected_digest"
exit 0
