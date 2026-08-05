#!/bin/bash
# KVM AFTER (through the wrapper) — scope-keyed, no confounding with ambient orphans.
# For each run: while the boxed hermit inner livelocks it holds a DISTINCT pid ns; after
# the wrapper's CPU-budget kill fires, cgroup.kill reaps the whole scope subtree, the
# systemd scope cgroup is removed, and 0 processes hold that ns => namespace released,
# with NO external kill.
set -u
ROOT=/home/newton/work/dev-hermit
HB="$ROOT/hermit/target/debug/hermit"
WRAP="$ROOT/scripts/hermit-box-run"
N=${1:-2}
BUDGET=${2:-6}
root_ns=$(readlink /proc/self/ns/pid)
cores_leaked=0; ns_retained=0; held_ok=0
for i in $(seq 1 "$N"); do
  LOG=$(mktemp)
  "$WRAP" --cpu-budget "$BUDGET" --cores 1 --label kvm.livelock -- \
    "$HB" run --backend kvm --base-env=minimal --max-timeslice=disabled --tmp=/tmp -- /bin/sh -c 'exit 23' \
    > "$LOG" 2>&1 &
  WPID=$!
  scope=""; cgdir=""; inner_pid=""; inner_ns=""
  for _ in $(seq 1 80); do
    [ -z "$scope" ] && scope=$(sed -n 's/.*transient systemd scope \(safe-ci-[0-9]*\.scope\).*/\1/p' "$LOG" 2>/dev/null | head -1)
    if [ -n "$scope" ] && [ -z "$cgdir" ]; then cgdir=$(find /sys/fs/cgroup -type d -name "$scope" 2>/dev/null | head -1); fi
    if [ -n "$cgdir" ]; then
      while read -r pf; do
        while read -r pid; do
          [ -d /proc/"$pid" ] || continue
          [ "$(cat /proc/$pid/comm 2>/dev/null)" = "hermit" ] || continue
          ns=$(readlink /proc/"$pid"/ns/pid 2>/dev/null)
          [ -n "$ns" ] && [ "$ns" != "$root_ns" ] && { inner_pid="$pid"; inner_ns="$ns"; }
        done < "$pf"
      done < <(find "$cgdir" -name cgroup.procs 2>/dev/null)
      [ -n "$inner_ns" ] && break
    fi
    sleep 0.2
  done
  [ -n "$inner_ns" ] && held_ok=$((held_ok+1))
  st="?"; [ -n "$inner_pid" ] && st=$(awk '{print $3}' /proc/$inner_pid/stat 2>/dev/null)
  wait "$WPID"; rc=$?
  sleep 1
  # AFTER checks
  alive=0; [ -n "$inner_pid" ] && [ -d /proc/"$inner_pid" ] && alive=1
  holders=0; if [ -n "$inner_ns" ]; then for p in $(ls /proc | grep -E '^[0-9]+$'); do [ "$(readlink /proc/$p/ns/pid 2>/dev/null)" = "$inner_ns" ] && holders=$((holders+1)); done; fi
  scope_gone="yes"; [ -n "$cgdir" ] && [ -d "$cgdir" ] && scope_gone="NO"
  reason=$(sed -n 's/.*(\([0-9]*s, CPU-TIMEOUT[^)]*\)).*/\1/p' "$LOG" | head -1)
  echo "-- trial $i: while-running inner=$inner_pid state=$st ns=$inner_ns | wrapper rc=$rc ($reason) | after: inner_alive=$alive ns_holders=$holders scope_removed=$scope_gone"
  [ "$alive" -eq 1 ] && cores_leaked=$((cores_leaked+1))
  [ "$holders" -gt 0 ] && ns_retained=$((ns_retained+1))
  rm -f "$LOG"
done
echo "RESULT-KVM-AFTER: held_distinct_ns_while_running=$held_ok/$N | cores_leaked=$cores_leaked/$N | namespaces_retained=$ns_retained/$N (0/0 => whole subtree reaped, ns released, no external kill)"
