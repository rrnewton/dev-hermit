#!/usr/bin/env bash
# Does a scope survive its main process when an orphan stays behind?
# This is the exact leak mechanism the task describes:
#   "systemd removes a scope only when EMPTY".
#
# ARM A: main command exits, but a setsid escapee (reparents to init) stays.
#        -> if the scope persists, the leak is confirmed structurally.
# ARM B: same, but the escapee is under a CPUQuota -> does quota REAP it?
set -uo pipefail
U=$(id -u)
APP="/sys/fs/cgroup/user.slice/user-${U}.slice/user@${U}.service/app.slice"

arm() {
    local name=$1 quota=$2
    local unit="leaktest-${name}-$$"
    local dir="$APP/${unit}.scope"
    local extra=()
    [[ $quota != none ]] && extra=(-p CPUQuota="$quota")

    # Main process spawns a setsid spinner then EXITS immediately.
    systemd-run --user --scope --collect -u "$unit" "${extra[@]}" -- \
        bash -c 'setsid bash -c "while :; do :; done" </dev/null >/dev/null 2>&1 & exit 0' \
        >/dev/null 2>&1
    sleep 2

    local exists=no nproc=0 escapee="" cpu1 cpu2 quota_rb
    if [[ -d $dir ]]; then
        exists=yes
        nproc=$(wc -l <"$dir/cgroup.procs" 2>/dev/null || echo 0)
        escapee=$(head -1 "$dir/cgroup.procs" 2>/dev/null)
    fi
    quota_rb=$(cat "$dir/cpu.max" 2>/dev/null || echo "(no cpu.max file)")

    # Measure whether the escapee is throttled: CPU-seconds over a 3s window.
    cpu1=0; cpu2=0
    if [[ -n $escapee && -r /proc/$escapee/stat ]]; then
        cpu1=$(awk '{print $14+$15}' /proc/$escapee/stat)
        sleep 3
        cpu2=$(awk '{print $14+$15}' /proc/$escapee/stat 2>/dev/null || echo "$cpu1")
    fi
    local burn=$(( cpu2 - cpu1 ))   # jiffies per 3s; 300 == one full core

    # Still alive after the window? (i.e. did anything REAP it?)
    local alive=no
    [[ -n $escapee && -d /proc/$escapee ]] && alive=yes

    printf '{"arm":"%s","cpuquota_requested":"%s","cpu_max_readback":"%s",' \
        "$name" "$quota" "$quota_rb"
    printf '"scope_still_exists_after_main_exit":"%s","procs_in_scope":%s,' "$exists" "${nproc:-0}"
    printf '"escapee_pid":"%s","cpu_jiffies_per_3s":%s,"pct_of_one_core":%s,' \
        "$escapee" "$burn" "$(awk -v b=$burn 'BEGIN{printf "%.1f", b/3.0}')"
    printf '"escapee_alive_after_window":"%s","reaped_by_quota":%s}\n' \
        "$alive" "$([[ $alive == no ]] && echo true || echo false)"

    # Clean up OUR OWN scope only: atomic kill of the whole cgroup.
    if [[ -d $dir ]]; then
        echo 1 >"$dir/cgroup.kill" 2>/dev/null
        sleep 1
    fi
    systemctl --user stop "${unit}.scope" >/dev/null 2>&1
    printf '{"arm":"%s-after-cgroup-kill","scope_exists":"%s","escapee_alive":"%s"}\n' \
        "$name" \
        "$([[ -d $dir ]] && echo yes || echo no)" \
        "$([[ -n $escapee && -d /proc/$escapee ]] && echo yes || echo no)"
}

arm "no-quota"   none
arm "with-quota" "20%"
