#!/usr/bin/env bash
# Analyze an INFO log for the H9 rearm-failure signature.
# Interleaves "COMMIT turn N" with "inbound timer preemption event" and skid
# markers, then reports whether preemption CEASES before the run ends while
# COMMIT turns keep climbing and SleepUntil(0) explodes afterward.
# Usage: preempt_timeline.sh <info.log> [label]
set -uo pipefail
LOG="${1:?info.log}"; LABEL="${2:-$(basename "$(dirname "$LOG")")}"
[ -f "$LOG" ] || { echo "$LABEL: NO LOG ($LOG)"; exit 0; }
awk -v label="$LABEL" '
  /COMMIT turn/ { t=$0; sub(/.*COMMIT turn /,"",t); sub(/[^0-9_].*/,"",t); gsub(/_/,"",t); turn=t+0; last_commit=turn }
  /inbound timer preemption event/ { np++; if(!first_p) first_p=turn; last_p=turn }
  /Clock perf counter exceeds target|timer\.rs:80[0-9]|Consider increasing skid margin|perf counter/ { nskid++; if(!first_skid_turn) first_skid_turn=turn; last_skid_turn=turn }
  /SleepUntil\(LogicalTime\(0\)\)/ { su0_total++; if(last_p) su0_after=su0_after_marker; if(np>0 && turn>=last_p) su0_since_lastp++ }
  { if(np>0 && turn>last_p) after_lastp_commits++ }
  END {
    printf "%s: preempts=%d first_p_turn=%s last_p_turn=%s last_commit_turn=%s gap=%d skids=%d first_skid_turn=%s last_skid_turn=%s su0_total=%d su0_after_last_preempt=%d\n",
      label, np+0, (first_p?first_p:"-"), (last_p?last_p:"-"), (last_commit?last_commit:"-"),
      (last_commit&&last_p)?(last_commit-last_p):-1, nskid+0,
      (first_skid_turn?first_skid_turn:"-"), (last_skid_turn?last_skid_turn:"-"),
      su0_total+0, su0_since_lastp+0
    # H9 verdict heuristic
    if (np>0 && last_commit>0 && last_p>0) {
      g=last_commit-last_p
      if (g > 1000 && su0_since_lastp > 1000)
        printf "  -> H9 SIGNATURE: preemption CEASED %d turns before end; %d SleepUntil(0) after last preempt", g, su0_since_lastp
      else
        printf "  -> preemption continued to within %d turns of end (no clear rearm-failure)", g
      if (nskid>0 && last_skid_turn>=last_p-50) printf " ; skid marker near cessation (turn %s)", last_skid_turn
      printf "\n"
    } else if (np==0) {
      printf "  -> no preemption events at all (norcb-style: burn-out absent by config)\n"
    }
  }' "$LOG"