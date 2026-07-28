#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

command -v jq >/dev/null || { echo "test-preload-unload.sh requires jq" >&2; exit 1; }
command -v cc >/dev/null || { echo "test-preload-unload.sh requires a C compiler" >&2; exit 1; }
command -v readelf >/dev/null || { echo "test-preload-unload.sh requires readelf" >&2; exit 1; }
if [[ $(uname -s) != Linux ]]; then
  echo "the preload unload test requires Linux ELF semantics" >&2
  exit 1
fi

target_dir=$(cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
messages=$(mktemp)
trap 'rm -f "$messages"' EXIT
cargo build --release -p shmem-pod-preload-shim \
  --message-format=json-render-diagnostics >"$messages"
shim=$(jq -r \
  'select(.reason == "compiler-artifact" and .target.name == "shmem_pod_preload_shim") | .filenames[]? | select(endswith(".so"))' \
  "$messages" | tail -n 1)
if [[ -z $shim || ! -f $shim ]]; then
  echo "Cargo did not report the preload shim shared object" >&2
  exit 1
fi
if ! readelf -d "$shim" | grep -Eq 'FLAGS_1.*NODELETE'; then
  echo "preload shim lacks ELF DF_1_NODELETE" >&2
  exit 1
fi

harness="$target_dir/preload-nodelete-lifetime"
cc -std=c11 -O2 -Wall -Wextra -Werror \
  -I demos/connector demos/connector/nodelete_lifetime.c -ldl -o "$harness"
"$harness" "$shim"
