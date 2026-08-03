#!/usr/bin/env python3
"""ETA-to-blame estimator for multisect (tool-cost-awareness-convention).

The owner named the exact failure mode to prevent: an agent blindly launches a
search and it runs for 24 hours. So before any expensive work multisect must
print, up front, the expected cost derived from *measured* probe parameters --
and if that cost is enormous, say so prominently and name the levers.

This module is the calculation. It emits the canonical ci-hub cost line

    COST ESTIMATE tool=<name> wall=<s>s cpu=<s>s basis='<...>'

(see ci-hub/TOOL-COST-CONVENTION.md) so the estimate is machine-readable and
consistent with every other dev-hermit tool. The orchestrator wraps the actual
search in ci-hub/bin/tool-cost, which prints the matching COST ACTUAL line with
real wall+CPU on every exit path -- including failure/abort.

The estimator is deliberately import-clean (no side effects) so another tool can
reuse `estimate()` and `render()` directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ProbeCost:
    """Per-probe cost, ideally from a calibration probe rather than a guess."""

    build_cold_s: float  # cold targeted-minimal build (first rep on a commit)
    build_warm_s: float  # warm rebuild (reps 2..N on the same commit)
    test_s: float  # a passing test run
    hang_timeout_s: float  # inner timeout a WEDGED rep burns instead of test_s
    measured: bool = True  # False => bootstrap guess, labelled in the basis


@dataclass
class SearchShape:
    """The search's structural parameters."""

    interval_commits: int  # number of commits strictly between good..bad
    k: int  # interior probes sampled per round
    reps: int  # repetitions per commit (amplification for rare bugs)
    jobs: int  # commits sampled concurrently (engine ThreadPool width)
    wedge_fraction: float = 0.5  # share of reps expected to hit the hang timeout


@dataclass
class Estimate:
    wall_s: float
    cpu_s: float
    rounds: int
    total_probes: int
    total_reps: int
    basis: str
    levers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _rounds(interval_commits: int, k: int) -> int:
    """Bisection rounds: each round shrinks the interval by ~1/(k+1)."""
    if interval_commits <= 1:
        return 1
    if k < 1:
        k = 1
    return max(1, math.ceil(math.log(interval_commits) / math.log(k + 1)))


def estimate(probe: ProbeCost, shape: SearchShape) -> Estimate:
    """Compute the up-front ETA-to-blame and lever suggestions."""
    rounds = _rounds(shape.interval_commits, shape.k)
    # Each round samples up to k interior commits plus the two endpoints; the
    # engine reuses endpoint results across rounds, so ~k fresh commits/round is
    # the honest driver of cost. Round up to include endpoint re-checks once.
    probes_per_round = shape.k + 2
    total_probes = rounds * probes_per_round
    total_reps = total_probes * shape.reps

    # Per-rep cost. Rep 1 on each commit is cold; reps 2..N are warm. A WEDGED
    # rep burns the hang timeout instead of a passing test_s. We size the
    # expected rep cost with wedge_fraction and amortize the single cold build
    # across the commit's reps.
    def commit_cost() -> tuple[float, float]:
        # returns (wall_of_serial_reps, cpu_of_serial_reps) for ONE commit
        cold = probe.build_cold_s
        warm_builds = max(0, shape.reps - 1) * probe.build_warm_s
        green_reps = shape.reps * (1.0 - shape.wedge_fraction)
        wedged_reps = shape.reps * shape.wedge_fraction
        run_wall = green_reps * probe.test_s + wedged_reps * probe.hang_timeout_s
        wall = cold + warm_builds + run_wall
        # CPU: a build is ~parallel-heavy; approximate CPU ~= wall for build
        # (the calibration reports both, so callers can pass a CPU-aware probe
        # by inflating build_*; here we treat build CPU ~ measured cold CPU is
        # folded by the caller). A spinning wedge pins ~1 core => cpu ~ wall.
        cpu = wall
        return wall, cpu

    commit_wall, commit_cpu = commit_cost()

    # Reps are serial per commit; commits run jobs-wide concurrent. Wall is the
    # critical path = commits/jobs batches, each batch ~ one commit's serial
    # reps. CPU is the SUM over all probes (never divided by parallelism).
    jobs = max(1, shape.jobs)
    batches_per_round = math.ceil(probes_per_round / jobs)
    wall_s = rounds * batches_per_round * commit_wall
    cpu_s = total_probes * commit_cpu

    src = "measured calibration probe" if probe.measured else "BOOTSTRAP GUESS (no calibration)"
    basis = (
        f"{shape.interval_commits} commits, k={shape.k}, reps={shape.reps}, "
        f"jobs={shape.jobs} => {rounds} rounds x {probes_per_round} probes "
        f"({total_reps} reps); per-rep from {src}: cold_build={probe.build_cold_s:.0f}s "
        f"warm_build={probe.build_warm_s:.0f}s test={probe.test_s:.1f}s "
        f"hang_timeout={probe.hang_timeout_s:.0f}s wedge_frac={shape.wedge_fraction:.2f}"
    )

    est = Estimate(
        wall_s=wall_s,
        cpu_s=cpu_s,
        rounds=rounds,
        total_probes=total_probes,
        total_reps=total_reps,
        basis=basis,
    )

    if not probe.measured:
        est.warnings.append(
            "Estimate is a BOOTSTRAP GUESS -- run a calibration probe for a real ETA."
        )

    # If the ETA is large, say so prominently and name the levers.
    if wall_s >= 3600:
        est.warnings.append(
            f"ETA-to-blame is LARGE: ~{_hms(wall_s)} wall (~{_hms(cpu_s)} CPU). "
            "Consider the levers below before committing."
        )
        est.levers = _levers(probe, shape, rounds)
    elif wall_s >= 900:
        est.levers = _levers(probe, shape, rounds)
    return est


def _levers(probe: ProbeCost, shape: SearchShape, rounds: int) -> list[str]:
    levers: list[str] = []
    levers.append(
        "Narrow the commit interval (fewer rounds): each halving of the interval "
        f"removes ~1 of {rounds} rounds. Use a known-green anchor as --good."
    )
    if shape.reps > 1:
        lower = max(1, shape.reps // 2)
        levers.append(
            f"Lower reps (N={shape.reps} -> {lower}) roughly halves cost, but "
            "raises the miss probability for a rare bug -- state the confidence "
            "cost: at per-instance hang rate p, a truly-broken commit is missed "
            f"with probability ~(1-p)^N (N={shape.reps} -> {lower})."
        )
    levers.append(
        f"More parallelism (jobs={shape.jobs}): wall scales ~1/jobs until you run "
        "out of cores or fleet courtesy; CPU is unchanged."
    )
    if probe.hang_timeout_s > 4 * max(probe.test_s, 1.0):
        levers.append(
            f"Lower the hang timeout ({probe.hang_timeout_s:.0f}s): wedged reps burn "
            "the full timeout. Set it just above a healthy test run to cut the "
            "broken side's cost -- but leave margin so a slow-but-progressing run "
            "is not misread as a hang (CPU-vs-wall on completion tells them apart)."
        )
    levers.append(
        "Lighter build config: the C3 profile (CARGO_INCREMENTAL=0 + "
        "line-tables-only + split-debuginfo) is already the measured floor that "
        "keeps usable backtraces; going lighter loses backtraces this bug needs."
    )
    return levers


def _hms(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def render(est: Estimate, tool: str = "multisect/search") -> str:
    """Human-facing block; the canonical COST ESTIMATE line goes via cost_line()."""
    lines = [
        "==================== multisect ETA-to-blame ====================",
        f"  rounds:       {est.rounds}",
        f"  probes:       {est.total_probes}  (reps total: {est.total_reps})",
        f"  est. WALL:    {_hms(est.wall_s)}   ({est.wall_s:.0f}s)",
        f"  est. CPU:     {_hms(est.cpu_s)}   ({est.cpu_s:.0f}s)",
        f"  basis:        {est.basis}",
    ]
    for w in est.warnings:
        lines.append(f"  !! {w}")
    if est.levers:
        lines.append("  levers to shrink the search:")
        for lev in est.levers:
            lines.append(f"    - {lev}")
    lines.append("================================================================")
    return "\n".join(lines)


def cost_line(est: Estimate, tool: str = "multisect/search") -> str:
    """The canonical ci-hub COST ESTIMATE line (machine-readable)."""
    return (
        f"COST ESTIMATE tool={tool} wall={est.wall_s:.3f}s cpu={est.cpu_s:.3f}s "
        f"basis={est.basis!r}"
    )
