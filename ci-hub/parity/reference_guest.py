#!/usr/bin/env python3
"""Refuse, AT EMISSION, any parity cell measured on a guest that cannot exercise
its dimension -- and make every emitted cell say which guest produced it.

WHY THIS IS A GATE AND NOT A CONVENTION. Twice a dimension has been measured on
a guest that could not exercise it, producing an ambiguous zero that reads as
agreement. "Remember to use the right guest" is exactly the kind of rule that
holds until the night someone is drained, so the reference guest is pinned per
dimension and the emission path refuses anything else.

THE TRAP THIS EXISTS TO CLOSE: POPULATED OUTPUT IS NOT A VALID CONTROL.
`/bin/true` emits 31 [stack] hashes and had 0/31 cross-backend agreement. A
"did we get any records?" check passes it. Measured 2026-08-07, ALL FOUR
reference guests emit nonzero [heap] AND [stack] records, and `/bin/true` emits
MORE stack records (31) than the real stack guest (17) -- so record count does
not even correlate with exercise, let alone establish it. What separates them is
that the stack guest's [stack] mapping GROWS (0x21000 -> 0x209000, 2 distinct
ranges) while `/bin/true`'s never does (1 range, span 0).

So the predicate is IDENTITY + WITNESS, never volume:
  1. identity -- the guest's source sha256 IS the one pinned for this dimension;
  2. witness  -- that run printed the guest's own witness line, proving the
                 guest's code actually ran rather than dying in the loader;
  3. structure -- for `stack`, the mapping must additionally be observed to grow.

Fail-closed everywhere: an unknown dimension, an unpinned guest, a missing
witness, or an unreadable manifest REFUSES. There is no "assume fine" path,
because the failure this guards against is precisely a silent pass.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MANIFEST = Path(__file__).resolve().parent / "reference-guests.json"


class RefusedError(Exception):
    """Emission refused. Never caught-and-defaulted by the harness."""


@dataclass(frozen=True)
class Cell:
    """One emitted parity cell. The guest travels WITH the value, always.

    A cell that cannot name its guest is not a weaker cell, it is not a cell:
    the whole defect being closed is a number whose provenance was implicit.
    """

    dimension: str
    backend: str
    value: Any
    guest: str
    guest_sha256: str
    witness: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_row(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "backend": self.backend,
            "value": self.value,
            "guest": self.guest,
            "guest_sha256": self.guest_sha256,
            "witness": self.witness,
            "notes": list(self.notes),
        }


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load the pinned set. A malformed manifest raises -- never a silent {}."""
    p = path or MANIFEST
    try:
        data = json.loads(p.read_text())
    except OSError as e:
        raise RefusedError(f"{p}: cannot read reference-guest manifest: {e}")
    except ValueError as e:
        raise RefusedError(f"{p}: malformed reference-guest manifest: {e}")
    if not isinstance(data.get("dimensions"), dict) or not data["dimensions"]:
        raise RefusedError(f"{p}: manifest has no dimensions")
    return data


def reference_for(dimension: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """The pinned guest for one dimension, or REFUSE."""
    m = manifest or load_manifest()
    dims = m["dimensions"]
    if dimension not in dims:
        raise RefusedError(
            f"unknown dimension {dimension!r}; pinned dimensions are "
            f"{sorted(dims)}. Add a reference guest before measuring it."
        )
    return dims[dimension]


def source_sha256(path: str | Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as e:
        raise RefusedError(f"{path}: cannot hash guest source: {e}")


def stack_growth_ranges(detlog_text: str) -> set[tuple[int, int]]:
    """Distinct [stack] VMA ranges seen in an INFO detlog."""
    return {
        (int(a, 16), int(b, 16))
        for a, b in re.findall(r"(0x[0-9a-f]+)-(0x[0-9a-f]+)\s[^\n]*\[stack\]", detlog_text)
    }


def emit(
    dimension: str,
    backend: str,
    value: Any,
    *,
    guest_source: str | Path,
    guest_stdout: str,
    detlog_text: str = "",
    manifest: dict[str, Any] | None = None,
    notes: Iterable[str] = (),
) -> Cell:
    """Build one cell, REFUSING if this guest cannot exercise this dimension.

    Returns a Cell that necessarily records its guest; raises RefusedError
    otherwise. There is deliberately no flag to bypass this.
    """
    m = manifest or load_manifest()
    ref = reference_for(dimension, m)

    actual = source_sha256(guest_source)
    expected = ref["source_sha256"]
    if actual != expected:
        raise RefusedError(
            f"dimension {dimension!r} may only be measured on its reference guest "
            f"{ref['source']} (sha256 {expected[:12]}...), but this cell was "
            f"measured on {guest_source} (sha256 {actual[:12]}...). "
            f"{ref.get('why','')} "
            f"Disqualified: {'; '.join(ref.get('disqualifies', []))}"
        )

    pattern = ref["witness_stdout_regex"]
    if not re.search(pattern, guest_stdout or "", re.M):
        raise RefusedError(
            f"dimension {dimension!r}: reference guest {ref['source']} did not print "
            f"its witness {pattern!r}. The right binary ran the wrong way (loader "
            f"failure, truncation, or a killed run); its output cannot be a cell."
        )

    if ref.get("witness_requires_stack_growth"):
        ranges = stack_growth_ranges(detlog_text)
        sizes = {b - a for a, b in ranges}
        if len(sizes) < 2:
            raise RefusedError(
                f"dimension {dimension!r}: the [stack] mapping never grew "
                f"({len(ranges)} distinct range(s), {len(sizes)} distinct size(s)). "
                "A static stack mapping means the frames were never deep enough to "
                "move it, so any hashes emitted describe loader scratch. This is the "
                "/bin/true failure mode: 31 populated hashes, 0/31 agreement."
            )

    return Cell(
        dimension=dimension,
        backend=backend,
        value=value,
        guest=ref["source"],
        guest_sha256=actual,
        witness=re.search(pattern, guest_stdout, re.M).group(0),
        notes=tuple(notes),
    )
