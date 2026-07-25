#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

cargo build --release --workspace
target/release/pod-compiler \
  --source pod-code/src/lib.rs \
  --output target/pod/pod.bin \
  --object target/pod/pod.o \
  --manifest target/pod/pod.manifest

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
