#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# cleanup_stale_eden.sh — reclaim resources from stale eden/codesync mounts.
#
# fbsource imports leave codesync eden clones under /tmp/codesync-*/fbsource
# (== /data/tmpvol/codesync-*/fbsource; /tmp and /data/tmpvol share a device).
# Each is a live edenfs FUSE checkout with a buck-out btrfs submount, so they
# hold inodes, edenfs-daemon memory, and disk long after the import that made
# them has finished. This script removes ONLY those codesync clones, never the
# primary checkouts below ORC_DEV_ROOT (default: $HOME/work/orc-dev).
#
# SAFE BY DEFAULT: it prints a plan and changes nothing unless you pass --apply.
#
# Usage:
#   scripts/cleanup_stale_eden.sh                 # dry-run: show what would go
#   scripts/cleanup_stale_eden.sh --apply         # actually remove stale clones
#   scripts/cleanup_stale_eden.sh --min-age-hours 12 --apply
#
# Options:
#   --apply               perform removals (default: dry-run)
#   --min-age-hours N      only remove clones whose dir is older than N h (def 6)
#   -h, --help             this help
#
# A codesync mount is eligible only when ALL hold:
#   * its path matches   /tmp/codesync-*   or   /data/tmpvol/codesync-*
#   * its path is outside ORC_DEV_ROOT (the protected primaries)
#   * the backing codesync dir is older than --min-age-hours

set -uo pipefail

APPLY=0
MIN_AGE_HOURS=6
PRIMARY_CHECKOUT_ROOT="${ORC_DEV_ROOT:-$HOME/work/orc-dev}"
while (($#)); do
    case "$1" in
        --apply) APPLY=1 ;;
        --min-age-hours) MIN_AGE_HOURS=${2:?need N}; shift ;;
        -h | --help) sed -n '3,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "cleanup_stale_eden.sh: unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

if ! command -v edenfsctl >/dev/null 2>&1; then
    echo "edenfsctl not found; nothing to do." >&2
    exit 0
fi

# --- Guardrail: decide whether a mount path is a removable codesync clone. ---
function is_eligible_path {
    local p=$1
    # Protect the primary checkouts unconditionally.
    [[ $p == "$PRIMARY_CHECKOUT_ROOT" \
        || $p == "$PRIMARY_CHECKOUT_ROOT/"* ]] && return 1
    # Only ever touch codesync temp clones.
    [[ $p == /tmp/codesync-* || $p == /data/tmpvol/codesync-* ]] && return 0
    return 1
}

# backing codesync dir for a mount like /tmp/codesync-X/fbsource -> /tmp/codesync-X
function codesync_root {
    local p=$1
    while [[ $p == */* ]]; do
        case "$(basename "$p")" in
            codesync-*) echo "$p"; return 0 ;;
        esac
        p=$(dirname "$p")
    done
    return 1
}

mapfile -t mounts < <(timeout 30 edenfsctl list 2>/dev/null || true)

declare -a plan=()
declare -a protected=()
age_min=$((MIN_AGE_HOURS * 60))

for m in "${mounts[@]}"; do
    [[ -z $m ]] && continue
    if ! is_eligible_path "$m"; then
        protected+=("$m")
        continue
    fi
    root=$(codesync_root "$m" || echo "")
    if [[ -z $root || ! -e $root ]]; then
        # Mount with no backing dir we can age-check: treat as stale.
        plan+=("$m"$'\t'"${root:-?}"$'\t'"no-backing-dir")
        continue
    fi
    if find "$root" -maxdepth 0 -mmin "+$age_min" >/dev/null 2>&1 \
        && [[ -n $(find "$root" -maxdepth 0 -mmin "+$age_min" 2>/dev/null) ]]; then
        plan+=("$m"$'\t'"$root"$'\t'"older-than-${MIN_AGE_HOURS}h")
    else
        plan+=("$m"$'\t'"$root"$'\t'"SKIP:younger-than-${MIN_AGE_HOURS}h")
    fi
done

echo "Protected (never removed):"
if ((${#protected[@]})); then printf '  - %s\n' "${protected[@]}"; else echo "  (none)"; fi
echo

mode="DRY-RUN (no changes; pass --apply to act)"
((APPLY == 1)) && mode="APPLY"
echo "Mode: $mode   min-age: ${MIN_AGE_HOURS}h"
echo "Stale codesync eden clones:"

removed=0 skipped=0 eligible=0
if ((${#plan[@]} == 0)); then
    echo "  (none found)"
fi
for row in "${plan[@]}"; do
    IFS=$'\t' read -r mnt root reason <<<"$row"
    if [[ $reason == SKIP:* ]]; then
        echo "  skip  $mnt  (${reason#SKIP:})"
        ((skipped++))
        continue
    fi
    ((eligible++))
    if ((APPLY == 0)); then
        echo "  would-remove  $mnt  ($reason)"
        continue
    fi
    echo "  removing  $mnt  ($reason)"
    if timeout 120 edenfsctl remove --yes "$mnt" >/dev/null 2>&1; then
        # Clean leftover manifest + now-orphaned backing dir/json siblings.
        if [[ -n $root && $root == *codesync-* ]]; then
            rm -f "${root}.json" "${root}"-*.json 2>/dev/null
            rmdir "$root" 2>/dev/null || true
        fi
        echo "    ✓ removed"
        ((removed++))
    else
        echo "    ✗ edenfsctl remove failed (left in place)"
        ((skipped++))
    fi
done

echo
if ((APPLY == 1)); then
    echo "Done: removed ${removed}, skipped ${skipped}."
else
    echo "Dry-run: ${eligible} clone(s) eligible for removal, ${skipped} skipped (too young)."
    echo "Re-run with --apply to reclaim them."
fi
