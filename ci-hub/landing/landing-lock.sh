#!/usr/bin/env bash
# Deterministic shared-file LANDING MUTEX for the dev-hermit PR drain.
#
# Why this exists: every backend-parity / e2e-manifest PR mutates the SAME two
# shared registries (tests/e2e/manifests/backend-parity-c.toml and
# tests/e2e/manifests/inventory/test-files.json). When N landers push+merge
# concurrently, each land moves origin/main and DIRTYs every other in-flight PR,
# so the pack "serializes" by collision-and-retry -- burning the single
# self-hosted [gate] runner and converging slowly. This mutex turns that scrum
# into an orderly queue: a lander acquires the lock before its
# re-union -> push -> stamp -> merge -> ancestry-verify sequence and releases it
# after, so exactly one land is in flight at a time.
#
# Design (small + deterministic):
#   * flock(1) makes each check-and-set on the lockfile atomic across processes.
#   * The "held" state is a LEASE with an expiry, not a held fd -- so acquire in
#     one shell and release in another Just Work, and (a) a dead holder cannot
#     wedge the pack: once its lease expires, the next waiter reclaims it.
#   * (b) The lockfile records the holder's agent + PR + host + timestamps for
#     debuggability (`status` prints them).
#   * (c) Waiters enqueue in a FIFO so ordering is deterministic and each waiter
#     sees its position; `release` frees the lock immediately so the head of the
#     queue proceeds on its next (short) poll rather than polling blindly.
#
# Lockfile:  ~/work/dev-hermit/.landing-lock         (holder metadata; the lock)
# Guard:     ~/work/dev-hermit/.landing-lock.guard   (flock target; impl detail)
# Queue:     ~/work/dev-hermit/.landing-lock.queue   (FIFO waiters)
# All three are machine-local, gitignored runtime state.
#
# Usage:
#   landing-lock.sh acquire --agent NAME --pr N [--wait S] [--hold S]
#   landing-lock.sh renew   --agent NAME        [--hold S]
#   landing-lock.sh release --agent NAME
#   landing-lock.sh status
#   landing-lock.sh run     --agent NAME --pr N [--wait S] [--hold S] -- CMD...
#
# Exit: 0 ok; 1 wait-timeout; 2 usage; 3 not-owner / internal.
set -uo pipefail

PARENT=$(git -C "$(dirname -- "${BASH_SOURCE[0]}")/.." rev-parse --show-toplevel 2>/dev/null \
         || echo "$HOME/work/dev-hermit")
LOCK="$PARENT/.landing-lock"
GUARD="$LOCK.guard"
QUEUE="$LOCK.queue"

DEFAULT_WAIT=1800     # give up acquiring after 30 min
DEFAULT_HOLD=900      # a single land must finish within 15 min or its lease lapses
POLL=3                # seconds between acquire polls
GUARD_WAIT=30         # max wait for the short internal flock critical section

now()  { date +%s; }
die()  { echo "landing-lock: $*" >&2; exit 2; }
note() { echo "landing-lock: $*" >&2; }

# Run "$@" under an exclusive flock on GUARD. Returns the body's exit code, or 3
# if the guard could not be taken (should not happen in practice).
under_guard() {
    exec 9>"$GUARD" || return 3
    if ! flock -x -w "$GUARD_WAIT" 9; then exec 9>&-; return 3; fi
    "$@"; local rc=$?
    flock -u 9; exec 9>&-
    return $rc
}

# Read holder field ($1) from LOCK, empty if absent.
holder_field() {
    [[ -f "$LOCK" ]] || return 0
    sed -n "s/^$1=//p" "$LOCK" | head -1
}

holder_is_live() {
    [[ -f "$LOCK" ]] || return 1
    local exp; exp=$(holder_field expires_at)
    [[ -n "$exp" ]] || return 1
    (( $(now) < exp ))
}

write_holder() {  # agent pr hold [reclaimed_from]
    local agent=$1 pr=$2 hold=$3 reclaimed=${4:-} t; t=$(now)
    {
        echo "agent=$agent"
        echo "pr=$pr"
        echo "host=$(hostname -s 2>/dev/null || echo unknown)"
        echo "acquired_at=$t"
        echo "acquired_human=$(date -d "@$t" '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || echo "$t")"
        echo "expires_at=$(( t + hold ))"
        [[ -n "$reclaimed" ]] && echo "reclaimed_from=$reclaimed"
    } >"$LOCK"
}

queue_prune_and_head() {  # prune stale waiters (> 2*DEFAULT_WAIT old); echo head agent
    local t; t=$(now); local cutoff=$(( t - 2*DEFAULT_WAIT ))
    [[ -f "$QUEUE" ]] || { echo ""; return 0; }
    awk -F'\t' -v c="$cutoff" '$1+0 >= c' "$QUEUE" >"$QUEUE.tmp" 2>/dev/null || : >"$QUEUE.tmp"
    mv "$QUEUE.tmp" "$QUEUE"
    head -1 "$QUEUE" | cut -f2
}

queue_enqueue() {  # agent pr -- append if not already queued
    local agent=$1 pr=$2
    touch "$QUEUE"
    if ! cut -f2 "$QUEUE" | grep -Fxq "$agent"; then
        printf '%s\t%s\t%s\n' "$(now)" "$agent" "$pr" >>"$QUEUE"
    fi
}

queue_remove() {  # agent
    local agent=$1
    [[ -f "$QUEUE" ]] || return 0
    grep -vP "^[0-9]+\t\Q$agent\E\t" "$QUEUE" >"$QUEUE.tmp" 2>/dev/null || : >"$QUEUE.tmp"
    mv "$QUEUE.tmp" "$QUEUE"
}

# ---- try once to take the lock for AGENT/PR; echo status token -----------------
# Tokens: ACQUIRED | ACQUIRED_RECLAIMED | HELD:<agent>:<secs_left> | WAIT_TURN:<head>
try_acquire() {
    local agent=$1 pr=$2 hold=$3
    queue_enqueue "$agent" "$pr"
    local head; head=$(queue_prune_and_head)
    if holder_is_live; then
        local h; h=$(holder_field agent)
        if [[ "$h" == "$agent" ]]; then          # re-entrant: refresh our own lease
            write_holder "$agent" "$pr" "$hold"; queue_remove "$agent"; echo "ACQUIRED"; return 0
        fi
        echo "HELD:$h:$(( $(holder_field expires_at) - $(now) ))"; return 0
    fi
    # lock is free or lease lapsed -> only the FIFO head may take it
    if [[ -n "$head" && "$head" != "$agent" ]]; then echo "WAIT_TURN:$head"; return 0; fi
    local reclaimed=""
    [[ -f "$LOCK" ]] && reclaimed=$(holder_field agent)
    if [[ -n "$reclaimed" ]]; then
        write_holder "$agent" "$pr" "$hold" "$reclaimed"; queue_remove "$agent"; echo "ACQUIRED_RECLAIMED:$reclaimed"; return 0
    fi
    write_holder "$agent" "$pr" "$hold"; queue_remove "$agent"; echo "ACQUIRED"; return 0
}

cmd_acquire() {
    local agent="" pr="" wait=$DEFAULT_WAIT hold=$DEFAULT_HOLD
    while [[ $# -gt 0 ]]; do case "$1" in
        --agent) agent=$2; shift 2;; --pr) pr=$2; shift 2;;
        --wait) wait=$2; shift 2;; --hold) hold=$2; shift 2;;
        *) die "acquire: unknown arg $1";; esac; done
    [[ -n "$agent" && -n "$pr" ]] || die "acquire needs --agent and --pr"
    local deadline=$(( $(now) + wait )) last=""
    while :; do
        local tok; tok=$(under_guard try_acquire "$agent" "$pr" "$hold")
        case "$tok" in
            ACQUIRED) note "ACQUIRED by $agent for PR #$pr (lease ${hold}s)"; return 0;;
            ACQUIRED_RECLAIMED:*) note "ACQUIRED by $agent for PR #$pr; reclaimed lapsed lease from ${tok#ACQUIRED_RECLAIMED:}"; return 0;;
            HELD:*) [[ "$tok" != "$last" ]] && note "waiting: held by ${tok#HELD:} (agent:secs_left); queued as $agent";;
            WAIT_TURN:*) [[ "$tok" != "$last" ]] && note "waiting: lock free, ahead of me in queue: ${tok#WAIT_TURN:}";;
        esac
        last=$tok
        if (( $(now) >= deadline )); then under_guard queue_remove "$agent"; note "TIMEOUT after ${wait}s"; return 1; fi
        sleep "$POLL"
    done
}

cmd_renew() {
    local agent="" hold=$DEFAULT_HOLD
    while [[ $# -gt 0 ]]; do case "$1" in
        --agent) agent=$2; shift 2;; --hold) hold=$2; shift 2;;
        *) die "renew: unknown arg $1";; esac; done
    [[ -n "$agent" ]] || die "renew needs --agent"
    under_guard _renew "$agent" "$hold"
}
_renew() {
    local agent=$1 hold=$2
    [[ "$(holder_field agent)" == "$agent" ]] || { note "renew: $agent does not hold the lock"; return 3; }
    write_holder "$agent" "$(holder_field pr)" "$hold"; note "renewed $agent lease ${hold}s"
}

cmd_release() {
    local agent=""
    while [[ $# -gt 0 ]]; do case "$1" in --agent) agent=$2; shift 2;; *) die "release: unknown arg $1";; esac; done
    [[ -n "$agent" ]] || die "release needs --agent"
    under_guard _release "$agent"
}
_release() {
    local agent=$1
    if [[ ! -f "$LOCK" ]]; then note "release: no lock held"; return 0; fi
    local h; h=$(holder_field agent)
    if [[ "$h" != "$agent" ]]; then note "release: lock is held by $h, not $agent; refusing"; return 3; fi
    rm -f "$LOCK"; queue_remove "$agent"
    local head; head=$(queue_prune_and_head)
    if [[ -n "$head" ]]; then note "RELEASED by $agent; lock FREE -> next: $head"; else note "RELEASED by $agent; lock FREE (queue empty)"; fi
}

cmd_status() {
    if holder_is_live; then
        echo "HELD:"
        sed 's/^/  /' "$LOCK"
        echo "  secs_left=$(( $(holder_field expires_at) - $(now) ))"
    elif [[ -f "$LOCK" ]]; then
        echo "LAPSED (reclaimable):"; sed 's/^/  /' "$LOCK"
    else
        echo "FREE"
    fi
    if [[ -s "$QUEUE" ]]; then echo "queue (FIFO):"; nl -ba "$QUEUE" | sed 's/^/  /'; fi
}

cmd_run() {
    local agent="" pr="" wait=$DEFAULT_WAIT hold=$DEFAULT_HOLD
    while [[ $# -gt 0 ]]; do case "$1" in
        --agent) agent=$2; shift 2;; --pr) pr=$2; shift 2;;
        --wait) wait=$2; shift 2;; --hold) hold=$2; shift 2;;
        --) shift; break;; *) die "run: unknown arg $1";; esac; done
    [[ -n "$agent" && -n "$pr" && $# -gt 0 ]] || die "run needs --agent, --pr, and -- CMD..."
    cmd_acquire --agent "$agent" --pr "$pr" --wait "$wait" --hold "$hold" || return 1
    # heartbeat: renew the lease every hold/3 so a genuinely long land keeps it.
    ( while sleep $(( hold/3 )); do "$0" renew --agent "$agent" --hold "$hold" >/dev/null 2>&1 || exit; done ) &
    local hb=$!
    "$@"; local rc=$?
    kill "$hb" 2>/dev/null
    cmd_release --agent "$agent"
    return $rc
}

sub=${1:-help}; [[ $# -gt 0 ]] && shift
case "$sub" in
    acquire) cmd_acquire "$@";;
    renew)   cmd_renew "$@";;
    release) cmd_release "$@";;
    status)  cmd_status "$@";;
    run)     cmd_run "$@";;
    help|-h|--help)
        sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//';;
    *) die "unknown subcommand: $sub (try: acquire|renew|release|status|run|help)";;
esac
