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
