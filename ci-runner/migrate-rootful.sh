#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

sudo -v

for name in hermit-ci-runner reverie-ci-runner; do
  ./podman-host.sh rm -f "${name}" >/dev/null 2>&1 || true
  systemctl --user disable "container-${name}.service" >/dev/null 2>&1 || true
  rm -f "${HOME}/.config/systemd/user/container-${name}.service"
done
systemctl --user daemon-reload

# Rootful Podman has separate image storage from rootless Podman.
make -B build
make start START_ARGS=--detach
make CONFIG_DIR=instances/reverie start START_ARGS=--detach

for name in hermit-ci-runner reverie-ci-runner; do
  ./podman-root.sh exec "${name}" \
    unshare --user --map-root-user --pid --fork --uts --mount \
    sh -c 'mkdir -p /tmp/sysfs-smoke && mount -t sysfs sysfs /tmp/sysfs-smoke && umount /tmp/sysfs-smoke'
done

echo "Rootful Hermit and Reverie runners are running; nested sysfs mounts passed."
