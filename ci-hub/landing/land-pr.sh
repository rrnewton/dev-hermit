#!/usr/bin/env bash
# Shared single-PR lander for the FIFO-serialized manifest / e2e / backend-parity
# bucket. This is the DISCOVERABLE home for the landing sequence that used to live
# only in scratch/. It carries the three race-tolerance fixes below AND is always
# self-wrapped in `ci-hub land-lock run --child-deadline`, so the landing lease is
# bound to THIS bounded child's lifetime -- no hand-rolled `renewer.sh` loop that
# can outlive a dead agent and wedge the FIFO (the 2040-minute starvation bug).
#
# Sequence (while holding the land-lock):
#   fetch fresh main -> GitHub-free eligibility gate (label present OR clean
#   full-validate record for the exact pre-rebase head, via ci-hub
#   validate-status; else ABANDON, never fabricate green) -> rebase (union|plain)
#   + push -> re-stamp locally-validated (metadata, AFTER push, since
#   `synchronize` strips it) -> bounded merge-gate poll -> gh pr merge --rebase
#   (NEVER --admin) -> ancestry-verify.
#
# Three fixes distilled from the 2026-08-03 stuck-gate diagnosis:
#   1. Race-tolerant gate poll: never bail on a transient merge-gate FAILURE; poll
#      the latest exact-head pull_request/workflow_dispatch Actions run and ride
#      through FAILURE/IN_PROGRESS to SUCCESS. PR statusCheckRollup is too narrow:
#      it omits the workflow_run-triggered dispatch that can carry the success.
#   2. The merge command is the mergeability arbiter, NOT mergeStateStatus (which
#      sticks at UNKNOWN); attempt `gh pr merge --rebase` in a bounded retry loop.
#   3. Self-heal the lagging-invalidate label strip: on a COMPLETED/FAILURE run
#      with the label now absent, re-add it (the `labeled` event refires green).
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
#   --union          use the additive manifest union-rebase (union-rebase.sh);
#                    default is a plain `git rebase origin/main`.
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
      --repo "$R" --pr "$PR" --source "$ROOT/hermit" \
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

# 1. fresh main
with-proxy git -C "$WT" fetch -q origin main || abandon "fetch origin/main failed" 2
with-proxy git -C "$WT" fetch -q origin "$BR" 2>/dev/null || true

# 1b. GITHUB-FREE LANDING GATE (owner P0 lander-lands-on-local-validate-only).
# We stamp locally-validated in step 4 to satisfy the merge-gate WITHOUT waiting
# on hosted CI. That stamp is only legitimate if this PR has ALREADY earned it:
# either the label is present already, OR the local validate ledger holds a clean
# full-coverage PASS for the PR's exact PRE-REBASE head. A clean rebase (step 2)
# replays that validated content; a conflicting rebase abandons before any stamp.
# This is the single decision point for GitHub-free eligibility -- never stamp a
# head we cannot show was validated (no fabricated green). The predicate lives in
# ci-hub validate-status (lib/validate_status.rs), never looser than the
# validate.sh:4161 stamp guard.
ORIG=$(git -C "$WT" rev-parse "origin/$BR" 2>/dev/null) || abandon "cannot resolve origin/$BR head for eligibility gate" 4
PRELABELS=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")' 2>/dev/null)
if grep -q locally-validated <<<"$PRELABELS"; then
  say "landing eligibility: locally-validated already present (head=$ORIG)"
else
  VS=$("$ROOT/ci-hub/ci-hub" validate-status --sha "$ORIG" 2>&1); VRC=$?
  say "validate-status(head=$ORIG) rc=$VRC: $VS"
  case "$VRC" in
    0) say "landing eligibility: clean full-validate record for $ORIG" ;;
    3) abandon "GitHub-free landing gate: PR head $ORIG has a clean full-validate record that FAILED (known-failing); refusing to land" 4 ;;
    *) abandon "GitHub-free landing gate: PR head $ORIG has neither the locally-validated label nor a clean full-validate PASS record (validate-status rc=$VRC); refusing to fabricate green" 4 ;;
  esac
fi

# 2. rebase onto latest main + push
if [ "$UNION" -eq 1 ]; then
  ulog="/tmp/land-$PR-union.log"
  "$SCRIPT_DIR/union-rebase.sh" "$WT" "$BR" --push >"$ulog" 2>&1
  RES=$(grep -E '^RESULT' "$ulog" | tail -1)
  say "union: ${RES:-<none> (see $ulog)}"
  case "$RES" in
    *" CLEAN"|*" UNIONED") : ;;
    *) abandon "union-rebase did not converge: ${RES:-see $ulog}" 3 ;;
  esac
else
  git -C "$WT" checkout -q -B "_land_$PR" "origin/$BR" || abandon "checkout origin/$BR failed" 3
  if ! GIT_EDITOR=true git -C "$WT" rebase origin/main >/dev/null 2>&1; then
    git -C "$WT" rebase --abort >/dev/null 2>&1
    git -C "$WT" checkout -q --detach origin/main 2>/dev/null || true
    abandon "plain rebase onto origin/main conflicted (needs owner/--union)" 3
  fi
  with-proxy git -C "$WT" push -q --force-with-lease origin "HEAD:$BR" || abandon "force-with-lease push failed" 3
  git -C "$WT" checkout -q --detach origin/main 2>/dev/null || true
fi

# 3. record pushed head
with-proxy git -C "$WT" fetch -q origin "$BR"
HEAD=$(git -C "$WT" rev-parse "origin/$BR")
say "pushed head=$HEAD"

# 4. re-stamp locally-validated (METADATA only) AFTER the push, then verify it
# stuck. The push's `synchronize` strips it; re-stamping never re-pushes.
with-proxy gh pr edit "$PR" -R "$R" --add-label locally-validated >/dev/null || abandon "add locally-validated label failed" 4
sleep 4
LB=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")')
grep -q locally-validated <<<"$LB" || abandon "locally-validated stripped immediately (labels=$LB)" 4
say "stamped; labels=$LB"

# 4b. a draft PR cannot be merged; marking ready fires a fresh (label-present)
# merge-gate run, which the poll below waits on.
if [ "$(with-proxy gh pr view "$PR" -R "$R" --json isDraft -q .isDraft)" = "true" ]; then
  with-proxy gh pr ready "$PR" -R "$R" >/dev/null && say "marked ready (was draft)"
  sleep 4
fi

# 5. FIX 1 + FIX 3: bounded, race-tolerant merge-gate poll. Query Actions by the
# exact PR head and admit only pull_request/workflow_dispatch runs. The latter is
# load-bearing: merge-gate's workflow_run controller dispatches the real PR-head
# gate, but that success is absent from PR statusCheckRollup. Ride through
# transient FAILURE/IN_PROGRESS/QUEUED to COMPLETED/SUCCESS. On a failure with
# the label now absent, re-add it so `labeled` refires the gate. UNKNOWN/stuck is
# actionable: at the measured deadline, ABANDON visibly instead of waiting.
selector="$SCRIPT_DIR/merge-gate-status.jq"
deadline=$((SECONDS+GATE_DEADLINE)); gate=""; cj="UNAVAILABLE/PENDING"; gate_detail="no successful Actions query"
while (( SECONDS < deadline )); do
  if actions_json=$(with-proxy gh api \
      "repos/$R/actions/workflows/merge-gate.yml/runs?head_sha=$HEAD&per_page=100" \
      2>/dev/null); then
    row=$(jq -r --arg sha "$HEAD" -f "$selector" <<<"$actions_json")
    IFS=$'\t' read -r gate_status gate_conclusion gate_event gate_run gate_url gate_created <<<"$row"
    cj="${gate_status}/${gate_conclusion}"
    gate_detail="event=$gate_event run=$gate_run created=$gate_created url=$gate_url"
  else
    cj="UNAVAILABLE/PENDING"
    gate_detail="Actions API unavailable"
  fi
  say "merge-gate(exact-head)=$cj $gate_detail"
  [ "$cj" = "COMPLETED/SUCCESS" ] && { gate=ok; break; }
  if [ "$cj" = "COMPLETED/FAILURE" ]; then
    lb=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")' 2>/dev/null)
    if ! grep -q locally-validated <<<"$lb"; then
      with-proxy gh pr edit "$PR" -R "$R" --add-label locally-validated >/dev/null 2>&1 \
        && say "re-stamped locally-validated (lagging-invalidate strip)"
    fi
  fi
  sleep 15
done
[ "$gate" = ok ] || abandon "merge-gate deadline ${GATE_DEADLINE}s exceeded for exact head $HEAD (last=$cj; $gate_detail)" 5

# 6. FIX 2: the merge command is the mergeability arbiter. Attempt `gh pr merge
# --rebase` (NEVER --admin) in a bounded retry loop -- the call forces GitHub to
# recompute mergeability, resolving a stuck UNKNOWN here. Treat "already merged"
# as success; a genuine block surfaces as a persistent error after the budget.
merged=""; out=""
for mtries in $(seq 12); do
  out=$(with-proxy gh pr merge "$PR" -R "$R" --rebase 2>&1) && { merged=ok; break; }
  grep -qi 'already merged' <<<"$out" && { say "already merged"; merged=ok; break; }
  say "merge attempt $mtries not-ready: $(tr '\n' ' ' <<<"$out" | tail -c 160)"
  sleep 15
done
[ "$merged" = ok ] || abandon "gh pr merge --rebase did not succeed after 12 tries (last: $(tr '\n' ' ' <<<"$out" | tail -c 160))" 6

# 7. ancestry-verify: a PR-head hash is NOT a landing. Confirm the merge commit is
# reachable from origin/main before declaring success.
with-proxy git -C "$ROOT/hermit" fetch -q origin main 2>/dev/null \
  || with-proxy git -C "$WT" fetch -q origin main
MC=$(with-proxy gh pr view "$PR" -R "$R" --json mergeCommit -q .mergeCommit.oid)
GITDIR="$ROOT/hermit"; git -C "$GITDIR" cat-file -e "$MC" 2>/dev/null || GITDIR="$WT"
if git -C "$GITDIR" merge-base --is-ancestor "$MC" origin/main 2>/dev/null; then
  if "$ROOT/ci-hub/remediation/land_and_arm.py" complete --repo "$R" --pr "$PR"; then
    say "LANDED:$MC"; exit 0
  fi
  say "POST-LAND ARM PENDING: $MC is landed; durable recovery will retry"
  with-proxy gh pr comment "$PR" -R "$R" --body \
    "[coordinator, $MODEL] LANDED at $MC, but immediate exact-SHA verification arming failed. The durable prepared intent remains open and the ORC recovery watcher will retry; this landing is not being reported complete." \
    >/dev/null 2>&1 || say "WARN: could not post post-land arm warning"
  exit 8
else
  abandon "merge reported but $MC is NOT an ancestor of origin/main (verify manually)" 7
fi
