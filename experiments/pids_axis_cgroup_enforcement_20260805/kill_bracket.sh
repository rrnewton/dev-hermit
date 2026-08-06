#!/usr/bin/env bash
# Kill-enforcement bracket for the pids axis.
#
# ARM A (kill fired)     : write 1 to cgroup.kill  -> every member must die.
# ARM B (kill NOT fired) : identical setup + identical wait, no kill
#                          -> every member must SURVIVE.
#
# Arm B is what makes Arm A mean something: it proves the deaths in A were
# caused by cgroup.kill and not by the processes exiting on their own.
#
# Safety (Hard Invariant 15): we only ever act on a transient unit we just
# created, whose cgroup contains only our own children. No pattern/name kills.
set -uo pipefail

UID_N=$(id -u)
BASE="/sys/fs/cgroup/user.slice/user-${UID_N}.slice/user@${UID_N}.service/app.slice"
HERE="$(cd "$(dirname "$0")" && pwd)"
KIDS=${KIDS:-12}
SETTLE=${SETTLE:-1}
OBSERVE=${OBSERVE:-2}

run_arm() {
    local arm=$1 unit=$2 fire_kill=$3
    local dir="$BASE/${unit}.service"

    systemd-run --user --unit="$unit" --collect -p TasksMax=64 \
        -- python3 "$HERE/runaway.py" "$KIDS" >/dev/null 2>&1
    sleep "$SETTLE"

    if [[ ! -d $dir ]]; then
        echo "{\"arm\":\"$arm\",\"error\":\"cgroup dir absent: $dir\"}"
        return 1
    fi

    # Membership read from the LIVE cgroup, not from what we asked for.
    mapfile -t pids < <(cat "$dir/cgroup.procs" 2>/dev/null)
    local n_before=${#pids[@]}
    local max_readback pids_current alive_before=0 p
    max_readback=$(cat "$dir/pids.max" 2>/dev/null)
    pids_current=$(cat "$dir/pids.current" 2>/dev/null)
    for p in "${pids[@]}"; do [[ -d /proc/$p ]] && alive_before=$((alive_before + 1)); done

    local killed_flag=false
    if [[ $fire_kill == yes ]]; then
        echo 1 >"$dir/cgroup.kill" 2>/dev/null && killed_flag=true
    fi
    sleep "$OBSERVE"

    local alive_after=0
    for p in "${pids[@]}"; do [[ -d /proc/$p ]] && alive_after=$((alive_after + 1)); done
    local procs_after="absent"
    [[ -d $dir ]] && procs_after=$(wc -l <"$dir/cgroup.procs" 2>/dev/null || echo unreadable)

    printf '{"arm":"%s","unit":"%s","kill_fired":%s,"kill_write_ok":%s,' \
        "$arm" "$unit" "$([[ $fire_kill == yes ]] && echo true || echo false)" "$killed_flag"
    printf '"pids_max_readback":"%s","pids_current":"%s",' "$max_readback" "$pids_current"
    printf '"members_before":%d,"alive_before":%d,"alive_after":%d,"cgroup_procs_after":"%s",' \
        "$n_before" "$alive_before" "$alive_after" "$procs_after"
    printf '"all_died":%s,"all_survived":%s}\n' \
        "$([[ $alive_before -gt 0 && $alive_after -eq 0 ]] && echo true || echo false)" \
        "$([[ $alive_before -gt 0 && $alive_after -eq $alive_before ]] && echo true || echo false)"

    # Clean up our own transient unit (Arm B is still running by design).
    systemctl --user stop "${unit}.service" >/dev/null 2>&1
    systemctl --user reset-failed "${unit}.service" >/dev/null 2>&1
    return 0
}

run_arm "A-kill-fired"     "pids-killA-$$" yes
run_arm "B-kill-not-fired" "pids-killB-$$" no
