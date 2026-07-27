#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
command -v jq >/dev/null || { echo "run-preload-demo.sh requires jq" >&2; exit 1; }

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "the preload demo currently requires Linux x86-64" >&2
  exit 1
fi

target_dir=$(cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
artifact_dir="$target_dir/preload-demo"
mkdir -p "$artifact_dir"

build_messages=$(mktemp)
trap 'rm -f "$build_messages"' EXIT
if ! cargo build --release \
  -p shmem-pod-image-compiler \
  -p shmem-pod-preload-shim \
  -p shmem-pod-preload-guest \
  -p shmem-pod-preload-host \
  --message-format=json-render-diagnostics >"$build_messages"; then
  jq -r 'select(.reason == "compiler-message") | .message.rendered // empty' \
    "$build_messages" >&2
  exit 1
fi
jq -r 'select(.reason == "compiler-message") | .message.rendered // empty' \
  "$build_messages" >&2

cargo_executable() {
  local target=$1
  local path
  path=$(jq -r --arg target "$target" \
    'select(.reason == "compiler-artifact" and .target.name == $target) | .executable // empty' \
    "$build_messages" | tail -n 1)
  if [[ -z $path || ! -x $path ]]; then
    echo "Cargo did not report executable target $target" >&2
    exit 1
  fi
  printf '%s\n' "$path"
}

cargo_shared_library() {
  local target=$1
  local path
  path=$(jq -r --arg target "$target" \
    'select(.reason == "compiler-artifact" and .target.name == $target) | .filenames[]? | select(endswith(".so"))' \
    "$build_messages" | tail -n 1)
  if [[ -z $path || ! -f $path ]]; then
    echo "Cargo did not report shared-library target $target" >&2
    exit 1
  fi
  printf '%s\n' "$path"
}

compiler=$(cargo_executable shmem-pod-image-compiler)
host=$(cargo_executable shmem-pod-preload-host)
guest=$(cargo_executable shmem-pod-preload-guest)
shim=$(cargo_shared_library shmem_pod_preload_shim)
compiler_args=(
  --source poc/code/src/lib.rs
  --sdk-manifest Cargo.toml
  --sdk-source src/lib.rs
  --sdk-rlib "$artifact_dir/libshmem_pod.rlib"
  --linker-script poc/code/pod.ld
  --output "$artifact_dir/pod.bin"
  --object "$artifact_dir/pod.o"
  --elf "$artifact_dir/pod.elf"
  --manifest "$artifact_dir/pod.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  compiler_args+=(--rustc "$POD_RUSTC")
fi
"$compiler" "${compiler_args[@]}"

artifact_sha256=$(sed -n 's/^artifact_sha256=//p' "$artifact_dir/pod.manifest")
if [[ ! $artifact_sha256 =~ ^[0-9a-f]{64}$ ]]; then
  echo "compiler manifest lacks a valid artifact SHA-256" >&2
  exit 1
fi

"$host" \
  --image "$artifact_dir/pod.bin" \
  --sha256 "$artifact_sha256" \
  --shim "$shim" \
  --guest "$guest" \
  --depth "${POD_DEPTH:-2}" \
  --fanout "${POD_FANOUT:-2}" \
  --threads "${POD_THREADS:-2}" \
  --calls "${POD_CALLS:-100}"
