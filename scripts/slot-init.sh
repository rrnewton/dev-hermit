#!/usr/bin/env bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT_DIR

# Flat, direct-worktree layout:
#   worktrees/slotNN          -> Hermit worktree   (from the hermit/ primary)
#   worktrees_reverie/slotNN  -> Reverie worktree  (from the reverie/ primary)
readonly HERMIT_WORKTREES="$ROOT_DIR/worktrees"
readonly REVERIE_WORKTREES="$ROOT_DIR/worktrees_reverie"

function usage {
    cat <<'EOF'
Usage: ./scripts/slot-init.sh SLOT [PRODUCT] [START_POINT]

Create one or both direct product worktrees for a numbered slot. Each worktree
is the checkout root itself, created from the matching primary checkout:

  worktrees/slotNN          Hermit worktree  (from the hermit/ primary)
  worktrees_reverie/slotNN  Reverie worktree (from the reverie/ primary)

  SLOT         slotNN, where NN is two or more digits (e.g. slot01, slot17).
  PRODUCT      hermit | reverie | both  (default: both).
  START_POINT  Commit or branch to base the detached worktree on
               (default: the primary checkout's current HEAD).

The worktree is created detached at START_POINT. Create a task-specific feature
branch inside the slot before editing, and register it in the matching ACTIVE.md.

Example:
  ./scripts/slot-init.sh slot01
  ./scripts/slot-init.sh slot02 hermit
  ./scripts/slot-init.sh slot03 reverie main
EOF
}

if (($# < 1)) || (($# > 3)) || [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    [[ $# -ge 1 && ${1:-} != "-h" && ${1:-} != "--help" ]] && exit 2
    exit 0
fi

readonly SLOT=${1:-}
if [[ ! $SLOT =~ ^slot[0-9]{2,}$ ]]; then
    echo "Invalid SLOT: '$SLOT' (expected slotNN, e.g. slot01)" >&2
    usage >&2
    exit 2
fi

readonly PRODUCT=${2:-both}
case "$PRODUCT" in
    hermit | reverie | both) ;;
    *)
        echo "Invalid PRODUCT: '$PRODUCT' (expected hermit, reverie, or both)" >&2
        usage >&2
        exit 2
        ;;
esac

readonly START_POINT_OVERRIDE=${3:-}

# add_worktree PRIMARY_DIR WORKTREES_PARENT
#   Create $WORKTREES_PARENT/$SLOT as a detached worktree of the primary repo.
function add_worktree {
    local primary_dir=$1
    local worktrees_parent=$2
    local slot_dir="$worktrees_parent/$SLOT"

    if [[ ! -d $primary_dir ]]; then
        echo "Missing primary checkout: $primary_dir" >&2
        exit 1
    fi
    if [[ -e $slot_dir ]]; then
        echo "Refusing to overwrite occupied slot: $slot_dir" >&2
        exit 1
    fi

    local start_point=$START_POINT_OVERRIDE
    if [[ -z $start_point ]]; then
        start_point=$(git -C "$primary_dir" rev-parse --abbrev-ref HEAD)
    fi

    mkdir -p "$worktrees_parent"
    git -C "$primary_dir" worktree add --detach "$slot_dir" "$start_point"

    if ! git -C "$slot_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Worktree creation failed: $slot_dir" >&2
        exit 1
    fi
    echo "Initialized $slot_dir (detached at $start_point)"
}

if [[ $PRODUCT == hermit || $PRODUCT == both ]]; then
    add_worktree "$ROOT_DIR/hermit" "$HERMIT_WORKTREES"
fi
if [[ $PRODUCT == reverie || $PRODUCT == both ]]; then
    add_worktree "$ROOT_DIR/reverie" "$REVERIE_WORKTREES"
fi
