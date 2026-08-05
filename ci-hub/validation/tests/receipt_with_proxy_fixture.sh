#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} == git && ${2:-} == ls-remote ]]; then
    tip=$(<"$CI_HUB_TEST_TIP_FILE")
    printf '%s\trefs/heads/main\n' "$tip"
    exit 0
fi

if [[ ${1:-} != gh ]]; then
    exec "$@"
fi
shift

if [[ ${1:-} == pr && ${2:-} == view ]]; then
    printf '%s\n' "$CI_HUB_TEST_PR_HEAD"
    exit 0
fi
if [[ ${1:-} == pr && ${2:-} == comment ]]; then
    body=
    previous=
    for argument in "$@"; do
        if [[ $previous == --body ]]; then
            body=$argument
            break
        fi
        previous=$argument
    done
    [[ -n $body ]]
    printf '%s' "$body" >"$CI_HUB_TEST_COMMENT_BODY"
    printf 'https://example.invalid/comment/1\n'
    exit 0
fi
if [[ ${1:-} == pr && ${2:-} == edit ]]; then
    exit 0
fi

if [[ ${1:-} == api ]]; then
    joined=" $* "
    if [[ $joined == *" repos/rrnewton/dev-hermit/git/ref/heads/validation-receipts "* ]]; then
        printf '{"object":{"sha":"%s"}}\n' "$CI_HUB_TEST_RECEIPT_COMMIT"
        exit 0
    fi
    if [[ $joined == *" repos/rrnewton/hermit/issues/"*"/comments?per_page=100 "* ]]; then
        if [[ -n ${CI_HUB_TEST_COMMENTS_JSON:-} ]]; then
            cat -- "$CI_HUB_TEST_COMMENTS_JSON"
        else
            printf '[[]]\n'
        fi
        exit 0
    fi

    endpoint=
    content=
    is_put=false
    for argument in "$@"; do
        [[ $argument == --method ]] && is_put=true
        [[ $argument == repos/rrnewton/dev-hermit/contents/* ]] && endpoint=$argument
        [[ $argument == content=* ]] && content=${argument#content=}
    done
    if [[ -n $endpoint && $endpoint == *"?ref=validation-receipts" ]]; then
        exit 1
    fi
    if [[ $is_put == true && -n $endpoint && -n $content ]]; then
        path=${endpoint#repos/rrnewton/dev-hermit/contents/}
        target="$CI_HUB_TEST_RECEIPT_ROOT/$CI_HUB_TEST_RECEIPT_COMMIT/$path"
        mkdir -p -- "$(dirname -- "$target")"
        printf '%s' "$content" | base64 --decode >"$target"
        printf '{"commit":{"sha":"%s"}}\n' "$CI_HUB_TEST_RECEIPT_COMMIT"
        exit 0
    fi
fi

printf 'unexpected fixture command:' >&2
printf ' %q' "$@" >&2
printf '\n' >&2
exit 88
