#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

command -v jq >/dev/null || { echo "run-ptrace-demo.sh requires jq" >&2; exit 1; }
command -v cc >/dev/null || { echo "run-ptrace-demo.sh requires a C compiler" >&2; exit 1; }
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "the ptrace demo currently requires Linux x86-64" >&2
  exit 1
fi

target_dir=$(cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
artifact_dir="$target_dir/ptrace-demo"
mkdir -p "$artifact_dir"

build_messages=$(mktemp)
trap 'rm -f "$build_messages"' EXIT
if ! cargo build --release \
  -p shmem-pod-image-compiler \
  -p shmem-pod-preload-shim \
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
  jq -r --arg target "$target" \
    'select(.reason == "compiler-artifact" and .target.name == $target) | .executable // empty' \
    "$build_messages" | tail -n 1
}

cargo_shared_library() {
  local target=$1
  jq -r --arg target "$target" \
    'select(.reason == "compiler-artifact" and .target.name == $target) | .filenames[]? | select(endswith(".so"))' \
    "$build_messages" | tail -n 1
}

compiler=$(cargo_executable shmem-pod-image-compiler)
host=$(cargo_executable shmem-pod-preload-host)
shim=$(cargo_shared_library shmem_pod_preload_shim)
for artifact in "$compiler" "$host" "$shim"; do
  [[ -n $artifact && -e $artifact ]] || { echo "Cargo did not report a required artifact" >&2; exit 1; }
done
cc -std=c11 -O2 -Wall -Wextra -Werror \
  demos/ptrace/target.c -o "$artifact_dir/ptrace-target"
cc -std=c11 -O2 -Wall -Wextra -Werror \
  demos/connector/context_layout.c -o "$artifact_dir/context-layout"
"$artifact_dir/context-layout"

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

set +e
"$host" \
  --mode ptrace \
  --image "$artifact_dir/pod.bin" \
  --sha256 "$artifact_sha256" \
  --shim "$shim" \
  --guest "$artifact_dir/ptrace-target"
status=$?
set -e
if (( status != 0 )); then
  {
    echo "ptrace bootstrap failed with status $status"
    echo "kernel=$(uname -r)"
    echo "uid=$(id -u)"
    echo "yama_ptrace_scope=$(cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo unavailable)"
    echo "seccomp=$(sed -n 's/^Seccomp:[[:space:]]*//p' /proc/self/status)"
    echo "The demo uses parent-to-child PTRACE_SEIZE and process_vm_writev; LSM,"
    echo "Yama, seccomp, or container policy may deny either operation."
  } >&2
  exit "$status"
fi
