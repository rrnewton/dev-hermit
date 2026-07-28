#!/usr/bin/env bash
# Reject owner- and machine-specific paths in tracked build/run files.

set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
readonly ROOT_DIR

if (($#)); then
    repos=("$@")
else
    repos=("$ROOT_DIR")
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
            | */third-party/* | */vendor/* | */worktrees/* \
            | */scripts/check-portable-paths.sh/)
            return 0 ;;
        *) return 1 ;;
    esac
}

found=0
for repo in "${repos[@]}"; do
    [[ -e "$repo/.git" ]] || continue
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
            awk '
                {
                    probe = tolower($0)
                    gsub(/\/(home|users)\/(user|test|example)([^[:alnum:]_.-]|$)/,
                         "/generic/", probe)
                    if (probe ~ /\/(home|users)\/[[:alnum:]_.-]+([^[:alnum:]_.-]|$)/ ||
                        probe ~ /(^|[^[:alnum:]_])newton([^[:alnum:]_]|$)/ ||
                        probe ~ /devbig[[:alnum:]._-]*/ ||
                        probe ~ /\/usr\/local\/bin\//) {
                        print FNR ":" $0
                    }
                }
            ' "$file"
        )
    done < <(git -C "$repo" ls-files -z)
done

if ((found)); then
    echo "portability check failed: replace literal homes/hosts with HOME, repo-relative paths, PATH lookup, or explicit environment overrides" >&2
    exit 1
fi

echo "Portability path check passed."
