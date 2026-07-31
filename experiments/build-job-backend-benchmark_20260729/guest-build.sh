#!/usr/bin/env bash
set -euo pipefail

archive=${1:?usage: guest-build.sh ARCHIVE JOBS}
jobs=${2:?usage: guest-build.sh ARCHIVE JOBS}
case $jobs in
    ''|*[!0-9]*|0) printf 'error: JOBS must be a positive integer\n' >&2; exit 2 ;;
esac

export LC_ALL=C
export TZ=UTC
export SOURCE_DATE_EPOCH=1609459200
export ZERO_AR_DATE=1

work_dir=/tmp/build-job-backend-benchmark
rm -rf "$work_dir"
mkdir -p "$work_dir"
trap 'rm -rf "$work_dir"' EXIT

/usr/bin/tar --no-same-owner -xzf "$archive" -C "$work_dir"
source_dir=$work_dir/zlib-1.3.1
cd "$source_dir"

CC=/usr/bin/gcc \
CFLAGS="-O2 -fno-ident -fdebug-prefix-map=$source_dir=." \
./configure --static >configure.log 2>&1
/usr/bin/touch -d "@$SOURCE_DATE_EPOCH" Makefile

/usr/bin/make --no-print-directory --silent --assume-old=Makefile -j"$jobs" \
    AR=/usr/bin/ar ARFLAGS=rcD 'RANLIB=/usr/bin/ranlib -D'

printf 'package=zlib-1.3.1 jobs=%s\n' "$jobs"
for artifact in libz.a example minigzip; do
    printf 'artifact_sha256=%s:%s\n' \
        "$artifact" "$(/usr/bin/sha256sum "$artifact" | /usr/bin/cut -d ' ' -f 1)"
done
printf 'example_output_sha256=%s\n' \
    "$(./example | /usr/bin/sha256sum | /usr/bin/cut -d ' ' -f 1)"
