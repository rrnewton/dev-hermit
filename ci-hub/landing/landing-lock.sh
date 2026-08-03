#!/usr/bin/env bash
# Compatibility path for active lander heartbeats during the Rust cutover.
set -euo pipefail
ROOT=$(git -C "$(dirname -- "${BASH_SOURCE[0]}")/../.." rev-parse --show-toplevel)
exec "$ROOT/ci-hub/ci-hub.rs" land-lock "$@"
