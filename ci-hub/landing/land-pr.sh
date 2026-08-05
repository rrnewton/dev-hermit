#!/usr/bin/env bash
# Shared single-PR lander for the FIFO-serialized manifest / e2e / backend-parity
# bucket. This is the DISCOVERABLE home for the landing sequence that used to live
# only in scratch/. It carries the three race-tolerance fixes below AND is always
# self-wrapped in `ci-hub land-lock run --child-deadline`, so the landing lease is
# bound to THIS bounded child's lifetime -- no hand-rolled `renewer.sh` loop that
# can outlive a dead agent and wedge the FIFO (the 2040-minute starvation bug).
#
# Sequence (while holding the land-lock):
#   fetch fresh main + immutable PR head -> exact-head hard-green authority
#   (counted local full OR hosted portable+privileged) -> bounded merge-gate poll
#   -> head-matched GitHub rebase merge WITHOUT rewriting the PR branch ->
#   ancestry-verify -> arm exact replay-SHA post-facto validation.
#
# Three fixes distilled from the 2026-08-03 stuck-gate diagnosis:
#   1. Trinary gate poll: PASSED lands, FAILED stops, and NO_RESULT blocks while
#      re-dispatching. PR statusCheckRollup is too narrow: it omits the
#      workflow_run-triggered dispatch that can carry the success.
#   2. The merge command is the mergeability arbiter, NOT mergeStateStatus (which
#      sticks at UNKNOWN); attempt `gh pr merge --rebase` in a bounded retry loop.
#   3. A genuine gate failure is never overwritten by re-stamping metadata.
#
# Boxing principle applied to ourselves: EVERY wait here is bounded, and every
# terminal bail emits a visible ABANDON signal (stderr + a role-tagged PR comment)
# so an abandoned PR never silently languishes (the #244 pattern). On any bail the
# `land-lock run` wrapper releases the lock so the next FIFO waiter proceeds.
#
# Usage:
#   ci-hub/landing/land-pr.sh <PR> <BRANCH> [--union] [--agent NAME]
#                             [--gate-deadline SECS] [--child-deadline SECS]
#                             [--foreground]
#   --union          rejected: automatic union conflict resolution cannot retain
#                    soft green without a typed resolver judgement.
#   --agent NAME     lock holder + PR-comment role tag (default: hermit-lander).
#   --gate-deadline  bound on the merge-gate poll (default 1080).
#   --child-deadline hard ceiling for the whole land subtree (default: twice the
#                    gate deadline); passed to `land-lock run`, which kills and
#                    releases on breach. It must be greater than gate-deadline.
#   --foreground     diagnostic escape hatch; default launches under nohup+setsid
#                    with a durable timestamped log and returns immediately.
set -uo pipefail

PR=""; BR=""; UNION=0; INNER=0; DETACHED_CHILD=0; FOREGROUND=0
AGENT="hermit-lander"
MODEL="${LANDER_MODEL:-opus-4.8}"
# Measured 2026-08-04 over 11 successful pull_request demo-hot-path runs created
# since 2026-08-03T23:00Z: median=586s, p90=646s, p95/p99/max=864s. The default
# is ceil(max * 1.25) = 1080s; the whole-child ceiling gets two gate windows.
GATE_DEADLINE=1080
CHILD_DEADLINE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --union) UNION=1 ;;
    --agent) AGENT="$2"; shift ;;
    --gate-deadline) GATE_DEADLINE="$2"; shift ;;
    --child-deadline) CHILD_DEADLINE="$2"; shift ;;
    --foreground) FOREGROUND=1 ;;
    --_detached) DETACHED_CHILD=1 ;;
    --_inner) INNER=1 ;;
    -h|--help) sed -n '1,50p' "$0"; exit 0 ;;
    -*) echo "land-pr: unknown flag $1" >&2; exit 2 ;;
    *) if [ -z "$PR" ]; then PR="$1"; elif [ -z "$BR" ]; then BR="$1"; else echo "land-pr: extra arg $1" >&2; exit 2; fi ;;
  esac
  shift
done
if [ -z "$PR" ] || [ -z "$BR" ]; then
  echo "usage: land-pr.sh <PR> <BRANCH> [--union] [--agent NAME] [--gate-deadline S] [--child-deadline S]" >&2
  exit 2
fi
case "$GATE_DEADLINE" in ''|*[!0-9]*|0) echo "land-pr: gate deadline must be positive seconds" >&2; exit 2 ;; esac
if [ -z "$CHILD_DEADLINE" ]; then
  CHILD_DEADLINE=$((GATE_DEADLINE * 2))
fi
case "$CHILD_DEADLINE" in ''|*[!0-9]*|0) echo "land-pr: child deadline must be positive seconds" >&2; exit 2 ;; esac
if [ "$CHILD_DEADLINE" -le "$GATE_DEADLINE" ]; then
  echo "land-pr: child deadline must be greater than gate deadline" >&2
  exit 2
fi
if [ -n "${CI_HUB_DOCS_PARSE_ONLY:-}" ]; then
  printf 'DOCS PARSE OK: land-pr.sh pr=%s branch=%s union=%s agent=%s\n' \
    "$PR" "$BR" "$UNION" "$AGENT"
  exit 0
fi
if [ "$UNION" -eq 1 ]; then
  echo "land-pr: --union is refused: automatic conflict resolution cannot mint soft green without a typed resolver judgement" >&2
  exit 3
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
WT="$ROOT/worktrees/lander/hermit"
R=rrnewton/hermit
say(){ echo "[land#$PR] $*"; }
comment_abandon(){
  local reason="$1"
  with-proxy gh pr comment "$PR" -R "$R" --body \
    "[coordinator, $MODEL] ABANDONED landing attempt: ${reason}. The lock was released so the FIFO can continue. Retry if the PR remains open; durable recovery will arm exact-SHA verification if GitHub completed the merge." \
    >/dev/null 2>&1 || say "WARN: could not post ABANDON comment"
}
comment_no_result(){
  local reason="$1"
  with-proxy gh pr comment "$PR" -R "$R" --body \
    "[coordinator, $MODEL] NO-RESULT landing pause: ${reason}. No failing verdict was recorded; the missing check was re-dispatched and this PR remains blocked until it produces PASSED or FAILED." \
    >/dev/null 2>&1 || say "WARN: could not post NO-RESULT comment"
}

# A real land exceeds the agent shell's foreground time budget. Launch the
# entire self-healing lock/land/arm sequence in a new session by default; the
# timestamped log is the durable observation surface across agent recycling.
if [ "$INNER" -eq 0 ] && [ "$DETACHED_CHILD" -eq 0 ] && [ "$FOREGROUND" -eq 0 ]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  safe_pr=${PR//[^A-Za-z0-9_.-]/_}
  log_dir="${CI_HUB_LANDING_LOG_DIR:-$ROOT/ignored/ci-hub/landing}"
  log="$log_dir/land-pr${safe_pr}-${stamp}-$$.log"
  mkdir -p "$log_dir"
  detached_args=(--_detached "$PR" "$BR" --agent "$AGENT" \
    --gate-deadline "$GATE_DEADLINE" --child-deadline "$CHILD_DEADLINE")
  [ "$UNION" -eq 1 ] && detached_args+=(--union)
  printf 'DETACHED LAND START pr=%s branch=%s agent=%s started_at=%s\n' \
    "$PR" "$BR" "$AGENT" "$stamp" >"$log"
  nohup setsid "$0" "${detached_args[@]}" </dev/null >>"$log" 2>&1 &
  detached_pid=$!
  printf 'DETACHED LAND PID pid=%s\n' "$detached_pid" >>"$log"
  printf 'DETACHED LAND: pid=%s log=%s\n' "$detached_pid" "$log"
  exit 0
fi

# --- outer: self-wrap in land-lock run so the lease is bound to this bounded ---
# child. `run` acquires (FIFO), heartbeats only while we live, and ALWAYS releases
# on exit; --child-deadline hard-kills + releases if we ever wedge.
if [ "$INNER" -eq 0 ]; then
  # A replacement lander inherits durable remediation from state, without
  # depending on a wake sent to its predecessor. This acknowledges discovery;
  # the obligation remains open until the repair SHA is explicitly resolved.
  "$ROOT/ci-hub/ci-hub" inherit-obligations --agent "$AGENT" \
    --session "${ORC_AGENT_SESSION_ID:-${HOSTNAME:-unknown}:$$}"
  args=(--_inner "$PR" "$BR" --agent "$AGENT" --gate-deadline "$GATE_DEADLINE")
  [ "$UNION" -eq 1 ] && args+=(--union)
  # Persist the exact-SHA verification obligation intent before the bounded
  # child can merge. If this process dies after the merge, the ORC recovery
  # watcher observes the merged SHA and arms both verifiers.
  if ! "$ROOT/ci-hub/remediation/land_and_arm.py" prepare \
      --repo "$R" --pr "$PR" --source "$WT" \
      --land-mode speculative --actor "$AGENT"; then
    say "ABANDON: could not prepare the post-land verification obligation"
    comment_abandon "could not prepare the mandatory post-land verification obligation"
    exit 2
  fi
  "$ROOT/ci-hub/ci-hub" land-lock run --agent "$AGENT" --pr "$PR" \
    --child-deadline "$CHILD_DEADLINE" -- "$0" "${args[@]}"
  rc=$?
  # The hard deadline kills the entire inner process group, so only this outer
  # supervisor remains able to emit the durable PR-side abandonment signal.
  case "$rc" in
    1) comment_abandon "timed out waiting for the landing lock" ;;
    124) comment_abandon "land subtree exceeded the ${CHILD_DEADLINE}s hard deadline and was killed" ;;
  esac
  exit "$rc"
fi

# --- inner: we now hold the land-lock ----------------------------------------
# Visible terminal ABANDON signal: stderr + role-tagged PR comment, then exit.
abandon(){
  local reason="$1" code="${2:-1}"
  say "ABANDON: $reason"
  comment_abandon "$reason"
  exit "$code"
}

# 1. Freeze the exact PR head and current base. Do NOT rewrite the branch: its
# hard-green and adversarial-review evidence remain applicable to X, while
# GitHub's head-matched rebase merge produces the probabilistic replay Z.
with-proxy git -C "$WT" fetch -q origin main || abandon "fetch origin/main failed" 2
with-proxy git -C "$WT" fetch -q origin "$BR" || abandon "fetch origin/$BR failed" 2
BASE=$(git -C "$WT" rev-parse origin/main) || abandon "cannot resolve fresh origin/main" 2
PR_META=$(with-proxy gh pr view "$PR" -R "$R" \
  --json state,headRefName,headRefOid,baseRefName,isDraft 2>/dev/null) \
  || abandon "cannot read exact PR identity" 2
HEAD=$(jq -r .headRefOid <<<"$PR_META")
LIVE_BRANCH=$(jq -r .headRefName <<<"$PR_META")
LIVE_BASE=$(jq -r .baseRefName <<<"$PR_META")
LIVE_STATE=$(jq -r .state <<<"$PR_META")
[ "$LIVE_STATE" = OPEN ] || abandon "PR state is $LIVE_STATE, not OPEN" 2
[ "$LIVE_BRANCH" = "$BR" ] || abandon "branch mismatch: argument=$BR GitHub=$LIVE_BRANCH" 2
[ "$LIVE_BASE" = main ] || abandon "PR targets $LIVE_BASE, not main" 2
REMOTE_HEAD=$(git -C "$WT" rev-parse "origin/$BR") \
  || abandon "cannot resolve origin/$BR" 2
[ "$REMOTE_HEAD" = "$HEAD" ] \
  || abandon "GitHub/git head mismatch: GitHub=$HEAD origin/$BR=$REMOTE_HEAD" 2
[[ "$HEAD" =~ ^[0-9a-f]{40}$ ]] || abandon "PR head is not a full commit id: $HEAD" 2
say "frozen source=$HEAD current-main-base=$BASE (branch left unchanged)"

# 2. One hard-green authority, two interchangeable execution sources. A label is
# only a cache. The JSON record carries the exact SHA and source identities.
HARD_JSON=$(python3 "$SCRIPT_DIR/hard_green.py" --sha "$HEAD" --repo "$R" --json 2>&1)
HARD_RC=$?
say "source hard-green rc=$HARD_RC: $(jq -r '.verdict // "unparseable"' <<<"$HARD_JSON" 2>/dev/null)"
case "$HARD_RC" in
  0) : ;;
  3) abandon "exact source $HEAD is hard-red or has contradictory authorities: $HARD_JSON" 4 ;;
  4) abandon "exact source $HEAD has no hard-green result from local full or hosted portable+privileged" 4 ;;
  *) abandon "could not evaluate exact-source hard-green authority: $HARD_JSON" 4 ;;
esac
HARD_AUTHORITIES=$(jq -r '.passing_authorities | join(",")' <<<"$HARD_JSON")

# Preserve the local label as a derived cache when local evidence was the passing
# source; never require it for the hosted hard-green path.
if grep -q 'local-full-validate' <<<"$HARD_AUTHORITIES"; then
  "$ROOT/ci-hub/ci-hub" apply-local-label --pr "$PR" --repo "$R" \
    || abandon "ledger-guarded local evidence publication failed" 4
fi

# A draft PR cannot be merged; marking ready fires a fresh exact-head gate.
if [ "$(jq -r .isDraft <<<"$PR_META")" = "true" ]; then
  with-proxy gh pr ready "$PR" -R "$R" >/dev/null && say "marked ready (was draft)"
  sleep 4
fi

# 5. FIX 1 + FIX 3: bounded, race-tolerant merge-gate poll. Query Actions by the
# exact PR head and admit only pull_request/workflow_dispatch runs. The latter is
# load-bearing: merge-gate's workflow_run controller dispatches the real PR-head
# gate, but that success is absent from PR statusCheckRollup. A genuine FAILED
# result stops immediately. NO_RESULT (cancelled/skipped/neutral/pending/absent)
# blocks without becoming a failure and re-dispatches the gate once per observed
# terminal hole. UNKNOWN/stuck exits with a distinct temporary/no-result code.
selector="$SCRIPT_DIR/merge-gate-status.jq"
outcome_helper="$ROOT/ci-hub/check_outcome.py"
deadline=$((SECONDS+GATE_DEADLINE)); gate=""; cj="UNAVAILABLE/PENDING"; gate_detail="no successful Actions query"; dispatched_run=""; gate_status="UNAVAILABLE"; gate_conclusion="PENDING"
while (( SECONDS < deadline )); do
  if actions_json=$(with-proxy gh api \
      "repos/$R/actions/workflows/merge-gate.yml/runs?head_sha=$HEAD&per_page=100" \
      2>/dev/null); then
    gate_run=$(python3 "$outcome_helper" \
        --select-latest-run --head-sha "$HEAD" \
        --event pull_request --event workflow_dispatch <<<"$actions_json")
    row=$(jq -r -f "$selector" <<<"$gate_run")
    IFS=$'\t' read -r gate_status gate_conclusion gate_event gate_run gate_url gate_created <<<"$row"
    cj="${gate_status}/${gate_conclusion}"
    gate_detail="event=$gate_event run=$gate_run created=$gate_created url=$gate_url"
  else
    gate_status="UNAVAILABLE"
    gate_conclusion="PENDING"
    cj="UNAVAILABLE/PENDING"
    gate_detail="Actions API unavailable"
  fi
  check_outcome=$(python3 "$outcome_helper" --status "$gate_status" --conclusion "$gate_conclusion")
  say "merge-gate(exact-head)=$cj outcome=$check_outcome $gate_detail"
  case "$check_outcome" in
    PASSED) gate=ok; break ;;
    FAILED) abandon "merge-gate produced a genuine FAILED verdict for exact head $HEAD ($cj; $gate_detail)" 5 ;;
    NO_RESULT)
      # A terminal hole or an absent run needs a new observation. Do not duplicate
      # an already queued/running run, and do not dispatch the same terminal hole
      # repeatedly while GitHub is still creating its successor.
      if { [ "$gate_status" = "COMPLETED" ] || [ "$gate_status" = "MISSING" ]; } &&
         [ "${gate_run:--}" != "$dispatched_run" ]; then
        if with-proxy gh workflow run merge-gate.yml -R "$R" --ref "$BR" -f pr_number="$PR"; then
          dispatched_run="${gate_run:--}"
          say "merge-gate NO_RESULT re-dispatched for exact head $HEAD"
        else
          say "merge-gate NO_RESULT re-dispatch failed; will retry"
        fi
      fi
      ;;
  esac
  sleep 15
done
if [ "$gate" != ok ]; then
  reason="merge-gate remained NO_RESULT for exact head $HEAD through the ${GATE_DEADLINE}s deadline (last=$cj; $gate_detail)"
  say "NO-RESULT: $reason"
  comment_no_result "$reason"
  exit 75
fi

# Re-check identity at the final authorization boundary. The hard-green record
# was for X; a moved PR head cannot inherit it.
live_head=$(with-proxy gh pr view "$PR" -R "$R" --json headRefOid -q .headRefOid 2>/dev/null) \
  || abandon "could not resolve the live PR head before merge authorization" 5
[ "$live_head" = "$HEAD" ] \
  || abandon "PR head moved after hard-green authorization (expected $HEAD, observed ${live_head:-missing})" 5

# 6. FIX 2: the merge command is the mergeability arbiter. Attempt `gh pr merge
# --rebase` (NEVER --admin) in a bounded retry loop -- the call forces GitHub to
# recompute mergeability, resolving a stuck UNKNOWN here. Treat "already merged"
# as success; a genuine block surfaces as a persistent error after the budget.
with-proxy git -C "$WT" fetch -q origin main \
  || abandon "could not refresh main immediately before merge" 6
BASE_BEFORE_MERGE=$(git -C "$WT" rev-parse origin/main)
say "speculative replay requested: hard source=$HEAD onto observed main=$BASE_BEFORE_MERGE"
case "$AGENT" in
  hermit-coord|codex-coord|*codex*)
    with-proxy gh pr edit "$PR" -R "$R" --add-label codex-coord >/dev/null 2>&1 \
      || say "WARN: could not apply codex-coord label"
    ;;
esac
merged=""; out=""
for mtries in $(seq 12); do
  out=$(with-proxy gh pr merge "$PR" -R "$R" --rebase \
    --match-head-commit "$HEAD" 2>&1) && { merged=ok; break; }
  grep -qi 'already merged' <<<"$out" && { say "already merged"; merged=ok; break; }
  say "merge attempt $mtries not-ready: $(tr '\n' ' ' <<<"$out" | tail -c 160)"
  sleep 15
done
[ "$merged" = ok ] || abandon "gh pr merge --rebase did not succeed after 12 tries (last: $(tr '\n' ' ' <<<"$out" | tail -c 160))" 6

# 7. ancestry-verify: a PR-head hash is NOT a landing. Confirm the replay commit is
# reachable from origin/main before declaring success.
with-proxy git -C "$WT" fetch -q origin main \
  || abandon "could not fetch landed main for ancestry verification" 7
MC=$(with-proxy gh pr view "$PR" -R "$R" --json mergeCommit -q .mergeCommit.oid)
if git -C "$WT" merge-base --is-ancestor "$MC" origin/main 2>/dev/null; then
  ARM_OUT=$("$ROOT/ci-hub/remediation/land_and_arm.py" complete \
    --repo "$R" --pr "$PR" 2>&1)
  ARM_RC=$?
  if [ "$ARM_RC" -eq 0 ]; then
    with-proxy gh pr comment "$PR" -R "$R" --body \
      "[coordinator, $MODEL] codex-coord/hermit-coord coordinating with orc-coord: LANDED source $HEAD after exact-SHA hard green from ${HARD_AUTHORITIES:-unknown}; GitHub rebased it onto the then-current main (observed pre-merge base $BASE_BEFORE_MERGE) as replay $MC. Replay $MC is soft green until the durable post-facto local/GitHub obligation completes. ${ARM_OUT}" \
      >/dev/null 2>&1 || say "WARN: could not post landing evidence comment"
    say "LANDED:$MC source-hard=$HARD_AUTHORITIES replay-soft post-facto-armed"
    exit 0
  fi
  say "POST-LAND ARM PENDING: $MC is landed; durable recovery will retry"
  with-proxy gh pr comment "$PR" -R "$R" --body \
    "[coordinator, $MODEL] LANDED at $MC, but immediate exact-SHA verification arming failed. The durable prepared intent remains open and the ORC recovery watcher will retry; this landing is not being reported complete." \
    >/dev/null 2>&1 || say "WARN: could not post post-land arm warning"
  exit 8
else
  abandon "merge reported but $MC is NOT an ancestor of origin/main (verify manually)" 7
fi
