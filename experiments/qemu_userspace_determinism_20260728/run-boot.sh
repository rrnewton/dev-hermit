#!/bin/bash
# Boot the userspace-battery initramfs inside QEMU, with QEMU itself running as
# a userspace process under Hermit. Writes the full serial console to $1.
#
# Usage: ./run-boot.sh <out.log> [compat|strict]
#   compat (default): --no-sequentialize-threads --max-timeslice disabled
#                     --no-virtualize-cpuid   (fast, ~15-25s)
#   strict:           default serialized scheduler (slow, ~170s), the real
#                     Hermit determinism guarantee
set -uo pipefail
cd "$(dirname "$0")"

OUT="${1:?usage: run-boot.sh <out.log> [compat|strict]}"
PROFILE="${2:-compat}"

REPO="$(cd ../.. && pwd)"
HERMIT="${HERMIT_BIN:-$REPO/hermit/target/release/hermit}"
BZIMAGE="${KERNEL_IMAGE:-$REPO/ignored/qemu-linux/bzImage}"
INITRD="$(pwd)/userspace-initramfs.cpio.gz"
QEMU="${QEMU_BIN:-qemu-system-x86_64}"

QEMU_ARGS=(
  -nodefaults -nic none -m 256M
  -accel tcg,thread=single -smp 1
  -icount shift=0,sleep=off
  -rtc base=utc,clock=vm
  -kernel "$BZIMAGE"
  -initrd "$INITRD"
  -display none -serial stdio -monitor none -no-reboot
  -append 'console=ttyS0 panic=-1 rdinit=/init'
)

if [ "$PROFILE" = "strict" ]; then
  HERMIT_FLAGS=(--log error run --no-virtualize-cpuid)
  TIMEOUT=240s
else
  HERMIT_FLAGS=(--log error run --no-sequentialize-threads --max-timeslice disabled --no-virtualize-cpuid)
  TIMEOUT=90s
fi

echo "# profile=$PROFILE hermit=$HERMIT bzImage=$BZIMAGE" >&2
timeout --signal=KILL "$TIMEOUT" "$HERMIT" "${HERMIT_FLAGS[@]}" -- "$QEMU" "${QEMU_ARGS[@]}" >"$OUT" 2>&1
rc=$?
echo "# exit=$rc" >&2
exit $rc
