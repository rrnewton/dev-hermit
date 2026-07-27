#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
command -v jq >/dev/null || { echo "run-poc.sh requires jq" >&2; exit 1; }

target_dir=$(cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
artifact_dir="$target_dir/pod"
mkdir -p "$artifact_dir"

cargo test --workspace
build_messages=$(mktemp)
trap 'rm -f "$build_messages"' EXIT
if ! cargo build --release --workspace --message-format=json-render-diagnostics \
  >"$build_messages"; then
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

compiler=$(cargo_executable pod-compiler)
host=$(cargo_executable pod-host)
guest=$(cargo_executable pod-guest)
preload=$(cargo_shared_library pod_preload)

compiler_args=(
  --source pod-code/src/lib.rs
  --output "$artifact_dir/pod.bin"
  --object "$artifact_dir/pod.o"
  --manifest "$artifact_dir/pod.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  compiler_args+=(--rustc "$POD_RUSTC")
fi
"$compiler" "${compiler_args[@]}"

forbidden_args=(
  --source fixtures/relocating-pod.rs
  --output "$artifact_dir/forbidden.bin"
  --object "$artifact_dir/forbidden.o"
  --manifest "$artifact_dir/forbidden.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  forbidden_args+=(--rustc "$POD_RUSTC")
fi
if "$compiler" "${forbidden_args[@]}" \
  >"$artifact_dir/forbidden.stdout" 2>"$artifact_dir/forbidden.stderr"; then
  echo "relocation gate accepted a forbidden external call" >&2
  exit 1
fi
grep -q "contains a relocation" "$artifact_dir/forbidden.stderr"
echo "PASS compiler rejected a method containing an external relocation"

unsafe_args=(
  --source fixtures/unsafe-absolute-pod.rs
  --output "$artifact_dir/unsafe-absolute.bin"
  --object "$artifact_dir/unsafe-absolute.o"
  --manifest "$artifact_dir/unsafe-absolute.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  unsafe_args+=(--rustc "$POD_RUSTC")
fi
"$compiler" "${unsafe_args[@]}" >/dev/null
echo "PASS relocation gate demonstrated it is not a code-safety verifier"

for mode in coarse fine atomic; do
  "$host" \
    --image "$artifact_dir/pod.bin" \
    --instance "$artifact_dir/${mode}.instance" \
    --preload "$preload" \
    --guest "$guest" \
    --mode "$mode" \
    --depth "${POD_DEPTH:-2}" \
    --fanout "${POD_FANOUT:-3}" \
    --threads "${POD_THREADS:-2}" \
    --iterations "${POD_ITERATIONS:-10000}"
done
