#!/usr/bin/env bash

set -euo pipefail

DEMO_LABEL="Demo 7: QEMU Linux Strict L2"
DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DEMO_DIR/.." && pwd)"
HERMIT_REPO="${HERMIT_REPO:-$ROOT/hermit}"
HERMIT_BIN="${HERMIT_BIN:-$HERMIT_REPO/target/release/hermit}"
KERNEL_IMAGE="${KERNEL_IMAGE:-/boot/vmlinuz}"
QEMU_BIN="${QEMU_BIN:-$(command -v qemu-system-x86_64 || true)}"
QEMU_L2_PHASE_TIMEOUT_SECONDS="${QEMU_L2_PHASE_TIMEOUT_SECONDS:-360}"
if [[ -z ${OUTPUT_DIR:-} ]]; then
  case "$ROOT/" in
    /tmp/*)
      # Hermit mounts a private tmpfs over /tmp. Keep QEMU's initramfs input
      # visible when validating from a disposable checkout under /tmp.
      OUTPUT_DIR="/var/tmp/hermit-qemu-strict-l2-$UID"
      ;;
    *)
      OUTPUT_DIR="$ROOT/ignored/qemu-linux/strict-l2"
      ;;
  esac
fi

# shellcheck disable=SC1091  # Path is resolved relative to this script at runtime.
source "$DEMO_DIR/lib/display.sh"

demo_header "$DEMO_LABEL"
printf '%s\n' \
  "Hermit boots a real Linux kernel once as an oracle, then repeats that" \
  "exact QEMU boot twice under --strict --verify and compares Detcore logs." \
  "Backend: ptrace. Assurance: L2. Relaxations: none."

if [[ ${DEMO_SKIP_BUILD:-0} != 1 ]]; then
  make --no-print-directory -s -C "$ROOT" check-deps build-hermit
fi

[[ -x $HERMIT_BIN ]] || {
  printf 'ERROR: Hermit release binary not found: %s\n' "$HERMIT_BIN" >&2
  exit 1
}
[[ -n $QEMU_BIN && -x $QEMU_BIN ]] || {
  printf 'ERROR: qemu-system-x86_64 is required; set QEMU_BIN.\n' >&2
  exit 1
}
[[ -r $KERNEL_IMAGE ]] || {
  printf 'ERROR: readable x86-64 kernel image required: %s\n' "$KERNEL_IMAGE" >&2
  exit 1
}

printf '\nHermit: %s\nQEMU: %s\nKernel: %s\nArtifacts: %s\n\n' \
  "$HERMIT_BIN" "$QEMU_BIN" "$KERNEL_IMAGE" "$OUTPUT_DIR"

env \
  HERMIT_BIN="$HERMIT_BIN" \
  KERNEL_IMAGE="$KERNEL_IMAGE" \
  QEMU_BIN="$QEMU_BIN" \
  QEMU_L2_PHASE_TIMEOUT_SECONDS="$QEMU_L2_PHASE_TIMEOUT_SECONDS" \
  OUTPUT_DIR="$OUTPUT_DIR" \
  bash "$HERMIT_REPO/tests/qemu-boot/strict_l2_test.sh"

printf '\nPASS: QEMU Linux strict L2 deterministic boot verified.\n'
printf '%s\n' \
  "Portability: this demo uses the host system QEMU, which is not pinned." \
  "QEMU snapshots are not guaranteed portable across machines or QEMU builds."
