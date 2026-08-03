#!/usr/bin/env bash
set -euo pipefail

# Run Podman through the host user manager when the caller is itself inside a
# user namespace and cannot join Podman's persistent rootless namespace.
exec systemd-run --quiet --user --wait --pipe --collect \
  --property=KillMode=process --working-directory="$(pwd)" podman "$@"
