#!/usr/bin/env bash
# Shared single-PR lander for the FIFO-serialized manifest / e2e / backend-parity
# bucket. This is the DISCOVERABLE home for the landing sequence that used to live
# only in scratch/. It carries the three race-tolerance fixes below AND is always
# self-wrapped in `ci-hub land-lock run --child-deadline`, so the landing lease is
# bound to THIS bounded child's lifetime -- no hand-rolled `renewer.sh` loop that
# can outlive a dead agent and wedge the FIFO (the 2040-minute starvation bug).
#
# MUTATING PATH DISABLED: GitHub's server-side rebase API cannot atomically bind
# the observed target base. Help/docs parsing remains for archaeology, but the
# executable path refuses before mutation and points to safe-exact-head-land.
#
# Sequence (while holding the land-lock):
#   fetch fresh main -> GitHub-free eligibility gate (clean full-validate record
#   for the exact pre-rebase head; the label is non-authoritative) -> rebase
#   (union|plain) + push -> require a clean record for the exact pushed head ->
#   derive locally-validated through apply-local-label -> bounded merge-gate poll
#   -> gh pr merge --rebase (NEVER --admin) -> ancestry-verify.
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

# This legacy path asks GitHub to replay commits server-side. GitHub accepts an
# expected head but offers no atomic expected-base precondition, so main can
# move between our observation and the replay. It therefore cannot bind source
# X, observed base Y, and replay result Z strongly enough to authorize landing.
# Keep help/docs parsing available, but fail closed before any mutation until
# the minimal current-main safe lander extraction supplies that transaction.
echo "land-pr: REFUSED: legacy server-side replay cannot bind the actual base; use safe-exact-head-land after its current-main extraction lands" >&2
exit 4

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
# The label is only a cache. It never authorizes landing independently of the
# source ledger, including when shared credentials applied it. The exact PR head
# must have a clean full-coverage PASS record. The same predicate is checked
# again after rebase because a SHA-changing rebase invalidates the old receipt.
ORIG=$(git -C "$WT" rev-parse "origin/$BR" 2>/dev/null) || abandon "cannot resolve origin/$BR head for eligibility gate" 4
PRELABELS=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")' 2>/dev/null)
VS=$("$SCRIPT_DIR/local-validation-eligibility.sh" "$ORIG" "$PRELABELS" 2>&1); VRC=$?
say "local-validation eligibility(head=$ORIG) rc=$VRC: $VS"
case "$VRC" in
  0) say "landing eligibility: clean full-validate record for $ORIG" ;;
  3) abandon "GitHub-free landing gate: PR head $ORIG has a clean full-validate record that FAILED (known-failing); refusing to land" 4 ;;
  4) abandon "GitHub-free landing gate: PR head $ORIG has no clean full-validate PASS record; observed labels are non-authoritative" 4 ;;
  *) abandon "GitHub-free landing gate: could not evaluate exact-head validation evidence (rc=$VRC)" 4 ;;
esac

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

# 4. The pushed exact head needs its own ledger receipt. A rebase that changed
# the SHA cannot inherit the old authorization. Only the ledger-guarded applier
# may materialize the cache label; the lander never types it directly.
#
# 4a. Re-mint count-backed schema-5 rows from durable logs BEFORE reading the
# ledger. hermit's validate.sh writes a count-less schema-3 receipt when it can't
# reach the parent count helper; with the uncounted-receipt grandfather removed,
# such a genuine green would be NotValidated. The scan (append-safe, idempotent)
# upgrades HEAD's row from its own log so a real green is not stranded by a
# producer that failed to inline its counts. Best-effort: never aborts landing —
# eligibility below remains the authoritative fail-closed gate.
"$ROOT/ci-hub/validate/scan-finalize.sh" --hermit-checkout "$WT" || true
# Capture the VERBATIM first_error_line of every surviving red log into the
# durable append-only sidecar (ignored/validate-red-attribution.jsonl) BEFORE the
# /tmp log is evicted. Append-only + idempotent, so it races no appender and never
# duplicates; best-effort (never a fatal error) and never affects the landing
# verdict below -- it only preserves attribution that would otherwise die with the
# log. Until hermit validate.sh inlines first_error_line into the red row it
# writes, this is what makes a red attributable to which BUG (not just which gate)
# after the log is gone.
python3 "$ROOT/ci-hub/validate/attribute_reds.py" --last 0 --persist >/dev/null 2>&1 || true
PUSHLABELS=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")' 2>/dev/null)
VS=$("$SCRIPT_DIR/local-validation-eligibility.sh" "$HEAD" "$PUSHLABELS" 2>&1); VRC=$?
say "post-push local-validation eligibility(head=$HEAD) rc=$VRC: $VS"
[ "$VRC" -eq 0 ] || abandon "pushed head $HEAD has no clean exact-head full-validate PASS record; validate it before stamping" 4
"$ROOT/ci-hub/ci-hub" apply-local-label --pr "$PR" --repo "$R" \
  || abandon "ledger-guarded apply-local-label failed" 4
sleep 4
LB=$(with-proxy gh pr view "$PR" -R "$R" --json labels -q '[.labels[].name]|join(",")')
grep -q locally-validated <<<"$LB" || abandon "locally-validated stripped immediately (labels=$LB)" 4
say "ledger-derived label present; labels=$LB"

# 4b. a draft PR cannot be merged; marking ready fires a fresh (label-present)
# merge-gate run, which the poll below waits on.
if [ "$(with-proxy gh pr view "$PR" -R "$R" --json isDraft -q .isDraft)" = "true" ]; then
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

# 5b. The exact pushed head must carry a dereferenceable validation receipt at
# the final authorization boundary. A label or well-shaped comment is only a
# pointer; the parent-pinned verifier resolves the immutable receipt and checks
# its digest, repository, head, counted ledger row, and coverage obligations.
live_head=$(with-proxy gh pr view "$PR" -R "$R" --json headRefOid -q .headRefOid 2>/dev/null) \
  || abandon "could not resolve the live PR head before receipt authorization" 5
[ "$live_head" = "$HEAD" ] \
  || abandon "PR head moved before receipt authorization (expected $HEAD, observed ${live_head:-missing})" 5
receipt_comments=$(mktemp) \
  || abandon "could not allocate the receipt-comment observation file" 5
if ! with-proxy gh api --paginate --slurp \
    "repos/$R/issues/$PR/comments?per_page=100" >"$receipt_comments"; then
  rm -f -- "$receipt_comments"
  abandon "could not fetch comments for exact-head receipt authorization" 5
fi
receipt_detail=$("$ROOT/ci-hub/validation/verify_receipt.sh" \
  --repo "$R" --sha "$HEAD" --comments "$receipt_comments" 2>&1)
receipt_rc=$?
rm -f -- "$receipt_comments"
if [ "$receipt_rc" -ne 0 ]; then
  abandon "exact-head validation receipt REFUSED for $HEAD: ${receipt_detail:-no receipt}" 5
fi
say "exact-head validation receipt authorized: $receipt_detail"

# 6. FIX 2: the merge command is the mergeability arbiter. Attempt `gh pr merge
# --rebase` (NEVER --admin) in a bounded retry loop -- the call forces GitHub to
# recompute mergeability, resolving a stuck UNKNOWN here. Treat "already merged"
# as success; a genuine block surfaces as a persistent error after the budget.
merged=""; out=""
for mtries in $(seq 12); do
  out=$(with-proxy gh pr merge "$PR" -R "$R" --rebase \
    --match-head-commit "$HEAD" 2>&1) && { merged=ok; break; }
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
