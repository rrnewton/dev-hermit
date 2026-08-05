#!/usr/bin/env bash
# Verify that an evidence comment resolves to an immutable, counted receipt.
set -uo pipefail

receipt_repo=rrnewton/dev-hermit
receipt_branch=validation-receipts
repo=rrnewton/hermit
sha=
comments_file=
fixture_root=
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)
ci_hub="$root/ci-hub/ci-hub"
hermit_repo="$root/hermit"

usage() {
    cat >&2 <<'EOF'
Usage: verify-local-validation-receipt.sh --sha SHA --comments FILE [options]

Options:
  --repo OWNER/REPO       PR repository (default: rrnewton/hermit)
  --hermit-repo DIR       Checkout/object store containing exact Hermit SHA
  --fixture-receipts DIR  Read DIR/<receipt-commit>/<receipt-path> instead of GitHub
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha) sha=${2:-}; shift 2 ;;
        --comments) comments_file=${2:-}; shift 2 ;;
        --repo) repo=${2:-}; shift 2 ;;
        --hermit-repo) hermit_repo=${2:-}; shift 2 ;;
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

    # Only the immutable wrapper contract stays here. The embedded ledger row is
    # handed to the ONE Rust semantic verifier; this shell never certifies it.
    if jq -e \
        --arg sha "$sha" --arg repo "$repo" '
        .schema_version == 1
        and .repository == $repo
        and .commit == $sha
        # Host-in-identity (Req2): the run_id binds sha + started_at + producing
        # host, so the ledger host cannot be swapped without breaking identity.
        and (.ledger_record.host | (type == "string") and (length > 0))
        and (.run_id == ($sha + "@" + .ledger_record.started_at + "@" + .ledger_record.host))
        and (.log_sha256 | test("^[0-9a-f]{64}$"))
        and .ledger_record.commit == $sha
        and .ledger_record.log_file == .source_log_file
        and (.durable_log_file | startswith("/"))
    ' "$receipt_file" >/dev/null; then
        row_ledger=$(mktemp)
        if jq -c '.ledger_record' "$receipt_file" >"$row_ledger" && \
           "$ci_hub" validate-status --repo "$repo" --sha "$sha" \
             --ledger "$row_ledger" --hermit-repo "$hermit_repo" --json \
             >/dev/null 2>&1; then
            printf 'receipt_commit=%s receipt_path=%s receipt_sha256=%s\n' \
                "$receipt_commit" "$receipt_path" "$receipt_digest"
            rm -f -- "$row_ledger" "$receipt_file"
            exit 0
        fi
        rm -f -- "$row_ledger"
    fi
    rm -f -- "$receipt_file"
done

printf 'no immutable counted local-validation receipt for exact head %s\n' "$sha" >&2
exit 1
