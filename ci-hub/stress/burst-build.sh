#!/usr/bin/env bash
# Adapter: maps nightly.sh's positional contract (<sha> <width> <timeout>
# <workload>) onto a sha-accurate burst WITHOUT reflink-copying target/.
#
# WHY no reflink: `cp -a --reflink=auto hermit/target <wt>/target` is (a) blocked
# by this host's BPFJailer FS policy for the agent (3pai) role — FILE_OPEN on the
# clone — and (b) a known cmake-cache poisoner for native builds (see memory
# "reflink-seed-cmake-cache-cross-worktree-pollution"). So instead of a throwaway
# per-SHA worktree seeded from the primary (what `stress-burst --build-at` does),
# this adapter keeps ONE persistent, detached worktree and just checks out the
# recorded SHA into it each night. Its target/ persists across nights, so the
# build is a cold compile the first night and incremental thereafter — no reflink,
# no primary-checkout mutation, and never a collision with multisect's per-SHA
# worktrees. The burst then runs via `stress-burst --prebuilt` on that worktree,
# whose HEAD == the recorded SHA (sha-accurate attribution).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERMIT="$ROOT/hermit"
WT="$ROOT/ignored/ci-hub/stress-wt/nightly"
SHA="$1"; WIDTH="${2:-64}"; TIMEOUT="${3:-20}"; WL="$4"
BIN_NAME="${WL%%:*}"
short() { git -C "$WT" rev-parse --short HEAD 2>/dev/null || echo unknown; }

mkdir -p "$(dirname "$WT")"

# Provision the persistent worktree once; reuse it thereafter (checkout new SHA).
if [ ! -e "$WT/.git" ]; then
  [ -e "$WT" ] && { git -C "$HERMIT" worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"; }
  git -C "$HERMIT" worktree prune 2>/dev/null || true
  git -C "$HERMIT" worktree add --detach "$WT" "$SHA" >/dev/null 2>&1 \
    || { echo "$SHA,,,,,,,,WT_FAIL"; exit 0; }
else
  # Detached worktree: only target/ (gitignored) changes between nights, so a
  # plain detach-checkout to the new SHA is clean. The SHA object is already in
  # the shared object DB (nightly.sh fetched origin/main in the primary first).
  git -C "$WT" checkout -q --detach "$SHA" >/dev/null 2>&1 \
    || { echo "$SHA,$(short),,$WIDTH,,,,,WT_FAIL"; exit 0; }
fi

# Build the burst binary in-tree (incremental; NO reflink seed of target/).
bstart=$(date +%s)
if ! ( cd "$WT" && cargo test -p detcore --test "$BIN_NAME" --no-run ) >/dev/null 2>&1; then
  echo "$SHA,$(short),$(( $(date +%s)-bstart )),$WIDTH,,,,,BUILD_FAIL"; exit 0
fi

# Burst against the just-built, sha-matched binary.
exec "$ROOT/ci-hub/stress/stress-burst" --prebuilt "$WT" \
  --width "$WIDTH" --timeout "$TIMEOUT" --workload "$WL"
