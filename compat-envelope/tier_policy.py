#!/usr/bin/env python3
"""Tier reading policy: blank means UNKNOWN, and nothing may default to a pass.

WHY THIS EXISTS. `tier` records which comparison a row's verdict was earned by.
It was added to the schema contract but never to the data: at 2026-08-07 it is
present in 1 of 4 committed scorecards and empty in 100% of rows. So every
consumer that reads it is reading a blank, and the only question that matters is
what a blank turns into.

THE DEFECT THIS PREVENTS. If a blank tier defaults to any passing value, every
unmeasured cell silently claims a standard no run ever held it to -- and there
are currently 2284 such rows. That is the fake-green default in its purest form:
the claim is manufactured by the reader, not by the producer.

THE RULE, deliberately stronger than "never default to a *passing* tier":
**defaulting may only ever produce UNKNOWN.** Enumerating which tiers count as
passing would put the guard one vocabulary change behind the producers -- a new
tier name would be admitted as a default before anyone noticed. Refusing every
non-unknown default has no such gap and needs no vocabulary.

This module does not populate `tier`. Back-filling it from a neighbouring column
would manufacture measurements no run made, which is the same error stated in
`migrate-scorecard-schema.py`: "Blank means unmeasured." Populating tier is a
PRODUCER change; this is the consumer-side contract that keeps the blanks honest.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

#: The only value a missing/blank tier may read as.
UNKNOWN = "unknown"

#: Values that mean "no tier was recorded". `None` covers a column that does not
#: exist at all, which is the case for 3 of the 4 scorecards.
_BLANKS = ("", "-", "n/a", "none", "null")


class TierDefaultRefused(ValueError):
    """A caller tried to default an unrecorded tier to something other than UNKNOWN."""


class RaggedRow(ValueError):
    """A row's field count disagrees with the header. Refuse; do not silently realign."""


def read_tier(row: dict, *, default: str = UNKNOWN) -> str:
    """Return the row's recorded tier, or UNKNOWN when none was recorded.

    `default` exists only so a caller can state its intent explicitly; supplying
    anything other than UNKNOWN is refused rather than honoured. Note the guard
    fires on the *argument*, not on whether the row happens to be blank -- an
    unsafe default is a bug at the call site whether or not this particular row
    would have hit it.
    """
    if default != UNKNOWN:
        raise TierDefaultRefused(
            f"refusing to default an unrecorded tier to {default!r}; "
            f"the only permitted default is {UNKNOWN!r}. A blank tier means the run "
            f"recorded no comparison, so any other default asserts a standard no run met."
        )
    value = row.get("tier")
    if value is None:
        return UNKNOWN
    value = value.strip()
    # Case-fold only for the blank test; a real tier is returned exactly as the
    # producer wrote it, so nothing downstream sees a normalised spelling.
    return UNKNOWN if value.lower() in _BLANKS else value


def is_recorded(row: dict) -> bool:
    """True only when the producer actually wrote a tier for this row."""
    return read_tier(row) != UNKNOWN


@dataclass
class RowCheck:
    line: int
    fields: int


def iter_rows_strict(path: Path) -> Iterator[dict]:
    """Yield rows, refusing any whose field count disagrees with the header.

    `csv.DictReader` silently absorbs extra fields under a `None` key and pads
    short rows with `None`, so a ragged file parses "successfully" while every
    column past the break is misaligned. That is how three rows of `reason`
    free-text came to look like recorded tier values. Refuse instead.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        width = len(header)
        for lineno, fields in enumerate(reader, start=2):
            if not fields:
                continue
            if len(fields) != width:
                raise RaggedRow(
                    f"{path}:{lineno}: {len(fields)} fields against a {width}-field header. "
                    f"Almost always an unquoted comma in a free-text column such as `reason`; "
                    f"realigning it would misattribute every later column."
                )
            yield dict(zip(header, fields))


def survey(path: Path) -> tuple[int, int, int]:
    """(rows, rows_with_a_recorded_tier, ragged_rows) -- counts with their denominator."""
    rows = recorded = ragged = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        width = len(header)
        for fields in reader:
            if not fields:
                continue
            rows += 1
            if len(fields) != width:
                ragged += 1
                continue
            if is_recorded(dict(zip(header, fields))):
                recorded += 1
    return rows, recorded, ragged


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", nargs="+", type=Path)
    args = parser.parse_args(argv)
    total = rec = rag = 0
    for path in args.csv:
        r, c, g = survey(path)
        total, rec, rag = total + r, rec + c, rag + g
        print(f"{path}: rows={r} tier_recorded={c}/{r} ragged={g}")
    print(f"TOTAL: rows={total} tier_recorded={rec}/{total} ragged={rag}")
    # Ragged rows are a hard failure: they make every downstream column suspect.
    return 1 if rag else 0


if __name__ == "__main__":
    raise SystemExit(main())
