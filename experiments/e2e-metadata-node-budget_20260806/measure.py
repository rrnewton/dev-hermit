#!/usr/bin/env python3
"""Measure one DAG node command boxed in its own cgroup, separating CPU from wall.

Why a cgroup and not /usr/bin/time: the question this experiment answers is
whether `e2e.metadata` is slow because it BURNS more CPU under load or because
it WAITS, and a polled aggregate cannot answer that. The transient scope gives
`cpu.stat usage_usec` (CPU actually consumed by the whole process tree) and
`memory.peak` (a cgroup-recorded peak, not a sampled one) for exactly the
processes the node spawns.

The inner shell reads its OWN cgroup out of /proc/self/cgroup and then reads the
counters back from /sys/fs/cgroup before exiting -- the scope's cgroup is
destroyed the moment the unit exits, and, more importantly, reading the live
process's own cgroup is the only way to know the caps and counters belong to the
thing that actually ran rather than to whatever the flags claimed.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path


def loadavg():
    with open("/proc/loadavg") as f:
        parts = f.read().split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def run_once(label, cmd, cwd, outdir, mem_max=None, cpu_quota=None):
    """Run `cmd` inside a transient scope and return a measurement record."""
    tag = f"w17-e2emeta-{uuid.uuid4().hex[:12]}"
    # MUST be absolute. The inner shell runs with cwd set to the checkout under
    # test, so a relative outdir resolves against THAT directory, the first
    # redirect fails, and the run produces no counters at all while still
    # looking like it executed.
    work = (Path(outdir).resolve()) / tag
    work.mkdir(parents=True, exist_ok=True)

    inner = f"""
CG=$(sed 's/^0:://' /proc/self/cgroup)
echo "$CG" > {work}/cgroup.txt
# Prove the caps bind to THIS process's cgroup rather than to the flags we passed.
cat /sys/fs/cgroup"$CG"/cpu.max     > {work}/cpu.max.txt     2>/dev/null || true
cat /sys/fs/cgroup"$CG"/memory.max  > {work}/memory.max.txt  2>/dev/null || true
start=$(date +%s.%N)
( {cmd} ) > {work}/stdout.log 2> {work}/stderr.log
rc=$?
end=$(date +%s.%N)
# Counters must be read BEFORE the unit exits; the cgroup goes away with it.
cat /sys/fs/cgroup"$CG"/cpu.stat        > {work}/cpu.stat.txt        2>/dev/null || true
cat /sys/fs/cgroup"$CG"/memory.peak     > {work}/memory.peak.txt     2>/dev/null || true
cat /sys/fs/cgroup"$CG"/memory.events   > {work}/memory.events.txt   2>/dev/null || true
cat /sys/fs/cgroup"$CG"/pids.peak       > {work}/pids.peak.txt       2>/dev/null || true
echo "$rc" > {work}/rc.txt
echo "$start $end" > {work}/wall.txt
"""

    unit_args = ["systemd-run", "--user", "--scope", "--quiet", f"--unit={tag}"]
    if mem_max is not None:
        unit_args += ["-p", f"MemoryMax={mem_max}"]
    if cpu_quota is not None:
        unit_args += ["-p", f"CPUQuota={cpu_quota}"]

    load_before = loadavg()
    t0 = time.monotonic()
    proc = subprocess.run(
        unit_args + ["bash", "-c", inner],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    t1 = time.monotonic()
    load_after = loadavg()

    def read(name, default=""):
        p = work / name
        return p.read_text().strip() if p.exists() else default

    cpu_stat = {}
    for line in read("cpu.stat.txt").splitlines():
        bits = line.split()
        if len(bits) == 2:
            cpu_stat[bits[0]] = int(bits[1])

    mem_events = {}
    for line in read("memory.events.txt").splitlines():
        bits = line.split()
        if len(bits) == 2:
            mem_events[bits[0]] = int(bits[1])

    wall_inner = None
    w = read("wall.txt").split()
    if len(w) == 2:
        wall_inner = float(w[1]) - float(w[0])

    rc_text = read("rc.txt")
    record = {
        "label": label,
        "tag": tag,
        "cmd": cmd,
        "cwd": str(cwd),
        "rc": int(rc_text) if rc_text.isdigit() else None,
        "outer_rc": proc.returncode,
        "wall_s": round(wall_inner, 3) if wall_inner is not None else None,
        "wall_outer_s": round(t1 - t0, 3),
        "cpu_usage_s": round(cpu_stat.get("usage_usec", 0) / 1e6, 3),
        "cpu_user_s": round(cpu_stat.get("user_usec", 0) / 1e6, 3),
        "cpu_system_s": round(cpu_stat.get("system_usec", 0) / 1e6, 3),
        "mem_peak_bytes": int(read("memory.peak.txt") or 0),
        "pids_peak": int(read("pids.peak.txt") or 0),
        "oom_kill": mem_events.get("oom_kill", 0),
        "oom_events": mem_events.get("oom", 0),
        "mem_max_applied": read("memory.max.txt"),
        "cpu_max_applied": read("cpu.max.txt"),
        "cgroup": read("cgroup.txt"),
        "load1_before": load_before[0],
        "load1_after": load_after[0],
        "nproc": os.cpu_count(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # A count is not a rate: record utilisation against the real core count.
    record["load_pct_of_cores"] = round(
        100.0 * max(load_before[0], load_after[0]) / record["nproc"], 1
    )
    # The whole point of splitting these: >1 means genuinely parallel CPU work,
    # <<1 means the node is waiting rather than computing.
    if record["wall_s"]:
        record["cpu_per_wall"] = round(record["cpu_usage_s"] / record["wall_s"], 3)
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", required=True)
    ap.add_argument("--cmd", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--mem-max", default=None)
    ap.add_argument("--cpu-quota", default=None)
    ap.add_argument("--results", default=None, help="append JSONL here")
    args = ap.parse_args()

    for i in range(args.repeat):
        rec = run_once(
            f"{args.label}#{i + 1}",
            args.cmd,
            args.cwd,
            args.outdir,
            mem_max=args.mem_max,
            cpu_quota=args.cpu_quota,
        )
        line = json.dumps(rec)
        print(line, flush=True)
        if args.results:
            with open(args.results, "a") as f:
                f.write(line + "\n")


if __name__ == "__main__":
    main()
