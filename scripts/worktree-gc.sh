#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# worktree-gc.sh — enforce the worktree artifact budget policy.
#
# worktrees/ blows the 200 GB cap STRUCTURALLY: a single heavy backend lane
# holds hundreds of GB of Rust build output (target/), so cleanup passes only
# buy hours. This tool measures per-slot and total footprint, reports against a
# configurable cap + per-slot budget, and prunes regenerable build artifacts in
# a strict safety-ordered escalation. See ai_docs/worktree-artifact-budget-policy.md.
#
# SAFE BY DEFAULT: prints a report and changes nothing unless a prune/reclaim
# action flag is given. It NEVER discards unrecoverable work:
#   * a BUSY slot (any cargo/rustc/cc1/ld/make cwd'd under it) is skipped by all tiers
#   * tiers that remove full target/ or reclaim a slot require the worktree to be
#     CLEAN (no uncommitted changes) AND PUSHED (0 commits ahead of upstream)
#   * tier 1 only ever removes target/*/incremental/, which is never source or
#     commit data — cargo regenerates it on the next build
#
# Prune tiers (strict safety order):
#   1  incremental   target/*/incremental/ in any non-busy slot            (--prune-incremental)
#   2  idle-target   whole target/ of released/parked, clean, pushed slots (--prune-idle-targets)
#   3  reclaim       released, clean, pushed slots -> release-worktree      (--reclaim-released)
#
# Usage:
#   scripts/worktree-gc.sh                          # report only (default)
#   scripts/worktree-gc.sh --prune-incremental      # tier 1 (always-safe)
#   scripts/worktree-gc.sh --enforce                # escalate tiers until under cap
#   scripts/worktree-gc.sh --enforce --cap-gb 200 --slot-budget-gb 60
#
# Options:
#   --prune-incremental     remove target/*/incremental/ in non-busy slots
#   --prune-idle-targets    remove whole target/ of released+clean+pushed+idle slots
#   --reclaim-released      release-worktree --clean released+clean+pushed slots
#   --enforce               if total du > cap, run tiers 1..3 in order until under cap
#   --cap-gb N              global du cap in GB (default 200)
#   --slot-budget-gb N      per-slot du budget in GB, flagged in report (default 60)
#   --include-active        allow tier-1 incremental prune in ACTIVE (registered) slots
#                           too (still skips BUSY slots). Off by default.
#   -h, --help              this help
set -uo pipefail

PRUNE_INCR=0
PRUNE_IDLE=0
RECLAIM=0
ENFORCE=0
CAP_GB=200
SLOT_BUDGET_GB=60
INCLUDE_ACTIVE=0

while (($#)); do
    case "$1" in
        --prune-incremental) PRUNE_INCR=1 ;;
        --prune-idle-targets) PRUNE_IDLE=1 ;;
        --reclaim-released) RECLAIM=1 ;;
        --enforce) ENFORCE=1 ;;
        --cap-gb) CAP_GB=${2:?need N}; shift ;;
        --slot-budget-gb) SLOT_BUDGET_GB=${2:?need N}; shift ;;
        --include-active) INCLUDE_ACTIVE=1 ;;
        -h | --help) sed -n '3,45p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "worktree-gc.sh: unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

# --- Locate dev-hermit root (.gitmodules + hermit/ + reverie/ + liteinst2/). ---
find_root() {
    local dir; dir=$(pwd)
    while [[ $dir != / ]]; do
        if [[ -f $dir/.gitmodules && -d $dir/hermit && -d $dir/reverie && -d $dir/liteinst2 ]]; then
            echo "$dir"; return 0
        fi
        dir=$(dirname "$dir")
    done
    echo "worktree-gc.sh: could not locate dev-hermit root" >&2; exit 1
}
ROOT=$(find_root)
WT="$ROOT/worktrees"
STATE="$ROOT/worktree-state.json"
[[ -d $WT ]] || { echo "no worktrees/ dir at $WT"; exit 0; }

# du of a path in GB (integer), 0 if absent.
du_gb() {
    [[ -e $1 ]] || { echo 0; return; }
    local kb; kb=$(du -sk "$1" 2>/dev/null | cut -f1); echo $(( (kb + 524288) / 1048576 ))
}

# A slot is BUSY if any build process is cwd'd under it.
slot_busy() {
    local slot_dir=$1 pid cwd
    for pid in $(pgrep -x 'cargo|rustc|cc1|cc1plus|ld|make|ld.lld|lld' 2>/dev/null); do
        cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
        [[ $cwd == "$slot_dir"* ]] && return 0
    done
    return 1
}

# Registered status of a slot from worktree-state.json ("active"/"released"/"" if unknown).
slot_state() {
    local slot=$1
    [[ -f $STATE ]] || { echo ""; return; }
    python3 - "$STATE" "$slot" <<'PY' 2>/dev/null || echo ""
import json,sys
try:
    s=json.load(open(sys.argv[1]))
    slot=s.get("slots",{}).get(sys.argv[2])
    print(slot.get("status","active") if slot else "")
except Exception:
    print("")
PY
}

# git worktree clean (no uncommitted changes)?
wt_clean() {
    [[ -d $1 ]] || return 0
    [[ -z $(git -C "$1" status --porcelain 2>/dev/null) ]]
}

# git worktree fully pushed (upstream exists AND 0 commits ahead)?
wt_pushed() {
    [[ -d $1 ]] || return 0
    git -C "$1" rev-parse --abbrev-ref '@{upstream}' >/dev/null 2>&1 || return 1
    [[ $(git -C "$1" rev-list --count '@{upstream}..HEAD' 2>/dev/null || echo 1) == 0 ]]
}

# All product children of a slot clean + pushed?
slot_safe_to_drop() {
    local slot_dir=$1 c
    for c in hermit reverie liteinst2; do
        [[ -d $slot_dir/$c ]] || continue
        wt_clean "$slot_dir/$c" || return 1
        wt_pushed "$slot_dir/$c" || return 1
    done
    return 0
}

# ------------------------------------------------------------------ report ----
mapfile -t SLOTS < <(find "$WT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)
echo "worktree-gc: root=$ROOT  cap=${CAP_GB}GB  per-slot-budget=${SLOT_BUDGET_GB}GB"
if command -v compsize >/dev/null 2>&1; then
    real=$(compsize "$WT" 2>/dev/null | awk '/^TOTAL/{print $3}')
    [[ -n $real ]] && echo "real on-disk (compsize): $real  (du overstates via zstd+reflink)"
fi
printf '%-28s %8s %8s %6s %6s %6s  %s\n' SLOT du_GB incr_GB busy clean pushed status
total=0
declare -A SLOT_GB SLOT_INCR SLOT_BUSY SLOT_STATE
for slot_dir in "${SLOTS[@]}"; do
    slot=$(basename "$slot_dir")
    gb=$(du_gb "$slot_dir"); total=$((total+gb))
    incr=0
    while IFS= read -r d; do incr=$((incr + $(du_gb "$d"))); done \
        < <(find "$slot_dir" -type d -path '*/target/*/incremental' 2>/dev/null)
    busy=no; slot_busy "$slot_dir" && busy=YES
    clean=yes; slot_safe_to_drop "$slot_dir" || clean=no   # combined clean+pushed shorthand
    st=$(slot_state "$slot"); [[ -z $st ]] && st="unreg"
    SLOT_GB[$slot]=$gb; SLOT_INCR[$slot]=$incr; SLOT_BUSY[$slot]=$busy; SLOT_STATE[$slot]=$st
    flag=""; (( gb > SLOT_BUDGET_GB )) && flag=" <<OVER-BUDGET"
    cl=no; wt_all_clean=yes
    for c in hermit reverie liteinst2; do wt_clean "$slot_dir/$c" || wt_all_clean=no; done
    ps=no; slot_safe_to_drop "$slot_dir" && ps=yes
    printf '%-28s %8s %8s %6s %6s %6s  %s%s\n' \
        "$slot" "$gb" "$incr" "$busy" "$wt_all_clean" "$ps" "$st" "$flag"
done
echo "-----"
echo "TOTAL du: ${total}GB  (cap ${CAP_GB}GB)$( ((total>CAP_GB)) && echo '  *** OVER CAP ***')"
echo

# ---------------------------------------------------------------- actions ----
# Decide which tiers to run. --enforce escalates only while over cap.
run_incr=$PRUNE_INCR; run_idle=$PRUNE_IDLE; run_reclaim=$RECLAIM
if (( ENFORCE )); then
    (( total > CAP_GB )) && { run_incr=1; run_idle=1; run_reclaim=1; } \
        || echo "enforce: total ${total}GB already within cap ${CAP_GB}GB; no pruning needed."
fi

reclaimed=0
recompute_total() { total=0; for s in "${!SLOT_GB[@]}"; do total=$((total+${SLOT_GB[$s]})); done; }

# Tier 1: incremental caches (always regenerable).
if (( run_incr )); then
    echo "== tier 1: prune target/*/incremental/ =="
    for slot_dir in "${SLOTS[@]}"; do
        slot=$(basename "$slot_dir")
        (( ENFORCE )) && (( total <= CAP_GB )) && break
        if [[ ${SLOT_BUSY[$slot]} == YES ]]; then echo "  skip $slot (busy)"; continue; fi
        if [[ ${SLOT_STATE[$slot]} == active && $INCLUDE_ACTIVE == 0 ]]; then
            echo "  skip $slot (active; pass --include-active to prune its incremental)"; continue
        fi
        while IFS= read -r d; do
            [[ -d $d ]] || continue
            g=$(du_gb "$d")
            echo "  rm  $d  (${g}GB, regenerable)"
            rm -rf "$d" && { SLOT_GB[$slot]=$(( ${SLOT_GB[$slot]} - g )); reclaimed=$((reclaimed+g)); }
        done < <(find "$slot_dir" -type d -path '*/target/*/incremental' 2>/dev/null)
        recompute_total
    done
    echo
fi

# Tier 2: whole target/ of idle (released + clean + pushed + not busy) slots.
if (( run_idle )); then
    echo "== tier 2: prune whole target/ of released+clean+pushed idle slots =="
    for slot_dir in "${SLOTS[@]}"; do
        slot=$(basename "$slot_dir")
        (( ENFORCE )) && (( total <= CAP_GB )) && break
        [[ ${SLOT_BUSY[$slot]} == YES ]] && { echo "  skip $slot (busy)"; continue; }
        [[ ${SLOT_STATE[$slot]} == released ]] || { echo "  skip $slot (not released; state=${SLOT_STATE[$slot]})"; continue; }
        slot_safe_to_drop "$slot_dir" || { echo "  skip $slot (dirty or unpushed — refuse to touch)"; continue; }
        while IFS= read -r d; do
            [[ -d $d ]] || continue
            g=$(du_gb "$d")
            echo "  rm  $d  (${g}GB)"
            rm -rf "$d" && { SLOT_GB[$slot]=$(( ${SLOT_GB[$slot]} - g )); reclaimed=$((reclaimed+g)); }
        done < <(find "$slot_dir" -mindepth 2 -maxdepth 2 -type d -name target 2>/dev/null)
        recompute_total
    done
    echo
fi

# Tier 3: reclaim released+clean+pushed slots via release-worktree (push-then-remove).
if (( run_reclaim )); then
    echo "== tier 3: reclaim released+clean+pushed slots =="
    for slot_dir in "${SLOTS[@]}"; do
        slot=$(basename "$slot_dir")
        (( ENFORCE )) && (( total <= CAP_GB )) && break
        [[ ${SLOT_BUSY[$slot]} == YES ]] && { echo "  skip $slot (busy)"; continue; }
        [[ ${SLOT_STATE[$slot]} == released ]] || { echo "  skip $slot (not released)"; continue; }
        slot_safe_to_drop "$slot_dir" || { echo "  skip $slot (dirty or unpushed)"; continue; }
        echo "  reclaim $slot -> release-worktree.rs --slot $slot --clean"
        if "$ROOT/scripts/release-worktree.rs" --slot "$slot" --clean; then
            reclaimed=$((reclaimed + ${SLOT_GB[$slot]})); SLOT_GB[$slot]=0
        fi
        recompute_total
    done
    echo
fi

if (( PRUNE_INCR || PRUNE_IDLE || RECLAIM || ENFORCE )); then
    recompute_total
    echo "Done: reclaimed ~${reclaimed}GB du.  new TOTAL du: ${total}GB (cap ${CAP_GB}GB)."
else
    echo "Report only. Re-run with --prune-incremental (safe) or --enforce to act."
fi
