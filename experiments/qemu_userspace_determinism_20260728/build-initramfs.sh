#!/bin/bash
# Build a self-contained busybox initramfs whose /init is the userspace
# program battery (userspace-battery.sh). Isolated to this experiment dir so
# it never touches the shared ignored/qemu-linux/initramfs tree that other
# agents extend for the network/multi-VM milestones.
set -euo pipefail
cd "$(dirname "$0")"

BUSYBOX="${BUSYBOX:-/usr/sbin/busybox}"
ROOT=rootfs

file "$BUSYBOX" | grep -q "statically linked" || { echo "ERROR: $BUSYBOX not static"; exit 1; }

rm -rf "$ROOT"
mkdir -p "$ROOT"/{bin,sbin,etc,proc,sys,dev,tmp}
cp "$BUSYBOX" "$ROOT/bin/busybox"
chmod +x "$ROOT/bin/busybox"

( cd "$ROOT"
  for app in $(./bin/busybox --list-full); do
    mkdir -p "$(dirname "$app")"
    [ "$app" = "bin/busybox" ] || ln -sf /bin/busybox "$app"
  done )

cp userspace-battery.sh "$ROOT/init"
chmod +x "$ROOT/init"

printf 'root:x:0:0:root:/:/bin/sh\n' > "$ROOT/etc/passwd"
printf 'root:x:0:\n'                 > "$ROOT/etc/group"

( cd "$ROOT" && find . -print0 | cpio --null -o -H newc 2>/dev/null ) | gzip -9 > userspace-initramfs.cpio.gz
echo "Built userspace-initramfs.cpio.gz ($(stat -c%s userspace-initramfs.cpio.gz) bytes, $(zcat userspace-initramfs.cpio.gz | cpio -t 2>/dev/null | wc -l) entries)"
