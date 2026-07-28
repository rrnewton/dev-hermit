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
  local fault=$2
  local expected=$3
  shift 3
  if POD_FAULT="$fault" "$@" >"$work/$name.log" 2>&1; then
    echo "$name unexpectedly accepted injected fault $fault" >&2
    cat "$work/$name.log" >&2
    exit 1
  fi
  if ! grep -Fq "$expected" "$work/$name.log"; then
    echo "$name failed without the expected diagnostic: $expected" >&2
    cat "$work/$name.log" >&2
    exit 1
  fi
  echo "$name rejected $fault as expected: $expected"
}

expect_failure preload bad-context-digest "artifact authentication failed" \
  ./scripts/run-preload-demo.sh
expect_failure ptrace-artifact-transport unsealed-artifact \
  "remote bootstrap returned status -2" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-code-transport short-code \
  "remote bootstrap returned status -2" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-state-transport read-only-state \
  "remote bootstrap returned status -2" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-artifact-identity bad-context-digest \
  "remote bootstrap returned status -3" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-code-identity bad-code-bytes \
  "remote bootstrap returned status -3" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-api-identity bad-api-fingerprint \
  "remote bootstrap returned status -3" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-state-identity bad-state-generation \
  "remote bootstrap returned status -3" ./scripts/run-ptrace-demo.sh
expect_failure ptrace-runtime-mapping fixed-code-collision \
  "remote bootstrap returned status -6" \
  ./scripts/run-ptrace-demo.sh
