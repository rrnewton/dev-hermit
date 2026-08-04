#!/bin/bash
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# orphaned-task-detector.sh — REPORT-ONLY detector for taskgraph work that lost
# its owner to an agent recycle.
#
# THE DEFECT (task work-orphaned-by-agent-recycle-has-no-detector): an agent
# claims a tg task (status IN_PROGRESS, owner=<agent>), then recycles ~every
# 30-60 min. The task row survives with its owner string intact; the AGENT does
# not. Nothing detects this: an orphaned IN_PROGRESS task emits no wakeup, no
# failure, no queue entry — the wakeup system only surfaces READY tasks, so an
# owned-but-dead IN_PROGRESS task is invisible BY CONSTRUCTION. The absence of
# an owner emits no signal, exactly like a worktree slot held by a dead agent
# (see scripts/worktree-gc.sh orphan-candidate column). ONE root cause, two
# surfaces: NO LIVENESS SIGNAL TIES A RESOURCE TO A LIVE OWNER.
#
# WHY THIS SURFACE IS EXACT (and the slot surface was not):
#   tg has an explicit `owner` column AND the live fleet is directly
#   enumerable — every live agent is a tmux window on orc's isolated socket.
#   So "owner no longer exists" is an EXACT set-membership test, not a proxy.
#   The worktree-gc orphan column had to fall back to an idle-hours (source
#   mtime) heuristic precisely because worktree-state.json has no owner field
#   and no liveness link; there, an agent mid-thought looks idle and the signal
#   is ambiguous. HERE there is no idle-vs-dead ambiguity: a task owned by a
#   name that is NOT a live window is unambiguously orphaned, regardless of age.
#   => NO time threshold N is needed or used. Age is printed for triage only.
#   (Recycle cadence ~30-60 min is context, not a gate; deriving N would only
#   matter if we lacked the exact owner-existence check, which we do not.)
#
# REPORT ONLY — SAME SHAPE AS THE GC ORPHAN COLUMN. This tool NEVER reassigns,
# closes, or edits a task. It cross-references and prints. The coordinator
# decides routing (an orphan must become a task-with-an-owner OR an explicit
# close — never a silent third state). Reassignment is a human/coordinator call.
#
# LIVE FLEET SOURCE (the running thing, not a config — see CLAUDE.md "Verify A
# Mechanism By The Running Thing"): the actual tmux windows orc is running, read
# off orc's isolated socket. We collect window names across ALL sessions on that
# socket, so an agent living in any orc session counts as alive (conservative:
# over-counting alive => FEWER false orphans, never more).
#
# FAIL-SAFE: if the live fleet cannot be determined (socket missing, tmux fails,
# zero windows found) the tool ABORTS with exit 3 and flags NOTHING. It must
# never mass-flag every task as orphaned on a fleet-read failure — that is the
# same fail-safe posture as affected-test-selection -> FULL.
#
# Usage:
#   scripts/orphaned-task-detector.sh              # report orphaned IN_PROGRESS tasks
#   scripts/orphaned-task-detector.sh --all-status # also scan OPEN/BACKLOG owned tasks
#   scripts/orphaned-task-detector.sh --gate       # same report; exit 1 iff orphans found
#
# --gate changes ONLY the exit code, never the output: it lets a composite health
# poll (`ci-hub health`) go non-green when a real orphan exists, so orphans
# surface on EVERY poll instead of a one-off audit. The default (no --gate)
# keeps the report-only contract: exit 0 whether or not orphans exist. In BOTH
# modes a fail-safe fleet-read abort is exit 3 and flags nothing.
#
# Exit codes:
#   0  = ran; default mode (orphans may or may not exist, see report), or
#        --gate mode with ZERO orphans.
#   1  = --gate mode ONLY: ran and found >=1 orphan (actionable).
#   3  = could not determine the live fleet (nothing flagged; fail-safe).
#   64 = usage error.
set -uo pipefail

ALL_STATUS=0
GATE=0
while (($#)); do
    case "$1" in
        --all-status) ALL_STATUS=1 ;;
        --gate) GATE=1 ;;
        -h | --help) sed -n '/^# orphaned-task-detector\.sh —/,/^# Exit codes:/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "orphaned-task-detector.sh: unknown argument: $1" >&2; exit 64 ;;
    esac
    shift
done

command -v tg >/dev/null 2>&1 || { echo "orphaned-task-detector: 'tg' not on PATH" >&2; exit 3; }
command -v orc >/dev/null 2>&1 || { echo "orphaned-task-detector: 'orc' not on PATH" >&2; exit 3; }
command -v tmux >/dev/null 2>&1 || { echo "orphaned-task-detector: 'tmux' not on PATH" >&2; exit 3; }

# --- LIVE FLEET: window names across all sessions on orc's isolated socket. ---
SOCK=$(orc tmux ls 2>/dev/null | grep -oP 'TMUX_TMPDIR\):\s*\K\S+')
if [[ -z ${SOCK:-} || ! -d $SOCK ]]; then
    echo "orphaned-task-detector: FAIL-SAFE ABORT — cannot locate orc tmux socket" >&2
    echo "  (refusing to flag anything: an unreadable fleet must not mass-orphan tasks)" >&2
    exit 3
fi
export TMUX_TMPDIR="$SOCK"

declare -A LIVE
live_count=0
while IFS= read -r s; do
    [[ -z $s ]] && continue
    while IFS= read -r w; do
        [[ -z $w ]] && continue
        LIVE[$w]=1; live_count=$((live_count+1))
    done < <(tmux list-windows -t "$s" -F '#{window_name}' 2>/dev/null)
done < <(tmux list-sessions -F '#{session_name}' 2>/dev/null)

if (( live_count == 0 )); then
    echo "orphaned-task-detector: FAIL-SAFE ABORT — zero live windows on socket $SOCK" >&2
    echo "  (refusing to flag anything: an empty fleet read is a read failure, not 20 dead agents)" >&2
    exit 3
fi

# --- Owned, non-terminal tasks from the tg DB. Sentinel prefix 'ROW|' makes ---
# --- parsing immune to the sql header/separator/'(N rows)' footer.           ---
STATUS_FILTER="status='IN_PROGRESS'"
(( ALL_STATUS )) && STATUS_FILTER="status IN ('IN_PROGRESS','OPEN','BACKLOG')"
QUERY="SELECT 'ROW|' || owner || '|' || status || '|' || local_modified_at || '|' || local_id \
       FROM tasks WHERE owner IS NOT NULL AND owner != '' AND $STATUS_FILTER \
       ORDER BY owner, local_modified_at DESC"

now=$(date +%s)
echo "orphaned-task-detector: live fleet = ${#LIVE[@]} windows on $SOCK"
echo "  live agents: $(printf '%s ' "${!LIVE[@]}" | tr ' ' '\n' | sort | grep -v '^$' | tr '\n' ' ')"
echo "  scanning ${STATUS_FILTER}  [REPORT ONLY — no task is reassigned or closed]"
echo
printf '%-46s %-16s %-12s %8s\n' TASK OWNER status age_h
echo "-------------------------------------------------------------------------------------"

orphan_n=0; owned_n=0
declare -A ORPHAN_OWNERS
while IFS='|' read -r _tag owner status modified local_id; do
    owned_n=$((owned_n+1))
    # age in hours from last local modification (informational triage only).
    mt=$(date -d "$modified" +%s 2>/dev/null || echo "$now")
    age_h=$(( (now - mt) / 3600 ))
    if [[ -n ${LIVE[$owner]:-} ]]; then
        continue   # owner is a live window — not orphaned
    fi
    orphan_n=$((orphan_n+1))
    ORPHAN_OWNERS[$owner]=$(( ${ORPHAN_OWNERS[$owner]:-0} + 1 ))
    printf '%-46s %-16s %-12s %8s\n' "$local_id" "$owner" "$status" "$age_h"
done < <(tg sql "$QUERY" 2>/dev/null | grep '^ROW|')

echo "-------------------------------------------------------------------------------------"
echo "scanned $owned_n owned non-terminal task(s); ORPHANED (owner not in live fleet): $orphan_n"
if (( orphan_n > 0 )); then
    echo "orphan owners (agent no longer exists):"
    for o in "${!ORPHAN_OWNERS[@]}"; do echo "  $o  -> ${ORPHAN_OWNERS[$o]} task(s)"; done
    echo
    echo "ROUTE each (coordinator decision — never a silent third state):"
    echo "  * still wanted -> reassign: tg claim <task> (as the new owner) or tg update <task> --owner <agent>"
    echo "  * done/moot    -> close:    tg update <task> --status closed  (record why)"
    echo "This tool only DETECTS. It does not reassign or close anything."
fi
# --gate: signal orphan presence through the exit code so a composite health poll
# can go non-green. Default mode stays report-only (exit 0 regardless).
if (( GATE && orphan_n > 0 )); then
    exit 1
fi
exit 0
