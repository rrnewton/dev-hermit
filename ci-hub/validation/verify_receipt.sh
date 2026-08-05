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
    # Historical ci-hub comments used a service actor in the model slot. Keep
    # that exact tag readable; new comments use one of the four AGENTS.md role
    # forms, with a nonempty model for automated roles.
    def accepted_role_tag:
      . == "[impl agent, ci-hub]"
      or . == "[Human]"
      or (
        (capture("^\\[(?<role>impl agent|adversarial-reviewer agent|coordinator), (?<model>[^]]+)\\]$")? // {})
        | ((.model // "") | test("\\S"))
      );
    [ .[][]?
      | select(.user.login == $owner)
      | (.body // "") as $body
      | ($body | split("\n")[0]) as $role_tag
      | select($role_tag | accepted_role_tag)
      | $body
      | split("\n")[]
      | capture("^<!-- locally-validated-receipt commit=(?<commit>[0-9a-f]{40}) path=(?<path>[^ ]+) sha256=(?<digest>[0-9a-f]{64}) -->$")?
      | . + {role_class: (if $role_tag == "[impl agent, ci-hub]" then "legacy-service" else "current" end)}
    ] | reverse[] | [.commit, .path, .digest, .role_class] | @tsv
' "$comments_file" 2>/dev/null)

gh_cmd=(gh)
if command -v with-proxy >/dev/null 2>&1; then
    gh_cmd=(with-proxy gh)
fi

for candidate in "${candidates[@]}"; do
    IFS=$'\t' read -r receipt_commit receipt_path receipt_digest role_class <<<"$candidate"
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

    if jq -e --arg sha "$sha" --arg repo "$repo" --arg role_class "$role_class" '
        def integer:
          if type == "number" then . == floor else false end;
        def nonempty_string:
          if type == "string" then test("\\S") else false end;
        def selected_identity_valid:
          .digest_algorithm == "sha256"
          and .canonicalization == "serde_json::to_vec(HistoryRow)-v1"
          and (.digest | test("^[0-9a-f]{64}$"));
        def strong_selected_row($sha; $repo):
          .ledger_record as $row
          | $repo == "rrnewton/hermit"
          and ($row.schema_version | integer and . >= 4)
          and (
            $row.repo == "hermit"
            or $row.repo == "rrnewton/hermit"
            or ($row.schema_version == 4 and $row.repo == null)
          )
          and $row.commit == $sha
          and ($row.tree | type == "string" and test("^[0-9A-Fa-f]{40}$"))
          and $row.commit_anchored == true
          and $row.tree_dirty == false
          and $row.profile == "full"
          and $row.selection_mode == "full"
          and $row.result == "pass"
          and $row.raw_result == "pass"
          and $row.exit_code == 0
          and $row.failures == 0
          and ($row.checks | integer and . > 0)
          and ($row.gates_run | integer and . >= $row.gates_expected)
          and ($row.gates_expected | integer and . > 0)
          and $row.checks == $row.gates_run
          and ($row.gates | type == "array" and length == $row.gates_run)
          and all($row.gates[];
            (.name | nonempty_string)
            and .result == "pass"
            and .exit_code == 0
          )
          and ($row.executed_tests | integer and . > 0)
          and ($row.filtered_tests | integer and . >= 0)
          and ($row.started_at | nonempty_string)
          and ($row.finished_at | nonempty_string)
          and ($row.host | nonempty_string)
          and ($row.slot | nonempty_string)
          and ($row.log_file | nonempty_string)
          and (
            if $row.coverage == null
            then $row.schema_version == 4
            else (
              ($row.coverage.planned_test_nodes | integer and . > 0)
              and $row.coverage.zero_executed_nodes == []
              and $row.coverage.absent_nodes == []
            )
            end
          );
        .schema_version == 1
        and .repository == $repo
        and .commit == $sha
        and (.run_id == ($sha + "@" + .ledger_record.started_at))
        and (.log_sha256 | test("^[0-9a-f]{64}$"))
        and .ledger_record.commit == $sha
        and .ledger_record.profile == "full"
        and .ledger_record.selection_mode == "full"
        and .ledger_record.commit_anchored == true
        and .ledger_record.tree_dirty == false
        and .ledger_record.result == "pass"
        and .ledger_record.failures == 0
        and (.ledger_record.executed_tests | type == "number" and . > 0)
        and .ledger_record.log_file == .source_log_file
        and (.durable_log_file | startswith("/"))
        and (
          if $role_class == "legacy-service" and (has("selected_receipt_identity") | not)
            then true
            else (.selected_receipt_identity | selected_identity_valid)
            end
        )
        and ($role_class == "legacy-service" or strong_selected_row($sha; $repo))
        and (
          (.ledger_record.schema_version // 0) < 5
          or (
            .ledger_record.coverage.planned_test_nodes > 0
            and .ledger_record.coverage.zero_executed_nodes == []
            and .ledger_record.coverage.absent_nodes == []
          )
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
