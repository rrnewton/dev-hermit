#!/usr/bin/env bash
set -euo pipefail

: "${DRB_SNAPSHOT_BASE:?set DRB_SNAPSHOT_BASE}"

root=/wheezy-rootfs
archive=/cache/wheezy-rootfs.tar
if [[ -e "$archive" ]]; then
  echo "refusing to replace existing bootstrap archive: $archive" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates debootstrap fakechroot fakeroot

# Rootless Podman cannot create device nodes in its overlay. The foreign first
# stage only needs regular files; the second stage runs with Podman's /dev.
# shellcheck disable=SC2016 # Match the literal variable reference in debootstrap.
sed -i '/if ! check_sane_mount "$TARGET"; then/,/fi/d' /usr/sbin/debootstrap

fakechroot fakeroot debootstrap \
  --arch=amd64 \
  --components=main \
  --foreign \
  --no-check-gpg \
  --variant=fakechroot \
  wheezy "$root" "$DRB_SNAPSHOT_BASE"
if [[ -L "$root/dev" ]]; then
  rm "$root/dev"
  mkdir "$root/dev"
fi
if [[ -L "$root/proc" ]]; then
  rm "$root/proc"
  mkdir "$root/proc"
fi
tar --exclude='./dev/*' -C "$root" -cpf "$archive" .
echo "DRB foreign bootstrap complete"
