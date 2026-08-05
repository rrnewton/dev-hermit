#!/usr/bin/env bash
# Execute the complete receipt authority from one exact, trusted dev-hermit tree.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root=$(cd -- "$script_dir/../.." && pwd)
manifest="$script_dir/receipt-authority-bundle.json"
repo=rrnewton/hermit
sha=
comments=
target_repo=
expected_bundle_sha=
fixture_root=
fixture_tip=

safe_git() {
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
        -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
        -u GIT_COMMON_DIR -u GIT_NAMESPACE -u GIT_CONFIG_COUNT \
        -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 \
        GIT_NO_REPLACE_OBJECTS=1 git "$@"
}

usage() {
    cat >&2 <<'EOF'
Usage: verify_receipt_bundle.sh --sha FULL40 --comments FILE [options]

Options:
  --repo OWNER/REPO          Validation target (default: rrnewton/hermit)
  --target-repo DIR          Existing object store containing the exact target commit
  --expected-bundle-sha SHA  Require this dev-hermit checkout commit
  --fixture-receipts DIR     Test-only immutable-receipt fixture root
  --fixture-branch-tip SHA   Exact fixture validation-receipts branch tip
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha) sha=${2:-}; shift 2 ;;
        --comments) comments=${2:-}; shift 2 ;;
        --repo) repo=${2:-}; shift 2 ;;
        --target-repo) target_repo=${2:-}; shift 2 ;;
        --expected-bundle-sha) expected_bundle_sha=${2:-}; shift 2 ;;
        --fixture-receipts) fixture_root=${2:-}; shift 2 ;;
        --fixture-branch-tip) fixture_tip=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; usage; exit 2 ;;
    esac
done

if [[ $repo != rrnewton/hermit ]] || \
   [[ ! $sha =~ ^[0-9a-f]{40}$ ]] || \
   [[ ! $expected_bundle_sha =~ ^[0-9a-f]{40}$ ]] || \
   [[ -z $comments || ! -r $comments ]] || \
   [[ -n $fixture_root && ! $fixture_tip =~ ^[0-9a-f]{40}$ ]]; then
    usage
    exit 2
fi
if ! command -v jq >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1 || \
   ! command -v rust-script >/dev/null 2>&1; then
    echo 'receipt authority bundle requires jq, git, and rust-script' >&2
    exit 2
fi

bundle_sha=$(safe_git -C "$root" rev-parse HEAD)
if [[ -n $expected_bundle_sha && $bundle_sha != "$expected_bundle_sha" ]]; then
    printf 'receipt authority bundle mismatch: expected %s, checked out %s\n' \
        "$expected_bundle_sha" "$bundle_sha" >&2
    exit 2
fi
mapfile -t required_paths < <(jq -er \
    'select(.schema_version == 1) | .required_paths[]' "$manifest")
if [[ ${#required_paths[@]} -eq 0 ]]; then
    echo 'receipt authority bundle manifest has no required paths' >&2
    exit 2
fi
for relative in "${required_paths[@]}"; do
    if [[ $relative == /* || $relative == *..* ]] || \
       ! safe_git -C "$root" ls-files --error-unmatch -- "$relative" >/dev/null 2>&1 || \
       ! safe_git -C "$root" diff --quiet "$bundle_sha" -- "$relative"; then
        printf 'receipt authority bundle path is missing, untracked, or modified: %s\n' \
            "$relative" >&2
        exit 2
    fi
done

tmp=
cleanup() {
    if [[ -n $tmp && -d $tmp && $tmp == /tmp/* ]]; then
        rm -rf -- "$tmp"
    fi
}
trap cleanup EXIT

if [[ -z $target_repo ]]; then
    tmp=$(mktemp -d /tmp/ci-hub-receipt-target.XXXXXX)
    target_repo="$tmp/target.git"
    safe_git init --bare -q "$target_repo"
    fetch=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
        -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
        -u GIT_COMMON_DIR -u GIT_NAMESPACE -u GIT_CONFIG_COUNT \
        -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 GIT_NO_REPLACE_OBJECTS=1 \
        git -C "$target_repo" fetch --quiet --depth=1 --no-tags \
        "https://github.com/${repo}.git" "$sha")
    if command -v with-proxy >/dev/null 2>&1; then
        fetch=(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
            -u GIT_OBJECT_DIRECTORY -u GIT_ALTERNATE_OBJECT_DIRECTORIES \
            -u GIT_COMMON_DIR -u GIT_NAMESPACE -u GIT_CONFIG_COUNT \
            -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 GIT_NO_REPLACE_OBJECTS=1 \
            with-proxy git -C "$target_repo" fetch --quiet --depth=1 --no-tags \
            "https://github.com/${repo}.git" "$sha")
    fi
    if ! timeout --signal=TERM --kill-after=2s 60s "${fetch[@]}"; then
        echo "could not acquire exact target commit $repo@$sha" >&2
        exit 1
    fi
fi
if [[ $(safe_git -C "$target_repo" cat-file -t "$sha" 2>/dev/null || true) != commit ]]; then
    echo "target object is absent or not a commit: $repo@$sha" >&2
    exit 1
fi

args=(--repo "$repo" --sha "$sha" --comments "$comments" \
    --hermit-repo "$target_repo")
if [[ -n $fixture_root ]]; then
    args+=(--fixture-receipts "$fixture_root")
    args+=(--fixture-branch-tip "$fixture_tip")
fi
exec "$script_dir/verify_receipt.sh" "${args[@]}"
