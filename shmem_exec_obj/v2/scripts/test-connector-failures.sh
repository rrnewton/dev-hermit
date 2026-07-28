#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "connector failure tests currently require Linux x86-64" >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

expect_failure() {
  local name=$1
  local expected=$2
  shift 2
  if POD_FAULT=bad-context-digest "$@" >"$work/$name.log" 2>&1; then
    echo "$name unexpectedly accepted a context with the wrong digest" >&2
    cat "$work/$name.log" >&2
    exit 1
  fi
  if ! grep -Fq "$expected" "$work/$name.log"; then
    echo "$name failed without the expected diagnostic: $expected" >&2
    cat "$work/$name.log" >&2
    exit 1
  fi
  echo "$name rejected bad-context-digest as expected"
}

expect_failure preload "artifact authentication failed" \
  ./scripts/run-preload-demo.sh
expect_failure ptrace "remote bootstrap returned status -3" \
  ./scripts/run-ptrace-demo.sh
