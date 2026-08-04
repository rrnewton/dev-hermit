#!/usr/bin/env python3
"""Distinguish a DEFECT red from a FLAKE / CONTENTION red in the validate ledger.

The asymmetry this addresses: a GAP in the ledger invites re-measurement, but a
recorded ``result: fail`` does NOT — nobody re-runs a commit the ledger condemns.
So a spurious red is *more* damaging than a missing record and is invisible
because it looks exactly like a real one. This module makes a red carry its
CONDITIONS so it can be interpreted rather than merely believed — the same
principle as the executed-count check (a verdict without its conditions is
uninterpretable), applied to failures instead of passes.

Two MEASURED false-red sources, both real:
  1. CONTENTION on the shared cargo package cache / build dir: #1592 FAILED at
     ``-j16`` (concurrent with other validates) and PASSED at ``-j4`` solo — and
     ``-j4`` was faster. The red was contention, not a defect.
  2. A measured FLAKY CELL: ``command_strict_verify`` was 9 PASS / 1 FAIL of 10
     under identical conditions. One run of it is a 90/10 coin; one tail lands a
     permanent red. See ``flaky-cells.json``.

This is the READ-SIDE half (parent tooling): it reclassifies already-recorded
reds so the false ones surface for re-measurement, and it works retroactively on
the whole ledger history. The WRITE-SIDE half — hermit/validate.sh recording the
``-j`` width and concurrent-validate count at run time, splitting the DAG gate
into manifest-check vs lane-run, and enforcing "re-run solo at -j4 before writing
FAILURE" — is a separate hermit PR. Until then this module DERIVES concurrency
from overlapping ledger intervals and PARSES the failing cell names from the run
log, so it is useful before the producer changes land.

Every classification carries its OWN firing conditions in ``reasons`` — the
classifier refuses to launder a guess into a verdict, just like the records it
judges.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field

# A DAG node's own per-node verdict line, e.g.:
#   [test.command_strict_verify] ✗ FAIL   Portable command strict verification (113s, exit 101)
#   [test.command_strict_verify] test result: FAILED. 9 passed; 1 failed; ...
# Anchored on the `[test.<cell>]` prefix the safe-ci-dag-runner writes itself, and
# an UPPERCASE FAIL/FAILED token so a lowercase "0 failed" count never matches.
_NODE_PREFIX_RE = re.compile(r"^\[test\.(?P<cell>[^\]]+)\]\s+(?P<rest>.*)$")
_FAIL_TOKEN_RE = re.compile(r"(✗\s*FAIL\b|\bFAILED\b)")

FLAKY_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "flaky-cells.json")

# Contention rule threshold: the measured false red (#1592) was at -j16. -j4 solo
# passed, so widths at or below this are treated as low-contention even when other
# validates overlap. Kept as a named constant, not a magic literal.
CONTENTION_SAFE_JOBS = 4
FULL_GATES_EXPECTED = 5


def gate_counts(record: dict) -> tuple[int | None, int | None]:
    """Return (ran, expected), including the legacy full-profile fallback."""
    ran = record.get("gates_run", record.get("checks"))
    expected = record.get("gates_expected")
    # Schema-3 introduced the current anchored producer shape, whose full
    # profile has five gates. Do not apply that count to reconstructed schema-1
    # history: older complete full runs legitimately had four gates.
    if (
        expected is None
        and record.get("profile") == "full"
        and isinstance(record.get("schema_version"), int)
        and record["schema_version"] >= 3
    ):
        expected = FULL_GATES_EXPECTED
    return (
        ran if isinstance(ran, int) else None,
        expected if isinstance(expected, int) else None,
    )


def is_truncated(record: dict) -> bool:
    """True when this row did not complete its declared validation gate set."""
    if record.get("result") == "truncated":
        return True
    if record.get("exit_code") == 130:
        return True
    ran, expected = gate_counts(record)
    return expected is not None and expected > 0 and ran is not None and ran < expected


def effective_result(record: dict) -> object:
    """Result for analytics: incomplete rows are never product failures."""
    return "truncated" if is_truncated(record) else record.get("result")


def load_registry(path: str = FLAKY_REGISTRY_PATH) -> dict[str, dict]:
    """Return {cell_name: entry}. Missing/invalid file -> empty registry."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for entry in data.get("cells", []):
        if isinstance(entry, dict) and entry.get("cell"):
            out[str(entry["cell"])] = entry
    return out


def parse_failing_cells(log_path: str | None) -> list[str]:
    """Failing DAG-node cell names parsed from a run log, deduped in first-seen
    order. Empty when the log is absent or the failure was not a per-cell DAG
    node (a direct gate) — in which case the caller cannot attribute it to a
    known-flaky cell and must not treat it as flake."""
    if not log_path or not os.path.exists(log_path):
        return []
    seen: dict[str, None] = {}
    try:
        with open(log_path, errors="replace") as fh:
            for ln in fh:
                m = _NODE_PREFIX_RE.match(ln.rstrip("\n"))
                if not m:
                    continue
                if _FAIL_TOKEN_RE.search(m.group("rest")):
                    seen.setdefault(m.group("cell").strip(), None)
    except OSError:
        return []
    return list(seen)


def _epoch(ts: object) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def _interval(record: dict) -> tuple[float, float] | None:
    """[start, finish] epoch seconds for a record. Falls back to
    finish - real_seconds when started_at is absent (reconstructed rows)."""
    finish = _epoch(record.get("finished_at"))
    if finish is None:
        return None
    start = _epoch(record.get("started_at"))
    if start is None:
        secs = record.get("real_seconds")
        start = finish - secs if isinstance(secs, (int, float)) and secs >= 0 else finish
    return (min(start, finish), max(start, finish))


def derive_concurrent_validates(record: dict, all_records: list[dict]) -> int:
    """Count OTHER validate runs whose wall interval overlaps this run's.

    Contention is invisible in a single run's own output, and pre-schema-4 rows
    do not record it — so we recover it from the ledger's own timeline. Two runs
    overlap iff a.start < b.finish and b.start < a.finish. Identity is by
    log_file (unique per run) with an interval fallback so a record without a
    log_file is not treated as overlapping every anonymous sibling."""
    mine = _interval(record)
    if mine is None:
        return 0
    my_lf = record.get("log_file")
    count = 0
    for other in all_records:
        if other is record:
            continue
        if my_lf and other.get("log_file") == my_lf:
            continue
        oiv = _interval(other)
        if oiv is None:
            continue
        if mine[0] < oiv[1] and oiv[0] < mine[1]:
            count += 1
    return count


@dataclass
class FlakeAnalysis:
    verdict: str  # "defect" | "needs-rerun" | "truncated" | "n/a"
    reasons: list[str] = field(default_factory=list)
    failing_cells: list[str] = field(default_factory=list)
    flaky_failing_cells: list[str] = field(default_factory=list)
    concurrent_validates: int = 0
    jobs: object = None  # int width the run executed at, or None if unrecorded

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "failing_cells": self.failing_cells,
            "flaky_failing_cells": self.flaky_failing_cells,
            "concurrent_validates": self.concurrent_validates,
            "jobs": self.jobs,
        }


def classify(
    record: dict,
    all_records: list[dict],
    registry: dict[str, dict],
) -> FlakeAnalysis:
    """Classify one record's red as defect vs needs-rerun.

    NEVER mutates ``record``. Only ``fail``/``timeout`` results are judged;
    anything else returns verdict ``n/a``. A red is ``needs-rerun`` iff:
      (a) it has attributed failing cells and EVERY one is in the flaky
          registry (a mixed defect+flake failure stays ``defect`` — a real cell
          also broke), OR
      (b) no width is recorded (or width > CONTENTION_SAFE_JOBS) AND at least one
          other validate overlapped it in time (contention-suspect).
    Otherwise ``defect``. Each firing signal is named in ``reasons``."""
    if is_truncated(record):
        ran, expected = gate_counts(record)
        return FlakeAnalysis(
            verdict="truncated",
            reasons=[f"truncated: completed {ran}/{expected} required gates"],
        )

    result = record.get("result")
    if result not in ("fail", "timeout"):
        return FlakeAnalysis(verdict="n/a")

    jobs = record.get("jobs")  # schema-4 producer field; None until it lands
    failing = parse_failing_cells(record.get("log_file"))
    flaky_hits = [c for c in failing if c in registry]
    concurrent = derive_concurrent_validates(record, all_records)

    reasons: list[str] = []
    verdict = "defect"

    if failing and flaky_hits and set(failing) == set(flaky_hits):
        verdict = "needs-rerun"
        for c in flaky_hits:
            e = registry.get(c, {})
            reasons.append(
                "known-flaky-cell: {} (measured {}/{}) — single FAILED is a "
                "weighted coin, re-run solo".format(
                    c, e.get("observed_pass", "?"), e.get("sample_size", "?")))
    else:
        width_unknown = not isinstance(jobs, int)
        contended = concurrent > 0 and (width_unknown or jobs > CONTENTION_SAFE_JOBS)
        if contended:
            verdict = "needs-rerun"
            reasons.append(
                "contended: {} concurrent validate(s) overlapped; width {} — "
                "cargo cache/build-dir contention produced a false red before "
                "(#1592 -j16 fail vs -j4 pass), re-run solo at -j{}".format(
                    concurrent,
                    "unrecorded" if width_unknown else "-j%d" % jobs,
                    CONTENTION_SAFE_JOBS))
        if flaky_hits and verdict == "defect":
            # Mixed: a flaky cell AND a non-flaky cell failed -> real defect, but
            # surface the flaky component so a re-run separates the two.
            reasons.append(
                "defect: non-flaky cell(s) also failed ({}); flaky component: "
                "{}".format(
                    ", ".join(c for c in failing if c not in registry),
                    ", ".join(flaky_hits)))
    if verdict == "defect" and not reasons:
        reasons.append(
            "defect: no known-flaky failing cell and no overlapping validate"
            + (" (width -j%d)" % jobs if isinstance(jobs, int) else ""))

    return FlakeAnalysis(
        verdict=verdict,
        reasons=reasons,
        failing_cells=failing,
        flaky_failing_cells=flaky_hits,
        concurrent_validates=concurrent,
        jobs=jobs,
    )


def annotate(all_records: list[dict], registry: dict[str, dict] | None = None) -> None:
    """Attach a ``flake_analysis`` dict to every red record in place (additive;
    ``result`` is never changed)."""
    reg = registry if registry is not None else load_registry()
    for r in all_records:
        fa = classify(r, all_records, reg)
        if fa.verdict != "n/a":
            r["flake_analysis"] = fa.to_dict()
