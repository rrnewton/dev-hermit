#!/usr/bin/env bash
#
# boot.sh — boot the Linux guest, either bare (baseline) or under hermit.
#
# Usage:
#   ./boot.sh bare   [tcg|kvm]   # QEMU only, no hermit (default accel: tcg)
#   ./boot.sh hermit             # QEMU under hermit, deterministic-compat profile
#
# Run ./setup.sh first to populate ignored/qemu-linux/.
#
# Env overrides:
#   ARTIFACT_DIR   default: <repo>/ignored/qemu-linux
#   QEMU_BIN       default: qemu-system-x86_64 on PATH
#   HERMIT_BIN     default: <repo>/hermit/target/release/hermit (fallback: debug)
#   TIMEOUT_SECONDS default: 90
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
ARTIFACT_DIR=${ARTIFACT_DIR:-$repo_root/ignored/qemu-linux}
QEMU_BIN=${QEMU_BIN:-$(command -v qemu-system-x86_64 || true)}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS:-90}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ -n $QEMU_BIN && -x $QEMU_BIN ]] || fail "qemu-system-x86_64 not found; set QEMU_BIN"
[[ -r $ARTIFACT_DIR/bzImage ]] || fail "missing $ARTIFACT_DIR/bzImage — run ./setup.sh"

mode=${1:-bare}
case "$mode" in
  bare)
    accel=${2:-tcg}
    if [[ $accel == kvm ]]; then accel_args=(-enable-kvm -cpu host); else accel_args=(-accel tcg); fi
    initrd=$ARTIFACT_DIR/initramfs.cpio.gz
    [[ -r $initrd ]] || fail "missing $initrd — run ./setup.sh"
    echo ":: bare QEMU boot (accel=$accel). Expect: HERMIT-QEMU-BASELINE-BOOT-OK"
    exec timeout --kill-after=5 --signal=KILL "${TIMEOUT_SECONDS}s" \
      "$QEMU_BIN" \
      -kernel "$ARTIFACT_DIR/bzImage" \
      -initrd "$initrd" \
      -append "console=ttyS0 panic=1 hermit_autotest" \
      -nographic -no-reboot -m 512M "${accel_args[@]}"
    ;;
  hermit)
    hermit_bin=${HERMIT_BIN:-$repo_root/hermit/target/release/hermit}
    [[ -x $hermit_bin ]] || hermit_bin=$repo_root/hermit/target/debug/hermit
    [[ -x $hermit_bin ]] || fail "hermit binary not found; build it or set HERMIT_BIN"
    initrd=$ARTIFACT_DIR/initramfs-hermit.cpio.gz
    [[ -r $initrd ]] || fail "missing $initrd — run ./setup.sh"
    echo ":: QEMU under hermit (deterministic-compat). Expect: SHARED_FUTEX_QEMU_KERNEL_OK"
    # Flag rationale is documented in README.md ("Hermit flag profiles").
    exec timeout --kill-after=5 --signal=KILL "${TIMEOUT_SECONDS}s" \
      "$hermit_bin" --log error run \
      --no-sequentialize-threads \
      --preemption-timeout disabled \
      --no-virtualize-cpuid -- \
      "$QEMU_BIN" \
      -m 256M \
      -accel tcg,thread=single \
      -smp 1 \
      -icount shift=0,sleep=off \
      -kernel "$ARTIFACT_DIR/bzImage" \
      -initrd "$initrd" \
      -display none \
      -serial stdio \
      -monitor none \
      -no-reboot \
      -append 'console=ttyS0 panic=-1 rdinit=/init'
    ;;
  *)
    fail "unknown mode '$mode' (want: bare | hermit)"
    ;;
esac
