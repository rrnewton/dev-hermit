#!/usr/bin/env bash
set -euo pipefail

# Rootful Podman is required for nested sysfs/network namespace mounts. This
# intentionally prompts through sudo when the caller has no cached credential.
exec sudo podman "$@"
