#!/usr/bin/env python3
"""Bounded scheduled trigger for the Reverie auto-safe-bump.

The engine (`auto_bump`) is atomic and the gate (`validation_gate`) is real.
This is the thing a timer invokes, and its job is to make one attempt, under a
wall budget, and leave exactly one durable outcome record behind whatever
happens.

WHY A DURABLE OUTCOME IS PART OF THE DESIGN, NOT LOGGING
--------------------------------------------------------
A scheduled job that leaves nothing behind is indistinguishable from a timer
that never fired — which is precisely how an hourly delivery outage hid for ten
hours on this box. So every run appends one JSONL record BEFORE it can fail, and
updates it with the outcome. "The job ran and refused" and "the job never ran"
must never render the same.

Each record carries the exact source and target SHAs and the measured cost,
because a bump is a claim about two specific commits and a bare "ok" cannot be
audited later.

WHY IT IS BOUNDED
-----------------
Validation is box-exclusive and can take a long time. An unbounded scheduled job
that overruns its period stacks up runs competing for the same lock. The wall
budget makes the job return under its own power with a typed `budget-exceeded`
outcome instead of being killed with none — the same reasoning that governs the
CI timeout gate.

WHAT IT NEVER DOES
------------------
It never touches a primary checkout. It operates on an isolated checkout given
to it, and it never pushes: publishing a validated bump is a separate,
explicitly authorized step. A scheduled job that could push would be a scheduled
job that could push something nobody reviewed.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import auto_bump
import validation_gate

DEFAULT_BUDGET_SECONDS = 3600.0
DEFAULT_OUTCOME_LOG = Path.home() / ".local/state/hermit-reverie-pin-bump/outcomes.jsonl"


@dataclass
class Outcome:
    started_at: str
    repo: str
    outcome: str = "started"
    source_sha: str | None = None          # the pin BEFORE the bump
    target_sha: str | None = None          # the pin AFTER  the bump
    candidate_commit: str | None = None    # the commit validation was bound to
    entries_before: int | None = None
    entries_after: int | None = None
    files_changed: int | None = None
    validated: bool | None = None
    wall_seconds: float | None = None
    budget_seconds: float | None = None
    detail: str = ""
    rolled_back: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"{self.outcome} source={_short(self.source_sha)} "
                f"target={_short(self.target_sha)} entries="
                f"{self.entries_before}->{self.entries_after} "
                f"wall={self.wall_seconds:.1f}s" if self.wall_seconds is not None
                else f"{self.outcome} source={_short(self.source_sha)}")


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "-"


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_outcome(path: Path, outcome: Outcome) -> None:
    """One whole line, O_APPEND, so concurrent runs never tear a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(asdict(outcome), sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def run_once(
    repo: Path,
    *,
    ci_hub: Path,
    outcome_log: Path = DEFAULT_OUTCOME_LOG,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    validate: Callable[[], bool] | None = None,
    target: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """One bounded attempt. Always returns an Outcome; always records one."""
    started = clock()
    outcome = Outcome(started_at=_utc(), repo=str(repo), budget_seconds=budget_seconds)

    # Recorded BEFORE anything can fail, so a crashed run is still visible as an
    # attempt rather than as silence.
    append_outcome(outcome_log, outcome)

    def finish(state: str, detail: str = "") -> Outcome:
        outcome.outcome = state
        outcome.detail = detail
        outcome.wall_seconds = round(clock() - started, 3)
        append_outcome(outcome_log, outcome)
        return outcome

    try:
        entries = auto_bump.derive_entries(repo)
        distinct = sorted({r for e in entries for r in e.revs})
        outcome.entries_before = sum(len(e.revs) for e in entries)
        outcome.source_sha = distinct[0] if len(distinct) == 1 else None
        if len(distinct) > 1:
            # Already inconsistent before we started: bumping would mask it.
            return finish("refused-preexisting-inconsistency",
                          f"tree already carries {len(distinct)} distinct Reverie "
                          f"revisions {distinct}; fix that before bumping")

        target_sha = target or auto_bump.resolve_reverie_tip()
        outcome.target_sha = target_sha

        if outcome.source_sha == target_sha:
            outcome.entries_after = outcome.entries_before
            outcome.files_changed = 0
            return finish("noop-already-current",
                          f"all {outcome.entries_before} entries already at {target_sha[:12]}")

        if clock() - started > budget_seconds:
            return finish("budget-exceeded", "wall budget spent before the bump began")

        gate = validate or validation_gate.real_validator(repo, ci_hub=ci_hub)
        report = auto_bump.auto_safe_bump(repo, target=target_sha, validate=gate)

        outcome.entries_after = report.entries_after
        outcome.files_changed = len(report.changed_files)
        outcome.validated = report.validated
        state = getattr(gate, "state", {}) or {}
        outcome.candidate_commit = state.get("sha")  # type: ignore[assignment]

        elapsed = clock() - started
        if elapsed > budget_seconds:
            # The bump completed but overran; say so rather than reporting a
            # clean success, because the next scheduled tick needs to know the
            # period is too short for this work.
            return finish("bumped-over-budget",
                          f"bump validated but took {elapsed:.0f}s > budget {budget_seconds:.0f}s")
        return finish("bumped-and-validated",
                      f"{outcome.entries_before} entries -> {target_sha[:12]}")

    except auto_bump.BumpRefused as exc:
        return finish("refused", str(exc)[:400])
    except Exception as exc:  # noqa: BLE001 — fail-closed, and still recorded
        return finish("error", f"{type(exc).__name__}: {exc}"[:400])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="ISOLATED checkout to bump (never a primary)")
    ap.add_argument("--ci-hub", default=str(Path(__file__).resolve().parents[2] / "ci-hub" / "ci-hub"))
    ap.add_argument("--outcome-log", default=str(DEFAULT_OUTCOME_LOG))
    ap.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    ap.add_argument("--target", help="explicit 40-hex SHA (default: live reverie tip)")
    args = ap.parse_args(argv)

    outcome = run_once(
        Path(args.repo), ci_hub=Path(args.ci_hub),
        outcome_log=Path(args.outcome_log),
        budget_seconds=args.budget_seconds, target=args.target,
    )
    print(json.dumps(asdict(outcome), sort_keys=True))
    # Only a genuine error is a unit failure. A refusal is the safety property
    # working, and a no-op is the normal steady state; neither should turn a
    # systemd unit red and start paging.
    return 1 if outcome.outcome == "error" else 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
