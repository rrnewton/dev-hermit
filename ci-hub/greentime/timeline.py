#!/usr/bin/env python3
"""Sparse-signal green-time timeline: carry state forward between signal points.

THE MODEL. On a branch's linear history every commit either carries a SIGNAL
(``red`` / ``soft-green`` / ``hard-green``) or carries none. State is CARRIED
FORWARD from a signal point until the next signal point, where it flips. Any
non-zero amount of signal therefore yields a usable estimate, and the estimate
converges on truth as signal densifies; validating every commit is the precise
limit of the same model, not a different one.

WHY THIS REPLACES THE REIGN MODEL. The previous green-time reported ~81% of
wall-clock as "no data" and presented that as a measurement. It is not one: it
is a statement that we did not go and get the signal. A number that is 81%
"unknown" cannot be acted on, and worse, it *looks* like a result. Under carry-
forward there is no no-data bucket to hide in. What replaces it is an explicit
QUALITY report -- how sparse the signal is, where the holes are, and exactly
which commits to validate to close them.

WHAT IS HONESTLY UNKNOWABLE, STATED RATHER THAN HIDDEN:
  * Time BEFORE the first signal point has nothing to carry forward from. It is
    reported as ``unknown_lead`` and is never counted as green.
  * A red->green flip entirely inside a no-signal gap is invisible; the gap is
    attributed to the earlier state. This is a real bias, but a mild one in
    practice: fixing a breakage involves thrashing that itself leaves signal.
    ``max_gap_seconds`` bounds the error, which is exactly why densification is
    part of the metric and not an optional extra.

DENSIFICATION IS PART OF THE METRIC. ``plan_densification`` emits the commits
to validate so no gap exceeds a target (default one hour of commit-timestamp
realtime), choosing each next probe at the TIME midpoint of the widest
remaining gap, so every probe halves the worst error. The plan is ordered by
value: run it head-first and stop whenever the box gets busy.

Pure module: no network, no clock, no repo access. Callers supply commits and
signals; ``main()`` wires stdin/JSON so the same model is reusable by any
project rather than being a dev-hermit one-off.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

#: The three signal states, worst first. Order matters: `worst_of` uses it.
RED = "red"
SOFT_GREEN = "soft-green"
HARD_GREEN = "hard-green"
STATES = (RED, SOFT_GREEN, HARD_GREEN)

#: Absence of any carried state (before the first signal point).
UNKNOWN = "unknown"

DEFAULT_MAX_GAP_SECONDS = 3600


@dataclass(frozen=True)
class Commit:
    """One commit on the branch's linear history."""

    sha: str
    timestamp: int  # commit timestamp, unix seconds

    def __post_init__(self) -> None:
        if not self.sha:
            raise ValueError("commit sha must be non-empty")


@dataclass(frozen=True)
class Segment:
    """A maximal run of wall-clock time in one carried state."""

    state: str
    start_ts: int
    end_ts: int
    start_sha: str
    #: The signal commit this state was carried forward FROM, or None for the
    #: unknown lead. Recording it keeps a segment auditable: a reader can go
    #: back to the receipt that justifies every second attributed here.
    source_sha: str | None

    @property
    def seconds(self) -> int:
        return max(0, self.end_ts - self.start_ts)


@dataclass
class Gap:
    """A stretch of branch history with no signal, bounded by signal points."""

    #: Signal commit before the hole (None when the hole opens the history).
    before_sha: str | None
    #: Signal commit after the hole (None when the hole runs to the tip).
    after_sha: str | None
    start_ts: int
    end_ts: int
    #: Commits inside the hole, in history order. These are the probe candidates.
    commits: list[Commit] = field(default_factory=list)

    @property
    def seconds(self) -> int:
        return max(0, self.end_ts - self.start_ts)


def worst_of(states: Iterable[str]) -> str:
    """Worst state present, by STATES order. Used when a commit has several."""
    seen = [s for s in states if s in STATES]
    if not seen:
        raise ValueError("no recognised state")
    return min(seen, key=STATES.index)


def build_timeline(
    commits: Sequence[Commit],
    signals: Mapping[str, str],
    *,
    now: int,
) -> list[Segment]:
    """Carry signal state forward across ``commits`` (oldest first).

    ``now`` closes the final segment. Commits must be ordered oldest -> newest;
    that is the branch's linear history, not sort order, because a merge can
    place an older timestamp later and the CARRY direction is history order.
    """
    if not commits:
        return []
    for s in signals.values():
        if s not in STATES:
            raise ValueError(f"unknown signal state {s!r}; expected one of {STATES}")

    segments: list[Segment] = []
    state = UNKNOWN
    source: str | None = None
    seg_start_ts = commits[0].timestamp
    seg_start_sha = commits[0].sha

    for idx, commit in enumerate(commits):
        new_state = signals.get(commit.sha, state)
        if new_state != state:
            # Close the previous segment at this commit and flip.
            if commit.timestamp > seg_start_ts or state != UNKNOWN:
                segments.append(
                    Segment(state, seg_start_ts, commit.timestamp, seg_start_sha, source)
                )
            state = new_state
            source = commit.sha
            seg_start_ts = commit.timestamp
            seg_start_sha = commit.sha
        elif commit.sha in signals:
            # Same state re-confirmed: not a flip, but it IS fresh evidence, so
            # it becomes the source a later reader is pointed at.
            source = commit.sha
        del idx

    end_ts = max(now, commits[-1].timestamp)
    segments.append(Segment(state, seg_start_ts, end_ts, seg_start_sha, source))
    return [s for s in segments if s.seconds > 0]


def find_gaps(commits: Sequence[Commit], signals: Mapping[str, str], *, now: int) -> list[Gap]:
    """Stretches with no signal, widest-relevant first in history order."""
    gaps: list[Gap] = []
    current: Gap | None = None
    last_signal_sha: str | None = None

    for commit in commits:
        if commit.sha in signals:
            if current is not None:
                current.after_sha = commit.sha
                current.end_ts = commit.timestamp
                gaps.append(current)
                current = None
            last_signal_sha = commit.sha
            continue
        if current is None:
            current = Gap(
                before_sha=last_signal_sha,
                after_sha=None,
                start_ts=commit.timestamp,
                end_ts=commit.timestamp,
            )
        current.commits.append(commit)
        current.end_ts = commit.timestamp

    if current is not None:
        current.end_ts = max(now, current.end_ts)
        gaps.append(current)
    return gaps


def plan_densification(
    commits: Sequence[Commit],
    signals: Mapping[str, str],
    *,
    now: int,
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
    limit: int | None = None,
) -> list[str]:
    """SHAs to validate so no gap exceeds ``max_gap_seconds``, best value first.

    Each probe is the TIME midpoint of the currently widest gap, so it halves
    the worst attribution error rather than merely adding a data point. The
    result is ordered: run it head-first and stop when the box gets busy, and
    every prefix is still the best plan of its length.
    """
    known = dict(signals)
    plan: list[str] = []

    while limit is None or len(plan) < limit:
        gaps = [g for g in find_gaps(commits, known, now=now) if g.seconds > max_gap_seconds]
        gaps = [g for g in gaps if g.commits]
        if not gaps:
            break
        widest = max(gaps, key=lambda g: g.seconds)
        target_ts = (widest.start_ts + widest.end_ts) // 2
        probe = min(widest.commits, key=lambda c: (abs(c.timestamp - target_ts), c.sha))
        plan.append(probe.sha)
        # Assume the probe returns SOME signal. Which state it returns cannot
        # change WHERE the next probe belongs -- only the gap structure does --
        # so planning with a placeholder is exact, not an approximation.
        known[probe.sha] = HARD_GREEN
    return plan


def red_segments(segments: Sequence[Segment]) -> list[Segment]:
    return [s for s in segments if s.state == RED]


def plan_red_tightening(
    commits: Sequence[Commit],
    signals: Mapping[str, str],
    *,
    now: int,
) -> list[dict[str, object]]:
    """Per red observation, the probes that tighten its true extent.

    Two directions, both of which the metric needs and debugging wants anyway:
      * FORWARD to the fix -- a red segment otherwise runs to the next arbitrary
        signal point, which overstates red time.
      * BACKWARD to the first bad commit -- blame. Walking earlier is a natural
        part of debugging a breakage, so this is signal we were going to
        produce regardless; the point is to capture it instead of discarding it.
    """
    out: list[dict[str, object]] = []
    index = {c.sha: i for i, c in enumerate(commits)}
    for sha, state in signals.items():
        if state != RED or sha not in index:
            continue
        i = index[sha]
        earlier = [c for c in commits[:i] if c.sha not in signals]
        later = [c for c in commits[i + 1 :] if c.sha not in signals]
        out.append(
            {
                "red_sha": sha,
                # Midpoint probes: bisect, do not linear-scan.
                "first_bad_probe": earlier[len(earlier) // 2].sha if earlier else None,
                "fix_probe": later[len(later) // 2].sha if later else None,
                "unsignalled_before": len(earlier),
                "unsignalled_after": len(later),
            }
        )
    return out


def summarize(
    commits: Sequence[Commit],
    signals: Mapping[str, str],
    *,
    now: int,
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
    plan_limit: int | None = 20,
) -> dict[str, object]:
    """The full report: estimate + the quality of the estimate + what to do."""
    segments = build_timeline(commits, signals, now=now)
    gaps = find_gaps(commits, signals, now=now)
    by_state: dict[str, int] = {s: 0 for s in (*STATES, UNKNOWN)}
    for seg in segments:
        by_state[seg.state] += seg.seconds

    total = sum(by_state.values())
    # The denominator EXCLUDES the unknown lead: percentages describe the time
    # the model can actually speak about. The lead is reported beside them so it
    # can never be silently absorbed into a green number.
    attributable = total - by_state[UNKNOWN]
    over = [g for g in gaps if g.seconds > max_gap_seconds]

    def pct(x: int) -> float:
        return round(100.0 * x / attributable, 2) if attributable else 0.0

    return {
        "model": "sparse-signal-carry-forward",
        "commits": len(commits),
        "signal_points": sum(1 for c in commits if c.sha in signals),
        "seconds": dict(by_state),
        "attributable_seconds": attributable,
        "green_pct": pct(by_state[HARD_GREEN] + by_state[SOFT_GREEN]),
        "hard_green_pct": pct(by_state[HARD_GREEN]),
        "soft_green_pct": pct(by_state[SOFT_GREEN]),
        "red_pct": pct(by_state[RED]),
        "unknown_lead_seconds": by_state[UNKNOWN],
        "quality": {
            "max_gap_seconds": max(((g.seconds) for g in gaps), default=0),
            "target_max_gap_seconds": max_gap_seconds,
            "gaps_over_target": len(over),
            "seconds_in_gaps_over_target": sum(g.seconds for g in over),
            "meets_target": not over,
        },
        "densification_plan": plan_densification(
            commits, signals, now=now, max_gap_seconds=max_gap_seconds, limit=plan_limit
        ),
        "red_tightening": plan_red_tightening(commits, signals, now=now),
        "segments": [
            {
                "state": s.state,
                "start_ts": s.start_ts,
                "end_ts": s.end_ts,
                "seconds": s.seconds,
                "start_sha": s.start_sha,
                "source_sha": s.source_sha,
            }
            for s in segments
        ],
    }


def _load(stream) -> dict[str, object]:
    doc = json.load(stream)
    commits = [Commit(c["sha"], int(c["timestamp"])) for c in doc["commits"]]
    signals = dict(doc.get("signals") or {})
    now = int(doc.get("now") or (commits[-1].timestamp if commits else 0))
    return {"commits": commits, "signals": signals, "now": now}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sparse-signal green-time over a linear branch history.",
        epilog=(
            "Reads a JSON document on stdin: "
            '{"commits":[{"sha":..,"timestamp":..} oldest-first], '
            '"signals":{sha: red|soft-green|hard-green}, "now": unix}. '
            "Pure: no network, no repo access, no clock."
        ),
    )
    ap.add_argument("--max-gap-seconds", type=int, default=DEFAULT_MAX_GAP_SECONDS)
    ap.add_argument("--plan-limit", type=int, default=20)
    ap.add_argument("--format", choices=("json", "text"), default="json")
    args = ap.parse_args(argv)

    loaded = _load(sys.stdin)
    report = summarize(
        loaded["commits"],
        loaded["signals"],
        now=loaded["now"],
        max_gap_seconds=args.max_gap_seconds,
        plan_limit=args.plan_limit,
    )
    if args.format == "json":
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    q = report["quality"]
    print(f"model            : {report['model']}")
    print(f"commits/signals  : {report['commits']} / {report['signal_points']}")
    print(f"green            : {report['green_pct']}%  "
          f"(hard {report['hard_green_pct']}%, soft {report['soft_green_pct']}%)")
    print(f"red              : {report['red_pct']}%")
    print(f"unknown lead     : {report['unknown_lead_seconds']}s (excluded from the denominator)")
    print(f"max gap          : {q['max_gap_seconds']}s (target {q['target_max_gap_seconds']}s) "
          f"-> {'MEETS' if q['meets_target'] else 'MISSES'}")
    print(f"gaps over target : {q['gaps_over_target']} "
          f"covering {q['seconds_in_gaps_over_target']}s")
    if report["densification_plan"]:
        print("validate next    : " + " ".join(report["densification_plan"][:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
