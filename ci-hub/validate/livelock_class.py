#!/usr/bin/env python3
"""Separate a LIVELOCK from a contended wait on a killed step — the thing a
wall-clock budget cannot do.

THE PROBLEM
-----------
A wall-clock timeout says "this step used all its time". It cannot say WHY. Two
completely different failures land in the same bucket:

  * **livelock** — the step burned a full core spinning and would never have
    finished. Observed live 2026-08-04: `tests_misc-e2e5` in `step-test.detcore_misc`
    at 100% CPU in `futex_` for 224s, its `vfork` child `ptrace`-stopped at 0% CPU.
    A `vfork` parent must block until the child execs or exits; a ptrace-stopped
    child does neither, so the wait is UNSATISFIABLE — not slow, not flaky,
    IMPOSSIBLE. Re-dispatching it burns another full budget for the same
    non-result.
  * **contended wait** — the step was blocked (I/O, a lock, a loaded box) and
    would have finished with more time or a quieter host. Re-dispatching is
    exactly right.

Wall time alone cannot tell them apart, so a livelock PASSES on a quiet box and
FAILS on a loaded one — indistinguishable from a contention flake. CPU time can:
a spinner burns a core, a blocked wait does not.

THE DISCRIMINATOR, AND WHY THE OBVIOUS FORM IS WRONG
----------------------------------------------------
The tempting rule is "wall == CPU ⇒ livelock". Measured against the local
step-profile corpus that rule is **not safe on its own**, for two reasons:

1. `cpu/wall ≈ 1.0` is *ordinary* for a busy single-threaded step. Over 68 rows
   with a usable wall time the ratio distribution is
   **min 0.012 · p50 0.919 · p90 6.649 · max 127.262** — the median completed
   step already sits near 1.0, and parallel work routinely runs far above it.
   So the ratio alone flags healthy work.
2. Conversely a *multi-threaded* livelock is not `≈ 1.0` at all — N spinning
   threads give `≈ N`. "wall == CPU" only ever describes the single-threaded case.

So the ratio is a measure of **cores burned**, not a livelock test, and the
classification needs `timed_out` as a mandatory conjunct:

    timed_out AND cores_burned >= LIVELOCK_CORES   ->  LIVELOCK
    timed_out AND cores_burned <  LIVELOCK_CORES   ->  CONTENDED_WAIT
    timed_out AND no CPU data                      ->  UNKNOWN_NO_CPU  (never guessed)
    not timed_out                                  ->  NOT_APPLICABLE

`cores_burned = (user_s + sys_s) / elapsed_s`.

THE THRESHOLD IS A POLICY CHOICE INSIDE A MEASURED GAP
-------------------------------------------------------
Both sides are observed, and they are far apart:

| case | wall | cpu | cores_burned |
| --- | --- | --- | --- |
| confirmed livelock (`test.detcore_misc` @85626e18, runner step profile) | 600.013 | 599.986 | **0.99995** |
| killed-but-blocked (`test.rr_suite_contract`, local corpus) | 300.191 | 100.9 | **0.336** |
| killed, CPU excluded from parent getrusage (`detcore_misc` @3d5b42ce) | 601 | 7.47 | **0.012** |

`LIVELOCK_CORES = 0.90` sits inside the 0.336 → 0.99995 gap. That is a JUDGEMENT,
not a derivation, so `classify()` reports the threshold it used and the report
labels it. Anything in the band is reported with its exact `cores_burned` so a
reviewer can see how near the line it fell.

WHY `UNKNOWN_NO_CPU` IS A FIRST-CLASS VERDICT
----------------------------------------------
This classification **cannot be applied retroactively to the validate ledger**.
Per-gate ledger rows carry only `real_seconds`; there is no user/sys field at gate
granularity, and the top-level run's CPU is not a usable proxy — a run whose child
was measured live at 100% CPU for 601s recorded **7.47 CPU-seconds total**, because
the spinning child's CPU is excluded from the orchestrator's `getrusage`.

So a killed gate with no CPU data is **UNKNOWN**, and saying so is the point. Any
default here — "assume contended, re-dispatch" or "assume livelock, quarantine" —
would manufacture a verdict the data does not support, and the first of those is
exactly the behaviour that burns a full budget re-running a confirmed livelock.

THE DATA SOURCE THAT WORKS
--------------------------
The runner's **step-profile CSV** (`step_profiles_<machine>.csv`, written by
`safe-ci-dag-runner`'s `append_step_profiles`) carries per-step `elapsed_s`,
`user_s`, `sys_s`, `timed_out`, `returncode`, plus `effective_cores`,
`throttled_s` and co-tenancy columns. That is the instrument this classifier
consumes. Long-term the kill path should read the node's own cgroup `cpu.stat` at
kill time rather than a parent's `getrusage`, but the step profile already has
what the discriminator needs.

USAGE
  livelock_class.py --profiles CSV [CSV ...] [--json]
  from livelock_class import classify, classify_row

EXIT CODES
  0  no livelock found   ·   2  at least one LIVELOCK   ·   3  error
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict

LIVELOCK = "LIVELOCK"
CONTENDED_WAIT = "CONTENDED_WAIT"
UNKNOWN_NO_CPU = "UNKNOWN_NO_CPU"
NOT_APPLICABLE = "NOT_APPLICABLE"

EXIT_OK, EXIT_LIVELOCK, EXIT_ERROR = 0, 2, 3

#: Cores burned at or above which a KILLED step is called a livelock. A policy
#: choice inside the measured 0.336 -> 0.99995 gap (see the module docstring),
#: not a derivation — every report states it.
LIVELOCK_CORES = 0.90


@dataclass
class Verdict:
    step: str
    verdict: str
    wall_s: float | None
    cpu_s: float | None
    cores_burned: float | None
    threshold_cores: float
    reason: str


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def classify(
    *,
    step: str,
    wall_s: float | None,
    user_s: float | None,
    sys_s: float | None,
    timed_out: bool,
    threshold_cores: float = LIVELOCK_CORES,
) -> Verdict:
    """Classify one step. Pure: no I/O, no clock, no host inspection."""
    if not timed_out:
        return Verdict(step, NOT_APPLICABLE, wall_s, None, None, threshold_cores,
                       "the step was not killed at a budget; this classification "
                       "only distinguishes WHY a kill happened")

    if wall_s is None or wall_s <= 0:
        return Verdict(step, UNKNOWN_NO_CPU, wall_s, None, None, threshold_cores,
                       "no usable wall time; cores burned is not computable")

    if user_s is None and sys_s is None:
        return Verdict(
            step, UNKNOWN_NO_CPU, wall_s, None, None, threshold_cores,
            "killed with NO CPU accounting. Not guessable: a parent's getrusage "
            "excludes a spinning child's CPU (601s wall measured at 7.47 CPU-s "
            "while the child ran at 100%), so absence of CPU data is absence of "
            "evidence, not evidence of a blocked wait")

    cpu_s = (user_s or 0.0) + (sys_s or 0.0)
    cores = cpu_s / wall_s

    if cores >= threshold_cores:
        return Verdict(
            step, LIVELOCK, wall_s, cpu_s, cores, threshold_cores,
            f"burned {cores:.3f} core(s) for the whole budget before the kill "
            f"(>= {threshold_cores}); a blocked wait cannot consume CPU it is not "
            f"running. Re-dispatching spends another full budget on the same "
            f"non-result")
    return Verdict(
        step, CONTENDED_WAIT, wall_s, cpu_s, cores, threshold_cores,
        f"burned only {cores:.3f} core(s) across the budget (< {threshold_cores}); "
        f"the step was waiting, not spinning, so more time or a quieter host may "
        f"resolve it")


def classify_row(row: dict, threshold_cores: float = LIVELOCK_CORES) -> Verdict:
    """Classify one `step_profiles_*.csv` row."""
    return classify(
        step=row.get("step") or "<unnamed>",
        wall_s=_num(row.get("elapsed_s")),
        user_s=_num(row.get("user_s")),
        sys_s=_num(row.get("sys_s")),
        timed_out=_truthy(row.get("timed_out")),
        threshold_cores=threshold_cores,
    )


def classify_profiles(paths: list[str], threshold_cores: float = LIVELOCK_CORES):
    verdicts, unreadable = [], []
    for path in paths:
        try:
            with open(path, newline="") as handle:
                for row in csv.DictReader(handle):
                    verdicts.append(classify_row(row, threshold_cores))
        except OSError as exc:
            unreadable.append(f"{path}: {exc}")
    return verdicts, unreadable


# --------------------------------------------------------------------------- #
# Gate-level adapter: the junction a timeout consumer calls.                    #
# --------------------------------------------------------------------------- #

#: Exit codes that mean "killed at a wall budget" rather than "the product
#: failed": 124 = GNU timeout, 137 = SIGKILL (128+9), 143 = SIGTERM (128+15).
WALL_KILL_EXIT_CODES = (124, 137, 143, -9, -15)


def gate_is_wall_kill(gate: dict) -> bool:
    """Was this ledger gate killed at a budget (as opposed to failing)?"""
    if str(gate.get("result", "")).strip().lower() == "timeout":
        return True
    rc = gate.get("returncode")
    return isinstance(rc, int) and rc in WALL_KILL_EXIT_CODES


def index_profiles_by_step(paths: list[str]) -> dict[str, dict]:
    """Newest step-profile row per step name, keyed for gate lookup."""
    index: dict[str, dict] = {}
    for path in paths:
        try:
            with open(path, newline="") as handle:
                for row in csv.DictReader(handle):
                    step = row.get("step")
                    if step:
                        index[step] = row
        except OSError:
            continue
    return index


def classify_gate(gate: dict, profile_index: dict[str, dict],
                  threshold_cores: float = LIVELOCK_CORES) -> Verdict | None:
    """Classify one LEDGER gate row by joining it to step-profile CPU data.

    Returns ``None`` for a gate that was not killed at a budget — those are
    ordinary failures and this classification does not apply to them.

    A killed gate with no matching profile row yields ``UNKNOWN_NO_CPU``, NOT
    ``CONTENDED_WAIT``. That is the whole point: ledger gate rows carry only
    ``real_seconds``, so the CPU evidence simply is not there, and defaulting to
    "contended, re-dispatch" is what burns a second full budget re-running a
    confirmed livelock.
    """
    if not gate_is_wall_kill(gate):
        return None
    name = gate.get("name") or "<unnamed>"
    # Gate names may be lane-prefixed ("portable:test.x"); step profiles are not.
    bare = name.rsplit(":", 1)[-1]
    row = profile_index.get(bare) or profile_index.get(name)
    if row is None:
        return Verdict(
            name, UNKNOWN_NO_CPU, _num(gate.get("real_seconds")), None, None,
            threshold_cores,
            "killed at a budget with no step-profile row to join: the ledger gate "
            "carries wall time only, so livelock vs contended wait is NOT "
            "recoverable for this gate. Record per-node CPU at kill time (the "
            "node's own cgroup cpu.stat, not a parent getrusage) to make it so")
    verdict = classify_row(row, threshold_cores)
    verdict.step = name
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify killed steps as livelock vs contended wait.")
    parser.add_argument("--profiles", nargs="+", required=True,
                        help="step_profiles_*.csv path(s)")
    parser.add_argument("--threshold-cores", type=float, default=LIVELOCK_CORES)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    verdicts, unreadable = classify_profiles(args.profiles, args.threshold_cores)
    if unreadable and not verdicts:
        for problem in unreadable:
            print(f"livelock_class: {problem}", file=sys.stderr)
        return EXIT_ERROR

    killed = [v for v in verdicts if v.verdict != NOT_APPLICABLE]
    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    report = {
        "rows": len(verdicts),
        "killed_rows": len(killed),
        "counts": counts,
        "threshold_cores": args.threshold_cores,
        "threshold_basis": (
            "POLICY CHOICE inside the measured gap 0.336 (killed-but-blocked) -> "
            "0.99995 (confirmed livelock); not a derivation"),
        "unreadable": unreadable,
        "killed": [asdict(v) for v in killed],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"rows={len(verdicts)} killed={len(killed)} "
              f"threshold={args.threshold_cores} cores")
        print(f"  basis: {report['threshold_basis']}")
        for klass in sorted(counts):
            print(f"  {klass:<16} {counts[klass]}")
        for v in killed:
            mark = "🔴" if v.verdict == LIVELOCK else "  "
            cores = "n/a" if v.cores_burned is None else f"{v.cores_burned:.3f}"
            print(f"{mark} {v.step:<34} {v.verdict:<16} cores={cores} wall={v.wall_s}")
            print(f"      {v.reason}")
        for problem in unreadable:
            print(f"  unreadable: {problem}")
    return EXIT_LIVELOCK if counts.get(LIVELOCK) else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
