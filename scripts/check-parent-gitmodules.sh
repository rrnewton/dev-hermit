#!/usr/bin/env bash
set -euo pipefail

root=$(git -C "$(dirname -- "${BASH_SOURCE[0]}")/.." rev-parse --show-toplevel)
cd "$root"

expected=(
    "hermit|hermit|https://github.com/rrnewton/hermit.git"
    "reverie|reverie|https://github.com/rrnewton/reverie.git"
    "liteinst2|liteinst2|https://github.com/rrnewton/liteinst2.git"
)

mapfile -t configured < <(git config -f .gitmodules --get-regexp '^submodule\..*\.path$')
if [[ ${#configured[@]} -ne ${#expected[@]} ]]; then
    echo "ERROR: .gitmodules must contain exactly ${#expected[@]} parent submodules." >&2
    exit 1
fi

for spec in "${expected[@]}"; do
    IFS='|' read -r name path url <<<"$spec"
    actual_path=$(git config -f .gitmodules --get "submodule.$name.path" || true)
    actual_url=$(git config -f .gitmodules --get "submodule.$name.url" || true)
    update=$(git config -f .gitmodules --get "submodule.$name.update" || true)
    mode=$(git ls-files --stage -- "$path" | awk 'NR == 1 { print $1 }')

    [[ "$actual_path" == "$path" ]] || {
        echo "ERROR: submodule $name path is '$actual_path', expected '$path'." >&2
        exit 1
    }
    [[ "$actual_url" == "$url" ]] || {
        echo "ERROR: submodule $name URL is '$actual_url', expected '$url'." >&2
        exit 1
    }
    [[ "$update" == checkout ]] || {
        echo "ERROR: submodule $name must set update = checkout." >&2
        exit 1
    }
    [[ "$mode" == 160000 ]] || {
        echo "ERROR: $path is not recorded as a gitlink." >&2
        exit 1
    }
done

echo "Parent submodule policy is valid: all ${#expected[@]} gitlinks default to checkout."
