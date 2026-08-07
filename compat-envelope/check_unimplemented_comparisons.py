#!/usr/bin/env python3
"""Refuse a cell that appears to satisfy a comparison NOTHING PERFORMS.

THE CLASS THIS CLOSES, and why it is not covered by the landed guards.
`check_cell_comparison.py` refuses a verdict carrying no reference.
`check_emitter_schema.py` refuses an emitter whose row shape its target cannot
hold. Neither can see the state found on 2026-08-07: a comparison column that is
BLANK ON EVERY PUBLISHED ROW. Such a column has no producer behaviour at all --
it is not failing, it is absent -- and absence is invisible to both:

  * the schema guard sees the column present in the header and is satisfied;
  * the reference guard iterates verdicts, finds none, and reports `0 of 0
    verdict(s) lack a reference`, exit 0. It passes BECAUSE the producer is
    missing. A passing guard there currently means nothing.

A blank-everywhere column also cannot be mutation-tested: there is nothing to
plant a wrong value in, so the usual can-this-fail proof is unavailable. That is
what makes the state so durable -- every check it touches reports success.

WHY THIS MATTERS MORE THAN A WEAK GREEN. The strict standard is stdout + INFO
log + stack + heap. Measured across 4 scorecards / 2290 rows: stdout_parity 0 of
2278 non-blank; compared_log_messages 6 of 2290; stack_hash 6 of 624; heap_hash
6 of 624. Before those 6 rows, all four legs were zero. A cell claiming the
strict standard was claiming four comparisons of which NONE were recorded. That
is a green against an empty check.

WHAT THIS TOOL DOES. `scorecard-schema.json` declares, under
`unimplemented_comparisons`, which columns have no producer. This refuses any
non-blank value in one of them. The declaration is therefore load-bearing in
BOTH directions and cannot rot into a lie:

  * a row that populates a declared-unimplemented column is refused, so no cell
    can quietly appear to satisfy a comparison nobody performs;
  * implementing a real producer REQUIRES deleting the declaration in the same
    change, which is what makes the list shrink honestly instead of being
    forgotten while values start appearing underneath it.

It also reports `comparisons_with_no_column` -- exit code and detlog -- which are
a strictly worse state than an absent producer: the schema has nowhere to record
them, so a producer could not write them even if one existed. Those are reported,
never silently omitted, on the same rule that an unmeasurable emitter is refused
rather than skipped.

POPULATION DISCIPLINE, matching the sibling guards: the root is an explicit
argument defaulting to the repository root via `git rev-parse --show-toplevel`
(never `__file__`, never the cwd), every enumerated path is printed before any
verdict, and counts are reported as `k of N`.
"""

from __future__ import annotations

import argparse
import csv as csvmod
import json
import subprocess
import sys
from pathlib import Path

SCHEMA = "scorecard-schema.json"
EXCLUDE_SUFFIXES = (".pre-tier-migration",)


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise SystemExit("check_unimplemented_comparisons: not in a git checkout")
    return Path(out.stdout.strip())


def scorecards(envelope: Path) -> list[Path]:
    return sorted(
        p for p in envelope.glob("*scorecard*.csv")
        if not any(str(p).endswith(s) for s in EXCLUDE_SUFFIXES)
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=None,
                    help="repository root (default: git rev-parse --show-toplevel)")
    a = ap.parse_args(argv)
    root = a.root or repo_root()
    envelope = root / "compat-envelope"

    schema = json.loads((envelope / SCHEMA).read_text())
    declared = schema.get("unimplemented_comparisons") or {}
    no_producer = declared.get("columns_with_no_producer") or {}
    no_column = declared.get("comparisons_with_no_column") or {}

    files = scorecards(envelope)
    print("check_unimplemented_comparisons")
    print(f"  schema      : {(envelope / SCHEMA).relative_to(root)}")
    print(f"  population  : {len(files)} scorecard(s), enumerated by glob")
    for p in files:
        print(f"      {p.relative_to(root)}")

    if not files:
        print("\n  REFUSED: no scorecards found; an empty population cannot pass.")
        return 1

    # Census every declared column across every row, then refuse on the totals.
    offenders: list[str] = []
    rows_total = 0
    present: dict[str, int] = {c: 0 for c in no_producer}
    populated: dict[str, int] = {c: 0 for c in no_producer}

    for p in files:
        with p.open(newline="") as fh:
            rows = list(csvmod.DictReader(fh))
        rows_total += len(rows)
        if not rows:
            continue
        cols = set(rows[0].keys())
        for column in no_producer:
            if column not in cols:
                continue
            present[column] += len(rows)
            for line, row in enumerate(rows, start=2):
                value = (row.get(column) or "").strip()
                if value:
                    populated[column] += 1
                    if len(offenders) < 20:
                        offenders.append(
                            f"{p.name}:{line}: {column}={value!r} but the schema "
                            f"declares it has no producer"
                        )

    print(f"\n  declared with NO PRODUCER: {len(no_producer)} column(s)")
    for column in no_producer:
        n, tot = populated[column], present[column]
        state = "blank as declared" if n == 0 else f"*** {n} POPULATED ***"
        print(f"      {column:<26} {n} of {tot} row(s) non-blank   {state}")

    print(f"\n  declared NOT REPRESENTABLE: {len(no_column)} comparison(s)")
    for name, why in no_column.items():
        print(f"      {name:<26} {why.split('.')[0]}.")

    print(f"\n  rows examined: {rows_total}")

    if offenders:
        print("\n  REFUSED:")
        for o in offenders:
            print(f"    {o}")
        print(
            f"\n  A value appeared in a column the schema says nothing produces. "
            f"Either a real producer was added -- in which case DELETE that column "
            f"from unimplemented_comparisons in the same change, so the list "
            f"shrinks honestly -- or a cell is claiming a comparison that was "
            f"never performed. Do not blank the value to silence this; that "
            f"restores the invisible state this guard exists to end."
        )
        return 1

    total_declared = len(no_producer) + len(no_column)
    print(
        f"\n  result        : 0 of {rows_total} row(s) populate a declared-"
        f"unimplemented column; {total_declared} unimplemented comparison(s) "
        f"declared and none can be silently satisfied"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
