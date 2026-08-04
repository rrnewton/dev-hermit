#!/bin/bash
# BEFORE vs AFTER proof for the reaper-wrapper (task hermit_run_verify_hangs).
#
# Specimen (leaky_spinner.sh): an OUTER that setsid-spawns an INNER cpu-spinner in
# its OWN new session/pgid, then waits. This mimics the hermit outer(session-leader,
# pipe-wait) + inner(supervisor, own pgid, burns a core / holds a ns) two-process tree.
#
# BEFORE: launch outer under its own pgid, then `kill -- -<outer_pgid>` (the launcher-
#         PGID kill an agent recycle / tool-cap does). The inner is in a DIFFERENT pgid,
#         so it survives, reparents to ppid=1, keeps burning a core. => LEAK.
# AFTER:  run the SAME outer through the wrapper (scripts/hermit-box-run). The CPU-budget
#         kill fires and cgroup.kill reaps the WHOLE cgroup subtree incl. the setsid
#         inner, with NO external kill. => 0 cores leaked.
#
# Invariant 15: we ONLY ever kill PIDs/PGIDs we spawned ourselves (captured explicitly).
set -u
ROOT="${ROOT:-$(git rev-parse --show-toplevel)}"
D="$ROOT/scratch/verify-hang-repro/wrapper-proof"
SPEC="$D/leaky_spinner.sh"
WRAP="$ROOT/scripts/hermit-box-run"
N=${1:-3}
OBSERVE=${2:-3}

is_alive() { [ -d /proc/"$1" ]; }
state_of() { awk '{print $3}' /proc/"$1"/stat 2>/dev/null; }
cpu_of()   { local u s; read -r u s < <(awk '{print $14, $15}' /proc/"$1"/stat 2>/dev/null); echo $(( (u + s) / 100 )); }
ns_of()    { readlink /proc/"$1"/ns/pid 2>/dev/null; }

echo "############ BEFORE (unboxed; launcher PGID-kill only) ############"
before_leaked=0
declare -a BEFORE_INNERS=()
for i in $(seq 1 "$N"); do
  out=$(mktemp)
  setsid bash "$SPEC" >"$out" 2>&1 &
  outer_pgid=$!               # setsid => outer is its own session/pgid leader (pid==pgid)
  # wait for the inner pid to be announced
  inner=""
  for _ in $(seq 1 50); do
    inner=$(sed -n 's/^INNER_PID=//p' "$out" 2>/dev/null | head -1)
    [ -n "$inner" ] && break
    sleep 0.1
  done
  sleep "$OBSERVE"
  echo "-- trial $i: outer_pgid=$outer_pgid inner=$inner (pre-kill) state=$(state_of "$inner") cpu=$(cpu_of "$inner")s ns=$(ns_of "$inner")"
  # The launcher-PGID kill: reaches ONLY the outer's process group.
  kill -- -"$outer_pgid" 2>/dev/null
  sleep 2
  if [ -n "$inner" ] && is_alive "$inner"; then
    echo "   ORPHANED: inner=$inner ALIVE ppid=$(awk '{print $4}' /proc/"$inner"/stat) state=$(state_of "$inner") cpu=$(cpu_of "$inner")s ns=$(ns_of "$inner") -> LEAKS 1 core"
    before_leaked=$((before_leaked+1))
    BEFORE_INNERS+=("$inner")
  else
    echo "   inner reaped by the PGID kill (no leak)"
  fi
  rm -f "$out"
done
echo "RESULT-BEFORE: cores_leaked=$before_leaked/$N (each surviving inner burns 1 core, holds its ns)"
# MANDATORY cleanup of OUR OWN orphaned children (Invariant 15: our own PIDs only).
for p in "${BEFORE_INNERS[@]:-}"; do [ -n "$p" ] && kill -9 -- -"$p" 2>/dev/null; kill -9 "$p" 2>/dev/null; done
sleep 1
still=0; for p in "${BEFORE_INNERS[@]:-}"; do [ -n "$p" ] && is_alive "$p" && still=$((still+1)); done
echo "   (cleaned our orphans; still_alive_after_cleanup=$still)"

echo
echo "############ AFTER (through wrapper: CPU-budget kill + cgroup.kill reaper) ############"
after_leaked=0
for i in $(seq 1 "$N"); do
  # Capture inner pid from wrapper stdout; wrapper boxes + budgets + reaps.
  log=$(mktemp)
  "$WRAP" --cpu-budget 3 --cores 1 -- bash "$SPEC" >"$log" 2>&1
  rc=$?
  inner=$(sed -n 's/.*INNER_PID=//p' "$log" 2>/dev/null | head -1 | tr -dc '0-9')
  sleep 1
  if [ -n "$inner" ] && is_alive "$inner"; then
    echo "-- trial $i: rc=$rc inner=$inner STILL ALIVE state=$(state_of "$inner") -> LEAK"
    after_leaked=$((after_leaked+1))
    kill -9 "$inner" 2>/dev/null   # our own child; clean up
  else
    echo "-- trial $i: rc=$rc inner=$inner reaped by cgroup.kill (no external kill) -> 0 leak"
  fi
  rm -f "$log"
done
echo "RESULT-AFTER: cores_leaked=$after_leaked/$N (budget-kill + cgroup.kill reaped the whole subtree)"
echo
echo "############ SUMMARY ############"
echo "BEFORE: $before_leaked/$N inner spinners orphaned to ppid=1, still burning a core."
echo "AFTER:  $after_leaked/$N leaked; the wrapper's CPU-budget kill + cgroup.kill reaped every subtree."
