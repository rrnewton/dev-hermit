#!/usr/bin/env python3
"""Typed disposition for the HEAP dimension, so a constant stops counting as a measurement.

THE DEFECT, MEASURED (ptrace --strict --detlog-heap, 60 corpus rows attempted,
51 measured, 9 build-fail):

    heap == 0      3 / 51    5.9%
    heap == 2     34 / 51   66.7%
    heap  > 2     14 / 51   27.5%

and, the invariant that actually matters:

    the FIRST heap record is the SAME hash -- 74518f204d46de66 -- on
    *** 48 of 48 *** cells that have any heap record at all. 100%.

That record is the initial heap image, identical across guests. It is 16.6% of
all 290 heap records in the sample, and on the 34 cells sitting at heap==2 it is
*** half the dimension ***: one constant plus one guest record. A parity
comparison over such a cell is 50% pre-determined before the guest runs.

WHAT `2` IS NOT. It is not a libc-startup floor. Controls:
    noalloc  (full libc, zero mallocs)  -> heap = 0
    nolibc   (freestanding, no libc)    -> heap = 0
So libc starting up allocates nothing observable here; a cell at 2 really did
allocate once. `2` is a THIN measurement, not an artifact -- which is exactly why
it is harder to spot than a 0. A zero invites suspicion; a 2 looks like data.

THE RULE THIS MODULE ENFORCES. Report the GUEST-VARYING record count, not the raw
count, and type the absence rather than emitting a number that overstates it:

    no-heap-activity        no records at all            -> NO-RESULT
    heap-constant-only      records, but ALL are the      -> NO-RESULT
                            universal constant
    exercised               >= 1 guest-varying record     -> that count

`exercised` deliberately does NOT impose a minimum. A cell with one genuine
allocation measured one genuine allocation; inventing a threshold would replace a
constant with an arbitrary constant. What changes is that the universal record
never counts toward it.
"""

from __future__ import annotations

import re

#: The initial-heap-image hash. Measured identical on 48/48 cells that have any
#: heap record, including the two heaviest (36 and 43 records). Kept as a set so a
#: second such constant can be added with evidence rather than by widening a regex.
UNIVERSAL_HEAP_RECORDS = frozenset({
    "74518f204d46de660dff3ed003e92476bad8c691",
})

HEAP_RE = re.compile(r"\[heap\]->([0-9a-f]+)")

NO_RESULT = None


def heap_hashes(detlog_text: str) -> list[str]:
    """Every [heap] record hash, in emission order."""
    return HEAP_RE.findall(detlog_text or "")


def classify(hashes: list[str]) -> tuple[str, int | None]:
    """(disposition, guest_varying_count). Count is None for a NO-RESULT."""
    if not hashes:
        return "no-heap-activity", NO_RESULT
    varying = [h for h in hashes if h not in UNIVERSAL_HEAP_RECORDS]
    if not varying:
        return "heap-constant-only", NO_RESULT
    return "exercised", len(varying)


def disposition(detlog_text: str) -> dict:
    """The typed cell field. `heap_records_raw` is retained beside the corrected
    count so the correction is auditable rather than a silent overwrite."""
    hs = heap_hashes(detlog_text)
    kind, n = classify(hs)
    return {
        "heap_disposition": kind,
        "heap_guest_records": n,          # None == NO-RESULT, never 0-as-a-value
        "heap_records_raw": len(hs),
        "heap_constant_records": sum(1 for h in hs if h in UNIVERSAL_HEAP_RECORDS),
    }


def is_measurement(d: dict) -> bool:
    """Whether this cell may contribute to a heap-parity figure at all."""
    return d.get("heap_guest_records") is not None
