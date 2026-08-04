#!/usr/bin/env python3
"""Run a command with its whole process tree confined to K FREE cores.

Interim single-core-box mechanism for the gVisor / backend-perf methodology on
boxes where the cgroup `cpuset` controller is NOT delegated (e.g. the 3pai
sandbox: its scope exposes only `io memory pids`). Uses `sched_setaffinity`,
which inherits across fork+execve, so the whole tree is confined.

Per the standing benchmark rule it NEVER pins a fixed core id: it reads the
allowed set, samples /proc/stat briefly, and picks the K LEAST-BUSY free cores.
K=1 forces sequential execution (isolates instrumentation cost from parallelism
cost).

Usage:  run-on-k-free-cores.py <K> -- <command> [args...]
Example: run-on-k-free-cores.py 1 -- hermit run -- ./mybench
         run-on-k-free-cores.py 1 -- runsc do ./mybench      # native/gvisor too
"""
import os, sys, time

def pick_least_busy_free(k):
    allowed = sorted(os.sched_getaffinity(0))
    def snap():
        d = {}
        for line in open('/proc/stat'):
            if line.startswith('cpu') and len(line) > 3 and line[3].isdigit():
                p = line.split(); cid = int(p[0][3:])
                idle = int(p[4]) + int(p[5]); total = sum(int(x) for x in p[1:])
                d[cid] = (idle, total)
        return d
    a = snap(); time.sleep(0.3); b = snap()
    def idlefrac(c):
        di = b[c][0] - a[c][0]; dt = b[c][1] - a[c][1]
        return di / dt if dt else 1.0
    ranked = sorted((c for c in allowed if c in b), key=lambda c: -idlefrac(c))
    return ranked[:k]

def main():
    if len(sys.argv) < 4 or sys.argv[2] != '--':
        sys.exit(__doc__)
    k = int(sys.argv[1]); cmd = sys.argv[3:]
    cores = pick_least_busy_free(k)
    os.sched_setaffinity(0, set(cores))
    print(f"[run-on-k-free-cores] K={k} confined to cores {cores} "
          f"(least-busy free; affinity inherits to the whole tree)", file=sys.stderr)
    os.execvp(cmd[0], cmd)

if __name__ == '__main__':
    main()
