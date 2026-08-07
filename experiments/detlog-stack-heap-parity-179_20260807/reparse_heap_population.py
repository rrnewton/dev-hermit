#!/usr/bin/env python3
"""Re-parse ONLY: lift the heap-constant rate from a 51-cell sample to the 179 population.

No new runs. Everything here is derived from `results.csv` in this directory
(content commit d7168e259be2ccfbee8e9717b1a540fac219e41c), so the sweep's
measurement conditions -- Hermit 0041130c, Reverie 0ae0c01b, the fixed-179
population -- are inherited verbatim from the README rather than re-established.

WHAT IS BEING CORRECTED. Every heap dimension carries one initial-heap-image
record that is identical across guests (hash 74518f204d46de66..., measured
identical on 48/48 sampled cells that had any heap record). It is not a
measurement of the guest. The correction derived by the investigating task is
PER-CELL and uniform: **-1 record on every cell that has any heap record at
all**, independent of guest runtime and of backend. This script APPLIES that
correction across all 179; it does not re-derive it.

THE BOUND ON A RE-PARSE, stated up front because it limits what this can prove.
`results.csv` preserves per-cell record COUNTS (`ref_records`, `backend_records`)
and whole-dimension digests (`ref_dimension_sha256`), but NOT per-record hashes.
So the identity claim -- "the subtracted record is specifically 74518f20..." --
is NOT re-verifiable from this file. What IS verifiable here is the correction's
SHAPE (exactly one record per cell, uniform across backends), by two independent
population checks that need no hashes; see checks (1) and (3) below.
"""

from __future__ import annotations

import collections
import csv
import os
import sys

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.csv")
REFERENCE_LANE = "ptrace-control"


def heap_rows(path: str = RESULTS) -> list[dict]:
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh) if r["dimension"] == "heap"]


def reference_records(rows: list[dict]) -> dict[str, int]:
    """Per-cell raw heap record count from the ptrace reference.

    Check (1): this is asserted to be a per-CELL property, so it must agree
    across all six backend rows of a cell. If it did not, "-1 per cell" would be
    ill-defined and the correction could not be uniform.
    """
    seen: dict[str, set[int]] = {}
    for r in rows:
        try:
            seen.setdefault(r["test_id"], set()).add(int(r["ref_records"]))
        except ValueError:
            continue
    disagree = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    if disagree:
        raise AssertionError(
            f"ref_records is not a per-cell property for {len(disagree)} cells: "
            f"{list(disagree.items())[:3]} -- the -1-per-cell correction is not "
            f"well defined and must not be applied")
    return {k: v.pop() for k, v in seen.items()}


def disposition(raw: int) -> str:
    """The typed heap disposition, expressed in raw-count terms.

    Mirrors compat-envelope/heap_disposition.py: raw 0 -> no records at all;
    raw 1 -> the constant and nothing else; raw >= 2 -> at least one guest record.
    """
    if raw == 0:
        return "no-heap-activity"      # NO-RESULT
    if raw == 1:
        return "heap-constant-only"    # NO-RESULT
    return "exercised"


def main() -> int:
    rows = heap_rows()
    ref = reference_records(rows)                      # check (1) runs here
    n = len(ref)
    kinds = collections.Counter(disposition(v) for v in ref.values())
    exercised = {k for k, v in ref.items() if disposition(v) == "exercised"}
    dist = collections.Counter(ref.values())

    print(f"population: {n} cells, {len(rows)} heap rows "
          f"({len(rows)//n} targets/cell)\n")

    print("DISPOSITION OVER THE FULL 179")
    for kind in ("no-heap-activity", "heap-constant-only", "exercised"):
        c = kinds[kind]
        tag = "" if kind == "exercised" else "   NO-RESULT"
        print(f"  {kind:20s} {c:4d}  {c/n*100:5.1f}%{tag}")
    print(f"\n  POPULATION EXERCISED RATE: {len(exercised)}/{n} = "
          f"{len(exercised)/n*100:.1f}%   (51-cell sample said 94.1%)")

    # Check (3): a hard floor at raw==2 with an EMPTY bin at raw==1 is the
    # population signature of a per-cell +1 offset. Without a universal constant,
    # raw==1 would be the natural floor for the lightest allocating guest and
    # would be well populated given how many cells sit at exactly 2.
    print(f"\nOFFSET COROBORATION (needs no per-record hashes)")
    print(f"  raw==0 {dist[0]:4d}   raw==1 {dist[1]:4d}   raw==2 {dist[2]:4d}   "
          f"raw>=3 {sum(v for k, v in dist.items() if k >= 3):4d}")
    if dist[1] == 0 and dist[2] > 0:
        print(f"  -> floor at 2, bin at 1 EMPTY: consistent with one constant + >=1 guest record")
    else:
        print(f"  -> bin at 1 is populated ({dist[1]}): the uniform +1 offset is NOT supported")

    # Record-level share -- the number the sample got materially wrong.
    total = sum(ref.values())
    const = sum(1 for v in ref.values() if v > 0)
    print(f"\nRECORD-LEVEL SHARE OF THE CONSTANT")
    print(f"  population: {const}/{total} = {const/total*100:.2f}% of all heap records")
    print(f"  sample:     48/290 = 16.55%   <-- sample overstated by "
          f"{(48/290)/(const/total):.0f}x")

    print(f"\nPUBLISHED FIGURE, RE-DERIVED FROM THE ROWS (definition correction)")
    print(f"  {'backend':10s} {'published':>12s} {'corrected':>12s}")
    for be in sorted({r['backend'] for r in rows}):
        br = [r for r in rows if r["backend"] == be]
        old = sum(1 for r in br if r["result"] == "PASS")
        new = sum(1 for r in br if r["result"] == "PASS" and r["test_id"] in exercised)
        lane = "  (reference lane)" if be == REFERENCE_LANE else ""
        print(f"  {be:10s} {f'{old}/{len(br)}':>12s} {f'{new}/{len(exercised)}':>12s}{lane}")

    # Check (2): the correction must not manufacture or destroy a green. Every
    # dropped cell should already be a failure/no-result for an unrelated reason.
    print(f"\nDROPPED CELLS -- each must already be non-passing on its own")
    dropped = sorted(k for k in ref if k not in exercised)
    clean = True
    for t in dropped:
        rs = sorted({r["result"] for r in rows
                     if r["test_id"] == t and r["backend"] != REFERENCE_LANE})
        if any(x == "PASS" for x in rs):
            clean = False
        print(f"  raw={ref[t]:<3d} {t[:52]:52s} {rs}")
    print(f"  -> {'no PASS removed: numerator unchanged' if clean else 'A PASS WAS REMOVED -- investigate'}")
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
