#!/usr/bin/env bash
# Reject owner- and machine-specific paths in tracked build/run files.

set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly ROOT_DIR

if (($#)); then
    repos=("$@")
else
    repos=(
        "$ROOT_DIR"
        "$ROOT_DIR/hermit"
        "$ROOT_DIR/reverie"
        "$ROOT_DIR/liteinst2"
    )
fi

is_build_file() {
    case "$1" in
        *.sh | *.bash | *.rs | *.py | *.c | *.h | *.cc | *.cpp | *.cxx | *.go \
            | *.pl | *.rb | *.toml | *.yml | *.yaml | *.config | *.conf | *.cfg \
            | *.mk | Makefile | Makefile.* | */Makefile | */Makefile.* \
            | Dockerfile | */Dockerfile | Containerfile | */Containerfile \
            | .github/* | .claude/* | .llms/*)
            return 0 ;;
        *) return 1 ;;
    esac
}

is_excluded() {
    case "/$1/" in
        */.git/* | */ignored/* | */experiments/* | */scratch/* | */target/* \
            | */ai_docs/* | */docs/progress-reports/* \
            | */third-party/* | */vendor/* | */worktrees/* \
            | */scripts/check-portable-paths.sh/)
            return 0 ;;
        *) return 1 ;;
    esac
}

scan_file() {
    local path=$1
    awk -v path="$path" '
        function is_comment(line) {
            return line ~ /^[[:space:]]*(#|\/\/|\/\*|\*|<!--)/
        }

        function is_test_path(name) {
            return name ~ /(^|\/)(test|tests|testdata|fixtures)(\/|$)/ ||
                   name ~ /(^|\/)validate[.]sh$/
        }

        {
            original = $0
            probe = tolower(original)
            gsub(/\/(home|users)\/(user|test|example)([^[:alnum:]_.-]|$)/,
                 "/generic/", probe)

            owner_path = probe ~ /\/(home|users)\/[[:alnum:]_.-]+([^[:alnum:]_.-]|$)/
            owner_name = probe ~ /(^|[^[:alnum:]_])newton([^[:alnum:]_]|$)/
            owner_host = probe ~ /devbig[[:alnum:]._-]*/
            author_header = owner_name && is_comment(original) &&
                            probe ~ /(author|copyright|<[^>]+@[^>]+>)/

            if (owner_path || owner_host || (owner_name && !author_header)) {
                print FNR ":" original
                next
            }

            local_bin = probe ~ /\/usr\/local\/bin\//
            optional_probe = probe ~ /(read_link|symlink)[[:space:]]*\(/ ||
                             probe ~ /\[\[[^]]*-[[:space:]]*x[[:space:]]/
            if (local_bin && !is_comment(original) &&
                !is_test_path(path) && !optional_probe) {
                print FNR ":" original
            }
        }
    '
}

self_test() {
    local hits

    hits=$(printf '%s\n' 'cache="/home/ci-portability-owner/.cache"' |
        scan_file scripts/build.sh)
    [[ -n $hits ]] || {
        echo "portability self-test failed to reject an owner home" >&2
        return 1
    }

    hits=$(printf '%s\n' 'tool="/usr/local/bin/private-tool"' |
        scan_file scripts/build.sh)
    [[ -n $hits ]] || {
        echo "portability self-test failed to reject a live /usr/local/bin path" >&2
        return 1
    }

    hits=$(printf '%s\n' \
        'let tools = ["/usr/bin/python3", "/usr/local/bin/python3"];' |
        scan_file tests/tool_lookup.rs)
    [[ -z $hits ]] || {
        echo "portability self-test rejected a test PATH fallback" >&2
        return 1
    }

    hits=$(printf '%s\n' \
        '/* Author: Ryan Newton <rrnewton@example.com> */' |
        scan_file tests/author.c)
    [[ -z $hits ]] || {
        echo "portability self-test rejected an author header" >&2
        return 1
    }

    is_excluded ai_docs/measurements/reproduce.sh || {
        echo "portability self-test rejected historical provenance" >&2
        return 1
    }
}

self_test
found=0
for repo in "${repos[@]}"; do
    if [[ ! -e "$repo/.git" ]]; then
        echo "portability check failed: repository is not initialized: $repo" >&2
        exit 1
    fi
    repo_label=$(basename "$repo")
    while IFS= read -r -d '' path; do
        is_excluded "$path" && continue
        file="$repo/$path"
        [[ -f $file ]] || continue
        if ! is_build_file "$path" && [[ ! -x $file ]]; then
            continue
        fi

        while IFS= read -r hit; do
            printf '%s/%s:%s\n' "$repo_label" "$path" "$hit"
            found=1
        done < <(
            scan_file "$path" <"$file"
        )
    done < <(git -C "$repo" ls-files -z)
done

if ((found)); then
    echo "portability check failed: replace literal homes/hosts with HOME, repo-relative paths, PATH lookup, or explicit environment overrides" >&2
    exit 1
fi

echo "Portability path check passed."
