#!/usr/bin/env python3
"""Refuse to let any published scorecard lack the `tier` column.

WHY THIS DERIVES THE SET INSTEAD OF LISTING IT. Two scorecards were found still
publishing untiered rows, and the cause was that
`migrate-scorecard-schema.py` defaults to a single filename
(`csv_path ... default=.../scorecard.csv`, line ~194) -- so whoever ran the
migration migrated one file and the others silently kept the old schema. A guard
that hardcoded "check these four" would have exactly the same defect one
scorecard later: the set GROWS, and a stale list is how the class stays open.
So the set is globbed from disk on every run, and the count is printed so a
reader can see the denominator rather than trust it.

`.pre-tier-migration` backups are excluded on purpose: they are pre-migration
snapshots kept deliberately, and failing on them would train people to ignore
this check -- which is the failure mode that lets a real untiered scorecard
through.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = "tier"
EXCLUDE_SUFFIXES = (".pre-tier-migration",)


def scorecards(root: Path) -> list[Path]:
    return sorted(
        p for p in root.glob("*scorecard*.csv")
        if not any(str(p).endswith(s) for s in EXCLUDE_SUFFIXES)
    )


def header(path: Path) -> list[str]:
    with path.open(newline="") as fh:
        for row in csv.reader(fh):
            return [c.strip() for c in row]
    return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=HERE)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    found = scorecards(a.root)
    if not found:
        print(f"UNVERIFIABLE: no *scorecard*.csv under {a.root}", file=sys.stderr)
        return 2

    offenders = []
    for p in found:
        cols = header(p)
        if REQUIRED not in cols:
            offenders.append((p, len(cols)))

    if not a.quiet:
        print(f"scorecards enumerated: {len(found)} (derived by glob, not a list)")
        for p in found:
            cols = header(p)
            mark = "ok " if REQUIRED in cols else "NO-TIER"
            print(f"  {mark:<8} {p.name:<30} {len(cols)} cols")

    if offenders:
        print(f"\nREFUSED: {len(offenders)}/{len(found)} scorecard(s) publish untiered rows:",
              file=sys.stderr)
        for p, n in offenders:
            print(f"  {p.name} ({n} cols, no '{REQUIRED}')", file=sys.stderr)
        print("Run compat-envelope/migrate-scorecard-schema.py against EACH one -- it defaults to a "
              "single filename, which is how these were missed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
