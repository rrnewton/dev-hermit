#!/bin/sh
# compat-deep-app-redis-small: a SMALL, self-terminating redis client-server
# session for hermit --strict --verify. One redis-server (loopback TCP, epoll
# event loop + serverCron timer) plus a small redis-cli workload: SET/GET/INCR
# and one small pipeline. Kept deliberately tiny so the guest exits cleanly and
# quickly; the point is determinism of the epoll/timer-driven event loop, not
# throughput.
#
# MUST live OUTSIDE /tmp: hermit gives the guest a PRIVATE /tmp, so a script in
# the host /tmp is invisible in the container (openat -> ENOENT, exit 127). The
# private /tmp is however ideal for redis's rundir (clean per run).
set -eu

PORT=6399
RUNDIR=/tmp/redis-small-rundir          # inside the guest's private, clean /tmp
CLI="redis-cli -p $PORT"

rm -rf "$RUNDIR"
mkdir -p "$RUNDIR"

# Foreground server, persistence fully disabled, no external logfile.
redis-server \
  --port "$PORT" \
  --bind 127.0.0.1 \
  --protected-mode no \
  --save '' \
  --appendonly no \
  --daemonize no \
  --dir "$RUNDIR" \
  --logfile '' \
  --loglevel warning &
SERVER_PID=$!

# Deterministic readiness wait: poll PING until PONG. Under hermit the scheduler
# + virtual time make this converge in a fixed number of logical steps.
i=0
until [ "$($CLI ping 2>/dev/null)" = "PONG" ]; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "FATAL: redis-server did not become ready" >&2
    kill "$SERVER_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 0.05
done
echo "ready after $i poll(s)"

# --- small workload: SET / GET / INCR -------------------------------------
echo "set:   $($CLI set greeting hello)"
echo "get:   $($CLI get greeting)"
echo "incr1: $($CLI incr counter)"
echo "incr2: $($CLI incr counter)"
echo "incr3: $($CLI incrby counter 40)"
echo "final: $($CLI get counter)"

# --- small pipeline: several commands issued in one batch -------------------
# redis-cli --pipe sends all stdin commands without waiting per-reply, then
# reports the aggregate. This exercises epoll readiness batching (many commands
# ready on one wakeup) rather than one-command-per-round-trip.
echo "--- pipeline ---"
printf 'SET p:a 1\r\nSET p:b 2\r\nSET p:c 3\r\nINCR p:a\r\nAPPEND p:b XY\r\nMGET p:a p:b p:c\r\n' \
  | $CLI --pipe
echo "pipe.a: $($CLI get p:a)"
echo "pipe.b: $($CLI get p:b)"
echo "pipe.c: $($CLI get p:c)"
echo "dbsize: $($CLI dbsize)"

# Clean shutdown so the guest exits 0 and --verify has a clean-exiting process.
$CLI shutdown nosave 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
echo "done"
