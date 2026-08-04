#!/bin/bash
# batch.sh SRC JOBS COUNT CONC TAG  -> runs COUNT clean builds at -jJOBS, CONC at a time
set -u
SRC="$1"; JOBS="$2"; COUNT="$3"; CONC="$4"; TAG="$5"
D=$PWD/batch_$TAG; rm -rf "$D"; mkdir -p "$D"
pids=(); running=0; pass=0; fail=0; failruns=""
for i in $(seq 1 "$COUNT"); do
  ( ./run_build.sh "$SRC" "$JOBS" "$D/run$i" "$D/run$i.log" > "$D/res$i" 2>&1 ) &
  pids+=($!); running=$((running+1))
  if [ "$running" -ge "$CONC" ]; then wait -n; running=$((running-1)); fi
done
wait
for i in $(seq 1 "$COUNT"); do
  r=$(cat "$D/res$i" 2>/dev/null)
  if [ "$r" = PASS ]; then pass=$((pass+1)); else fail=$((fail+1)); failruns="$failruns $i"; fi
done
echo "TAG=$TAG JOBS=$JOBS COUNT=$COUNT -> PASS=$pass FAIL=$fail failruns:$failruns"
# keep failing logs, delete run trees to save disk
for i in $(seq 1 "$COUNT"); do rm -rf "$D/run$i"; done
