#!/bin/sh
# Self-terminating Redis client-server workload for hermit --strict --verify.
# Script lives outside /tmp (hermit gives the guest a private /tmp); redis's
# rundir is placed in the private /tmp so each run starts from a clean state.
set -e
PORT=6399
DIR=/tmp/redis-rundir
rm -rf "$DIR"; mkdir -p "$DIR"

redis-server \
  --port "$PORT" \
  --bind 127.0.0.1 \
  --protected-mode no \
  --save '' \
  --appendonly no \
  --daemonize no \
  --dir "$DIR" \
  --logfile '' \
  --loglevel warning &
SRV=$!

# Wait for readiness (deterministic under hermit's scheduler + virtual time).
i=0
while [ $i -lt 100 ]; do
  if redis-cli -h 127.0.0.1 -p "$PORT" ping 2>/dev/null | grep -q PONG; then
    break
  fi
  i=$((i+1))
  sleep 0.05
done
echo "READY after $i polls"

CLI="redis-cli -h 127.0.0.1 -p $PORT"
$CLI set foo bar
$CLI get foo
$CLI incr counter
$CLI incr counter
$CLI append foo baz
$CLI get foo
$CLI rpush mylist a b c
$CLI lrange mylist 0 -1
$CLI hset myhash f1 v1 f2 v2
$CLI hget myhash f1
$CLI sadd myset x
$CLI scard myset
$CLI dbsize

$CLI shutdown nosave 2>/dev/null || true
wait $SRV 2>/dev/null || true
echo "DONE"
