#!/usr/bin/env bash
# Confined-contention repro: force OS core contention on the python-hashseed
# --verify guest WITHOUT harming the shared box. Pin N CPU hogs AND the hermit
# run to the SAME small cpuset; the other ~312 cores (and all sibling agents)
# are untouched. Tests the RCB-skid-under-contention hypothesis directly.
#
# Launch under `systemd-run --user` so the whole process tree lives in one unit
# cgroup: teardown = `systemctl --user stop <unit>` (kills my own unit only,
# never a pattern match). Hogs are children of this script -> die with the unit.
set -uo pipefail
EXP=/home/newton/work/dev-hermit/experiments/python-hashseed-catcher_20260804
IGN="$EXP/ignored"
CPUSET="${1:-200-203}"     # small cpuset to confine contention to
NHOGS="${2:-16}"           # CPU hogs pinned to CPUSET (oversubscribe it)
NRUNS="${3:-1500}"

echo "# contention-run cpuset=$CPUSET nhogs=$NHOGS nruns=$NRUNS" >&2
hogpids=()
for ((h=0; h<NHOGS; h++)); do
  taskset -c "$CPUSET" bash -c 'while :; do :; done' &
  hogpids+=($!)
done
echo "${hogpids[*]}" > "$IGN/hogpids.txt"
echo "# spawned $NHOGS hogs on cpuset $CPUSET: ${hogpids[*]}" >&2

# Run the catcher with hermit pinned to the SAME cpuset.
HCPUSET="$CPUSET" bash "$EXP/catch.sh" "$NRUNS" "contention-c${CPUSET//-/_}-h${NHOGS}"
rc=$?

# Explicit teardown of my own hog children (unit-cgroup stop also covers this).
for p in "${hogpids[@]}"; do kill "$p" 2>/dev/null || true; done
echo "# contention-run done rc=$rc, hogs killed" >&2
exit $rc
