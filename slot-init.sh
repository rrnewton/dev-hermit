#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ROOT_DIR
WORKTREES_DIR="$ROOT_DIR/worktrees"
readonly WORKTREES_DIR

function usage {
    cat <<'EOF'
Usage: ./slot-init.sh SLOT [BRANCH] [START_POINT]

Create one permanent parent worktree and initialize its nested submodules.

  SLOT         One of slot01, slot02, slot03, or slot04.
  BRANCH       Parent branch for the slot (default: devbig-lead-SLOT).
  START_POINT  Commit or branch to start from (default: devbig-lead).

Example:
  ./slot-init.sh slot01
  ./slot-init.sh slot02 issue-123 devbig-lead
EOF
}

if (($# > 3)) || [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    if (($# > 3)); then
        exit 2
    fi
    exit 0
fi

readonly SLOT=${1:-}
if [[ ! $SLOT =~ ^slot0[1-4]$ ]]; then
    usage >&2
    exit 2
fi

readonly BRANCH=${2:-devbig-lead-$SLOT}
readonly START_POINT=${3:-devbig-lead}
readonly SLOT_DIR="$WORKTREES_DIR/$SLOT"

if [[ -e $SLOT_DIR ]]; then
    echo "Refusing to overwrite occupied slot: $SLOT_DIR" >&2
    exit 1
fi

mkdir -p "$WORKTREES_DIR"

if git -C "$ROOT_DIR" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$ROOT_DIR" worktree add "$SLOT_DIR" "$BRANCH"
else
    git -C "$ROOT_DIR" worktree add -b "$BRANCH" "$SLOT_DIR" "$START_POINT"
fi

git -C "$SLOT_DIR" submodule sync --recursive
git -C "$SLOT_DIR" submodule update --init --recursive

for repository in hermit reverie; do
    if ! git -C "$SLOT_DIR/$repository" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Submodule initialization failed: $SLOT_DIR/$repository" >&2
        exit 1
    fi
done

echo "Initialized $SLOT on $BRANCH from $START_POINT"
git -C "$SLOT_DIR" submodule status --recursive
