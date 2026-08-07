#!/usr/bin/env python3
"""The `dimension` column: where stack/heap/detlog cells live, without inventing one.

WHY THIS EXISTS. The scorecards are keyed by `test_id` x `backend` and carry no
memory-dimension column, so a stack cell, a heap cell and a detlog cell for the
same test and backend have nowhere to live as distinct rows. Searching every
committed scorecard at 2026-08-07 for a stack-related `test_id` returns exactly
two rows, and both are `c-programs/map-shadow-stack-enosys` -- a shadow-stack
SYSCALL check, not the stack-hash dimension. So the dimension work has had no
column to write to at all.

THE SAME TRAP AS `tier`, AND THE SAME ANSWER. Widening the file is safe; filling
it in is not. No existing row states which dimension it measured, so any
back-fill would assert a measurement the run never made. Historical rows get the
column EMPTY, which reads as UNSPECIFIED -- true of every row written before
producers learned to record it. Defaulting a blank to a concrete dimension is
refused, for the same reason a blank tier may not default to a pass: the claim
would be manufactured by the reader.

A SECOND GUARD THAT `tier` DOES NOT NEED. A dimension has a closed vocabulary,
so a typo is detectable: `stak` is not a new dimension, it is a broken row. A
recorded value outside the vocabulary is refused rather than passed through,
which stops a misspelling from quietly becoming a fifth dimension that no
consumer aggregates.
"""

from __future__ import annotations

import csv
import fcntl
import shutil
from pathlib import Path

#: The only value a missing/blank dimension may read as.
UNSPECIFIED = "unspecified"

#: Closed vocabulary. These are the four dimensions the self-determinism matrix
#: scores; anything else in the column is a defect, not a new dimension.
VALID_DIMENSIONS = ("stdout", "detlog", "stack", "heap")

_BLANKS = ("", "-", "n/a", "none", "null", UNSPECIFIED)

COLUMN = "dimension"


class DimensionDefaultRefused(ValueError):
    """A caller tried to default an unrecorded dimension to a concrete one."""


class UnknownDimension(ValueError):
    """A recorded dimension is outside the closed vocabulary. Refuse; do not invent."""


def read_dimension(row: dict, *, default: str = UNSPECIFIED) -> str:
    """Return the row's recorded dimension, or UNSPECIFIED when none was recorded.

    Supplying any other `default` is refused: a row that did not say which
    dimension it measured cannot be made to claim one by its reader.
    """
    if default != UNSPECIFIED:
        raise DimensionDefaultRefused(
            f"refusing to default an unrecorded dimension to {default!r}; the only "
            f"permitted default is {UNSPECIFIED!r}. A blank dimension means the run did "
            f"not record which dimension it measured, so any other default asserts one."
        )
    value = row.get(COLUMN)
    if value is None:
        return UNSPECIFIED
    value = value.strip()
    if value.lower() in _BLANKS:
        return UNSPECIFIED
    if value not in VALID_DIMENSIONS:
        raise UnknownDimension(
            f"{value!r} is not one of {VALID_DIMENSIONS}. A misspelling must not become "
            f"a fifth dimension that no consumer aggregates."
        )
    return value


def is_recorded(row: dict) -> bool:
    """True only when the producer wrote a dimension for this row."""
    return read_dimension(row) != UNSPECIFIED


def widen(path: Path, *, backup: bool = True) -> tuple[bool, int]:
    """Add an EMPTY `dimension` column. Returns (changed, rows_touched).

    Idempotent, fail-closed on an unreadable/headerless file, and takes the same
    `flock` the producers take so a concurrent append cannot interleave. Fills
    nothing: every existing row gets a blank, because none of them recorded a
    dimension.
    """
    with path.open("r+", newline="", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        rows = list(csv.reader(handle))
        if not rows or not rows[0]:
            raise ValueError(f"{path}: no header; refusing to widen a file I cannot recognise")
        header = rows[0]
        if COLUMN in header:
            return False, 0
        width = len(header)
        ragged = [i for i, r in enumerate(rows[1:], 2) if r and len(r) != width]
        if ragged:
            raise ValueError(
                f"{path}: rows {ragged[:5]} disagree with the {width}-field header; "
                f"refusing to widen a file whose columns are already misaligned"
            )
        if backup:
            shutil.copyfile(path, path.with_suffix(path.suffix + ".pre-dimension-migration"))
        out = [header + [COLUMN]] + [r + [""] for r in rows[1:] if r]
        handle.seek(0)
        handle.truncate()
        csv.writer(handle, lineterminator="\n").writerows(out)
        return True, len(out) - 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", nargs="+", type=Path)
    parser.add_argument("--widen", action="store_true", help="add the empty column in place")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    for path in args.csv:
        if args.widen:
            changed, n = widen(path, backup=not args.no_backup)
            print(f"{path}: {'widened' if changed else 'already has the column'} rows={n}")
        else:
            with path.open(newline="", encoding="utf-8") as h:
                header = next(csv.reader(h), [])
            print(f"{path}: dimension_column={'yes' if COLUMN in header else 'NO'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
