#!/usr/bin/env python3
"""Load-independence guardrail engine (inner worker pool).

Runs the Hermit ``--strict --verify`` determinism suite in a BOUNDED WORKER POOL
of parallel test repetitions. The parallel contention IS the load -- there is no
synthetic stress; a pool sized at or above Nprocs self-loads the host. Every
repetition of a given test must produce a byte-identical result; any divergence
ACROSS identical reps is a load-induced decision change == P0.

Two modes:
  * reps  --reps N     : enqueue N reps x each test, drain the queue, aggregate.
                         (VALIDATE phase -- confirm the engine + hash-diff work.)
  * timed --minutes M  : time-bounded FAIR hot loop under constant oversubscription
                         (pool > Nprocs). Tests take turns by fewest-started, so a
                         fast test runs many more times than a slow one in the same
                         window. Tracks per-test run counts + any divergence.
                         (This is the 1-hour torture mode.)

Per-rep oracle (two Hermit invocations, giving three cross-rep signals):
  A) hermit run --strict          -- CMD   -> sha256(guest stdout)  [output_hash]
  B) hermit run --strict --verify -- CMD   -> stderr verdict        [verify_pass]
                                              + "Logs contain A | B" [count_fp]

Divergence taxonomy (per test, across its reps):
  GREEN                          : 1 output_hash, all verify pass, 1 count_fp.
  P0 OUTPUT_DIVERGENCE           : >1 distinct output_hash.
  P0 VERIFY_FLIP                 : verify both passes and fails across reps.
  P0 SCHEDULE_FP_DIVERGENCE      : >1 distinct count_fp (detcore event counts moved).
  PREEXISTING_VERIFY_FAIL        : verify fails CONSISTENTLY (determinism defect,
                                   not load-induced; not a P0 for THIS guardrail).
  (timeouts are recorded as a perf/liveness note, never a determinism P0 by themselves.)

This script is meant to run as the single step of a safe-ci-dag-runner singleton
DAG (see guarded_run.py), which wraps it in an outer cgroup memory cap and
per-step profiling. It is standalone-runnable too (no cgroup cap) for quick dev.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# "Logs contain 2434 | 2434 messages total"  ->  (2434, 2434, "messages total")
_COUNT_RE = re.compile(r"Logs contain (\d+) \| (\d+) (.+)")
_VERIFY_OK = "Determinism verified"


def build_suite(hermit_root: str) -> dict[str, list[str]]:
    """The frozen deterministic-example determinism suite.

    Each value is the argv passed after ``hermit run ... --``. race.sh is
    intentionally chaotic and is EXCLUDED from an identity oracle."""
    ex = Path(hermit_root) / "examples"
    return {
        "date": ["bash", str(ex / "date.sh")],  # time source
        "devrand": ["bash", str(ex / "devrand.sh")],  # /dev/urandom
        "rand": ["python3", str(ex / "rand.py")],  # PRNG
        "progressbar": ["python3", str(ex / "timed-progress-bar.py")],  # time busy-loop
        "pipeline": ["bash", "-c", "echo hermit-load-guardrail | gzip | gunzip | sha256sum"],
    }


def _run(cmd: list[str], timeout: int) -> tuple[int | None, bytes, bytes, bool]:
    """Run a subprocess, capturing stdout/stderr. Returns (rc, out, err, timed_out)."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as e:
        return None, e.stdout or b"", e.stderr or b"", True


def _count_fp(stderr_text: str) -> str:
    """Normalized detcore event-count fingerprint from a --verify run's stderr.

    Strips the volatile /tmp/runN_log_* paths implicitly (we only keep the
    'Logs contain A | B <label>' numeric lines), so a stable schedule yields a
    stable fingerprint across independent processes."""
    triples = sorted(
        (label.strip(), int(a), int(b)) for a, b, label in _COUNT_RE.findall(stderr_text)
    )
    return "|".join(f"{lbl}={a}:{b}" for lbl, a, b in triples)


def run_one_rep(hermit_bin: str, name: str, argv: list[str], per_run_timeout: int) -> dict:
    """One full determinism probe of one test (two Hermit invocations)."""
    t0 = time.monotonic()
    rc_s, out_s, _err_s, to_s = _run(
        [hermit_bin, "run", "--strict", "--"] + argv, per_run_timeout
    )
    t1 = time.monotonic()
    output_hash = hashlib.sha256(out_s).hexdigest()[:16] if not to_s else None

    rc_v, _out_v, err_v, to_v = _run(
        [hermit_bin, "run", "--strict", "--verify", "--"] + argv, per_run_timeout
    )
    t2 = time.monotonic()
    err_text = err_v.decode("utf-8", "replace")
    verify_pass = (not to_v) and rc_v == 0 and _VERIFY_OK in err_text
    count_fp = _count_fp(err_text) if not to_v else None

    return {
        "test": name,
        "output_hash": output_hash,
        "strict_rc": rc_s,
        "strict_timed_out": to_s,
        "strict_s": round(t1 - t0, 3),
        "verify_pass": verify_pass,
        "verify_rc": rc_v,
        "verify_timed_out": to_v,
        "verify_s": round(t2 - t1, 3),
        "count_fp": count_fp,
        "wall_s": round(t2 - t0, 3),
        "t_start": round(t0, 3),
    }


class AmbientSampler(threading.Thread):
    """Background load-average sampler (host-load profiling supplement)."""

    def __init__(self, interval: float = 3.0) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[float] = []
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.samples.append(float(Path("/proc/loadavg").read_text().split()[0]))
            except (OSError, ValueError, IndexError):
                pass
            self._stop_evt.wait(self.interval)

    def stop(self) -> dict:
        self._stop_evt.set()
        self.join(timeout=self.interval + 1)
        if not self.samples:
            return {"load_min": None, "load_max": None, "load_avg": None, "n": 0}
        return {
            "load_min": round(min(self.samples), 2),
            "load_max": round(max(self.samples), 2),
            "load_avg": round(sum(self.samples) / len(self.samples), 2),
            "n": len(self.samples),
        }


def _classify(results: list[dict]) -> dict:
    """Aggregate one test's reps into a verdict."""
    non_to = [r for r in results if not r["strict_timed_out"] and not r["verify_timed_out"]]
    timeouts = len(results) - len(non_to)

    out_hashes = {r["output_hash"] for r in non_to if r["output_hash"] is not None}
    fps = {r["count_fp"] for r in non_to if r["count_fp"] is not None}
    verify_states = {r["verify_pass"] for r in non_to}
    n_pass = sum(1 for r in non_to if r["verify_pass"])
    n_fail = len(non_to) - n_pass

    p0 = False
    if len(out_hashes) > 1:
        verdict = "P0_OUTPUT_DIVERGENCE"
        p0 = True
    elif verify_states == {True, False}:
        verdict = "P0_VERIFY_FLIP"
        p0 = True
    elif len(fps) > 1:
        verdict = "P0_SCHEDULE_FP_DIVERGENCE"
        p0 = True
    elif non_to and n_pass == 0:
        verdict = "PREEXISTING_VERIFY_FAIL"  # consistent failure -> determinism defect, not load
    elif non_to and n_pass == len(non_to) and len(out_hashes) <= 1 and len(fps) <= 1:
        verdict = "GREEN"
    else:
        verdict = "INCONCLUSIVE"  # e.g. all reps timed out

    return {
        "verdict": verdict,
        "p0": p0,
        "reps": len(results),
        "reps_scored": len(non_to),
        "timeouts": timeouts,
        "verify_pass": n_pass,
        "verify_fail": n_fail,
        "distinct_output_hashes": sorted(out_hashes),
        "distinct_count_fps": len(fps),
        "sample_output_hash": next(iter(out_hashes), None),
        "sample_count_fp": next(iter(fps), None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Hermit load-independence guardrail (worker pool)")
    ap.add_argument("--mode", choices=["reps", "timed"], required=True)
    ap.add_argument("--reps", type=int, default=10, help="reps mode: reps per test")
    ap.add_argument("--minutes", type=float, default=60.0, help="timed mode: window minutes")
    ap.add_argument("--pool", type=int, default=0, help="worker-pool size (0 = auto)")
    ap.add_argument("--oversub", type=float, default=1.5, help="timed auto pool = nproc*oversub")
    ap.add_argument("--per-run-timeout", type=int, default=180, help="per hermit invocation (s)")
    ap.add_argument("--hermit-bin", required=True)
    ap.add_argument("--hermit-root", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--tests", default="", help="comma-separated subset (default: all)")
    args = ap.parse_args()

    nproc = os.cpu_count() or 1
    if args.pool > 0:
        pool = args.pool
    elif args.mode == "timed":
        pool = max(nproc + 1, min(2 * nproc, int(nproc * args.oversub)))  # oversubscribe
    else:
        pool = nproc  # reps: bound == Nprocs

    suite = build_suite(args.hermit_root)
    if args.tests:
        want = [t.strip() for t in args.tests.split(",") if t.strip()]
        suite = {k: v for k, v in suite.items() if k in want}
        if not suite:
            print(f"ERROR: no tests match {args.tests!r}", file=sys.stderr)
            return 2

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    jsonl_path = outdir / f"reps_{args.mode}_{ts}.jsonl"
    summary_path = outdir / f"summary_{args.mode}_{ts}.json"

    print(
        f"[guardrail] mode={args.mode} pool={pool} (nproc={nproc}) "
        f"tests={list(suite)} per_run_timeout={args.per_run_timeout}s",
        flush=True,
    )
    if args.mode == "reps":
        print(f"[guardrail] reps/test={args.reps} total_jobs={args.reps * len(suite)}", flush=True)
    else:
        print(f"[guardrail] window={args.minutes} min", flush=True)

    ambient = AmbientSampler()
    ambient.start()
    results: list[dict] = []
    rlock = threading.Lock()
    jf = jsonl_path.open("w")

    def record(res: dict) -> None:
        with rlock:
            results.append(res)
            jf.write(json.dumps(res) + "\n")
            jf.flush()

    wall0 = time.monotonic()

    if args.mode == "reps":
        jobs = [(name, i) for name in suite for i in range(args.reps)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=pool) as ex:
            futs = [
                ex.submit(run_one_rep, args.hermit_bin, name, suite[name], args.per_run_timeout)
                for (name, _i) in jobs
            ]
            for fut in concurrent.futures.as_completed(futs):
                record(fut.result())
    else:
        deadline = wall0 + args.minutes * 60.0
        started = {name: 0 for name in suite}
        slock = threading.Lock()

        def next_test() -> str | None:
            with slock:
                if time.monotonic() >= deadline:
                    return None
                name = min(started, key=lambda k: started[k])  # fewest-started == fair turns
                started[name] += 1
                return name

        def worker() -> None:
            while True:
                name = next_test()
                if name is None:
                    return
                record(run_one_rep(args.hermit_bin, name, suite[name], args.per_run_timeout))

        with concurrent.futures.ThreadPoolExecutor(max_workers=pool) as ex:
            for fut in [ex.submit(worker) for _ in range(pool)]:
                fut.result()

    jf.close()
    wall = time.monotonic() - wall0
    ambient_stats = ambient.stop()

    by_test = {name: _classify([r for r in results if r["test"] == name]) for name in suite}
    any_p0 = any(v["p0"] for v in by_test.values())
    any_preexisting = any(v["verdict"] == "PREEXISTING_VERIFY_FAIL" for v in by_test.values())

    summary = {
        "mode": args.mode,
        "timestamp": ts,
        "wall_s": round(wall, 1),
        "pool": pool,
        "nproc": nproc,
        "per_run_timeout": args.per_run_timeout,
        "total_reps": len(results),
        "hermit_bin": args.hermit_bin,
        "ambient_load": ambient_stats,
        "any_p0": any_p0,
        "any_preexisting_fail": any_preexisting,
        "by_test": by_test,
        "jsonl": str(jsonl_path),
    }
    if args.mode == "reps":
        summary["reps_per_test"] = args.reps
    else:
        summary["window_min"] = args.minutes

    summary_path.write_text(json.dumps(summary, indent=2))

    # Human-readable rollup (captured to the run log by run.sh; no stream flood).
    print("\n===== GUARDRAIL SUMMARY =====", flush=True)
    print(
        f"mode={args.mode} pool={pool} nproc={nproc} wall={wall:.1f}s "
        f"total_reps={len(results)} load[min/avg/max]="
        f"{ambient_stats['load_min']}/{ambient_stats['load_avg']}/{ambient_stats['load_max']}",
        flush=True,
    )
    for name, v in by_test.items():
        print(
            f"  {name:12s} {v['verdict']:26s} runs={v['reps']:5d} "
            f"scored={v['reps_scored']:5d} verify_pass={v['verify_pass']:5d} "
            f"verify_fail={v['verify_fail']:4d} timeouts={v['timeouts']:4d} "
            f"hashes={len(v['distinct_output_hashes'])} fps={v['distinct_count_fps']}",
            flush=True,
        )
    print(f"summary: {summary_path}", flush=True)
    print(f"any_p0={any_p0} any_preexisting_fail={any_preexisting}", flush=True)

    if any_p0:
        return 2
    if any_preexisting:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
