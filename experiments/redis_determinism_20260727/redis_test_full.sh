#!/bin/sh
# Expanded Redis determinism probe: many data types + INFO (host-state probe).
set -e
PORT=6399
DIR=/tmp/redis-rundir
rm -rf "$DIR"; mkdir -p "$DIR"

redis-server --port "$PORT" --bind 127.0.0.1 --protected-mode no \
  --save '' --appendonly no --daemonize no --dir "$DIR" \
  --logfile '' --loglevel warning &
SRV=$!

i=0
while [ $i -lt 100 ]; do
  if redis-cli -h 127.0.0.1 -p "$PORT" ping 2>/dev/null | grep -q PONG; then break; fi
  i=$((i+1)); sleep 0.05
done
echo "READY after $i polls"

C="redis-cli -h 127.0.0.1 -p $PORT"
# strings / counters
$C set s hello; $C append s world; $C get s; $C strlen s
$C set n 100; $C incr n; $C decr n; $C incrby n 5; $C decrby n 2
$C setrange s 0 J; $C getrange s 0 4
# bit ops
$C setbit b 7 1; $C getbit b 7; $C bitcount b
# lists
$C rpush L a b c d; $C lrange L 0 -1; $C lpop L; $C llen L
# hashes (order determined by hash seed -> deterministic across runs)
$C hset H f1 1 f2 2 f3 3; $C hgetall H; $C hkeys H; $C hvals H
# sets (SMEMBERS order = hash-seed dependent)
$C sadd S x y z; $C smembers S; $C scard S; $C sismember S y
# sorted sets (score order deterministic)
$C zadd Z 1 a 3 c 2 b; $C zrange Z 0 -1 WITHSCORES; $C zrank Z c
# expire / ttl (virtual-time based)
$C setex ek 100 v; $C ttl ek; $C persist ek; $C ttl ek
# type / exists / keys(sorted)
$C type L; $C exists S; $C keys '*' | sort
# transaction
$C multi
$C eval "redis.call('set','tx','1'); return redis.call('get','tx')" 0
# scan (cursor order hash-seed dependent)
$C scan 0
# INFO probes (strong host-state leak detector)
echo "--- INFO server (filtered) ---"
$C info server | grep -E 'redis_version|arch_bits|process_id|run_id|tcp_port|uptime_in_seconds'
echo "--- INFO clients/stats (filtered) ---"
$C info clients | grep -E 'connected_clients'
$C info keyspace

$C shutdown nosave 2>/dev/null || true
wait $SRV 2>/dev/null || true
echo "DONE"
