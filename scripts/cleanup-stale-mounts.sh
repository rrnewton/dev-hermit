#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# cleanup-stale-mounts.sh — reclaim resources from stale eden/codesync mounts.
#
# fbsource imports (codesync) leave behind EdenFS clones under
#   /tmp/codesync-*/fbsource   (== /var/tmp/codesync-*/fbsource
#                               == /data/tmpvol/codesync-*/fbsource;
#    those three roots are the SAME directory, bind-mounted at three paths).
# Each is a live edenfs FUSE checkout with a nested btrfs buck-out redirection,
# so it pins inodes, edenfs-daemon memory, and disk long after the import that
# created it has finished. Its `chataccd --subscribe` watcher is usually
# orphaned to `systemd --user` once the import job dies.
#
# This script removes ONLY those codesync clones, NEVER the primary
# ~/work/orc-dev/fbsource* checkouts (or anything else under work/orc-dev).
#
# SAFE BY DEFAULT: it prints a plan and changes nothing unless you pass --apply.
#
# Usage:
#   scripts/cleanup-stale-mounts.sh                    # dry-run: show the plan
#   scripts/cleanup-stale-mounts.sh --apply            # remove stale clones
#   scripts/cleanup-stale-mounts.sh --min-age-hours 12 --apply
#   scripts/cleanup-stale-mounts.sh --reconcile-orphans --apply
#
# Options:
#   --apply                 perform actions (default: dry-run / report only)
#   --min-age-hours N       only remove clones whose backing dir is older than
#                           N hours (default 6)
#   --timeout SECONDS       per-checkout hard cap for `eden rm` (default 1800).
#                           IMPORTANT: keep this LARGE. `eden rm` must be allowed
#                           to finish; killing it mid-finalize leaves half-removed
#                           client state (config.toml gone, config.json entry
#                           kept) that `eden doctor` reports as an unfixable
#                           "UnexpectedMountProblem". See --reconcile-orphans.
#   --reconcile-orphans     detect (and, with --apply, repair) checkouts left in
#                           that corrupted half-removed state by a previously
#                           interrupted `eden rm`. Repair edits eden's
#                           config.json (a timestamped backup is written first)
#                           and deletes the orphaned .eden/clients/<id> dir.
#   -h, --help              this help
#
# A codesync mount is eligible for removal only when ALL of these hold:
#   * its path matches   /tmp/codesync-*  |  /var/tmp/codesync-*  |
#                        /data/tmpvol/codesync-*
#   * its path does NOT contain  work/orc-dev  (the protected primaries)
#   * its backing codesync dir is older than --min-age-hours
#
# Companion: scripts/cleanup_stale_eden.sh is an earlier, simpler variant; this
# script supersedes it (no short kill of eden rm; kills orphaned chataccd;
# detects/repairs corrupted half-removed checkouts; handles all three roots).

set -uo pipefail

APPLY=0
MIN_AGE_HOURS=6
RM_TIMEOUT=1800
RECONCILE=0

while (($#)); do
    case "$1" in
        --apply) APPLY=1 ;;
        --min-age-hours) MIN_AGE_HOURS=${2:?need N}; shift ;;
        --timeout) RM_TIMEOUT=${2:?need SECONDS}; shift ;;
        --reconcile-orphans) RECONCILE=1 ;;
        -h | --help) sed -n '5,60p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "cleanup-stale-mounts.sh: unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

if ! command -v eden >/dev/null 2>&1 && ! command -v edenfsctl >/dev/null 2>&1; then
    echo "eden / edenfsctl not found; nothing to do." >&2
    exit 0
fi
EDEN=$(command -v eden || command -v edenfsctl)

EDEN_DIR="${EDENFSCTL_CONFIG_DIR:-$HOME/../$USER}"  # placeholder, resolved below
# Resolve eden state dir from the running daemon's --edenDir, else default.
EDEN_DIR=$(
    ps -o args= -C edenfs 2>/dev/null \
        | grep -o -- '--edenDir [^ ]*' | awk '{print $2}' | head -1
)
[[ -z $EDEN_DIR ]] && EDEN_DIR="/data/users/$USER/.eden"
CONFIG_JSON="$EDEN_DIR/config.json"
CLIENTS_DIR="$EDEN_DIR/clients"

# --- Guardrail: is this mount path a removable codesync clone? ---
function is_eligible_path {
    local p=$1
    [[ $p == *"work/orc-dev"* ]] && return 1          # protect primaries
    [[ $p == /tmp/codesync-* || $p == /var/tmp/codesync-* \
        || $p == /data/tmpvol/codesync-* ]] && return 0
    return 1
}

# backing codesync dir for /tmp/codesync-X/fbsource -> /tmp/codesync-X
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

# Kill the orphaned `chataccd --mount-point <mnt> --subscribe` watcher, if any.
function kill_chataccd {
    local mnt=$1 pids
    pids=$(pgrep -f "chataccd .*--mount-point $mnt( |$)" 2>/dev/null || true)
    [[ -z $pids ]] && return 0
    echo "    stopping orphaned chataccd for $mnt: $pids"
    ((APPLY == 1)) && kill $pids 2>/dev/null && sleep 1 || true
}

# ---------------------------------------------------------------------------
# Orphan detection/repair: config.json entry whose client config.toml is gone.
# ---------------------------------------------------------------------------
function reconcile_orphans {
    [[ -f $CONFIG_JSON ]] || { echo "  (no config.json at $CONFIG_JSON)"; return; }
    local found=0
    # Read "mountpath\tclientid" pairs from config.json.
    while IFS=$'\t' read -r mnt cid; do
        [[ -z $mnt ]] && continue
        local toml="$CLIENTS_DIR/$cid/config.toml"
        # Orphan = client config missing AND the mount dir no longer exists.
        if [[ ! -f $toml && ! -e $mnt ]]; then
            found=1
            echo "  ORPHAN  $mnt  (client=$cid: missing config.toml, no mount dir)"
            if ((APPLY == 1)); then
                cp -a "$CONFIG_JSON" "$CONFIG_JSON.bak.$(date +%s)" 2>/dev/null
                python3 - "$CONFIG_JSON" "$mnt" <<'PY'
import json, os, sys
p, key = sys.argv[1], sys.argv[2]
d = json.load(open(p))
d.pop(key, None)
tmp = p + ".tmp"
json.dump(d, open(tmp, "w"), indent=2)
open(tmp, "a").write("\n")
os.replace(tmp, p)
PY
                rm -rf "${CLIENTS_DIR:?}/$cid" 2>/dev/null || true
                echo "    ✓ reconciled (config.json entry + $CLIENTS_DIR/$cid removed)"
            fi
        fi
    done < <(python3 - "$CONFIG_JSON" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
for k, v in d.items():
    print(f"{k}\t{v}")
PY
    )
    ((found == 0)) && echo "  (no orphaned/corrupted checkouts)"
}

# ---------------------------------------------------------------------------
mapfile -t mounts < <(timeout 30 "$EDEN" list 2>/dev/null || true)

declare -a plan=() protected=()
age_min=$((MIN_AGE_HOURS * 60))

for m in "${mounts[@]}"; do
    [[ -z $m ]] && continue
    if ! is_eligible_path "$m"; then
        protected+=("$m"); continue
    fi
    root=$(codesync_root "$m" || echo "")
    if [[ -z $root || ! -e $root ]]; then
        plan+=("$m"$'\t'"${root:-?}"$'\t'"no-backing-dir"); continue
    fi
    if [[ -n $(find "$root" -maxdepth 0 -mmin "+$age_min" 2>/dev/null) ]]; then
        plan+=("$m"$'\t'"$root"$'\t'"older-than-${MIN_AGE_HOURS}h")
    else
        plan+=("$m"$'\t'"$root"$'\t'"SKIP:younger-than-${MIN_AGE_HOURS}h")
    fi
done

echo "eden state dir: $EDEN_DIR"
echo
echo "Protected (never removed):"
if ((${#protected[@]})); then printf '  - %s\n' "${protected[@]}"; else echo "  (none)"; fi
echo

mode="DRY-RUN (no changes; pass --apply to act)"
((APPLY == 1)) && mode="APPLY"
echo "Mode: $mode   min-age: ${MIN_AGE_HOURS}h   eden-rm-timeout: ${RM_TIMEOUT}s"
echo "Stale codesync eden clones:"

removed=0 skipped=0 eligible=0
((${#plan[@]} == 0)) && echo "  (none found)"
for row in "${plan[@]}"; do
    IFS=$'\t' read -r mnt root reason <<<"$row"
    if [[ $reason == SKIP:* ]]; then
        echo "  skip  $mnt  (${reason#SKIP:})"; ((skipped++)); continue
    fi
    ((eligible++))
    if ((APPLY == 0)); then
        echo "  would-remove  $mnt  ($reason)"; continue
    fi
    echo "  removing  $mnt  ($reason)"
    kill_chataccd "$mnt"
    # Let eden rm run to completion. A large --timeout is a backstop only;
    # do NOT set it small — an interrupted eden rm corrupts client state.
    if timeout "$RM_TIMEOUT" "$EDEN" rm -y "$mnt" 2>&1 | sed 's/^/    /'; then
        [[ -n $root && $root == *codesync-* ]] && {
            rm -f "${root}.json" "${root}"-*.json 2>/dev/null
            rmdir "$root" 2>/dev/null || true
        }
        echo "    ✓ removed"; ((removed++))
    else
        echo "    ✗ eden rm did not finish cleanly (left in place; check state" \
             "with 'eden doctor' and consider --reconcile-orphans)"
        ((skipped++))
    fi
done

echo
if ((RECONCILE == 1)); then
    echo "Orphaned / corrupted half-removed checkouts:"
    reconcile_orphans
    echo
fi

if ((APPLY == 1)); then
    echo "Done: removed ${removed}, skipped ${skipped}."
else
    echo "Dry-run: ${eligible} clone(s) eligible, ${skipped} skipped (too young)."
    echo "Re-run with --apply to reclaim them."
fi
