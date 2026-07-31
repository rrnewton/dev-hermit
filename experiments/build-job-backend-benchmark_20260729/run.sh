#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
workspace_root=$(cd "$script_dir/../.." && pwd)

backend=ptrace
jobs=8
mode=verify
hermit=$workspace_root/hermit/target/release/hermit
archive=$workspace_root/ignored/build-job-backend-benchmark/cache/zlib-1.3.1.tar.gz
archive_sha256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23

usage() {
    cat <<'EOF'
Usage: run.sh [OPTIONS]

  --backend NAME   Hermit backend (default: ptrace)
  --jobs N         Parallel make jobs (default: 8)
  --hermit PATH    Hermit release binary
  --archive PATH   Pinned zlib-1.3.1 archive
  --mode MODE      verify (four builds) or evidence (one strict build)
  -h, --help       Show this help

verify mode runs the complete `hermit run --strict --verify` command twice
through scripts/detached-verify.rs. Swap only --backend for head-to-head runs.
EOF
}

while (($#)); do
    case $1 in
        --backend) backend=${2:?missing backend}; shift 2 ;;
        --jobs) jobs=${2:?missing jobs}; shift 2 ;;
        --hermit) hermit=${2:?missing Hermit path}; shift 2 ;;
        --archive) archive=${2:?missing archive path}; shift 2 ;;
        --mode) mode=${2:?missing mode}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'error: unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

case $jobs in
    ''|*[!0-9]*|0) printf 'error: --jobs must be a positive integer\n' >&2; exit 2 ;;
esac
[[ $mode == verify || $mode == evidence ]] || {
    printf 'error: --mode must be verify or evidence\n' >&2
    exit 2
}
[[ -x $hermit ]] || { printf 'error: Hermit binary is not executable: %s\n' "$hermit" >&2; exit 2; }
[[ -f $archive ]] || { printf 'error: run %s/prepare.sh first\n' "$script_dir" >&2; exit 2; }

hermit=$(readlink -f "$hermit")
archive=$(readlink -f "$archive")
printf '%s  %s\n' "$archive_sha256" "$archive" | sha256sum --check --status || {
    printf 'error: zlib archive digest mismatch\n' >&2
    exit 2
}

name=build-job-${backend}-j${jobs}-${mode}
hermit_command=(
    "$hermit" run
    --backend "$backend"
    --strict
    --bind "$archive:/tmp/zlib-1.3.1.tar.gz"
)
if [[ $mode == verify ]]; then
    hermit_command+=(--verify)
fi
hermit_command+=(-- "$script_dir/guest-build.sh" /tmp/zlib-1.3.1.tar.gz "$jobs")

wrapper=$workspace_root/scripts/detached-verify.rs
common=(--name "$name" --tail 14 --grep Success --grep deterministic --grep artifact_sha256 --grep divergence --grep error)
if [[ $mode == verify ]]; then
    "$wrapper" verify-twice "${common[@]}" -- "${hermit_command[@]}"
else
    "$wrapper" run "${common[@]}" -- "${hermit_command[@]}"
fi
