#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
workspace_root=$(cd "$script_dir/../.." && pwd)
cache_dir=$workspace_root/ignored/build-job-backend-benchmark/cache
archive=$cache_dir/zlib-1.3.1.tar.gz
archive_url=https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz
archive_sha256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23

mkdir -p "$cache_dir"
if [[ ! -f $archive ]]; then
    command -v with-proxy >/dev/null || {
        printf 'error: with-proxy is required to download zlib\n' >&2
        exit 2
    }
    with-proxy curl -fL --retry 3 -o "$archive" "$archive_url"
fi

printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check --status || {
    printf 'error: unexpected archive digest: %s\n' "$archive" >&2
    exit 2
}
printf 'archive=%s\nsha256=%s\n' "$archive" "$archive_sha256"
