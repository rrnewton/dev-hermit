#!/bin/bash
set -euo pipefail

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
busybox=${BUSYBOX:-/usr/sbin/busybox}
init_program=${INIT_PROGRAM:-"$here/userspace-init"}
output=${1:-"$here/../../scratch/kvm-linux-boot-20260726/userspace-initramfs.cpio.gz"}
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT

file "$busybox" | grep -q 'statically linked' || {
  echo "busybox must be a statically linked x86-64 executable: $busybox" >&2
  exit 1
}

mkdir -p "$root"/{bin,dev,etc,home,proc,root,sbin,sys,tmp,usr}
install -m 0755 "$busybox" "$root/bin/busybox"
install -m 0755 "$init_program" "$root/init"
printf 'root:x:0:0:root:/:/bin/sh\n' >"$root/etc/passwd"
printf 'root:x:0:\n' >"$root/etc/group"

while IFS= read -r applet; do
  [ "$applet" = bin/busybox ] && continue
  mkdir -p "$root/$(dirname -- "$applet")"
  ln -s /bin/busybox "$root/$applet"
done < <("$busybox" --list-full)

# Stable ordering, ownership, timestamps, cpio inode numbers, and gzip header.
find "$root" -exec touch -h -d @0 {} +
mkdir -p "$(dirname -- "$output")"
(
  cd "$root"
  find . -print0 | sort -z |
    cpio --null --create --format=newc --owner=0:0 --reproducible 2>/dev/null
) | gzip -n -9 >"$output"

sha256sum "$busybox" "$output"
printf 'entries=%s bytes=%s output=%s\n' \
  "$(gzip -dc "$output" | cpio -it 2>/dev/null | wc -l)" \
  "$(stat -c %s "$output")" \
  "$output"
