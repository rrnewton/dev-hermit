#!/usr/bin/env python3
"""Safety wrapper: run the guardrail worker pool inside a cgroup-boxed singleton DAG.

Stack (owner-mandated):
  safe-ci-dag-runner (SINGLETON DAG, outer cgroup MEM cap + per-step profiling)
    -> our bounded worker pool of parallel --strict --verify reps (harness.py)
       -> cross-rep hash diff + divergence detection

Why a cgroup cap: a pool of hundreds of concurrent hermit processes could OOM the
host. reexec_in_scope re-execs this whole process inside a transient
``systemd-run --user --scope -p Delegate=yes -p MemorySwapMax=0 -p MemoryMax=<cap>``
so the SUM of every rep's memory is hard-bounded and swap-killed at the cap, and
the entire descendant tree (setsid escapees included) is reaped on exit. This is
No-Silent-Failure: if the scope can't be established or the cap can't be VERIFIED
active, we refuse to run rather than run advisory-only.

The DAG is a single step whose cmd is the harness invocation. safe-ci-dag-runner
supplies: (1) the per-step child cgroup (extra boxing + per-step memory.peak/oom
accounting) via Cgroups(), and (2) per-step + whole-run resource CSVs via
CsvMetricsSink (--perf-dir) == the profiling output.

Usage:
  guarded_run.py --mem-cap 64G --perf-dir DIR --step-timeout 4200 \
      --agent-utils-py <path> -- <harness argv...>
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


def parse_size(spec: str) -> int:
    s = spec.strip().upper()
    mult = 1
    if s.endswith("G"):
        mult, s = 1024**3, s[:-1]
    elif s.endswith("M"):
        mult, s = 1024**2, s[:-1]
    elif s.endswith("K"):
        mult, s = 1024, s[:-1]
    return int(float(s) * mult)


def main() -> int:
    ap = argparse.ArgumentParser(description="cgroup-boxed singleton-DAG guardrail launcher")
    ap.add_argument("--mem-cap", default="64G", help="outer cgroup MemoryMax (e.g. 64G)")
    ap.add_argument("--perf-dir", required=True, help="safe-ci perf CSV dir (profiling)")
    ap.add_argument("--step-timeout", type=int, default=4200, help="DAG step timeout (s)")
    ap.add_argument("--agent-utils-py", required=True, help="path to agent-utils/py (importable)")
    ap.add_argument("harness", nargs=argparse.REMAINDER, help="-- <harness argv...>")
    args = ap.parse_args()

    harness_argv = args.harness
    if harness_argv and harness_argv[0] == "--":
        harness_argv = harness_argv[1:]
    if not harness_argv:
        print("ERROR: no harness command after --", file=sys.stderr)
        return 2

    sys.path.insert(0, args.agent_utils_py)
    from safe_ci_dag_runner import (  # noqa: E402
        Cgroups,
        CsvMetricsSink,
        DagConfig,
        ResourceHint,
        Step,
        StepClass,
        run_dag,
    )
    from safe_ci_dag_runner import cgroup  # noqa: E402

    mem_cap = parse_size(args.mem_cap)

    # 1) Re-exec THIS process into the delegated systemd scope with the memory cap.
    #    skip_in_ci=False and use_aggregate_slice=False: always enforce the mem cap,
    #    and never install a CPU quota (we WANT CPU oversubscription as the load).
    reexec_argv = [sys.executable, os.path.abspath(__file__), *sys.argv[1:]]
    ok = cgroup.reexec_in_scope(
        reexec_argv,
        memory_max=mem_cap,
        cpu_count=None,
        use_aggregate_slice=False,
        skip_in_ci=False,
    )
    if not ok:
        print(
            "[guarded_run] REFUSING: systemd --user delegated scope unavailable; "
            "will not run parallel reps without a cgroup memory cap.",
            file=sys.stderr,
        )
        return 3

    # 2) We are now IN-SCOPE. Confirm the cap actually reached cgroup v2 (hard gate).
    print(f"[guarded_run] outer cgroup mem cap requested: MemoryMax={mem_cap} bytes", flush=True)
    if not cgroup.verify_scope_limits(expected_memory_max=mem_cap, expected_cpu_count=None):
        print(
            "[guarded_run] REFUSING: outer cgroup memory cap could not be VERIFIED active.",
            file=sys.stderr,
        )
        return 3
    cgroup.install_scope_teardown()

    # 3) Build the singleton DAG: one step = the harness worker pool.
    harness_cmd = " ".join(shlex.quote(a) for a in harness_argv)
    step = Step(
        group="guardrail",
        job="workerpool",
        desc="bounded worker pool of parallel --strict --verify reps",
        cmd=harness_cmd,
        timeout=args.step_timeout,
        hint=ResourceHint(
            classification=StepClass.CPU_BOUND,
            rss_baseline_bytes=mem_cap,  # the step may use up to the whole cap
        ),
    )
    cfg = DagConfig(steps=(step,))

    Path(args.perf_dir).mkdir(parents=True, exist_ok=True)
    cgroups = Cgroups()  # per-step child cgroup (effective now we are in-scope)
    if not cgroups.enabled:
        print(
            "[guarded_run] WARNING: per-step cgroup boxing not enabled; "
            "outer scope mem cap still applies.",
            file=sys.stderr,
        )
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        git_sha = "unknown"
    metrics = CsvMetricsSink(args.perf_dir, git_sha=git_sha)

    result = run_dag(cfg, jobs=1, cgroups=cgroups, metrics=metrics, verbosity=1)

    # 4) Profiling: report outer-scope peak memory + OOM events.
    cgroup.report_scope_usage()

    outcome = result.outcomes[0] if result.outcomes else None
    rc = outcome.returncode if (outcome and outcome.returncode is not None) else (0 if result.ok else 1)
    print(
        f"[guarded_run] step verdict ok={result.ok} returncode={rc} "
        f"(harness: 0=GREEN 2=P0 3=preexisting-fail)",
        flush=True,
    )
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
