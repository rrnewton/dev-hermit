#!/usr/bin/env python3
"""Standing self-determinism precondition for cross-backend parity emission.

A parity figure is a comparison between two sides. If either side does not
reproduce *itself* across two runs, the comparison is meaningless and any
percentage computed from it is noise wearing a number. This module makes that
precondition a gate that runs at emission time, rather than a sweep that was
true once.

The gate is keyed on the exact triple that parity is computed for --
``(guest, backend, dimension)`` -- because self-determinism is not a property of
a backend alone. Measured counterexample at Hermit 590fcc9e: KVM stack is
16/16 self-deterministic on a static guest that never executes RDTSC and
10/31 on ``/bin/true``. A per-backend verdict would have to pick one and would
be wrong about the other.

Three verdicts, and the distinction is load-bearing:

``PASS``            both runs agree on every ordinal; parity may be emitted.
``NOT_COMPARABLE``  the two runs disagree, or produced zero records. Emit the
                    literal string NOT-COMPARABLE plus the reason -- never a
                    percentage.
``UNMEASURED``      no record exists for this triple. This is *also* refused.
                    Defaulting an unmeasured cell to "permit" is how a gate
                    that exists stops covering the growing set.

Usage as a library::

    ledger = Ledger.from_rows(rows)
    decision = ledger.parity_decision("threaded", "heap", ("ptrace", "kvm"))
    if decision.emittable:
        emit(percentage)
    else:
        emit_not_comparable(decision.render())

Usage as a CLI::

    self_determinism_gate.py --ledger self-determinism.tsv --report
    self_determinism_gate.py --ledger self-determinism.tsv \
        --check guest=threaded dimension=heap backends=ptrace,kvm
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

PASS = "PASS"
NOT_COMPARABLE = "NOT_COMPARABLE"
UNMEASURED = "UNMEASURED"

#: Emitting a parity figure is permitted only for this verdict.
EMITTABLE = frozenset({PASS})

REQUIRED_FIELDS = (
    "guest",
    "backend",
    "dimension",
    "ordinal_matches",
    "denominator",
)


class LedgerError(ValueError):
    """The self-determinism ledger is malformed and cannot be trusted."""


@dataclass(frozen=True)
class Cell:
    """One measured ``(guest, backend, dimension)`` self-determinism result."""

    guest: str
    backend: str
    dimension: str
    ordinal_matches: int
    denominator: int
    #: Whether the two runs that produced these counts both exited cleanly.
    #: Agreement between two *failed* runs is not self-determinism. Measured
    #: case at 590fcc9e: fork_exec_pipeline/kvm/heap agrees 518/518 -- and both
    #: runs were killed (rc=137). Scoring that PASS would emit a parity figure
    #: from a guest that never finished.
    runs_ok: bool = True

    @property
    def verdict(self) -> str:
        if not self.runs_ok:
            return NOT_COMPARABLE
        # A zero denominator is NOT agreement. Two runs that both emitted
        # nothing agree vacuously, and scoring that as PASS is precisely the
        # "0/0 read as fine" failure this gate exists to prevent.
        if self.denominator == 0:
            return NOT_COMPARABLE
        if self.ordinal_matches == self.denominator:
            return PASS
        return NOT_COMPARABLE

    @property
    def reason(self) -> str:
        if not self.runs_ok:
            return (
                f"run failure ({self.backend} did not complete cleanly; "
                f"its {self.ordinal_matches}/{self.denominator} agreement is between failed runs)"
            )
        if self.denominator == 0:
            return f"vacuous n=0 ({self.backend} emitted no {self.dimension} records)"
        if self.ordinal_matches == self.denominator:
            return f"self-deterministic {self.ordinal_matches}/{self.denominator}"
        return (
            f"{self.backend} {self.dimension} is not self-deterministic: "
            f"{self.ordinal_matches}/{self.denominator} ordinals match between its own two runs"
        )

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.guest, self.backend, self.dimension)


@dataclass(frozen=True)
class Decision:
    """The gate's answer for one parity emission request."""

    guest: str
    dimension: str
    verdict: str
    reasons: tuple[str, ...]

    @property
    def emittable(self) -> bool:
        return self.verdict in EMITTABLE

    def render(self) -> str:
        """The literal cell text. Never a percentage unless emittable."""
        if self.emittable:
            return "; ".join(self.reasons)
        return f"NOT-COMPARABLE [{self.verdict}] {self.guest}/{self.dimension}: " + "; ".join(
            self.reasons
        )


class Ledger:
    """Self-determinism records, keyed by ``(guest, backend, dimension)``."""

    def __init__(self, cells: dict[tuple[str, str, str], Cell]) -> None:
        self._cells = cells

    # -- construction ---------------------------------------------------

    @classmethod
    def from_rows(cls, rows) -> "Ledger":
        cells: dict[tuple[str, str, str], Cell] = {}
        for index, row in enumerate(rows):
            missing = [f for f in REQUIRED_FIELDS if f not in row or row[f] in (None, "")]
            if missing:
                raise LedgerError(f"row {index}: missing required field(s) {','.join(missing)}")
            try:
                matches = int(row["ordinal_matches"])
                denominator = int(row["denominator"])
            except (TypeError, ValueError) as error:
                raise LedgerError(f"row {index}: non-integer counts: {error}") from error
            if matches < 0 or denominator < 0:
                raise LedgerError(f"row {index}: negative counts are not a measurement")
            if matches > denominator:
                raise LedgerError(
                    f"row {index}: {matches} matches exceeds denominator {denominator}"
                )
            runs_ok = True
            if "runs_ok" in row and row["runs_ok"] not in (None, ""):
                runs_ok = str(row["runs_ok"]).strip().lower() in ("1", "true", "yes", "ok")
            elif "status" in row and row["status"] not in (None, ""):
                # A status the producer emitted for a run that did not complete
                # cleanly. Unknown statuses fail closed.
                runs_ok = str(row["status"]).strip().upper() in (
                    "PASS",
                    "FAIL_SELF_NONDETERMINISM",
                    "REFUSED_ZERO_RESULT",
                )
            cell = Cell(
                guest=row["guest"].strip(),
                backend=row["backend"].strip(),
                dimension=row["dimension"].strip(),
                ordinal_matches=matches,
                denominator=denominator,
                runs_ok=runs_ok,
            )
            if cell.key in cells and cells[cell.key] != cell:
                raise LedgerError(
                    f"row {index}: conflicting duplicate record for {cell.key}; "
                    "a triple must have exactly one verdict"
                )
            cells[cell.key] = cell
        return cls(cells)

    @classmethod
    def from_path(cls, path: Path) -> "Ledger":
        text = path.read_text()
        delimiter = "\t" if "\t" in text.splitlines()[0] else ","
        return cls.from_rows(list(csv.DictReader(text.splitlines(), delimiter=delimiter)))

    # -- queries --------------------------------------------------------

    def lookup(self, guest: str, backend: str, dimension: str) -> Cell | None:
        return self._cells.get((guest, backend, dimension))

    def parity_decision(self, guest: str, dimension: str, backends) -> Decision:
        """Decide whether a parity figure may be emitted for this comparison.

        Every backend participating in the comparison must have a PASS record
        for this exact guest and dimension. A single non-PASS side refuses the
        whole emission -- parity has no meaning if any side is unstable.
        """
        backends = tuple(backends)
        if len(backends) < 2:
            raise ValueError("a parity comparison needs at least two backends")
        reasons: list[str] = []
        verdict = PASS
        for backend in backends:
            cell = self.lookup(guest, backend, dimension)
            if cell is None:
                # Refused, not permitted. An unmeasured cell is the growing-set
                # hole; letting it default to emittable defeats the gate.
                verdict = UNMEASURED if verdict == PASS else verdict
                reasons.append(
                    f"{backend}: UNMEASURED (no self-determinism record for "
                    f"{guest}/{backend}/{dimension})"
                )
                continue
            if cell.verdict != PASS:
                verdict = NOT_COMPARABLE
            reasons.append(f"{backend}: {cell.reason}")
        # NOT_COMPARABLE outranks UNMEASURED: a measured failure is a stronger
        # statement than an absent record, and should be the reported reason.
        if any(
            "not self-deterministic" in r or "vacuous n=0" in r or "run failure" in r
            for r in reasons
        ):
            verdict = NOT_COMPARABLE
        return Decision(guest=guest, dimension=dimension, verdict=verdict, reasons=tuple(reasons))

    # -- coverage -------------------------------------------------------

    def coverage(self) -> dict[str, int]:
        """Denominators the gate can state about itself."""
        counts = {PASS: 0, NOT_COMPARABLE: 0}
        for cell in self._cells.values():
            counts[cell.verdict] += 1
        return {
            "cells_recorded": len(self._cells),
            "cells_pass": counts[PASS],
            "cells_not_comparable": counts[NOT_COMPARABLE],
            "guests": len({k[0] for k in self._cells}),
            "backends": len({k[1] for k in self._cells}),
            "dimensions": len({k[2] for k in self._cells}),
        }

    def coverage_report(self) -> str:
        c = self.coverage()
        expected = c["guests"] * c["backends"] * c["dimensions"]
        lines = [
            "self-determinism gate coverage",
            f"  cells recorded    : {c['cells_recorded']}",
            f"  cells PASS        : {c['cells_pass']}",
            f"  NOT-COMPARABLE    : {c['cells_not_comparable']}",
            f"  guests x backends x dimensions = {c['guests']} x {c['backends']} x "
            f"{c['dimensions']} = {expected}",
            f"  UNMEASURED (gap)  : {expected - c['cells_recorded']}"
            "   <- refused at emission time, not permitted",
        ]
        return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--report", action="store_true", help="print coverage denominators")
    parser.add_argument(
        "--check",
        nargs="*",
        default=None,
        metavar="K=V",
        help="guest=G dimension=D backends=B1,B2 -- exit 0 emittable, 3 refused",
    )
    args = parser.parse_args(argv)

    try:
        ledger = Ledger.from_path(args.ledger)
    except (LedgerError, OSError) as error:
        print(f"self-determinism-gate: unusable ledger: {error}", file=sys.stderr)
        return 2

    if args.report:
        print(ledger.coverage_report())

    if args.check is not None:
        fields = dict(item.split("=", 1) for item in args.check)
        decision = ledger.parity_decision(
            fields["guest"], fields["dimension"], fields["backends"].split(",")
        )
        print(decision.render())
        return 0 if decision.emittable else 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
