#!/usr/bin/env bash
# Staged crates.io name-reservation ("squat") publisher for the Hermit/Reverie project.
#
# SAFETY: crates.io publishing is IRREVERSIBLE (a name, once published, can be
# yanked but NEVER deleted or reassigned). This script therefore DEFAULTS TO
# DRY-RUN and refuses to really publish unless you pass --yes-publish-for-real.
#
# Owner morning workflow (the "5-second confirm"):
#   1. export CARGO_REGISTRY_TOKEN=<your crates.io token>   # OR: cargo login
#   2. ./PUBLISH.sh                       # re-runs dry-run on every placeholder
#   3. ./PUBLISH.sh --yes-publish-for-real   # performs the real reservation
#
# Each placeholder is version 0.0.1, description + BSD-3-Clause license + repo
# metadata set, empty lib. After publishing you own the name; bump to real
# content later at 0.2.0+.

set -euo pipefail
cd "$(dirname "$0")/crates"

REAL=0
if [[ "${1:-}" == "--yes-publish-for-real" ]]; then REAL=1; fi

# Publish order is irrelevant for empty placeholders (no inter-deps), but listed
# leaves-first so the same script can be reused once real content lands.
CRATES=(
  # ---- liteinst2 repo name (required dep of reverie-liteinst) ----
  liteinst2
  # ---- reverie repo names ----
  safeptrace
  reverie-syscalls
  reverie-utils
  reverie-memory
  reverie-rpc-transport
  reverie-process
  reverie-preload
  reverie-core
  reverie-ptrace
  reverie-kvm
  reverie-liteinst
  reverie-dbi
  reverie-dbt
  reverie-e9patch
  reverie-dynamorio
  reverie-sabre
  # ---- hermit repo names ----
  test-allocator
  detcore-model
  detcore-dbi
  hermit-resources
  hermit-verify
  hermetic-infra
  hermit-run
)

if [[ $REAL -eq 1 ]]; then
  : "${CARGO_REGISTRY_TOKEN:?set CARGO_REGISTRY_TOKEN or run 'cargo login' before --yes-publish-for-real}"
  echo ">>> REAL PUBLISH MODE — this is IRREVERSIBLE. Sleeping 5s; Ctrl-C to abort."
  sleep 5
fi

for c in "${CRATES[@]}"; do
  if [[ $REAL -eq 1 ]]; then
    echo "=== PUBLISH $c ==="
    with-proxy cargo publish --manifest-path "$c/Cargo.toml" --allow-dirty
    # Reserve co-ownership for the team account here if desired:
    # with-proxy cargo owner --add <github-team> "$c"
  else
    echo "=== DRY-RUN $c ==="
    cargo publish --dry-run --allow-dirty --manifest-path "$c/Cargo.toml"
  fi
done

echo "DONE ($([[ $REAL -eq 1 ]] && echo REAL || echo dry-run)). ${#CRATES[@]} crates."
