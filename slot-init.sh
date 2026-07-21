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
Usage: ./slot-init.sh SLOT --owner OWNER --task TASK --purpose TEXT [OPTIONS]

Create one registered parent worktree and initialize its nested submodules.

  SLOT                 One of slot01 through slot12.
  --owner OWNER        Agent/team that owns the whole slot.
  --task TASK          Task identifier.
  --purpose TEXT       One-line purpose for ACTIVE.md.
  --branch BRANCH      Parent branch (default: devbig-lead-SLOT).
  --start-point REF    Parent start point (default: devbig-lead).

Example:
  ./slot-init.sh slot01 --owner hermit-api --task impl-example \
    --purpose "Implement the example change"
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
    usage
    exit 0
fi

readonly SLOT=${1:-}
if [[ ! $SLOT =~ ^slot(0[1-9]|1[0-2])$ ]]; then
    usage >&2
    exit 2
fi
shift

OWNER=""
TASK=""
PURPOSE=""
BRANCH="devbig-lead-$SLOT"
START_POINT="devbig-lead"

while (($#)); do
    case $1 in
        --owner)
            OWNER=${2:-}
            shift 2
            ;;
        --task)
            TASK=${2:-}
            shift 2
            ;;
        --purpose)
            PURPOSE=${2:-}
            shift 2
            ;;
        --branch)
            BRANCH=${2:-}
            shift 2
            ;;
        --start-point)
            START_POINT=${2:-}
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ -z $OWNER || -z $TASK || -z $PURPOSE ]]; then
    echo "--owner, --task, and --purpose are required" >&2
    usage >&2
    exit 2
fi

for value in "$OWNER" "$TASK" "$PURPOSE" "$BRANCH"; do
    if [[ $value == *"|"* || $value == *$'\n'* || $value == *$'\x60'* ]]; then
        echo "Registry values must be single-line text without '|' or backticks: $value" >&2
        exit 2
    fi
done

readonly SLOT_DIR="$WORKTREES_DIR/$SLOT"
readonly ACTIVE="$WORKTREES_DIR/ACTIVE.md"

if [[ ! -f $ACTIVE ]]; then
    echo "Missing required registry: $ACTIVE" >&2
    exit 1
fi

mkdir -p "$WORKTREES_DIR"

exec 9>"$WORKTREES_DIR/.slot-management.lock"
if ! flock --nonblock 9; then
    echo "Another coordinator is changing the slot pool; retry after it finishes." >&2
    exit 1
fi

if [[ -e $SLOT_DIR ]]; then
    echo "Refusing to overwrite occupied slot: $SLOT_DIR" >&2
    exit 1
fi

declare -A active_slots=()
# The backticks are literal Markdown delimiters in the registry table.
# shellcheck disable=SC2016
while IFS= read -r path; do
    [[ -n $path ]] && active_slots["${path#worktrees/}"]=1
done < <(sed -n 's/^| `\(worktrees\/[^\`]*\)`.*/\1/p' "$ACTIVE")

declare -A allocated_slots=()
for slot in "${!active_slots[@]}"; do
    allocated_slots["$slot"]=1
done
for path in "$WORKTREES_DIR"/slot[0-9][0-9]; do
    [[ -d $path ]] && allocated_slots["$(basename "$path")"]=1
done

for repository in "$ROOT_DIR" "$ROOT_DIR/hermit" "$ROOT_DIR/reverie"; do
    while IFS= read -r path; do
        [[ $path == "$WORKTREES_DIR/"* ]] || continue
        relative=${path#"$WORKTREES_DIR/"}
        case $relative in
            slot[0-9][0-9]/hermit|slot[0-9][0-9]/reverie)
                relative=${relative%/*}
                ;;
        esac
        allocated_slots["$relative"]=1
    done < <(git -C "$repository" worktree list --porcelain | sed -n 's/^worktree //p')
done

active_count=${#active_slots[@]}
parked_count=0
for slot in "${!allocated_slots[@]}"; do
    [[ -v active_slots["$slot"] ]] || ((parked_count += 1))
done

if ((active_count >= 12)); then
    echo "Refusing to create a thirteenth active slot ($active_count already exist)." >&2
    exit 1
fi
if ((parked_count > 5)); then
    echo "Refusing allocation while $parked_count parked slots exceed the cap of five." >&2
    echo "Reclaim parked caches first; see worktrees/ACTIVE.md." >&2
    exit 1
fi

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

function checkout_label {
    local repository=$1
    local branch
    branch=$(git -C "$repository" symbolic-ref --quiet --short HEAD || true)
    if [[ -n $branch ]]; then
        printf '%s' "$branch"
    else
        printf 'detached:%s' "$(git -C "$repository" rev-parse --short HEAD)"
    fi
}

# The backticks are literal Markdown delimiters in the registry table.
# shellcheck disable=SC2016
printf '| `worktrees/%s` | `%s` / `%s` | `%s` | `%s` | %s | %s |\n' \
    "$SLOT" "$OWNER" "$TASK" \
    "$(checkout_label "$SLOT_DIR/hermit")" \
    "$(checkout_label "$SLOT_DIR/reverie")" \
    "$(date +%F)" "$PURPOSE" >>"$ACTIVE"

echo "Initialized $SLOT on $BRANCH from $START_POINT"
echo "Registered $SLOT for $OWNER / $TASK in worktrees/ACTIVE.md"
git -C "$SLOT_DIR" submodule status --recursive
