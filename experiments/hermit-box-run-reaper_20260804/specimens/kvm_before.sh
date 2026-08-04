#!/bin/bash
# KVM BEFORE (unboxed, launcher PGID-kill) — real hermit inner supervisor holds a
# DISTINCT pid namespace, gets orphaned to ppid=1, survives the launcher PGID-kill.
# Attribution: we snapshot the set of pre-existing distinct-ns hermit pids FIRST, so we
# never mistake the standing ambient orphan (e.g. hermit-ptw's 2009586) for our own inner,
# and we ONLY ever kill pids we spawned (Invariant 15).
set -u
ROOT="${ROOT:-$(git rev-parse --show-toplevel)}"
HB="$ROOT/hermit/target/debug/hermit"
N=${1:-2}
OBSERVE=${2:-5}
HARDCAP=${3:-40}
root_ns=$(readlink /proc/self/ns/pid)

# baseline: distinct-ns hermit pids that already exist (NOT ours)
baseline=" "
for p in $(ls /proc | grep -E '^[0-9]+$'); do
  [ "$(cat /proc/$p/comm 2>/dev/null)" = "hermit" ] || continue
  ns=$(readlink /proc/$p/ns/pid 2>/dev/null)
  [ -n "$ns" ] && [ "$ns" != "$root_ns" ] && baseline="$baseline$p "
done
echo "baseline pre-existing distinct-ns hermit pids (NOT ours):$baseline"

leaked=0
declare -a MINE=()
for i in $(seq 1 "$N"); do
  setsid "$HB" run --backend kvm --base-env=minimal --max-timeslice=disabled --tmp=/tmp \
    -- /bin/sh -c 'exit 23' >/dev/null 2>&1 &
  launcher_pgid=$!
  # watchdog: force-kill OUR launcher pgid at HARDCAP (our own child only)
  ( sleep "$HARDCAP"; kill -9 -- -"$launcher_pgid" 2>/dev/null ) & wd=$!
  sleep "$OBSERVE"
  # find NEW distinct-ns hermit pid(s) not in baseline and not already claimed = our inner
  mine=""
  for p in $(ls /proc | grep -E '^[0-9]+$'); do
    [ "$(cat /proc/$p/comm 2>/dev/null)" = "hermit" ] || continue
    ns=$(readlink /proc/$p/ns/pid 2>/dev/null)
    [ -n "$ns" ] && [ "$ns" != "$root_ns" ] || continue
    case "$baseline" in *" $p "*) continue;; esac
    mine="$p"; baseline="$baseline$p "   # claim it so trial 2 won't re-pick trial 1's
    break
  done
  if [ -z "$mine" ]; then echo "-- trial $i: could not identify our inner (specimen may not have unshared yet)"; kill -- -"$launcher_pgid" 2>/dev/null; kill "$wd" 2>/dev/null; continue; fi
  ns=$(readlink /proc/"$mine"/ns/pid); st=$(awk '{print $3}' /proc/"$mine"/stat); ppid=$(awk '{print $4}' /proc/"$mine"/stat)
  echo "-- trial $i: launcher_pgid=$launcher_pgid our-inner=$mine state=$st ppid=$ppid ns=$ns (pre-kill)"
  # launcher PGID kill: reaches only the outer's process group
  kill -- -"$launcher_pgid" 2>/dev/null
  sleep 2
  if [ -d /proc/"$mine" ]; then
    echo "   ORPHANED: inner=$mine ALIVE ppid=$(awk '{print $4}' /proc/$mine/stat) state=$(awk '{print $3}' /proc/$mine/stat) ns=$(readlink /proc/$mine/ns/pid) -> LEAKS 1 core + holds ns"
    leaked=$((leaked+1)); MINE+=("$mine")
  else
    echo "   inner reaped by the PGID kill (no leak)"
  fi
  kill "$wd" 2>/dev/null
done
echo "RESULT-KVM-BEFORE: leaked=$leaked/$N (each surviving inner burns 1 core + holds a distinct pid ns)"
# MANDATORY cleanup of OUR OWN orphans only.
for p in "${MINE[@]:-}"; do [ -n "$p" ] && kill -9 -- -"$p" 2>/dev/null; kill -9 "$p" 2>/dev/null; done
sleep 1
still=0; for p in "${MINE[@]:-}"; do [ -n "$p" ] && [ -d /proc/"$p" ] && still=$((still+1)); done
echo "   cleaned our orphans; still_alive_after_cleanup=$still"
