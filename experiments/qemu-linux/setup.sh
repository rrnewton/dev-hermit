#!/usr/bin/env bash
#
# setup.sh — provision the QEMU/Linux artifacts into ignored/qemu-linux/.
#
# Text (this dir, version-controlled): setup.sh, boot.sh, qemu_init.c, README.md.
# Large binaries (ignored/qemu-linux/, gitignored): bzImage, initramfs images.
#
# What it does:
#   1. Copies a kernel bzImage from the host /boot into ARTIFACT_DIR.
#   2. Builds a busybox initramfs (baseline, no hermit) -> initramfs.cpio.gz.
#   3. Builds the freestanding qemu_init.c initramfs (hermit deterministic-compat
#      profile) -> initramfs-hermit.cpio.gz.
#
# Usage:   ./setup.sh
# Env overrides:
#   KERNEL_IMAGE   source kernel (default: /boot/vmlinuz or newest /boot/vmlinuz-*)
#   BUSYBOX        static busybox (default: /usr/sbin/busybox)
#   ARTIFACT_DIR   output dir (default: <repo>/ignored/qemu-linux)
#
# All tools (qemu-system-x86_64, busybox, gcc, cpio, gzip) are pre-installed on
# the devserver; nothing is downloaded. If you must fetch, wrap with `with-proxy`.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/../.." && pwd)
ARTIFACT_DIR=${ARTIFACT_DIR:-$repo_root/ignored/qemu-linux}
BUSYBOX=${BUSYBOX:-/usr/sbin/busybox}

fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# --- Resolve kernel image --------------------------------------------------
if [[ -z ${KERNEL_IMAGE:-} ]]; then
  if [[ -r /boot/vmlinuz ]]; then
    KERNEL_IMAGE=$(readlink -f /boot/vmlinuz)
  else
    KERNEL_IMAGE=$(ls -1t /boot/vmlinuz-* 2>/dev/null | head -1 || true)
  fi
fi
[[ -n ${KERNEL_IMAGE:-} && -r $KERNEL_IMAGE ]] || \
  fail "no readable kernel image; set KERNEL_IMAGE=/path/to/bzImage"

for tool in "$BUSYBOX" gcc cpio gzip; do
  command -v "$tool" >/dev/null 2>&1 || [[ -x $tool ]] || fail "missing tool: $tool"
done
file "$BUSYBOX" | grep -q "statically linked" || fail "$BUSYBOX is not static"

mkdir -p "$ARTIFACT_DIR"

# --- 1. Kernel -------------------------------------------------------------
cp -f "$KERNEL_IMAGE" "$ARTIFACT_DIR/bzImage"
printf 'kernel: %s -> %s (%s bytes)\n' \
  "$KERNEL_IMAGE" "$ARTIFACT_DIR/bzImage" "$(stat -c%s "$ARTIFACT_DIR/bzImage")"

# --- 2. Baseline busybox initramfs ----------------------------------------
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
mkdir -p "$root"/{bin,sbin,etc,proc,sys,dev,tmp}
cp "$BUSYBOX" "$root/bin/busybox"; chmod +x "$root/bin/busybox"
( cd "$root"
  for app in $(./bin/busybox --list-full); do
    mkdir -p "$(dirname "$app")"
    [[ $app == bin/busybox ]] || ln -sf /bin/busybox "$app"
  done )
cat > "$root/init" <<'INIT'
#!/bin/sh
mount -t proc     none /proc 2>/dev/null
mount -t sysfs    none /sys  2>/dev/null
mount -t devtmpfs none /dev  2>/dev/null || mount -t tmpfs none /dev 2>/dev/null
echo "=========================================="
echo "HERMIT-QEMU-BASELINE-BOOT-OK"
echo "kernel: $(uname -r)"
echo "=========================================="
if [ "${HERMIT_AUTOTEST:-}" = "1" ] || grep -q hermit_autotest /proc/cmdline 2>/dev/null; then
    echo "HERMIT-QEMU-AUTOTEST-DONE"; poweroff -f
fi
echo "Interactive busybox shell. Type 'poweroff -f' to exit."
exec /bin/sh
INIT
chmod +x "$root/init"
printf 'root:x:0:0:root:/:/bin/sh\n' > "$root/etc/passwd"
printf 'root:x:0:\n'                 > "$root/etc/group"
( cd "$root" && find . -print0 | cpio --null -o -H newc 2>/dev/null ) \
  | gzip -9 > "$ARTIFACT_DIR/initramfs.cpio.gz"
printf 'baseline initramfs: %s (%s bytes)\n' \
  "$ARTIFACT_DIR/initramfs.cpio.gz" "$(stat -c%s "$ARTIFACT_DIR/initramfs.cpio.gz")"

# --- 3. Hermit deterministic-compat initramfs (freestanding qemu_init.c) ---
hroot=$(mktemp -d); trap 'rm -rf "$root" "$hroot"' EXIT
gcc -Os -nostdlib -static -fno-stack-protector -fno-pie -no-pie \
  "$script_dir/qemu_init.c" -o "$hroot/init"
( cd "$hroot" && printf '.\n./init\n' | cpio --quiet -o -H newc ) \
  | gzip -9 > "$ARTIFACT_DIR/initramfs-hermit.cpio.gz"
printf 'hermit initramfs: %s (%s bytes)\n' \
  "$ARTIFACT_DIR/initramfs-hermit.cpio.gz" \
  "$(stat -c%s "$ARTIFACT_DIR/initramfs-hermit.cpio.gz")"

printf '\nsetup complete. Boot with: %s/boot.sh [bare|hermit]\n' "$script_dir"
