#!/usr/bin/env python3
"""pids-axis enforcement worker.

Runs INSIDE a real systemd user scope that has a TasksMax (pids.max) cap.
Verifies the mechanism by the RUNNING THING, not by the flag we asked for:
it discovers its own live cgroup from /proc/self/cgroup, reads pids.max back
from that directory, and proves its own PID is in that cgroup's cgroup.procs.

Then it forks children one at a time (each child holds a task slot via
signal.pause()) and records the exact fork ordinal that fails, with errno,
plus the pids.events transition.

Emits one JSON object on stdout.
"""
import errno
import json
import os
import signal
import sys
import time


def my_cgroup_dir():
    with open("/proc/self/cgroup") as f:
        rel = f.read().strip().split("::", 1)[1]
    return "/sys/fs/cgroup" + rel


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def read_events(d):
    ev = {}
    try:
        with open(os.path.join(d, "pids.events")) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 2:
                    ev[parts[0]] = int(parts[1])
    except Exception:
        pass
    return ev


def read_procs(d):
    try:
        with open(os.path.join(d, "cgroup.procs")) as f:
            return [int(x) for x in f.read().split()]
    except Exception:
        return []


def main():
    mode = sys.argv[1]              # "breach" | "control"
    want = int(sys.argv[2])         # children to ATTEMPT

    d = my_cgroup_dir()
    out = {
        "mode": mode,
        "requested_children": want,
        "cgroup_dir": d,
        "self_pid": os.getpid(),
    }

    # --- Mechanism binding: the running thing, not the config we asked for ---
    out["pids_max_readback"] = read_text(os.path.join(d, "pids.max"))
    procs_before = read_procs(d)
    out["self_in_cgroup_procs"] = os.getpid() in procs_before
    out["baseline_pids_current"] = read_int(os.path.join(d, "pids.current"))
    out["events_before"] = read_events(d)

    kids = []
    fork_failure = None
    for i in range(1, want + 1):
        try:
            pid = os.fork()
        except OSError as e:
            fork_failure = {
                "attempt_ordinal": i,
                "errno": e.errno,
                "errno_name": errno.errorcode.get(e.errno),
                "message": str(e),
            }
            break
        if pid == 0:
            # Child: hold exactly one task slot until signalled.
            try:
                signal.pause()
            except Exception:
                pass
            os._exit(0)
        kids.append(pid)

    out["children_forked"] = len(kids)
    out["fork_failure"] = fork_failure
    out["pids_current_at_peak"] = read_int(os.path.join(d, "pids.current"))
    out["pids_peak"] = read_text(os.path.join(d, "pids.peak"))
    out["events_after"] = read_events(d)
    out["all_children_live_concurrently"] = all(
        os.path.isdir("/proc/%d" % p) for p in kids
    )

    # Causal equation: baseline + successful children == pids.current at peak
    base = out["baseline_pids_current"]
    peak = out["pids_current_at_peak"]
    out["causal_equation"] = {
        "baseline": base,
        "children_forked": len(kids),
        "sum": (base + len(kids)) if base is not None else None,
        "observed_pids_current": peak,
        "holds": (base is not None and peak is not None and base + len(kids) == peak),
    }

    # --- Reap ONLY our own children (Hard Invariant 15: never a broad kill) ---
    for p in kids:
        try:
            os.kill(p, signal.SIGTERM)
        except Exception:
            pass
    reaped = 0
    for p in kids:
        try:
            os.waitpid(p, 0)
            reaped += 1
        except Exception:
            pass
    out["children_reaped"] = reaped

    time.sleep(0.2)
    out["pids_current_after_reap"] = read_int(os.path.join(d, "pids.current"))
    out["returned_to_baseline"] = out["pids_current_after_reap"] == base

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
