#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

cargo test --workspace
cargo build --release --workspace

compiler_args=(
  --source pod-code/src/lib.rs
  --output target/pod/pod.bin
  --object target/pod/pod.o
  --manifest target/pod/pod.manifest
)
if [[ -n ${POD_RUSTC:-} ]]; then
  compiler_args+=(--rustc "$POD_RUSTC")
fi
target/release/pod-compiler "${compiler_args[@]}"

forbidden_args=(
  --source fixtures/relocating-pod.rs
  --output target/pod/forbidden.bin
  --object target/pod/forbidden.o
  --manifest target/pod/forbidden.manifest
)
if [[ -n ${POD_RUSTC:-} ]]; then
  forbidden_args+=(--rustc "$POD_RUSTC")
fi
if target/release/pod-compiler "${forbidden_args[@]}" \
  >target/pod/forbidden.stdout 2>target/pod/forbidden.stderr; then
  echo "relocation gate accepted a forbidden external call" >&2
  exit 1
fi
grep -q "contains a relocation" target/pod/forbidden.stderr
echo "PASS compiler rejected a method containing an external relocation"

unsafe_args=(
  --source fixtures/unsafe-absolute-pod.rs
  --output target/pod/unsafe-absolute.bin
  --object target/pod/unsafe-absolute.o
  --manifest target/pod/unsafe-absolute.manifest
)
if [[ -n ${POD_RUSTC:-} ]]; then
  unsafe_args+=(--rustc "$POD_RUSTC")
fi
target/release/pod-compiler "${unsafe_args[@]}" >/dev/null
echo "PASS relocation gate demonstrated it is not a code-safety verifier"

for mode in coarse fine atomic; do
  target/release/pod-host \
    --image target/pod/pod.bin \
    --instance "target/pod/${mode}.instance" \
    --preload target/release/libpod_preload.so \
    --guest target/release/pod-guest \
    --mode "$mode" \
    --depth "${POD_DEPTH:-2}" \
    --fanout "${POD_FANOUT:-3}" \
    --threads "${POD_THREADS:-2}" \
    --iterations "${POD_ITERATIONS:-10000}"
done

v2_compiler_args=(
  --source pod-v2-code/src/lib.rs
  --sdk-manifest pod-v2-types/Cargo.toml
  --sdk-source pod-v2-types/src/lib.rs
  --sdk-rlib target/pod-v2/libshmem_pod.rlib
  --linker-script pod-v2-code/pod.ld
  --output target/pod-v2/pod-v2.bin
  --object target/pod-v2/pod-v2.o
  --elf target/pod-v2/pod-v2.elf
  --manifest target/pod-v2/pod-v2.manifest
)
mkdir -p target/pod-v2
if [[ -n ${POD_RUSTC:-} ]]; then
  v2_compiler_args+=(--rustc "$POD_RUSTC")
fi

run_v2_negative() {
  local name=$1
  local source=$2
  local expected=$3
  local args=(
    --source "$source"
    --sdk-manifest pod-v2-types/Cargo.toml
    --sdk-source pod-v2-types/src/lib.rs
    --sdk-rlib "target/pod-v2/lib${name}_sdk.rlib"
    --linker-script pod-v2-code/pod.ld
    --output "target/pod-v2/${name}.bin"
    --object "target/pod-v2/${name}.o"
    --elf "target/pod-v2/${name}.elf"
    --manifest "target/pod-v2/${name}.manifest"
  )
  if [[ -n ${POD_RUSTC:-} ]]; then
    args+=(--rustc "$POD_RUSTC")
  fi
  if target/release/pod-v2-compiler "${args[@]}" \
    >"target/pod-v2/${name}.stdout" 2>"target/pod-v2/${name}.stderr"; then
    echo "V2 compiler accepted forbidden fixture ${name}" >&2
    exit 1
  fi
  grep -Eq "$expected" "target/pod-v2/${name}.stderr"
  echo "PASS V2 compiler rejected ${name}"
}

run_v2_negative \
  outside-addend \
  pod-v2-compiler/tests/fixtures/outside-addend.rs \
  'effective target .* outside \.pod'
run_v2_negative \
  absolute \
  pod-v2-compiler/tests/fixtures/absolute.rs \
  'forbidden absolute relocation'
run_v2_negative \
  undefined \
  pod-v2-compiler/tests/fixtures/undefined.rs \
  'undefined symbol: pod_v2_missing_dependency'

provenance_args=(
  --source pod-v2-code/src/lib.rs
  --sdk-manifest pod-v2-types/Cargo.toml
  --sdk-source pod-v2-code/src/lib.rs
  --sdk-rlib target/pod-v2/libprovenance_sdk.rlib
  --linker-script pod-v2-code/pod.ld
  --output target/pod-v2/provenance.bin
  --object target/pod-v2/provenance.o
  --elf target/pod-v2/provenance.elf
  --manifest target/pod-v2/provenance.manifest
)
if [[ -n ${POD_RUSTC:-} ]]; then
  provenance_args+=(--rustc "$POD_RUSTC")
fi
if target/release/pod-v2-compiler "${provenance_args[@]}" \
  >target/pod-v2/provenance.stdout 2>target/pod-v2/provenance.stderr; then
  echo "V2 compiler accepted mismatched SDK provenance" >&2
  exit 1
fi
grep -q 'SDK crate root must be' target/pod-v2/provenance.stderr
echo "PASS V2 compiler rejected mismatched SDK provenance"

target/release/pod-v2-compiler "${v2_compiler_args[@]}"

v2_sha256=$(sed -n 's/^artifact_sha256=//p' target/pod-v2/pod-v2.manifest)
if [[ ! $v2_sha256 =~ ^[0-9a-f]{64}$ ]]; then
  echo "V2 compiler manifest lacks a valid artifact SHA-256" >&2
  exit 1
fi
target/release/pod-v2-host \
  --image target/pod-v2/pod-v2.bin \
  --sha256 "$v2_sha256" \
  --workers "${POD_V2_WORKERS:-2}" \
  --threads "${POD_V2_THREADS:-2}" \
  --iterations "${POD_V2_ITERATIONS:-500}"
