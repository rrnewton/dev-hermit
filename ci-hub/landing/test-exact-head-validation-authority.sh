#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
authority=$script_dir/exact-head-validation-authority.sh
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
stale=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
comments=$tmp/comments.json
printf '[]\n' >"$comments"

cat >"$tmp/ci-hub" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
command=$1
shift
repo= sha=
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) repo=$2; shift 2 ;;
        --sha) sha=$2; shift 2 ;;
        --json) shift ;;
        *) shift ;;
    esac
done
observed_sha=${STALE_SHA:-$sha}
if [[ $command == validate-status ]]; then
    case ${LOCAL_STATE:-no_result} in
        green)
            printf '{"repo":"%s","sha":"%s","verdict":"VALIDATED","exit_code":0,"qualifying_count":1,"newest_qualifying":{"sha":"%s"}}\n' "$repo" "$observed_sha" "$observed_sha"
            exit 0 ;;
        red)
            printf '{"repo":"%s","sha":"%s","verdict":"FAILED","exit_code":3,"qualifying_count":0,"newest_qualifying":null}\n' "$repo" "$observed_sha"
            exit 3 ;;
        *)
            printf '{"repo":"%s","sha":"%s","verdict":"NO-RESULT","exit_code":4,"qualifying_count":0,"newest_qualifying":null}\n' "$repo" "$observed_sha"
            exit 4 ;;
    esac
fi
case ${HOSTED_STATE:-no_result} in
    green)
        printf '{"authority":"github-actions-exact-head-jobs","repo":"%s","sha":"%s","state":"green","required_positive_count":1,"positive_count":1,"jobs":[{"state":"green","run_id":1,"job_id":2}]}\n' "$repo" "$observed_sha"
        exit 0 ;;
    red)
        printf '{"authority":"github-actions-exact-head-jobs","repo":"%s","sha":"%s","state":"red","required_positive_count":1,"positive_count":0,"jobs":[{"state":"red","run_id":1,"job_id":2}]}\n' "$repo" "$observed_sha"
        exit 3 ;;
    partial)
        printf '{"authority":"github-actions-exact-head-jobs","repo":"%s","sha":"%s","state":"no_result","required_positive_count":2,"positive_count":1,"jobs":[{"state":"green","run_id":1,"job_id":2},{"state":"no_result","run_id":1,"job_id":3}]}\n' "$repo" "$observed_sha"
        exit 4 ;;
    *)
        printf '{"authority":"github-actions-exact-head-jobs","repo":"%s","sha":"%s","state":"no_result","required_positive_count":1,"positive_count":0,"jobs":[]}\n' "$repo" "$observed_sha"
        exit 4 ;;
esac
SH
cat >"$tmp/verify-receipt" <<'SH'
#!/usr/bin/env bash
exit "${RECEIPT_RC:-0}"
SH
chmod +x "$tmp/ci-hub" "$tmp/verify-receipt"

run_case() {
    local expected=$1 label=$2
    shift 2
    local rc=0 output
    output=$(env CI_HUB_BIN="$tmp/ci-hub" VERIFY_RECEIPT_BIN="$tmp/verify-receipt" \
        "$@" "$authority" --repo rrnewton/hermit --sha "$sha") || rc=$?
    if [[ $rc -ne $expected ]]; then
        printf 'FAIL: %s expected rc=%s got rc=%s output=%s\n' \
            "$label" "$expected" "$rc" "$output" >&2
        exit 1
    fi
}

run_case 0 "local exact-head positive" env LOCAL_STATE=green HOSTED_STATE=no_result
run_case 0 "hosted exact-head positive" env LOCAL_STATE=no_result HOSTED_STATE=green
run_case 3 "local genuine red blocks hosted green" env LOCAL_STATE=red HOSTED_STATE=green
run_case 3 "hosted genuine red blocks local green" env LOCAL_STATE=green HOSTED_STATE=red
run_case 4 "both authorities no-result" env LOCAL_STATE=no_result HOSTED_STATE=no_result
run_case 4 "partial hosted set is no-result" env LOCAL_STATE=no_result HOSTED_STATE=partial
run_case 4 "stale local evidence is refused" env LOCAL_STATE=green HOSTED_STATE=no_result STALE_SHA="$stale"
run_case 4 "stale hosted evidence is refused" env LOCAL_STATE=no_result HOSTED_STATE=green STALE_SHA="$stale"

rc=0
env CI_HUB_BIN="$tmp/ci-hub" VERIFY_RECEIPT_BIN="$tmp/verify-receipt" \
    LOCAL_STATE=green HOSTED_STATE=no_result RECEIPT_RC=1 \
    "$authority" --repo rrnewton/hermit --sha "$sha" --comments "$comments" \
    >/dev/null || rc=$?
[[ $rc -eq 4 ]] || { echo "FAIL: unbound local receipt passed final boundary" >&2; exit 1; }
env CI_HUB_BIN="$tmp/ci-hub" VERIFY_RECEIPT_BIN="$tmp/verify-receipt" \
    LOCAL_STATE=green HOSTED_STATE=green RECEIPT_RC=1 \
    "$authority" --repo rrnewton/hermit --sha "$sha" --comments "$comments" \
    >/dev/null

echo "PASS: local/hosted OR positives=3; red/partial/no-result/stale/unbound negatives=7"
