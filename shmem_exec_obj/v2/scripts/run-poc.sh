#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
command -v jq >/dev/null || { echo "run-poc.sh requires jq" >&2; exit 1; }

target_dir=$(cargo metadata --format-version 1 --no-deps | jq -er .target_directory)
artifact_dir="$target_dir/pod-image"
mkdir -p "$artifact_dir"

cargo test --workspace --all-features
build_messages=$(mktemp)
trap 'rm -f "$build_messages"' EXIT
if ! cargo build --release --workspace --all-features \
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

compiler=$(cargo_executable shmem-pod-image-compiler)
host=$(cargo_executable shmem-pod-image-host)

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

run_negative() {
  local name=$1
  local source=$2
  local expected=$3
  local args=(
    --source "$source"
    --sdk-manifest Cargo.toml
    --sdk-source src/lib.rs
    --sdk-rlib "$artifact_dir/lib${name}_sdk.rlib"
    --linker-script poc/code/pod.ld
    --output "$artifact_dir/${name}.bin"
    --object "$artifact_dir/${name}.o"
    --elf "$artifact_dir/${name}.elf"
    --manifest "$artifact_dir/${name}.manifest"
  )
  if [[ -n ${POD_RUSTC:-} ]]; then
    args+=(--rustc "$POD_RUSTC")
  fi
  if "$compiler" "${args[@]}" \
    >"$artifact_dir/${name}.stdout" 2>"$artifact_dir/${name}.stderr"; then
    echo "compiler accepted forbidden fixture ${name}" >&2
    exit 1
  fi
  grep -Eq "$expected" "$artifact_dir/${name}.stderr"
  echo "PASS compiler rejected ${name}"
}

run_negative \
  outside-addend \
  poc/compiler/tests/fixtures/outside-addend.rs \
  'effective target .* outside \.pod'
run_negative \
  absolute \
  poc/compiler/tests/fixtures/absolute.rs \
  'forbidden absolute relocation'
run_negative \
  undefined \
  poc/compiler/tests/fixtures/undefined.rs \
  'undefined symbol: shmem_pod_missing_dependency'

provenance_args=(
  --source poc/code/src/lib.rs
  --sdk-manifest Cargo.toml
  --sdk-source poc/code/src/lib.rs
  --sdk-rlib "$artifact_dir/libprovenance_sdk.rlib"
  --linker-script poc/code/pod.ld
  --output "$artifact_dir/provenance.bin"
  --object "$artifact_dir/provenance.o"
  --elf "$artifact_dir/provenance.elf"
  --manifest "$artifact_dir/provenance.manifest"
)
if [[ -n ${POD_RUSTC:-} ]]; then
  provenance_args+=(--rustc "$POD_RUSTC")
fi
if "$compiler" "${provenance_args[@]}" \
  >"$artifact_dir/provenance.stdout" 2>"$artifact_dir/provenance.stderr"; then
  echo "compiler accepted mismatched SDK provenance" >&2
  exit 1
fi
grep -q 'SDK crate root must be' "$artifact_dir/provenance.stderr"
echo "PASS compiler rejected mismatched SDK provenance"

"$compiler" "${compiler_args[@]}"

artifact_sha256=$(sed -n 's/^artifact_sha256=//p' "$artifact_dir/pod.manifest")
if [[ ! $artifact_sha256 =~ ^[0-9a-f]{64}$ ]]; then
  echo "compiler manifest lacks a valid artifact SHA-256" >&2
  exit 1
fi
"$host" \
  --image "$artifact_dir/pod.bin" \
  --sha256 "$artifact_sha256" \
  --workers "${POD_WORKERS:-2}" \
  --threads "${POD_THREADS:-2}" \
  --iterations "${POD_ITERATIONS:-500}"
