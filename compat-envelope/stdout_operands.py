#!/usr/bin/env python3
"""A stdout parity verdict must be RE-DERIVABLE from the row that records it.

WHAT WAS WRONG. `stdout_parity` is a boolean with no operands beside it. Measured
2026-08-08 across the four published scorecards at HEAD:

    file                        rows   output_hash   ref_output_hash   stdout_parity
    e9patch-scorecard.csv        454     454/454          0/454           0/454
    fullcorpus-scorecard.csv    1200    1200/1200         0/1200          0/1200
    reverie-scorecard.csv         12      12/12           0/12         column absent
    scorecard.csv                624     402/624          0/624           0/624

The CANDIDATE operand is recorded 2068 times. The REFERENCE operand is recorded
ZERO times, in every file, though the column exists in all four. A parity needs
two operands, so no reader can tell parity-HELD from parity-DIFFERED from
NEVER-ATTEMPTED. The envelope-wide verdict is HELD 0 / DIFFERED 0 / UNMEASURED
2290 of 2290: stdout parity has never been measured in the published data.

THE THREE STATES, and why collapsing any two is the defect:

    HELD        both operands present and equal
    DIFFERED    both operands present and unequal
    UNMEASURED  either operand absent -- NOT a zero, NOT a pass

A blank that reads as "no problem" rather than "no measurement" is the same shape
as the `0/463` cross-backend figure that means *never measured at this standard*
rather than *measured and failed*.

WHAT THIS REFUSES, AND WHY IT IS GREEN TODAY. Today zero rows assert a parity
boolean without operands, and zero rows contradict their operands, so this gate
passes on the current data. It is not inert: it exists to keep that true. The
tempting "fix" for an empty column is to write a boolean into it, and that would
convert a visible gap into an invisible false record -- strictly worse than the
gap. So:

    * a row asserting `stdout_parity` with either operand missing  -> REFUSED
    * a row whose `stdout_parity` contradicts its own operands      -> REFUSED
    * a row with no operands and no assertion                       -> UNMEASURED,
      counted and printed, never coerced to 0 or to pass

BACKFILLING IS NOT AVAILABLE HERE BY CONSTRUCTION. This module only ever reads.
Historical rows without operands stay unmeasured; the only way to move one out of
UNMEASURED is for a producer to record the two hashes it actually compared.

Exit codes:
  0  no row asserts a parity its operands cannot support
  1  at least one does
  2  the population could not be enumerated
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The two SHA-256 operands. `output_hash` is the candidate, `ref_output_hash` the
#: reference. Named here so a schema rename is one edit and so their absence is
#: reported as a schema fault rather than silently read as a blank.
CANDIDATE, REFERENCE = "output_hash", "ref_output_hash"

#: Historical spelling. The README records that scorecards written before the
#: rename use the ambiguous `parity`; both are read so an old file is classified
#: rather than skipped.
PARITY_COLUMNS = ("stdout_parity", "parity")

HELD, DIFFERED, UNMEASURED = "HELD", "DIFFERED", "UNMEASURED"

_BLANKS = {"", "-", "n/a", "none", "null"}


class PopulationError(RuntimeError):
    """The set of scorecards to classify could not be enumerated."""


def _cell(row: dict, column: str) -> str:
    value = (row.get(column) or "").strip()
    return "" if value.lower() in _BLANKS else value


def parity_assertion(row: dict) -> str:
    """The recorded boolean, or "" if the row asserts nothing."""
    for column in PARITY_COLUMNS:
        value = _cell(row, column)
        if value in ("0", "1"):
            return value
    return ""


def classify(row: dict) -> tuple[str, str]:
    """(state, detail). The verdict re-derived from the row's own operands.

    Deliberately ignores the recorded boolean: the point is to answer the question
    FROM THE EVIDENCE, so that the boolean can then be checked against it. A
    classifier that consulted the boolean would be the label validating itself.
    """
    candidate, reference = _cell(row, CANDIDATE), _cell(row, REFERENCE)
    if candidate and reference:
        return (HELD, "") if candidate == reference else (
            DIFFERED, f"{CANDIDATE}={candidate[:19]}… != {REFERENCE}={reference[:19]}…")
    missing = [name for name, value in ((CANDIDATE, candidate), (REFERENCE, reference))
               if not value]
    return UNMEASURED, "no " + " and no ".join(missing)


@dataclass
class Violation:
    file: str
    line: int
    test_id: str
    backend: str
    reason: str

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.backend}/{self.test_id}: {self.reason}"


@dataclass
class Report:
    root: str
    files: list[str] = field(default_factory=list)
    rows: int = 0
    states: dict = field(default_factory=lambda: {HELD: 0, DIFFERED: 0, UNMEASURED: 0})
    #: Which operand is missing, for the UNMEASURED rows. This is the actionable
    #: half: "reference absent" names a producer to fix, "both absent" does not.
    missing: dict = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)

    def render(self) -> str:
        out = ["stdout-operand check (is the parity verdict re-derivable from the row?)",
               f"  resolved root : {self.root}",
               f"  population    : {len(self.files)} scorecard(s) enumerated"]
        out += [f"      {f}" for f in self.files]
        out.append("")
        out.append(f"  rows classified : {self.rows}")
        for state in (HELD, DIFFERED, UNMEASURED):
            out.append(f"      {state:11s} : {self.states[state]}")
        for reason, count in sorted(self.missing.items()):
            out.append(f"          {reason}: {count}")
        out.append(f"  unsupported parity assertions : {len(self.violations)}")
        out.append("  UNMEASURED is not zero and not a pass; it is counted here and "
                   "never coerced.")
        return "\n".join(out)


def check(root: Path) -> Report:
    found = sorted(root.glob("*scorecard*.csv"))
    if not found:
        raise PopulationError(f"no *scorecard*.csv under {root}")
    report = Report(root=str(root), files=[p.name for p in found])
    for path in found:
        with path.open(newline="", encoding="utf-8") as handle:
            for line, row in enumerate(csv.DictReader(handle), start=2):
                report.rows += 1
                state, detail = classify(row)
                report.states[state] += 1
                if state == UNMEASURED:
                    report.missing[detail] = report.missing.get(detail, 0) + 1
                asserted = parity_assertion(row)
                if not asserted:
                    continue
                reason = ""
                if state == UNMEASURED:
                    reason = (f"asserts stdout_parity={asserted} but {detail} -- a "
                              f"verdict with no operands cannot be re-derived or refuted")
                else:
                    expected = "1" if state == HELD else "0"
                    if asserted != expected:
                        reason = (f"asserts stdout_parity={asserted} but its operands "
                                  f"say {state} (expected {expected})")
                if reason:
                    report.violations.append(Violation(
                        path.name, line, row.get("test_id", "?"),
                        row.get("backend", "?"), reason))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=HERE)
    a = ap.parse_args(argv)
    try:
        report = check(a.root)
    except PopulationError as error:
        print(f"stdout-operands: REFUSED: {error}", file=sys.stderr)
        return 2
    print(report.render())
    if report.violations:
        print(f"\nREFUSED: {len(report.violations)} parity assertion(s) their own row "
              f"cannot support:", file=sys.stderr)
        for violation in report.violations[:20]:
            print(f"  {violation.render()}", file=sys.stderr)
        if len(report.violations) > 20:
            print(f"  ... {len(report.violations) - 20} more", file=sys.stderr)
        print("Record the two hashes that were compared, or record nothing. Writing a "
              "boolean into an empty column turns a visible gap into a false record.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
